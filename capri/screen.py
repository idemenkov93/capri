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
