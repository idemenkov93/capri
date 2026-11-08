"""Словарь курса (бестиарий): немецкий термин → русский. Из него переводчик субтитров берёт перевод терминов,
чтобы «Freigabe» на всех уроках было одним и тем же словом.

Откуда берётся: Документы\\Capri\\Уроки\\Словарь.md (таблица «| Deutsch | Русский | Заметка |» — можно вставить
свой бестиарий целиком), таблицы «Термины» из конспектов уроков и слова, добавленные двойным кликом в записи урока.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import notes as N

HEADER = "# Словарь курса\n\n| Deutsch | Русский | Заметка |\n|---|---|---|\n"
ROW = re.compile(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|(?:\s*([^|]*?)\s*\|)?\s*$")
_cache: dict = {"key": None, "terms": {}}


def path(root: Path | None = None) -> Path:
    return (root or N.lessons_dir()) / "Словарь.md"


def _rows(text: str) -> dict[str, str]:
    out = {}
    for ln in text.splitlines():
        m = ROW.match(ln.strip())
        if not m:
            continue
        de, ru = m.group(1).strip(), m.group(2).strip()
        if set(de) <= set("-: ") or de.lower() in ("deutsch", "термин", "begriff") or not ru:
            continue
        out[de] = ru
    return out


def load(root: Path | None = None) -> dict[str, str]:
    """Свой словарь важнее терминов из конспектов. Кэш — пока файлы не менялись."""
    root = root or N.lessons_dir()
    files = [path(root)] + [p / "конспект.md" for p in N.lessons(root)]
    files = [f for f in files if f.exists()]
    key = tuple((str(f), f.stat().st_mtime) for f in files)
    if _cache["key"] == key:
        return _cache["terms"]
    terms: dict[str, str] = {}
    for f in reversed(files):                   # конспекты, потом свой словарь — он перекрывает
        text = f.read_text(encoding="utf-8", errors="replace")
        if f.name == "конспект.md":
            m = re.search(r"^##\s*Термины\s*\n(.*?)(?=^##\s|\Z)", text, re.M | re.S)
            text = m.group(1) if m else ""
        terms.update(_rows(text))
    _cache.update(key=key, terms=terms)
    return terms


def add(de: str, ru: str, note: str = "", root: Path | None = None) -> Path:
    p = path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = p.read_text(encoding="utf-8") if p.exists() else HEADER
    if de.strip().lower() in {k.lower() for k in _rows(text)}:
        return p
    clean = lambda s: s.replace("|", "/").replace("\n", " ").strip()  # noqa: E731
    p.write_text(text.rstrip("\n") + f"\n| {clean(de)} | {clean(ru)} | {clean(note)} |\n", encoding="utf-8")
    return p


def _core(term: str) -> str:
    """«die Freigabe» → «Freigabe» (артикль не ищем в тексте)."""
    return re.sub(r"^(?:der|die|das)\s+", "", term.strip(), flags=re.I)


def relevant(lines: list[str], terms: dict[str, str], limit: int = 25) -> dict[str, str]:
    """Только термины, которые встречаются в этих фразах (промпт короче — перевод быстрее)."""
    text = " ".join(lines).lower()
    out = {}
    for de, ru in terms.items():
        core = _core(de).lower()
        if len(core) >= 2 and re.search(rf"(?<!\w){re.escape(core[:max(4, len(core) - 2)])}", text):
            out[de] = ru
            if len(out) >= limit:
                break
    return out
