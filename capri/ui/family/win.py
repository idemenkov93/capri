"""Приёмы для окон Windows 11 через WinAPI. На других системах функции ничего не делают."""
from __future__ import annotations

import ctypes
import os

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_SYSTEMBACKDROP_TYPE = 38
DWMWCP_ROUND = 2
DWMSBT_MAINWINDOW = 2           # Mica
WDA_NONE, WDA_EXCLUDEFROMCAPTURE = 0x0, 0x11


def _hwnd(widget) -> int:
    return int(widget.winId())


def _dwm(widget, attribute: int, value: int) -> bool:
    if os.name != "nt":
        return False
    try:
        v = ctypes.c_int(value)
        return ctypes.windll.dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(_hwnd(widget)), attribute, ctypes.byref(v), ctypes.sizeof(v)) == 0
    except Exception:  # noqa: BLE001
        return False


def round_corners(widget) -> bool:
    """Скруглить углы окна силами Windows 11 — окно остаётся непрозрачным и дешёвым в отрисовке."""
    return _dwm(widget, DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND)


def dark_title_bar(widget, dark: bool = True) -> bool:
    return _dwm(widget, DWMWA_USE_IMMERSIVE_DARK_MODE, int(dark))


def mica(widget) -> bool:
    """Материал Mica на фоне обычного окна (Windows 11 22H2+). Не сработало — остаётся сплошной фон."""
    return _dwm(widget, DWMWA_SYSTEMBACKDROP_TYPE, DWMSBT_MAINWINDOW)


def no_activate(widget) -> bool:
    """Окно не забирает фокус даже по клику: пока оно на экране, можно печатать в Teams."""
    if os.name != "nt":
        return False
    try:
        user32 = ctypes.windll.user32
        get, set_ = user32.GetWindowLongPtrW, user32.SetWindowLongPtrW
        get.restype = set_.restype = ctypes.c_ssize_t
        get.argtypes = [ctypes.c_void_p, ctypes.c_int]
        set_.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
        hwnd = ctypes.c_void_p(_hwnd(widget))
        set_(hwnd, GWL_EXSTYLE, get(hwnd, GWL_EXSTYLE) | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
        return True
    except Exception:  # noqa: BLE001
        return False


def hide_from_capture(widget, hidden: bool) -> bool:
    """Не показывать окно при демонстрации экрана и на снимках (оно видно только тебе)."""
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.user32.SetWindowDisplayAffinity(
            ctypes.c_void_p(_hwnd(widget)), WDA_EXCLUDEFROMCAPTURE if hidden else WDA_NONE))
    except Exception:  # noqa: BLE001
        return False


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def autostart(name: str, command: str | None) -> bool:
    """Запуск вместе с Windows: command — что запускать; None — убрать из автозапуска."""
    if os.name != "nt":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if command:
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, command)
            else:
                try:
                    winreg.DeleteValue(key, name)
                except FileNotFoundError:
                    pass
        return True
    except Exception:  # noqa: BLE001
        return False


def autostart_enabled(name: str) -> bool:
    if os.name != "nt":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, name)
        return True
    except Exception:  # noqa: BLE001
        return False
