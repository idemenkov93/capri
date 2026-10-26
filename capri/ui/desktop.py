"""Что есть на рабочем столе: окна других программ, на которые Капри может сесть.

На Windows используем WinAPI через ctypes (никаких лишних библиотек).
Читаем только положение и заголовок окон — их содержимое не трогаем.
На других системах список окон пустой, и Капри гуляет по низу экрана.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass

from PySide6.QtCore import QRect


@dataclass
class Win:
    hwnd: int
    rect: QRect   # в логических пикселях Qt
    title: str


def _scale() -> float:
    from PySide6.QtGui import QGuiApplication

    screen = QGuiApplication.primaryScreen()
    return screen.devicePixelRatio() if screen else 1.0


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    dwmapi = ctypes.windll.dwmapi
    DWMWA_EXTENDED_FRAME_BOUNDS = 9
    DWMWA_CLOAKED = 14
    SKIP_TITLES = {"Program Manager", "Windows Input Experience", "Settings", "Microsoft Text Input Application"}

    def _rect(hwnd: int) -> QRect | None:
        r = wintypes.RECT()
        # «видимая» рамка окна без невидимых теней Windows 10/11
        if dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r)) != 0:
            if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
                return None
        s = _scale()
        return QRect(round(r.left / s), round(r.top / s), round((r.right - r.left) / s), round((r.bottom - r.top) / s))

    def window_rect(hwnd: int) -> QRect | None:
        """Текущее положение окна или None, если окно закрыто/свёрнуто/скрыто."""
        if not user32.IsWindow(hwnd) or not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return None
        return _rect(hwnd)

    def list_windows() -> list[Win]:
        own_pid = os.getpid()
        found: list[Win] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def visit(hwnd, _):
            if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
                return True
            cloaked = ctypes.c_int(0)
            dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
            if cloaked.value:
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == own_pid:
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if not length:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if buf.value in SKIP_TITLES:
                return True
            rect = _rect(hwnd)
            if rect and rect.width() > 250 and rect.height() > 120:
                found.append(Win(int(hwnd), rect, buf.value))
            return True

        user32.EnumWindows(visit, 0)
        return found  # порядок: сверху вниз по Z (первое — самое верхнее окно)

else:  # Linux/macOS — пока без окон
    def window_rect(hwnd: int) -> QRect | None:
        return None

    def list_windows() -> list[Win]:
        return []


def perchable(windows: list[Win], screen: QRect, avatar_h: int) -> list[Win]:
    """Окна, на верхнем краю которых есть место посидеть (не развёрнутые на весь экран)."""
    out = []
    for w in windows:
        r = w.rect
        if r.top() - avatar_h < screen.top() + 4:      # над окном нет места (развёрнуто)
            continue
        if r.right() < screen.left() + 80 or r.left() > screen.right() - 80:
            continue
        # верхний край окна не должен быть закрыт окном, которое лежит выше по Z
        hidden = any(o.rect.contains(r.center().x(), r.top() + 2) for o in windows[: windows.index(w)])
        if not hidden:
            out.append(w)
    return out
