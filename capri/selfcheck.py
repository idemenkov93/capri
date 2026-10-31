"""`Capri.exe --check` — после сборки: всё ли упаковалось (каждый модуль импортируется, DLL на месте).

У оконной программы нет консоли, поэтому результат пишется в файл (по умолчанию check.txt рядом с exe).
Код выхода 0 — всё в порядке.
"""
from __future__ import annotations

import importlib
import pkgutil
import sys
import traceback
from pathlib import Path

# сторонние пакеты, без которых Капри не работает (а PyInstaller любит что-нибудь из них потерять)
REQUIRED = ["PySide6.QtWidgets", "PySide6.QtNetwork", "httpx", "yaml", "numpy", "sounddevice",
            "vosk", "faster_whisper", "ctranslate2", "edge_tts", "psutil", "PIL.ImageGrab", "send2trash"]
WINDOWS = ["pycaw.pycaw", "comtypes", "screen_brightness_control", "uiautomation",
           "winrt.windows.media.control", "keyring.backends.Windows"]
OPTIONAL = ["googleapiclient.discovery", "google_auth_oauthlib.flow", "icalendar", "recurring_ical_events", "piper"]


def run(out: str | None = None) -> int:
    import capri
    lines, bad = [], 0
    mods = sorted(m.name for m in pkgutil.walk_packages(capri.__path__, "capri.")
                  if not m.name.endswith(("__main__", ".selftest")))
    for name in mods + REQUIRED + (WINDOWS if sys.platform == "win32" else []) + OPTIONAL:
        try:
            importlib.import_module(name)
            lines.append(f"OK   {name}")
        except Exception as e:  # noqa: BLE001
            optional = name in OPTIONAL
            bad += 0 if optional else 1
            lines.append(f"{'WARN' if optional else 'FAIL'} {name}: {type(e).__name__}: {e}")
            if not optional:
                lines.append("     " + traceback.format_exc().strip().splitlines()[-1])
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(["capri"])
        from .ui.setup_wizard import SetupWizard
        w = SetupWizard(first_run=True)
        w.detect.wait(20000)
        w.close()
        lines.append("OK   мастер настройки открывается")
        del app
    except Exception as e:  # noqa: BLE001
        bad += 1
        lines.append(f"FAIL мастер настройки: {e}")
    lines.append(f"\n{'ВСЁ В ПОРЯДКЕ' if not bad else f'ОШИБОК: {bad}'}")
    path = Path(out) if out else Path(sys.executable).with_name("check.txt")
    path.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0 if not bad else 1
