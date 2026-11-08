"""Словарь курса (бестиарий): немецкий термин → русский. Из него переводчик субтитров берёт перевод терминов,
чтобы «Freigabe» на всех уроках было одним и тем же словом, а Капри отвечает «что такое Freigabe» без интернета.

Откуда берётся (ниже в списке — главнее):
  • таблицы «Термины» из конспектов уроков;
  • бестиарий в CSV («Deutsch;Русский;Заметка;Источник;Тема») — «импортируй бестиарий» кладёт копию в data\\glossary\\;
  • Документы\\Capri\\Уроки\\Словарь.md — таблица «| Deutsch | Русский | Заметка |» и слова, добавленные двойным кликом.
Бестиарий личный (там твои заметки), поэтому в установщик Капри он не входит: на новом компьютере — «импортируй
бестиарий» один раз. Обновления Капри папку data не трогают.
"""
from __future__ import annotations

import csv
import io
import re
import shutil
from pathlib import Path

from . import notes as N

HEADER = "# Словарь курса\n\n| Deutsch | Русский | Заметка |\n|---|---|---|\n"
ROW = re.compile(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|(?:\s*([^|]*?)\s*\|)?\s*$")
_cache: dict = {"key": None, "terms": {}, "entries": []}


def path(root: Path | None = None) -> Path:
    return (root or N.lessons_dir()) / "Словарь.md"


def csv_dir() -> Path:
    from ..config import expand
    return expand("data/glossary")


# ---------- CSV ----------

def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("latin-1")


def read_csv(p: Path) -> list[dict]:
    """CSV с «;» или «,»; с заголовком (Deutsch/Русский/Заметка/Тема) или без — тогда колонки по порядку."""
    text = _decode(Path(p).read_bytes())
    sample = text[:4000]
    delim = ";" if sample.count(";") >= sample.count(",") else ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    if not rows:
        return []
    head = [h.strip().lower() for h in rows[0]]

    def col(*names):
        return next((i for i, h in enumerate(head) if any(n in h for n in names)), None)
    i_de = col("deutsch", "german", "begriff", "термин")
    has_head = i_de is not None
    i_de = i_de if has_head else 0
    i_ru = col("русск", "russ", "перевод") if has_head else 1
    i_ru = 1 if i_ru is None else i_ru
    i_note = col("замет", "note", "notiz", "коммент") if has_head else 2
    i_topic = col("тема", "thema", "topic") if has_head else None
    out = []
    for r in rows[1 if has_head else 0:]:
        get = lambda i: r[i].strip() if i is not None and i < len(r) else ""  # noqa: E731
        de, ru = get(i_de), get(i_ru)
        if de and ru:
            out.append({"de": de, "ru": ru, "note": get(i_note), "topic": get(i_topic)})
    return out


def import_file(src: Path) -> int:
    """«импортируй бестиарий»: копия CSV в data\\glossary\\ (оригинал не трогаем). → сколько терминов."""
    src = Path(src)
    if src.suffix.lower() == ".md":
        n = len(_rows(src.read_text(encoding="utf-8", errors="replace")))
        if not n:
            raise ValueError("в файле нет таблицы «| Deutsch | Русский | … |»")
        d = csv_dir()
        d.mkdir(parents=True, exist_ok=True)
        rows = [{"de": k, "ru": v, "note": nt} for k, (v, nt) in
                _rows(src.read_text(encoding="utf-8", errors="replace")).items()]
        with open(d / (src.stem + ".csv"), "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["Deutsch", "Русский", "Заметка"])
            w.writerows([[r["de"], r["ru"], r["note"]] for r in rows])
        _cache["key"] = None
        return n
    rows = read_csv(src)
    if not rows:
        raise ValueError("в файле не нашла терминов (нужны колонки Deutsch;Русский;Заметка)")
    d = csv_dir()
    d.mkdir(parents=True, exist_ok=True)
    if src.resolve() != (d / src.name).resolve():
        shutil.copyfile(src, d / src.name)
    _cache["key"] = None
    return len(rows)


# ---------- Markdown ----------

def _rows(text: str) -> dict[str, tuple[str, str]]:
    out = {}
    for ln in text.splitlines():
        m = ROW.match(ln.strip())
        if not m:
            continue
        de, ru = m.group(1).strip(), m.group(2).strip()
        if set(de) <= set("-: ") or de.lower() in ("deutsch", "термин", "begriff") or not ru:
            continue
        out[de] = (ru, (m.group(3) or "").strip())
    return out


# ---------- всё вместе ----------

def _files(root: Path) -> list[Path]:
    lessons = [p / "конспект.md" for p in N.lessons(root)]
    d = csv_dir()
    csvs = sorted(d.glob("*.csv")) if d.exists() else []
    csvs += sorted(root.glob("*.csv")) if root.exists() else []
    return [f for f in lessons + csvs + [path(root)] if f.exists()]


def entries(root: Path | None = None) -> list[dict]:
    """Все термины: [{de, ru, note, topic}]. Кэш — пока файлы не менялись."""
    root = root or N.lessons_dir()
    files = _files(root)
    key = tuple((str(f), f.stat().st_mtime) for f in files)
    if _cache["key"] == key:
        return _cache["entries"]
    by_de: dict[str, dict] = {}
    for f in files:                                  # позже в списке — главнее
        try:
            if f.suffix.lower() == ".csv":
                rows = read_csv(f)
            else:
                text = f.read_text(encoding="utf-8", errors="replace")
                if f.name == "конспект.md":
                    m = re.search(r"^##\s*Термины\s*\n(.*?)(?=^##\s|\Z)", text, re.M | re.S)
                    text = m.group(1) if m else ""
                rows = [{"de": k, "ru": v, "note": n, "topic": ""} for k, (v, n) in _rows(text).items()]
        except (OSError, csv.Error):
            continue
        for r in rows:
            by_de[r["de"].lower()] = r
    out = list(by_de.values())
    _cache.update(key=key, entries=out, terms={r["de"]: r["ru"] for r in out})
    return out


def load(root: Path | None = None) -> dict[str, str]:
    entries(root)
    return _cache["terms"]


def add(de: str, ru: str, note: str = "", root: Path | None = None) -> Path:
    p = path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = p.read_text(encoding="utf-8") if p.exists() else HEADER
    if de.strip().lower() in {k.lower() for k in _rows(text)}:
        return p
    clean = lambda s: s.replace("|", "/").replace("\n", " ").strip()  # noqa: E731
    p.write_text(text.rstrip("\n") + f"\n| {clean(de)} | {clean(ru)} | {clean(note)} |\n", encoding="utf-8")
    return p


def variants(term: str) -> list[str]:
    """«Active Directory (AD)» → «Active Directory», «AD»; «Blackbox- / Whitebox-Test» → оба; без артикля."""
    out = []
    paren = re.findall(r"\(([^)]+)\)", term)
    base = re.sub(r"\([^)]*\)", " ", term)
    for part in re.split(r"\s*/\s*|,\s+|\s+vs\.?\s+|\s+(?:oder|und)\s+", base) + paren:
        part = re.sub(r"^(?:der|die|das)\s+", "", part.strip(" -–:;."), flags=re.I).strip()
        if len(part) >= 2 and not re.fullmatch(r"[\d.\s]+", part) and part not in out:
            out.append(part)
    return out or [term.strip()]


def _core(term: str) -> str:
    """«die Freigabe» → «Freigabe» (артикль не ищем в тексте)."""
    return re.sub(r"^(?:der|die|das)\s+", "", term.strip(), flags=re.I)


def relevant(lines: list[str], terms: dict[str, str], limit: int = 25) -> dict[str, str]:
    """Только термины, которые встречаются в этих фразах (промпт короче — перевод быстрее).
    Короткие (AD, DNS) — целым словом; длинные — по основе, чтобы «Freigaben», «Benutzerkonten» тоже ловились."""
    text = " ".join(lines).lower()
    out = {}
    for de, ru in terms.items():
        for v in variants(de):
            core = v.lower()
            if len(core) <= 4:
                hit = re.search(rf"(?<!\w){re.escape(core)}(?!\w)", text)
            else:
                hit = re.search(rf"(?<!\w){re.escape(core[:max(5, len(core) - 2)])}", text)
            if hit:
                out[de] = ru
                break
        if len(out) >= limit:
            break
    return out


def find(term: str, limit: int = 8, root: Path | None = None) -> list[dict]:
    """Поиск: точное совпадение (термин, вариант, перевод) — первым, потом вхождения в термин/перевод/заметку."""
    t = re.sub(r"^(?:der|die|das)\s+", "", term.strip().strip("?!.«»\"'"), flags=re.I).lower()
    if len(t) < 2:
        return []
    exact, starts, part = [], [], []
    for e in entries(root):
        vs = [v.lower() for v in variants(e["de"])] + [e["de"].lower()]
        ru = e["ru"].lower()
        if t in vs or t == ru:
            exact.append(e)
        elif any(v.startswith(t) for v in vs):
            starts.append(e)
        elif len(t) >= 3 and (any(t in v for v in vs) or t in ru or (len(t) >= 4 and t in e["note"].lower())):
            part.append(e)
    return (exact + starts + part)[:limit]


def line(e: dict) -> str:
    note = e.get("note", "")
    return f"**{e['de']}** — {e['ru']}" + (f". {note}" if note and note not in ("—", "-") else "")


def count(root: Path | None = None) -> int:
    return len(entries(root))
