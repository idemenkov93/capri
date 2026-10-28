"""«Капри, посмотри, что у меня за ошибка» — снимок экрана для Claude.

Сохраняем два PNG в рабочую папку Claude (workspace/screens): активное окно крупно и весь экран поменьше.
Claude Code открывает их сам (инструмент Read умеет смотреть картинки) и отвечает по тому, что видит.
Скриншоты уходят в Anthropic вместе с вопросом — только когда ты сам об этом просишь.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import sys
from pathlib import Path

RE_SCREEN = re.compile(
    r"(посмотри|глянь|взгляни|смотри|прочитай|прочти|видишь)\b.*\b(экран\w*|ошибк\w*|окн\w*|тут|здесь|это|павершел\w*|powershell|терминал\w*|консол\w*|код\w*)|"
    r"\bкуда (мне )?(нужно |надо )?(нажать|тыкнуть|ткнуть|кликнуть|жать)|\bчто (мне )?(тут|здесь) (нажать|выбрать|делать)|"
    r"\b(посмотри|смотри|глянь) (в|на) (мой |мою |моё |мое )?(гугл|google|хром\w*|chrome|браузер\w*|окно|страниц\w*)|"
    r"\bчто (это |тут |здесь |у меня |там )?за ошибк\w*|\bкакая (у меня |тут |там )?ошибк\w*|"
    r"\bчто (у меня )?(на|с) экран\w*|\bчто (это|тут|здесь) за (ошибк\w*|окн\w*|сообщени\w*)|\bчто (тут|здесь) написано|"
    r"\b(look at|check|read) (my |the )?(screen|error|window)|\bwhat('s| is) (on )?my screen|"
    r"\b(schau|guck)\w* (dir )?(auf )?(den |meinen )?(bildschirm|fehler)", re.I)


def is_screen_question(text: str) -> bool:
    return bool(RE_SCREEN.search(text))


def _foreground() -> tuple[str, tuple[int, int, int, int] | None]:
    """(заголовок, рамка) активного окна; None, если это окно самой Капри или узнать не вышло."""
    if sys.platform != "win32":
        return "", None
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    hwnd = u.GetForegroundWindow()
    if not hwnd:
        return "", None
    pid = wintypes.DWORD()
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    buf = ctypes.create_unicode_buffer(512)
    u.GetWindowTextW(hwnd, buf, 512)
    if pid.value == os.getpid():
        return buf.value, None
    rect = wintypes.RECT()
    # DWMWA_EXTENDED_FRAME_BOUNDS — настоящая рамка окна без невидимых теней
    if ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect)) != 0:
        u.GetWindowRect(hwnd, ctypes.byref(rect))
    box = (rect.left, rect.top, rect.right, rect.bottom)
    return buf.value, box if box[2] - box[0] > 50 and box[3] - box[1] > 50 else None


def capture(workdir: Path, keep: int = 10) -> tuple[list[str], str]:
    """Сделать снимки. Возвращает (пути относительно workdir, заголовок активного окна)."""
    from PIL import ImageGrab

    folder = Path(workdir) / "screens"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    title, box = _foreground()
    paths: list[str] = []
    full = ImageGrab.grab(all_screens=True)
    if box:
        ImageGrab.grab(bbox=box, all_screens=True).save(folder / f"window-{stamp}.png")
        paths.append(f"screens/window-{stamp}.png")
    if full.width > 1920:
        full = full.resize((1920, round(full.height * 1920 / full.width)))
    full.save(folder / f"screen-{stamp}.png")
    paths.append(f"screens/screen-{stamp}.png")
    for old in sorted(folder.glob("*.png"))[:-keep]:   # не копим сотни скриншотов
        try:
            old.unlink()
        except OSError:
            pass
    return paths, title


def prompt(question: str, paths: list[str], title: str) -> str:
    where = f" Активное окно: «{title}»." if title else ""
    files = ", ".join(paths)
    return (f"Пользователь спрашивает про то, что сейчас у него на экране: «{question}».{where}\n"
            f"Скриншоты лежат в текущей папке: {files}. Открой их инструментом Read и посмотри внимательно. "
            "Если там ошибка — скажи, что она значит и как её исправить (точные команды). "
            "Ответ короткий, для озвучки: 2–5 предложений, команды — отдельной строкой.")


# ----------------------------------------------------------------- «посмотри и нажми»: Claude находит место на экране
def screenshot_for_locate(workdir: Path) -> tuple[str, float, int, int, int, int]:
    """Снимок всех экранов (уменьшенный до 1600 по ширине). → (путь, масштаб, x0, y0, ширина, высота)."""
    import ctypes

    from PIL import ImageGrab
    img = ImageGrab.grab(all_screens=True)
    x0 = y0 = 0
    if sys.platform == "win32":
        x0 = ctypes.windll.user32.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN: левый край всех мониторов
        y0 = ctypes.windll.user32.GetSystemMetrics(77)
    scale = min(1.0, 1600 / img.width)
    if scale < 1.0:
        img = img.resize((round(img.width * scale), round(img.height * scale)))
    folder = Path(workdir) / "screens"
    folder.mkdir(parents=True, exist_ok=True)
    name = f"locate-{dt.datetime.now():%Y%m%d-%H%M%S}.png"
    img.save(folder / name)
    return f"screens/{name}", scale, x0, y0, img.width, img.height


def locate_prompt(what: str, path: str, w: int, h: int) -> str:
    return (f"Это скриншот экрана пользователя ({w}×{h} пикселей): {path}. Открой его инструментом Read.\n"
            f"Найди на нём: «{what}» — кнопку, ссылку, видео, вкладку или пункт меню, на который нужно нажать.\n"
            "Ответь ТОЛЬКО одной строкой JSON без пояснений: {\"x\": число, \"y\": число} — центр этого места "
            "в пикселях ЭТОГО изображения. Если такого нет — {\"none\": true}.")


def parse_point(answer: str) -> tuple[int, int] | None:
    import json
    for m in re.finditer(r"\{[^{}]*\}", answer or ""):
        try:
            d = json.loads(m.group(0))
        except ValueError:
            continue
        if d.get("none"):
            return None
        if isinstance(d.get("x"), (int, float)) and isinstance(d.get("y"), (int, float)):
            return int(d["x"]), int(d["y"])
    return None


def to_screen(pt: tuple[int, int], scale: float, x0: int, y0: int) -> tuple[int, int]:
    return round(x0 + pt[0] / scale), round(y0 + pt[1] / scale)
