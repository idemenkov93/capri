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


class Sprite:
    def __init__(self, path: Path):
        self.path = path
        self.movie: QMovie | None = None
        self.pixmap: QPixmap | None = None
        if path.suffix.lower() in (".gif", ".webp"):
            self.movie = QMovie(str(path))
            self.movie.setCacheMode(QMovie.CacheAll)
        else:
            self.pixmap = QPixmap(str(path))

    def ok(self) -> bool:
        return (self.movie is not None and self.movie.isValid()) or (self.pixmap is not None and not self.pixmap.isNull())

    def start(self):
        if self.movie and self.movie.state() != QMovie.Running:
            self.movie.start()

    def frame(self) -> QPixmap:
        return self.movie.currentPixmap() if self.movie else self.pixmap


class SpriteSet:
    def __init__(self, source: str = ""):
        self.by_kind: dict[str, list[Sprite]] = {}
        self._current: dict[str, Sprite] = {}
        if source:
            self._load(Path(source))

    def _load(self, src: Path):
        if src.is_file():
            files = [src]
        elif src.is_dir():  # папки, начинающиеся с «_», пропускаем (например _old, _to_delete)
            files = sorted(p for p in src.rglob("*") if p.suffix.lower() in EXTS
                           and not any(part.startswith("_") for part in p.relative_to(src).parts))
        else:
            files = []
        for f in files:
            sp = Sprite(f)
            if sp.ok():
                kind = classify(f.stem) or "idle"
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

    def shuffle(self, kind: str):
        """Сменить вариант анимации (например, другой idle через какое-то время)."""
        self._current.pop(kind, None)
