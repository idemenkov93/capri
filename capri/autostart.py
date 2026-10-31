"""«Запускать Капри вместе с Windows» — значение Capri в HKCU\\...\\Run (то же, что ставит галочка в установщике)."""
from __future__ import annotations

import sys
from pathlib import Path

KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
NAME = "Capri"


def command() -> str:
    """Что запускать: Capri.exe, а из исходников — run_capri.bat."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    bat = Path(__file__).resolve().parent.parent / "run_capri.bat"
    return f'"{bat}"'


def enabled() -> bool:
    if sys.platform != "win32":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as k:
            winreg.QueryValueEx(k, NAME)
            return True
    except OSError:
        return False


def set_enabled(on: bool) -> None:
    if sys.platform != "win32":
        return
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, NAME, 0, winreg.REG_SZ, command())
        else:
            try:
                winreg.DeleteValue(k, NAME)
            except FileNotFoundError:
                pass
