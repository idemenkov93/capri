"""Конвейер: события от источника → перевод → окно и файл. Один фоновый поток.

Правила очереди:
  - partial не копятся: нужен только последний (он сразу идёт в окно серым);
  - final ждут в очереди; если накопилось несколько — переводятся одной пачкой;
  - фраза, прождавшая дольше STALE секунд, показывается без перевода (иначе отставание только растёт).
"""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable

from .capture.base import Event

STALE = 20.0
BATCH = 6


@dataclass
class Line:
    speaker: str
    text: str           # немецкий оригинал
    translation: str    # русский перевод (пусто — не перевелось)
    engine: str
    ms: int             # от «предложение готово» до «перевод получен»
    error: str = ""


class Pipeline(threading.Thread):
    def __init__(self, router, on_line: Callable[[Line], None],
                 on_partial: Callable[[str, str], None] = lambda speaker, text: None,
                 on_draft: Callable[[str], None] = lambda text: None,
                 transcript=None, live_interval: float | None = None, clock=time.monotonic):
        super().__init__(name="pipeline", daemon=True)
        self.router, self.transcript = router, transcript
        self.on_line, self.on_partial, self.on_draft = on_line, on_partial, on_draft
        self.live_interval, self.clock = live_interval, clock
        self.queue: queue.Queue = queue.Queue()
        self._stop_flag = threading.Event()
        self.context = ""                   # предыдущая фраза — подсказка переводчику
        self.partial = ("", "")
        self._shown = ("", "")
        self._draft_for, self._draft_at = "", 0.0
        self.stats = {"lines": 0, "skipped": 0, "ms": [], "chars": 0}

    def push(self, event: Event) -> None:
        self.queue.put(event)

    def stop(self) -> None:
        self._stop_flag.set()

    # ---------- один шаг (вызывается в цикле; в тестах — напрямую)
    def step(self, timeout: float = 0.1) -> None:
        finals: list[Event] = []
        try:
            event = self.queue.get(timeout=timeout)
            while True:
                if event.kind == "final":
                    finals.append(event)
                else:
                    self.partial = (event.speaker, event.text)
                if len(finals) >= BATCH:
                    break
                event = self.queue.get_nowait()
        except queue.Empty:
            pass
        if finals:
            self._translate(finals)
        if self.partial != self._shown:     # недописанную фразу показываем сразу, без перевода
            self._shown = self.partial
            self.on_partial(*self.partial)
        self._maybe_draft()

    def _translate(self, finals: list[Event]) -> None:
        now = self.clock()
        fresh = []
        for e in finals:
            if now - e.seen_at <= STALE:
                fresh.append(e)
            else:                           # слишком старое — показываем оригинал и идём дальше
                self.stats["skipped"] += 1
                self._emit(e, "", "", 0, "пропущено: перевод отстал")
        if not fresh:
            return
        results = self.router.translate([e.text for e in fresh], self.context)
        done = self.clock()
        for e, r in zip(fresh, results):
            self._emit(e, r.text, r.engine, int((done - e.seen_at) * 1000), r.error)
        self.context = fresh[-1].text
        self._draft_for = ""

    def _emit(self, e: Event, translation: str, engine: str, ms: int, error: str) -> None:
        self.stats["lines"] += 1
        self.stats["chars"] += len(e.text)
        if translation:
            self.stats["ms"].append(ms)
            del self.stats["ms"][:-500]
        if self.transcript is not None:
            self.transcript.add(e.speaker, e.text, translation)
        self.on_line(Line(e.speaker, e.text, translation, engine, ms, error))

    def _maybe_draft(self) -> None:
        """На видеокарте: черновой перевод недописанной фразы, пока человек ещё говорит."""
        text = self.partial[1]
        if not self.live_interval or not text or text == self._draft_for or not self.queue.empty():
            return
        if self.clock() - self._draft_at < self.live_interval or len(text.split()) < 3:
            return
        self._draft_for, self._draft_at = text, self.clock()
        try:
            self.on_draft(self.router.offline.translate([text], self.context)[0])
        except Exception:  # noqa: BLE001 — черновик не обязателен
            pass

    def run(self) -> None:
        while not self._stop_flag.is_set():
            self.step()

    def summary(self) -> str:
        """Сводка за сессию — только числа."""
        ms = sorted(self.stats["ms"])
        if not ms:
            return f"фраз {self.stats['lines']}, переводов нет"
        median, p95 = ms[len(ms) // 2], ms[min(len(ms) - 1, int(len(ms) * 0.95))]
        return (f"фраз {self.stats['lines']}, пропущено {self.stats['skipped']}, символов {self.stats['chars']}, "
                f"задержка: медиана {median} мс, 95 % до {p95} мс")
