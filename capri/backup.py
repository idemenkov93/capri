"""Бэкап папок в папку на компьютере (или на флешке / втором диске): ZIP с датой в имени, хранится N последних.

Настройки — в data/state.json → backup: {folders: [...], dest: "...", keep: 5, every: "off|day|week", last: время}.
Старые копии удаляются только свои — с именем «Capri-бэкап ….zip» в папке назначения.
"""
from __future__ import annotations

import datetime as dt
import re
import threading
import time
import zipfile
from pathlib import Path
from typing import Callable

from .convert import ConvertError, human_size

PREFIX = "Capri-бэкап"
RX = re.compile(rf"^{PREFIX} (\d{{4}}-\d{{2}}-\d{{2}} \d{{4}})(?: \d+)?\.zip$")
EVERY = {"off": 0, "day": 86400, "week": 7 * 86400}
SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv", "$RECYCLE.BIN", "System Volume Information"}


def defaults() -> dict:
    return {"folders": [], "dest": "", "keep": 5, "every": "off", "last": 0}


def due(cfg: dict, now: float | None = None) -> bool:
    period = EVERY.get(cfg.get("every", "off"), 0)
    if not period or not cfg.get("folders") or not cfg.get("dest"):
        return False
    return (now or time.time()) - float(cfg.get("last") or 0) >= period


def _files(folder: Path, dest: Path):
    for p in folder.rglob("*"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.is_file() and dest not in p.parents:          # бэкап внутри копируемой папки — не копируем сам себя
            yield p


def make(folders: list[str], dest: str, keep: int = 5, progress: Callable[[float, str], None] | None = None,
         cancel: threading.Event | None = None, now: dt.datetime | None = None) -> tuple[Path, int, int, list[str]]:
    """→ (zip, сколько файлов, сколько байт исходников, пропущенные). Пропускаем занятые файлы, не падаем."""
    if not folders:
        raise ConvertError("не выбраны папки для бэкапа")
    dst_dir = Path(dest)
    if not dest or not dst_dir.exists():
        raise ConvertError(f"нет папки для бэкапа: {dest or 'не выбрана'} (флешка не вставлена?)")
    srcs = [Path(f) for f in folders if Path(f).exists()]
    if not srcs:
        raise ConvertError("выбранных папок больше нет")
    stamp = (now or dt.datetime.now()).strftime("%Y-%m-%d %H%M")
    out = dst_dir / f"{PREFIX} {stamp}.zip"
    n = 2
    while out.exists():
        out = dst_dir / f"{PREFIX} {stamp} {n}.zip"
        n += 1
    plan = [(s, f) for s in srcs for f in _files(s, dst_dir)]
    total = sum(f.stat().st_size for _, f in plan) or 1
    done = count = 0
    skipped: list[str] = []
    tmp = out.with_suffix(".part")
    tops: dict[Path, str] = {}
    for s_ in srcs:                       # две папки с одинаковым именем — «Фото», «Фото (2)»
        base = s_.name or s_.drive.rstrip(":\\") or "folder"
        top, k = base, 2
        while top in tops.values():
            top, k = f"{base} ({k})", k + 1
        tops[s_] = top
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=5, allowZip64=True) as z:
            for src, f in plan:
                if cancel is not None and cancel.is_set():
                    raise ConvertError("Остановлено.")
                arc = f"{tops[src]}/{f.relative_to(src).as_posix()}"
                try:
                    z.write(f, arc)
                    count += 1
                except (OSError, PermissionError):
                    skipped.append(str(f))
                done += f.stat().st_size if f.exists() else 0
                if progress:
                    progress(done / total, f.name)
        tmp.replace(out)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    rotate(dst_dir, keep)
    return out, count, done, skipped


def rotate(dest: Path, keep: int) -> list[Path]:
    """Оставить keep последних копий, остальные (только «Capri-бэкап …zip») — удалить."""
    ours = sorted((p for p in dest.iterdir() if RX.match(p.name)), key=lambda p: (RX.match(p.name).group(1), p.stat().st_mtime))
    old = ours[:-keep] if keep > 0 else []
    for p in old:
        p.unlink(missing_ok=True)
    return old


def describe(cfg: dict) -> str:
    folders = cfg.get("folders") or []
    every = {"off": "вручную", "day": "каждый день", "week": "раз в неделю"}.get(cfg.get("every", "off"), "вручную")
    last = cfg.get("last")
    when = dt.datetime.fromtimestamp(last).strftime("%d.%m %H:%M") if last else "ещё не было"
    return (f"Папки: {', '.join(Path(f).name or f for f in folders) or 'не выбраны'}\n"
            f"Куда: {cfg.get('dest') or 'не выбрано'}\nХраню копий: {cfg.get('keep', 5)}\nКогда: {every}\nПоследний: {when}")


def summary(out: Path, count: int, size: int, skipped: list[str]) -> str:
    s = f"Бэкап готов: {count} файлов ({human_size(size)}) → {out.name}, {human_size(out.stat().st_size)}."
    if skipped:
        s += f" Пропустила {len(skipped)} занятых файлов."
    return s
