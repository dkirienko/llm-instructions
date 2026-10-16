#!/usr/bin/env python3
"""Сборка одностраничного сайта с инструкциями для машинного чтения.

Вход:   site.json   — метаданные (заголовок, версия, адрес)
        content.md  — тело инструкций (единственный файл для правки)
        templates/index.html — вёрстка с плейсхолдерами {{...}}

Выход:  index.html  — страница для человека и для браузерного агента
        raw.md      — инструкции в Markdown
        llms.txt    — инструкции в чистом тексте (основная точка для LLM)

Использование:
    python3 tools/build.py           пересобрать
    python3 tools/build.py --check   проверить, что закоммиченные файлы актуальны
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

try:
    import markdown
except ImportError:
    sys.exit("Нужен пакет python-markdown:  pip install markdown")

ROOT = Path(__file__).resolve().parent.parent
MD_EXTENSIONS = ["extra", "toc", "sane_lists"]

C_CONTENT = "\x00CONTENT\x00"
C_SUMMARY = "\x00SUMMARY\x00"
TOKEN_RE = re.compile(r"\{\{([A-Z_]+)\}\}")


# --------------------------------------------------------------------------- ввод

def load_config() -> dict:
    cfg = json.loads((ROOT / "site.json").read_text(encoding="utf-8"))
    for key in ("title", "description", "summary", "version", "owner", "repo"):
        if not cfg.get(key):
            sys.exit(f"site.json: не заполнено поле {key!r}")
    return cfg


def load_content() -> str:
    text = (ROOT / "content.md").read_text(encoding="utf-8")

    # Ведущие служебные блоки не должны попасть в выдачу: HTML-комментарии
    # (заметки для автора) и H1, который дублировал бы заголовок из site.json.
    while True:
        stripped = re.sub(r"\A\s*<!--.*?-->\s*", "", text, flags=re.S)
        stripped = re.sub(r"\A\s*#\s+[^\n]*\n+", "", stripped)
        if stripped == text:
            break
        text = stripped

    if re.search(r"<!--", text):
        print("внимание: в content.md остались HTML-комментарии, они попадут в выдачу",
              file=sys.stderr)
    if not text.strip():
        sys.exit("content.md пуст")
    return text.strip() + "\n"


# ------------------------------------------------------------------- производные

def resolve_date(cfg: dict) -> tuple[str, str]:
    """Возвращает (ISO для datetime=, текст для показа)."""
    raw = cfg.get("date")
    if raw:
        try:
            d = datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            sys.exit(f"site.json: date должен быть в формате YYYY-MM-DD, получено {raw!r}")
    else:
        d = datetime.now(timezone.utc).date()
    return d.isoformat(), d.strftime("%Y-%m-%d")


def resolve_canonical(cfg: dict) -> str:
    domain = cfg.get("domain")
    base = f"https://{domain}" if domain else f"https://{cfg['owner']}.github.io/{cfg['repo']}"
    return base.rstrip("/") + "/"


def render_markdown(body: str) -> str:
    md = markdown.Markdown(extensions=MD_EXTENSIONS, output_format="html")
    return md.convert(body)


# ------------------------------------------------------------------------ выводы

def build_raw_md(cfg: dict, body: str, date_text: str, canonical: str) -> str:
    return (
        f"# {cfg['title']}\n\n"
        f"{cfg['summary']}\n\n"
        f"- Версия: {cfg['version']}\n"
        f"- Обновлено: {date_text}\n"
        f"- Канонический адрес: {canonical}\n"
        f"- Служебная страница для машинного чтения: "
        f"{canonical.rstrip('/')}/llms.txt\n\n"
        f"---\n\n"
        f"{body}"
    )


def build_llms_txt(cfg: dict, body: str, date_text: str, canonical: str) -> str:
    base = canonical.rstrip("/")
    repo_url = f"https://github.com/{cfg['owner']}/{cfg['repo']}"
    return (
        f"# {cfg['title']}\n\n"
        f"> {cfg['summary']}\n\n"
        f"Документ предназначен для чтения языковой моделью или агентом в автоматическом\n"
        f"режиме. Ниже — полные инструкции целиком, без сокращений и отсылок к другим файлам.\n\n"
        f"- Версия: {cfg['version']}\n"
        f"- Обновлено: {date_text}\n"
        f"- Аудитория: {cfg.get('audience') or 'не указана'}\n"
        f"- Язык: {cfg.get('lang', 'ru')}\n\n"
        f"## Файлы\n\n"
        f"- [Полные инструкции, чистый текст]({base}/llms.txt): этот документ\n"
        f"- [Полные инструкции, Markdown]({base}/raw.md): то же содержимое в Markdown\n"
        f"- [Страница для человека]({base}/): то же содержимое в HTML\n"
        f"- [Исходный репозиторий]({repo_url}): правки, история версий\n\n"
        f"## Инструкции\n\n"
        f"{body}"
    )


def build_html(cfg: dict, body_html: str, summary_html: str, date_iso: str,
               date_text: str, canonical: str) -> str:
    template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")

    template = template.replace("{{CONTENT}}", C_CONTENT)
    template = template.replace("{{SUMMARY_HTML}}", C_SUMMARY)

    values = {
        "TITLE": cfg["title"],
        "DESCRIPTION": cfg["description"],
        "CANONICAL": canonical,
        "VERSION": cfg["version"],
        "DATE": date_text,
        "DATE_ISO": date_iso,
        "LANG": cfg.get("lang", "ru"),
    }
    attr_safe = {"TITLE", "DESCRIPTION", "CANONICAL", "VERSION", "DATE", "DATE_ISO", "LANG"}

    def substitute(match: re.Match) -> str:
        token = match.group(1)
        if token not in values:
            sys.exit(f"templates/index.html: неизвестный плейсхолдер {{{{{token}}}}}")
        value = values[token]
        return html.escape(value, quote=True) if token in attr_safe else value

    page = TOKEN_RE.sub(substitute, template)
    page = page.replace(C_SUMMARY, summary_html).replace(C_CONTENT, body_html)

    if "\x00" in page:
        sys.exit("внутренняя ошибка: незаполненный слот")
    return page


def outputs(cfg: dict, body: str) -> dict[str, str]:
    date_iso, date_text = resolve_date(cfg)
    canonical = resolve_canonical(cfg)
    body_html = render_markdown(body)
    summary_html = f"<p><strong>{html.escape(cfg['summary'])}</strong></p>\n"

    return {
        "index.html": build_html(cfg, body_html, summary_html, date_iso, date_text, canonical),
        "raw.md": build_raw_md(cfg, body, date_text, canonical),
        "llms.txt": build_llms_txt(cfg, body, date_text, canonical),
    }


# ----------------------------------------------------------------------------- main

def main() -> int:
    parser = argparse.ArgumentParser(description="Сборка сайта с инструкциями для LLM")
    parser.add_argument("--check", action="store_true",
                        help="не записывать файлы, а лишь проверить их актуальность")
    args = parser.parse_args()

    cfg = load_config()
    body = load_content()
    files = outputs(cfg, body)

    if args.check:
        stale = []
        for name, text in files.items():
            path = ROOT / name
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                stale.append(name)
        if stale:
            print("устаревшие файлы (запустите python3 tools/build.py): " + ", ".join(stale),
                  file=sys.stderr)
            return 1
        print("всё актуально")
        return 0

    for name, text in files.items():
        (ROOT / name).write_text(text, encoding="utf-8")
        print(f"записан {name} ({len(text.encode('utf-8'))} байт)")

    print(f"\nканонический адрес: {resolve_canonical(cfg)}")
    print(f"версия {cfg['version']}, дата {resolve_date(cfg)[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())