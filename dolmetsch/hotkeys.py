"""Горячие клавиши через RegisterHotKey: Windows сама сообщает о нажатии, клавиатуру не опрашиваем.

Регистрация живёт в отдельном потоке: Windows присылает сообщение WM_HOTKEY именно тому потоку,
который вызвал RegisterHotKey.
"""
from __future__ import annotations

import ctypes
import os
import threading
from typing import Callable

MODS = {"alt": 0x0001, "ctrl": 0x0002, "shift": 0x0004, "win": 0x0008}
MOD_NOREPEAT = 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
SPECIAL = {"space": 0x20, "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74, "f6": 0x75,
           "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B}


def parse(combo: str) -> tuple[int, int] | None:
    """«Win+Alt+J» → (модификаторы, код клавиши). Непонятное сочетание → None."""
    mods, key = 0, None
    for part in (p.strip().lower() for p in combo.split("+")):
        if part in MODS:
            mods |= MODS[part]
        elif part in SPECIAL:
            key = SPECIAL[part]
        elif len(part) == 1 and part.isalnum():
            key = ord(part.upper())
        else:
            return None
    return (mods, key) if mods and key else None


class Hotkeys(threading.Thread):
    def __init__(self, combos: dict[str, str], on_press: Callable[[str], None]):
        super().__init__(name="hotkeys", daemon=True)
        self.combos, self.on_press = combos, on_press
        self.failed: list[str] = []          # сочетания, которые занять не удалось
        self.ready = threading.Event()
        self._tid = 0

    def run(self) -> None:
        if os.name != "nt":
            self.ready.set()
            return
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        self._tid = kernel32.GetCurrentThreadId()
        actions = {}
        for n, (action, combo) in enumerate(self.combos.items(), 1):
            parsed = parse(combo)
            if parsed and user32.RegisterHotKey(None, n, parsed[0] | MOD_NOREPEAT, parsed[1]):
                actions[n] = action
            else:
                self.failed.append(combo)    # занято другой программой или записано с ошибкой
        self.ready.set()
        from ctypes import wintypes
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam in actions:
                self.on_press(actions[msg.wParam])
        for n in actions:
            user32.UnregisterHotKey(None, n)

    def stop(self) -> None:
        if os.name == "nt" and self._tid:
            ctypes.windll.user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
