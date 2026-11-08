"""Глобальные горячие клавиши (работают в любой программе), без хуков клавиатуры: опрос GetAsyncKeyState
раз в 30 мс в отдельном потоке — это почти ноль процессора.

  Ctrl+Shift+T — перевести выделенный текст (окно у курсора)
  Ctrl+Win (держать) — диктовка: говоришь, отпускаешь — текст печатается туда, где курсор
  Ctrl+Shift+Q — конвертер: выделенные файлы → круговое меню
  Ctrl+Shift+Space — открыть / спрятать чат
Клавиши меняются в config.yaml → hotkeys.
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QObject, Signal

VK = {"ctrl": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B, "rwin": 0x5C, "space": 0x20,
      **{chr(c).lower(): c for c in range(0x41, 0x5B)}, **{str(d): 0x30 + d for d in range(10)},
      **{f"f{i}": 0x6F + i for i in range(1, 13)}}


def parse(combo: str) -> list[int]:
    """«Ctrl+Shift+T» → [0x11, 0x10, 0x54]. Неизвестная клавиша → []."""
    out = []
    for part in combo.lower().replace(" ", "").split("+"):
        if part not in VK:
            return []
        out.append(VK[part])
    return out


class HotkeyPoller(QObject):
    """Сигналы приходят в главный поток (Qt сам ставит их в очередь); опрос — в фоновом daemon-потоке."""
    translate = Signal()            # нажали сочетание «перевести выделенное»
    dictate = Signal(bool)          # True — начали держать сочетание диктовки, False — отпустили
    dictate_cancel = Signal()       # это было Ctrl+Win+стрелка и т.п. — записанное выбросить
    chat = Signal()                 # открыть / спрятать чат (без персонажа на экране это главный вход)
    convert = Signal()              # конвертер для выделенных файлов

    def __init__(self, translate_keys: str = "Ctrl+Shift+T", dictate_keys: str = "Ctrl+Win",
                 chat_keys: str = "Ctrl+Shift+Space", convert_keys: str = "Ctrl+Shift+Q"):
        super().__init__()
        self.tr_keys, self.dict_keys, self.chat_keys = parse(translate_keys), parse(dictate_keys), parse(chat_keys)
        self.conv_keys = parse(convert_keys)
        self._stop = False
        self._thread = None

    def start(self):
        import threading
        self._thread = threading.Thread(target=self.run, name="hotkeys", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop = True

    def wait(self, ms: int = 500):
        if self._thread:
            self._thread.join(ms / 1000)

    def run(self):
        if sys.platform != "win32":
            return
        import ctypes
        get = ctypes.windll.user32.GetAsyncKeyState

        def down(vks):
            return bool(vks) and all(get(v) & 0x8000 or (v == 0x5B and get(0x5C) & 0x8000) for v in vks)

        import time
        tr_was = chat_was = conv_was = False
        held_since = 0.0            # когда зажали сочетание диктовки
        recording = cancelled = False
        others = (0x25, 0x26, 0x27, 0x28, 0x44, 0x73, 0x09)   # стрелки, D, F4, Tab — это Ctrl+Win+… Windows, не диктовка
        while not self._stop:
            tr = down(self.tr_keys)
            if tr and not tr_was:
                self.translate.emit()
            tr_was = tr
            ch = down(self.chat_keys)
            if ch and not chat_was:
                self.chat.emit()
            chat_was = ch
            cv = down(self.conv_keys)
            if cv and not conv_was:
                self.convert.emit()
            conv_was = cv
            dk = down(self.dict_keys) and not (get(0x10) & 0x8000) and not (get(0x12) & 0x8000)
            now = time.monotonic()
            if dk:
                if not held_since:
                    held_since, cancelled = now, False
                if any(get(v) & 0x8000 for v in others):
                    cancelled = True
                    if recording:
                        recording = False
                        self.dictate_cancel.emit()
                elif not recording and not cancelled and now - held_since >= self.HOLD:
                    recording = True              # держит дольше 0,35 с — значит, диктует
                    self.dictate.emit(True)
            else:
                if recording:
                    self.dictate.emit(False)
                held_since, recording = 0.0, False
            time.sleep(0.03)

    HOLD = 0.35
