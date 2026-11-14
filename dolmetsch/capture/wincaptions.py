"""Источник Б: субтитры Windows 11 (Win+Ctrl+L). Работают с любым звуком: Zoom, Meet, видео.

Окно субтитров принадлежит процессу LiveCaptions.exe, текст лежит в элементе с id CaptionsTextBlock.
ВНИМАНИЕ: структура окна взята из открытых проектов и на машинах Игоря ещё не проверена.
Если id не найдётся, берём самый длинный текст в окне.
"""
from __future__ import annotations

import subprocess
import time

from . import uia
from .base import Event
from .rolling import RollingTracker

PROFILE = {"procs": ["livecaptions.exe"], "text_ids": ["CaptionsTextBlock"], "exe": "LiveCaptions.exe"}


class WindowsCaptionsSource:
    name = "Субтитры Windows"

    def __init__(self, profile: dict | None = None, auto=None, get_proc=None, launcher=None):
        self.profile = profile or PROFILE
        self.auto, self.get_proc = auto, get_proc
        self.launcher = launcher or (lambda exe: subprocess.Popen([exe]))
        self.tracker = RollingTracker()
        self.status = "no_window"            # no_window | no_text | ok
        self.title = ""
        self.read_ms: list[float] = []
        self._text = None

    def windows(self) -> list:
        return uia.top_windows(self.auto, set(self.profile["procs"]), self.get_proc)

    def launch(self) -> bool:
        """Включить субтитры Windows (то же, что Win+Ctrl+L)."""
        try:
            self.launcher(self.profile["exe"])
            return True
        except Exception:  # noqa: BLE001
            return False

    def find(self) -> bool:
        self._text = None
        wins = self.windows()
        if not wins:
            self.status = "no_window"
            return False
        self.status = "no_text"
        for win in wins:
            ctrl = uia.find_by_ids(self.auto, win, self.profile["text_ids"]) or self._longest_text(win)
            if ctrl is not None:
                self._text, self.status = ctrl, "ok"
                return True
        return False

    def _longest_text(self, win):
        """Запасной способ: самый длинный текстовый элемент окна."""
        best, best_len, n = None, 0, 0
        for ctrl, _depth in self.auto.WalkControl(win, includeTop=False, maxDepth=20):
            n += 1
            if n > 300:
                break
            if uia.safe(lambda: ctrl.ControlTypeName, "") == "TextControl":
                size = len(str(uia.safe(lambda: ctrl.Name, "")))
                if size > best_len:
                    best, best_len = ctrl, size
        return best

    def poll(self, now: float | None = None) -> list[Event] | None:
        now = time.monotonic() if now is None else now
        if self._text is None:
            return None
        started = time.perf_counter()
        try:
            text = str(self._text.Name or "")
        except Exception:  # noqa: BLE001 — окно субтитров закрыли
            self._text, self.status = None, "no_window"
            return None
        self.read_ms.append((time.perf_counter() - started) * 1000)
        return self.tracker.update(text, now)
