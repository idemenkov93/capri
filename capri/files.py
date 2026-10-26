"""Папки и файлы голосом: «создай папку Проекты на рабочем столе», «создай текстовый документ план в документах»,
«удали папку тест с рабочего стола» (удаление — только в Корзину и только после «Да»).

Капри работает только в «твоих» местах: рабочий стол, документы, загрузки, изображения, музыка, видео,
домашняя папка, папка проекта и диски кроме системного. Системные папки (Windows, Program Files…) — никогда.
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from .actions import Action

# слово в фразе → какая папка (CSIDL для SHGetFolderPath, None — особый случай)
PLACES = [
    (r"рабоч\w* стол\w*|десктоп\w*|desktop|schreibtisch", "desktop"),
    (r"документ\w*|documents?|dokumente\w*", "documents"),
    (r"загрузк\w*|загрузок|скачанн\w*|downloads?", "downloads"),
    (r"изображени\w*|картинк\w*|фотограф\w*|pictures?|bilder\w*", "pictures"),
    (r"музык\w*|music|musik", "music"),
    (r"видео|videos?", "videos"),
    (r"домашн\w* папк\w*|home", "home"),
    (r"проект\w* капри|папк\w* капри|капри", "project"),
]
CSIDL = {"desktop": 0x10, "documents": 0x05, "pictures": 0x27, "music": 0x0D, "videos": 0x0E}
PLACE_NOM = {"desktop": "рабочий стол", "documents": "документы", "downloads": "загрузки", "pictures": "изображения",
             "music": "музыку", "videos": "видео", "home": "домашнюю папку", "project": "папку Капри"}
PLACE_NAMES = {"desktop": "рабочем столе", "documents": "документах", "downloads": "загрузках", "pictures": "изображениях",
               "music": "музыке", "videos": "видео", "home": "домашней папке", "project": "папке Капри"}
RE_DRIVE = re.compile(r"\bдиск\w*\s+([a-zа-я])\b|\b([a-z]):\\?|drive\s+([a-z])\b", re.I)
CYR_DRIVE = {"с": "C", "д": "D", "е": "E", "ф": "F", "г": "G", "це": "C", "де": "D"}
BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def known_folder(key: str) -> Path:
    if key == "home":
        return Path.home()
    if key == "project":
        from .config import PROJECT_ROOT
        return PROJECT_ROOT
    if sys.platform == "win32":
        import ctypes
        if key == "downloads":
            # «Загрузки» есть только как Known Folder (GUID), а не CSIDL
            from ctypes import wintypes

            class GUID(ctypes.Structure):
                _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]
            g = GUID(0x374DE290, 0x123F, 0x4565, (ctypes.c_ubyte * 8)(0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E, 0x46, 0x7B))
            out = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(out)) == 0:
                path = Path(out.value)
                ctypes.windll.ole32.CoTaskMemFree(out)
                return path
        elif key in CSIDL:
            buf = ctypes.create_unicode_buffer(260)
            if ctypes.windll.shell32.SHGetFolderPathW(None, CSIDL[key], None, 0, buf) == 0:
                return Path(buf.value)
    return Path.home() / {"desktop": "Desktop", "documents": "Documents", "downloads": "Downloads",
                          "pictures": "Pictures", "music": "Music", "videos": "Videos"}.get(key, "")


def find_place(text: str) -> tuple[Path, str] | None:
    """«на рабочем столе» → (путь, как назвать). Диск: «на диске E» → E:\\."""
    m = RE_DRIVE.search(text)
    if m:
        letter = next(g for g in m.groups() if g)
        letter = CYR_DRIVE.get(letter.lower(), letter).upper()
        if len(letter) == 1 and letter.isascii():
            return Path(f"{letter}:\\"), f"диске {letter}"
    for rx, key in PLACES:
        if re.search(rx, text, re.I):
            return known_folder(key), PLACE_NAMES[key]
    return None


def safe_roots() -> list[Path]:
    roots = [known_folder(k) for k in ("desktop", "documents", "downloads", "pictures", "music", "videos", "project")]
    roots.append(Path.home())
    sysdrive = os.environ.get("SystemDrive", "C:").upper()
    for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
        if f"{letter}:" != sysdrive and os.path.exists(f"{letter}:\\"):
            roots.append(Path(f"{letter}:\\"))
    return roots


FORBIDDEN = re.compile(r"\\(windows|program files( \(x86\))?|programdata|\$recycle\.bin|system volume information|appdata)(\\|$)", re.I)


def check_safe(path: Path, deleting: bool = False) -> str | None:
    """Ошибка (текст) или None, если здесь можно работать."""
    p = path.resolve() if path.exists() else path
    if FORBIDDEN.search(str(p)):
        return "это системная папка — туда не лезу"
    roots = [r.resolve() for r in safe_roots()]
    if not any(p == r or r in p.parents for r in roots):
        return "это не твоя папка (можно: рабочий стол, документы, загрузки, изображения, музыка, видео, другие диски)"
    if deleting and any(p == r for r in roots):
        return "это целая системная папка или диск — её удалять нельзя"
    if deleting and p == Path.home().resolve():
        return "это твоя домашняя папка — её удалять нельзя"
    return None


def clean_name(name: str) -> str:
    name = name.strip().strip("«»\"'“”„ ").rstrip(".!?")
    name = BAD.sub("", name)
    return re.sub(r"\s+", " ", name).strip()[:120]


# --- разбор фраз --------------------------------------------------------------------
PLACE_TAIL = r"(?:\s+(?:на|в|во|из|с|со|on|in|from|auf|im|in der)\s+(?P<place>.+))?$"
RE_MKDIR = re.compile(r"^(?:создай|сделай|создать|make|create|erstell\w*)\s+(?:новую\s+)?(?:папку|директорию|каталог|folder|directory|ordner)\s+"
                      r"(?:с названием\s+|под названием\s+|named\s+|called\s+)?(?P<name>.+?)" + PLACE_TAIL, re.I)
RE_MKFILE = re.compile(r"^(?:создай|сделай|создать|make|create|erstell\w*)\s+(?:новый\s+)?(?:текстовый\s+)?(?:документ|файл|text file|file|textdatei|datei)\s+"
                       r"(?:с названием\s+|под названием\s+|named\s+|called\s+)?(?P<name>.+?)"
                       r"(?:\s+(?:на|в|во|on|in|auf|im)\s+(?P<place>.+?))?(?:\s+(?:с текстом|с содержимым|with text|mit text)\s+(?P<text>.+))?$", re.I)
RE_DELETE = re.compile(r"^(?:удали|удалить|сотри|убери|delete|remove|lösch\w*)\s+(?P<what>папку|файл|документ|folder|file|ordner|datei)\s+"
                       r"(?P<name>.+?)" + PLACE_TAIL, re.I)
RE_OPEN_PLACE = re.compile(r"^(?:открой|покажи|open|öffne)\s+(?:папку\s+)?(?P<name>.+?)" + PLACE_TAIL, re.I)


@dataclass
class Target:
    path: Path
    where: str


def _target(name: str, place: str | None) -> Target | str:
    """Куда именно: имя + место (по умолчанию — рабочий стол). Строка — это ошибка."""
    base = None
    if place:
        # «в папке Проекты на рабочем столе» — вложенная папка (проверяем первой, иначе победит «рабочий стол»)
        m = re.match(r"(?:папке|папку|folder|ordner)\s+(.+?)\s+(?:на|в|во|on|in|auf)\s+(.+)$", place, re.I)
        if m and (outer := find_place(m.group(2))):
            base = (outer[0] / clean_name(m.group(1)), f"папке «{clean_name(m.group(1))}» на {outer[1]}")
        else:
            base = find_place(place)
        if not base:
            return f"не поняла, где это: «{place}»"
    base = base or (known_folder("desktop"), PLACE_NAMES["desktop"])
    n = clean_name(name)
    if not n:
        return "как назвать?"
    return Target(base[0] / n, base[1])


def match(text: str) -> Action | str | None:
    """Action (создать/удалить/открыть), строка-ошибка или None — это не про файлы."""
    t = text.strip().rstrip(".!?")
    if m := RE_MKFILE.match(t):
        name = clean_name(m.group("name"))
        if not re.search(r"\.\w{1,5}$", name):
            name += ".txt"
        tg = _target(name, m.group("place"))
        if isinstance(tg, str):
            return tg
        if err := check_safe(tg.path):
            return err
        return Action("mkfile", f"Создать файл «{tg.path.name}» ({tg.where})",
                      {"path": str(tg.path), "text": (m.group("text") or "").strip()}, needs_confirm=False)
    if m := RE_MKDIR.match(t):
        tg = _target(m.group("name"), m.group("place"))
        if isinstance(tg, str):
            return tg
        if err := check_safe(tg.path):
            return err
        return Action("mkdir", f"Создать папку «{tg.path.name}» ({tg.where})", {"path": str(tg.path)}, needs_confirm=False)
    if m := RE_DELETE.match(t):
        folder = m.group("what").lower() in ("папку", "folder", "ordner")
        tg = _target(m.group("name"), m.group("place"))
        if isinstance(tg, str):
            return tg
        path = tg.path
        if not folder and not path.exists() and not path.suffix:
            path = path.with_suffix(".txt")
        if not path.exists():
            return f"не нашла «{path.name}» ({tg.where})"
        if err := check_safe(path, deleting=True):
            return err
        kind = "папку" if path.is_dir() else "файл"
        extra = ""
        if path.is_dir():
            n = sum(1 for _ in path.rglob("*"))
            extra = f", внутри {n} объектов" if n else ", она пустая"
        return Action("trash", f"Удалить в Корзину {kind} «{path.name}» ({tg.where}{extra})", {"path": str(path)}, needs_confirm=True)
    return None


def _is_place_only(name: str) -> bool:
    """«загрузки», «мои документы», «папку видео», «диск E» — да; «понравившаяся музыка», «ютуб мюзик» — нет."""
    n = name.strip().lower()
    if RE_DRIVE.fullmatch(n) or re.fullmatch(r"диск\w*\s+[a-zа-я]", n):
        return True
    return any(re.fullmatch(rf"(?:мо\w+\s+)?(?:папк\w*\s+)?(?:{rx})", n, re.I) for rx, _ in PLACES)


def match_open(text: str) -> Action | None:
    """«открой загрузки», «открой папку Проекты на рабочем столе», «открой диск E» — в Проводнике."""
    m = RE_OPEN_PLACE.match(text.strip().rstrip(".!?"))
    if not m:
        return None
    name, place = m.group("name"), m.group("place")
    if not place and (base := find_place(name)) and _is_place_only(name):
        nom = next((v for k, v in PLACE_NOM.items() if PLACE_NAMES[k] == base[1]), base[1].replace("диске", "диск"))
        return Action("open_folder", f"Открыть {nom}", {"path": str(base[0])}, needs_confirm=False)
    if place and re.match(r"папк", text.split()[1] if len(text.split()) > 1 else "", re.I):
        tg = _target(name, place)
        if isinstance(tg, Target) and tg.path.is_dir() and not check_safe(tg.path):
            return Action("open_folder", f"Открыть папку «{tg.path.name}»", {"path": str(tg.path)}, needs_confirm=False)
    return None


def execute(action: Action) -> str:
    p = Path(action.params["path"])
    if action.kind == "mkdir":
        if p.exists():
            return f"Папка «{p.name}» уже есть: {p}"
        p.mkdir(parents=True)
        return f"Создала папку: {p}"
    if action.kind == "mkfile":
        if p.exists():
            return f"Файл «{p.name}» уже есть — не трогаю: {p}"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(action.params.get("text", "") + ("\n" if action.params.get("text") else ""), encoding="utf-8")
        return f"Создала файл: {p}"
    if action.kind == "trash":
        if err := check_safe(p, deleting=True):   # проверяем ещё раз прямо перед удалением
            return f"Не удаляю: {err}"
        from send2trash import send2trash
        send2trash(str(p))
        return f"Удалила в Корзину: {p} (можно восстановить)"
    if action.kind == "open_folder":
        if sys.platform == "win32":
            os.startfile(str(p))  # noqa: S606 — только известные папки
        return f"Открываю {p}"
    raise ValueError(action.kind)
