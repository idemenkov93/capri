"""Скины — наборы анимаций Капри, которые можно переключать в меню «🎭 Внешность».

Скин = папка в skins/ (из Capri.exe — %LOCALAPPDATA%\\Capri\\skins):
    skins/
      капри/              ← название скина = имя папки
        desktop/          ← Капри на рабочем столе (лучше GIF с прозрачным фоном)
        hud/              ← экран в окне «Personal Link» (подойдут клипы с фоном)
      другая/
        idle.gif, walk.gif, dance1.gif…   ← можно и без desktop/hud: тогда одни и те же картинки везде
Как называть файлы (idle, walk, sit, think, talk, listen, teleport, dance) — см. ui/sprites.py.
Папки, имя которых начинается с «_», пропускаются.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from .config import FROZEN, PROJECT_ROOT, expand, resource

EXTS = (".gif", ".png", ".webp")
STAR = "__star__"          # без картинок — неоновая звезда


def skins_dir() -> Path:
    return PROJECT_ROOT / "skins"


def custom_dir() -> Path:
    return expand("assets/custom")


def has_images(folder: Path, flat: bool = False) -> bool:
    if not folder.is_dir():
        return False
    files = folder.iterdir() if flat else folder.rglob("*")
    return any(p.is_file() and p.suffix.lower() in EXTS and not any(part.startswith("_") for part in p.relative_to(folder).parts)
               for p in files)


def list_skins() -> list[str]:
    d = skins_dir()
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_dir() and not p.name.startswith("_") and has_images(p))


def source_in(base: Path, where: str) -> str:
    """Где в папке скина картинки для where (desktop | hud): своя подпапка, иначе сама папка (если в ней картинки)."""
    sub = base / where
    if has_images(sub):
        return str(sub)
    if has_images(base, flat=True):
        return str(base)
    # есть только другая подпапка (например, только hud/) — для этого места картинок нет
    return ""


def source(skin: str | None, where: str) -> str | None:
    """Путь для выбранного скина; None — скин не выбран/пропал (тогда берём assets/custom, как раньше)."""
    if not skin:
        return None
    if skin == STAR:
        return ""
    base = skins_dir() / skin
    return source_in(base, where) if base.is_dir() else None


def first_run_copy() -> None:
    """Capri.exe: личная сборка несёт твои анимации внутри; при первом запуске кладём их в папку пользователя,
    чтобы их можно было дополнять (просто закинуть ещё GIF и перезапустить)."""
    if not FROZEN:
        return
    src, dst = resource("assets/custom"), custom_dir()
    if has_images(src) and not dst.exists():
        shutil.copytree(src, dst)
    skins_dir().mkdir(parents=True, exist_ok=True)
    readme = skins_dir() / "ПРОЧТИ.txt"
    if not readme.exists():
        readme.write_text(__doc__ or "", encoding="utf-8")
