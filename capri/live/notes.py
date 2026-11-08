"""После урока: транскрипт (немецкий + перевод), конспект по-русски (Markdown и PDF), общий индекс уроков,
«повтори прошлый урок», «что мы проходили по DNS».

Папка: Документы\\Capri\\Уроки\\2026-11-08 0900 — Тема\\ (транскрипт.md, конспект.md, конспект.pdf).
Звук не сохраняется — только текст.
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Callable
from urllib.parse import quote_plus

WHO = {"system": "Они", "mic": "Ты"}
CHUNK = 14000                    # столько символов транскрипта влезает в один запрос комфортно


def lessons_dir() -> Path:
    from ..files import known_folder
    return known_folder("documents") / "Capri" / "Уроки"


def _ts(sec: float) -> str:
    sec = int(max(0, sec))
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def transcript_md(lines, started: dt.datetime) -> str:
    out = [f"# Транскрипт {started:%d.%m.%Y %H:%M}", "",
           "_«Они» — звук компьютера (учитель, собеседник), «Ты» — твой микрофон. Звук не сохранялся._", ""]
    for ln in lines:
        mark = f" 🔔 {ln.trigger}" if getattr(ln, "trigger", "") else ""
        out.append(f"**[{_ts(ln.start)}] {WHO.get(ln.source, ln.source)}:** {ln.text}{mark}")
        if ln.translation:
            out.append(f"> {ln.translation}")
        out.append("")
    return "\n".join(out)


def transcript_plain(lines) -> str:
    return "\n".join(f"[{_ts(ln.start)}] {WHO.get(ln.source, ln.source)}: {ln.text}" for ln in lines)


def save_session(lines, started: dt.datetime, root: Path | None = None) -> Path:
    root = root or lessons_dir()
    folder = root / f"{started:%Y-%m-%d %H%M}"
    n = 2
    while folder.exists() and (folder / "транскрипт.md").exists():
        folder = root / f"{started:%Y-%m-%d %H%M} ({n})"
        n += 1
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "транскрипт.md").write_text(transcript_md(lines, started), encoding="utf-8")
    return folder


NOTES_PROMPT = """Ниже — транскрипт урока (курс IT-Support / Systemadministration, Германия). Язык урока — {lang}.
«Они» — учитель и группа, «Ты» — ученик Игорь (немецкий B1). Сделай конспект ПО-РУССКИ, простыми словами.
Строго в таком формате (Markdown), без вступлений:

# <тема урока в 3–7 словах>

## Коротко
<3–5 предложений: о чём был урок>

## Главное по пунктам
- <что объяснили, по порядку; команды и настройки — точно, как говорили>

## Термины
| Deutsch | Русский | Что это |
|---|---|---|
| <термин> | <перевод> | <коротко> |

## Что задали
- <домашнее задание, сроки, что подготовить; если ничего — «Ничего не задали»>

## Вопросы и ответы
- <вопросы учителя или группы и ответы на них>

## Что повторить
- <темы, которые стоит повторить>
YT: <поисковый запрос для YouTube на немецком или английском>
YT: <ещё запрос>
YT: <ещё запрос>

Пиши только то, что есть в транскрипте. Не выдумывай.

Транскрипт:
{text}"""

PART_PROMPT = """Это часть {i} из {n} транскрипта урока (IT-Support, Германия). Выпиши по-русски кратко, но точно:
что объяснили (команды, настройки, термины с немецким оригиналом), что задали, вопросы и ответы. Без вступлений.

{text}"""


def split_text(text: str, size: int = CHUNK) -> list[str]:
    parts, cur = [], []
    total = 0
    for ln in text.splitlines():
        if total + len(ln) > size and cur:
            parts.append("\n".join(cur))
            cur, total = [], 0
        cur.append(ln)
        total += len(ln) + 1
    if cur:
        parts.append("\n".join(cur))
    return parts


def make_notes(ask: Callable[[str], str], text: str, lang: str = "немецкий",
               progress: Callable[[str], None] = lambda s: None) -> str:
    """Длинный урок — сначала по частям, потом общий конспект из выжимок (map → reduce)."""
    parts = split_text(text)
    if len(parts) > 1:
        notes = []
        for i, part in enumerate(parts, 1):
            progress(f"Читаю часть {i} из {len(parts)}…")
            notes.append(f"--- Часть {i} ---\n" + ask(PART_PROMPT.format(i=i, n=len(parts), text=part)).strip())
        text = "\n\n".join(notes)
    progress("Пишу конспект…")
    return finish(ask(NOTES_PROMPT.format(lang=lang, text=text)))


def finish(md: str) -> str:
    """«YT: запрос» → ссылки на поиск YouTube (ссылки строим сами — модель ссылки не придумывает)."""
    md = re.sub(r"^```(?:markdown|md)?\s*|\s*```$", "", md.strip())

    def yt(m):
        q = m.group(1).strip().strip("«»\"")
        return f"- ▶ [{q}](https://www.youtube.com/results?search_query={quote_plus(q)})"
    return re.sub(r"^\s*-?\s*YT:\s*(.+)$", yt, md, flags=re.M)


def title_of(md: str) -> str:
    m = re.search(r"^#\s+(.+)$", md, re.M)
    return (m.group(1).strip() if m else "Урок")[:80]


def safe_name(s: str) -> str:
    return re.sub(r'[<>:"/\\|?*\n\r\t]+', " ", s).strip(" .")[:60] or "Урок"


def write_notes(folder: Path, md: str) -> Path:
    """Сохранить конспект и переименовать папку «2026-11-08 0900 — Тема»."""
    (folder / "конспект.md").write_text(md, encoding="utf-8")
    title = safe_name(title_of(md))
    if " — " not in folder.name:
        new = folder.with_name(f"{folder.name} — {title}")
        if not new.exists():
            folder = folder.rename(new)
    update_index(folder.parent)
    return folder


def md_to_pdf(md: str, path: Path) -> Path:
    """Markdown → PDF силами Qt (без лишних библиотек). Вызывать из главного потока."""
    from PySide6.QtCore import QMarginsF, QSizeF
    from PySide6.QtGui import QFont, QPageLayout, QPageSize, QPdfWriter, QTextDocument
    w = QPdfWriter(str(path))
    w.setPageLayout(QPageLayout(QPageSize(QPageSize.A4), QPageLayout.Portrait, QMarginsF(15, 15, 15, 15),
                                QPageLayout.Millimeter))
    w.setResolution(150)
    doc = QTextDocument()
    doc.setDefaultFont(QFont("Segoe UI", 10))
    doc.setDefaultStyleSheet("table { border-collapse: collapse; } td, th { border: 1px solid #999; padding: 3px; }")
    doc.setMarkdown(md)
    doc.setPageSize(QSizeF(w.width(), w.height()))
    doc.print_(w)
    return path


# ====================================================================== индекс и поиск
def lessons(root: Path | None = None) -> list[Path]:
    root = root or lessons_dir()
    if not root.exists():
        return []
    return sorted((p for p in root.iterdir() if p.is_dir() and re.match(r"\d{4}-\d{2}-\d{2}", p.name)), key=lambda p: p.name)


def update_index(root: Path) -> Path:
    rows = ["# Мои уроки", "", "Каждый урок — папка с транскриптом и конспектом. Скажи Капри «что мы проходили по …».", ""]
    for p in reversed(lessons(root)):
        md = p / "конспект.md"
        title = title_of(md.read_text(encoding="utf-8")) if md.exists() else "без конспекта"
        date = p.name[:10]
        rows.append(f"- **{date}** — [{title}](<{p.name}/{'конспект.md' if md.exists() else 'транскрипт.md'}>)")
    path = root / "Индекс.md"
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def search(term: str, root: Path | None = None, limit: int = 6) -> list[tuple[Path, str, list[str]]]:
    """→ [(папка урока, тема, строки с этим словом)] — сначала свежие."""
    rx = re.compile(re.escape(term.strip()), re.I)
    found = []
    for p in reversed(lessons(root)):
        hits, title = [], p.name
        for name in ("конспект.md", "транскрипт.md"):
            f = p / name
            if not f.exists():
                continue
            text = f.read_text(encoding="utf-8")
            if name == "конспект.md":
                title = title_of(text)
            hits += [ln.strip(" >*-") for ln in text.splitlines() if rx.search(ln) and not ln.startswith("#")][:4]
        if hits:
            found.append((p, title, hits[:4]))
        if len(found) >= limit:
            break
    return found


def latest(root: Path | None = None) -> Path | None:
    for p in reversed(lessons(root)):
        if (p / "конспект.md").exists() or (p / "транскрипт.md").exists():
            return p
    return None
