"""Набор анимаций аватара из папки assets/custom.

Положи GIF/PNG в папку. Анимация выбирается по слову в имени файла:
  idle / stand   — стоит или висит в воздухе (если нет других — всё идёт сюда)
  walk / run     — идёт
  sit            — сидит на окне
  think / load   — думает
  talk / speak   — говорит
  listen         — слушает
  teleport / tp  — исчезает и появляется
Файлы без ключевых слов становятся дополнительными вариантами idle — Капри их чередует.

Папка с кадрами (0001.png, 0002.png, … — хотя бы 3 штуки) — это одна анимация.
Её тип берётся из имени папки: walk/0001.png, wave/…, think_frames/… (fps — 12 кадров в секунду).
"""
from __future__ import annotations

import random
import re
from pathlib import Path

from PySide6.QtGui import QMovie, QPixmap

KEYWORDS = {
    "walk": ("walk", "run", "move"),
    "sit": ("sit", "seat"),
    "think": ("think", "load", "process"),
    "talk": ("talk", "speak", "say"),
    "listen": ("listen", "hear"),
    "teleport": ("teleport", "tp", "warp", "blink"),
    "idle": ("idle", "stand", "default", "main", "float"),
}
EXTS = {".gif", ".png", ".webp"}


def classify(name: str) -> str | None:
    """walk_left.gif → walk, capri-sitting2.gif → sit. Сравниваем по началу слов, а не по подстроке."""
    tokens = [t for t in re.split(r"[^a-zа-яё]+", name.lower()) if t]
    for kind, words in KEYWORDS.items():
        if any(t.startswith(w) for t in tokens for w in words):
            return kind
    return None


FRAME_RX = re.compile(r"^\D*\d+$")  # 0001, frame_12, кадр3 — имя заканчивается номером


def frame_folder(folder: Path) -> list[Path] | None:
    """Кадры анимации, если папка — это последовательность пронумерованных PNG; иначе None."""
    pngs = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in (".png", ".webp")]
    if len(pngs) < 3 or not all(FRAME_RX.match(p.stem) for p in pngs):
        return None
    return sorted(pngs, key=lambda p: (int(re.search(r"\d+$", p.stem).group()), p.stem))


class FrameMovie:
    """Как QMovie, но из папки кадров: currentPixmap() отдаёт кадр по времени."""

    def __init__(self, frames: list[Path], fps: int = 12):
        import time as _time
        self._time = _time
        self.pixmaps = [QPixmap(str(f)) for f in frames]
        self.pixmaps = [p for p in self.pixmaps if not p.isNull()]
        self.fps = fps
        self.t0 = _time.monotonic()

    def isValid(self) -> bool:
        return bool(self.pixmaps)

    def start(self):
        pass

    def currentPixmap(self) -> QPixmap:
        i = int((self._time.monotonic() - self.t0) * self.fps) % len(self.pixmaps)
        return self.pixmaps[i]


class Sprite:
    def __init__(self, path: Path, fps: int = 12):
        self.path = path
        self.movie = None
        self.pixmap: QPixmap | None = None
        if path.is_dir():
            self.movie = FrameMovie(frame_folder(path) or [], fps)
        elif path.suffix.lower() in (".gif", ".webp"):
            self.movie = QMovie(str(path))
            self.movie.setCacheMode(QMovie.CacheAll)
        else:
            self.pixmap = QPixmap(str(path))

    def ok(self) -> bool:
        return (self.movie is not None and self.movie.isValid()) or (self.pixmap is not None and not self.pixmap.isNull())

    def start(self):
        if isinstance(self.movie, QMovie):
            if self.movie.state() == QMovie.Paused:
                self.movie.setPaused(False)
            elif self.movie.state() != QMovie.Running:
                self.movie.start()

    def pause(self):
        if isinstance(self.movie, QMovie) and self.movie.state() == QMovie.Running:
            self.movie.setPaused(True)

    def frame(self) -> QPixmap:
        return self.movie.currentPixmap() if self.movie else self.pixmap


class SpriteSet:
    def __init__(self, source: str = "", fps: int = 12):
        self.fps = fps
        self.by_kind: dict[str, list[Sprite]] = {}
        self._current: dict[str, Sprite] = {}
        if source:
            self._load(Path(source))

    def _load(self, src: Path):
        if src.is_file():
            files = [src]
        elif src.is_dir():  # папки, начинающиеся с «_», пропускаем (например _old, _to_delete)
            if frame_folder(src):  # сама папка — это одна анимация из кадров
                files = [src]
            else:
                dirs = [d for d in src.rglob("*") if d.is_dir() and not any(part.startswith("_") for part in d.relative_to(src).parts)]
                anim_dirs = {d for d in dirs if frame_folder(d)}
                files = sorted(anim_dirs, key=str) + sorted(
                    p for p in src.rglob("*") if p.is_file() and p.suffix.lower() in EXTS
                    and p.parent not in anim_dirs
                    and not any(part.startswith("_") for part in p.relative_to(src).parts))
        else:
            files = []
        for f in files:
            sp = Sprite(f, self.fps)
            if sp.ok():
                kind = classify(f.name if f.is_dir() else f.stem) or "idle"
                self.by_kind.setdefault(kind, []).append(sp)

    def __bool__(self):
        return bool(self.by_kind)

    def kinds(self) -> dict[str, int]:
        return {k: len(v) for k, v in self.by_kind.items()}

    def get(self, kind: str) -> Sprite | None:
        """Спрайт для состояния; если такого нет — idle; если и его нет — любой."""
        pool = self.by_kind.get(kind) or self.by_kind.get("idle") or next(iter(self.by_kind.values()), None)
        if not pool:
            return None
        sp = self._current.get(kind)
        if sp is None or sp not in pool:
            sp = random.choice(pool)
            self._current[kind] = sp
        sp.start()
        return sp

    def pause_all(self):
        """Окно скрыто — GIF-ы не нужно декодировать (большие GIF-ы ощутимо грузят процессор)."""
        for pool in self.by_kind.values():
            for sp in pool:
                sp.pause()

    def shuffle(self, kind: str):
        """Сменить вариант анимации (например, другой idle через какое-то время)."""
        self._current.pop(kind, None)
