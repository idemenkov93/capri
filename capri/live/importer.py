"""Старые материалы — в базу уроков, чтобы «что мы проходили по DNS» искало и по ним.

  Granola — читаем её локальный кэш (%APPDATA%\\Granola\\cache-v*.json): заметки и транскрипты встреч → папки уроков
            «2026-10-15 0900 — Тема [Granola]» с конспект.md / транскрипт.md. Повторный импорт добавляет только новое.
  Папки   — PDF, Word, PowerPoint, TXT, Markdown (например, материалы Zukunftsmotor) → текст в Уроки\\Материалы\\<папка>\\…md.
            Изменённые файлы обновляются, остальные пропускаются.
Оригиналы не трогаем — только читаем.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from . import notes as N

TEXT_EXT = (".pdf", ".docx", ".pptx", ".txt", ".md", ".rtf", ".odt")


# ====================================================================== Granola
def granola_cache() -> Path | None:
    roots = [Path(os.environ.get("APPDATA", "")) / "Granola", Path.home() / "AppData" / "Roaming" / "Granola",
             Path.home() / "Library" / "Application Support" / "Granola"]
    for r in roots:
        if r.is_dir():
            files = sorted(r.glob("cache-v*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            if files:
                return files[0]
    return None


def _state(raw: dict) -> dict:
    """cache-v3: {"cache": "<json-строка>"} → {"state": {...}}; бывает и без обёртки."""
    c = raw.get("cache", raw)
    if isinstance(c, str):
        c = json.loads(c)
    return c.get("state", c) if isinstance(c, dict) else {}


def prosemirror_text(node) -> str:
    """Заметки Granola в формате ProseMirror (JSON-дерево) → Markdown попроще."""
    if not isinstance(node, dict):
        return ""
    t = node.get("type")
    if t == "text":
        return node.get("text", "")
    inner = "".join(prosemirror_text(c) for c in node.get("content", []) or [])
    if t == "heading":
        return "#" * int((node.get("attrs") or {}).get("level", 2)) + " " + inner + "\n\n"
    if t == "listItem":
        return "- " + inner.strip() + "\n"
    if t in ("bulletList", "orderedList"):
        return inner + "\n"
    if t == "paragraph":
        return inner + "\n\n"
    if t == "hardBreak":
        return "\n"
    return inner


def granola_docs(state: dict) -> list[dict]:
    docs = state.get("documents") or {}
    items = docs.values() if isinstance(docs, dict) else docs
    trs = state.get("transcripts") or {}
    out = []
    for d in items:
        if not isinstance(d, dict) or d.get("deleted_at") or not d.get("id"):
            continue
        notes = d.get("notes_markdown") or ""
        if not notes and isinstance(d.get("notes"), dict):
            notes = prosemirror_text(d["notes"]).strip()
        if not notes:
            notes = d.get("notes_plain") or ""
        summary = ""
        panel = d.get("last_viewed_panel") or {}
        if isinstance(panel, dict) and isinstance(panel.get("content"), dict):
            summary = prosemirror_text(panel["content"]).strip()
        tr = trs.get(d["id"]) or []
        out.append({"id": d["id"], "title": (d.get("title") or "Встреча").strip(), "created": d.get("created_at") or "",
                    "notes": notes.strip(), "summary": summary,
                    "transcript": [x for x in tr if isinstance(x, dict) and x.get("text")]})
    return out


def _when(s: str) -> dt.datetime:
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
    except (ValueError, AttributeError):
        return dt.datetime(2000, 1, 1)


def import_granola(cache: Path | None = None, root: Path | None = None) -> tuple[int, int]:
    """→ (новых, всего в Granola)."""
    cache = cache or granola_cache()
    if cache is None:
        raise FileNotFoundError("Granola на этом компьютере не нашла (нет папки %APPDATA%\\Granola)")
    root = root or N.lessons_dir()
    root.mkdir(parents=True, exist_ok=True)
    state = _state(json.loads(cache.read_text(encoding="utf-8")))
    docs = granola_docs(state)
    seen = {p.read_text(encoding="utf-8").strip() for p in root.glob("*/.granola-id")}
    added = 0
    for d in docs:
        if d["id"] in seen or not (d["notes"] or d["summary"] or d["transcript"]):
            continue
        when = _when(d["created"])
        folder = root / f"{when:%Y-%m-%d %H%M} — {N.safe_name(d['title'])} [Granola]"
        k = 2
        while folder.exists():
            folder = root / f"{when:%Y-%m-%d %H%M} — {N.safe_name(d['title'])} [Granola {k}]"
            k += 1
        folder.mkdir(parents=True)
        body = d["summary"] or d["notes"]
        md = f"# {d['title']}\n\n_Импорт из Granola, {when:%d.%m.%Y %H:%M}_\n\n{body}\n"
        if d["summary"] and d["notes"]:
            md += f"\n## Мои заметки\n\n{d['notes']}\n"
        (folder / "конспект.md").write_text(md, encoding="utf-8")
        if d["transcript"]:
            lines = [f"# Транскрипт: {d['title']}", ""]
            for x in d["transcript"]:
                who = "Ты" if str(x.get("source", "")).lower() in ("microphone", "mic") else "Они"
                ts = _when(x.get("start_timestamp", "")).strftime("%H:%M:%S") if x.get("start_timestamp") else ""
                lines.append(f"**[{ts}] {who}:** {x['text'].strip()}")
            (folder / "транскрипт.md").write_text("\n\n".join(lines) + "\n", encoding="utf-8")
        (folder / ".granola-id").write_text(d["id"], encoding="utf-8")
        added += 1
    N.update_index(root)
    return added, len(docs)


# ====================================================================== файлы
def _xml_text(data: bytes, tag_text: str, tag_par: str) -> str:
    root = ET.fromstring(data)
    out = []
    for par in root.iter(tag_par):
        s = "".join(t.text or "" for t in par.iter(tag_text))
        if s.strip():
            out.append(s)
    return "\n".join(out)


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
P = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def extract_text(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in (".txt", ".md"):
        return path.read_text(encoding="utf-8", errors="replace")
    if ext == ".pdf":
        from pypdf import PdfReader
        return "\n\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)
    if ext == ".docx":
        with zipfile.ZipFile(path) as z:
            return _xml_text(z.read("word/document.xml"), f"{W}t", f"{W}p")
    if ext == ".pptx":
        with zipfile.ZipFile(path) as z:
            slides = sorted((n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                            key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[1]).group(1)))
            return "\n\n".join(f"--- Слайд {i} ---\n" + _xml_text(z.read(n), f"{P}t", f"{P}p")
                               for i, n in enumerate(slides, 1))
    if ext == ".odt":
        with zipfile.ZipFile(path) as z:
            root = ET.fromstring(z.read("content.xml"))
            return "\n".join("".join(e.itertext()) for e in root.iter() if e.tag.endswith("}p") or e.tag.endswith("}h"))
    if ext == ".rtf":
        raw = path.read_text(encoding="latin-1", errors="replace")
        raw = re.sub(r"\\'([0-9a-f]{2})", lambda m: bytes([int(m.group(1), 16)]).decode("cp1252", "replace"), raw)
        return re.sub(r"\\[a-z]+-?\d* ?|[{}]", "", raw)
    return ""


def import_folder(src: Path, root: Path | None = None, progress=lambda f, s: None) -> tuple[int, int, list[str]]:
    """→ (новых/обновлённых, пропущено без изменений, ошибки)."""
    root = (root or N.lessons_dir()) / "Материалы" / N.safe_name(src.name)
    files = [p for p in src.rglob("*") if p.is_file() and p.suffix.lower() in TEXT_EXT and not p.name.startswith("~$")]
    done = skipped = 0
    errors = []
    for i, f in enumerate(sorted(files)):
        progress(i / max(1, len(files)), f.name)
        dst = root / f.relative_to(src).with_suffix(f.suffix + ".md")
        stamp = f"<!-- источник: {f} | {int(f.stat().st_mtime)} -->"
        if dst.exists() and dst.read_text(encoding="utf-8").splitlines()[:1] == [stamp]:
            skipped += 1
            continue
        try:
            text = extract_text(f).strip()
        except Exception as e:  # noqa: BLE001 — битый файл: пропускаем, не падаем
            errors.append(f"{f.name}: {type(e).__name__}")
            continue
        if not text:
            errors.append(f"{f.name}: текста нет (скан?)")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(f"{stamp}\n# {f.stem}\n\n{text}\n", encoding="utf-8")
        done += 1
    return done, skipped, errors


def search_materials(term: str, root: Path | None = None, limit: int = 6) -> list[tuple[Path, str, list[str]]]:
    base = (root or N.lessons_dir()) / "Материалы"
    if not base.exists():
        return []
    rx = re.compile(re.escape(term.strip()), re.I)
    out = []
    for f in sorted(base.rglob("*.md")):
        hits = [ln.strip() for ln in f.read_text(encoding="utf-8", errors="replace").splitlines()[2:] if rx.search(ln)]
        if hits:
            out.append((f, f.name[:-3], [h[:200] for h in hits[:3]]))
        if len(out) >= limit:
            break
    return out
