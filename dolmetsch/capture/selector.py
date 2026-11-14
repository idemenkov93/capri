"""Выбор источника субтитров и поток, который его опрашивает.

Правила:
  1. Есть панель субтитров Teams — работаем с ней (там есть имена).
  2. Открыто окно субтитров Windows — работаем с ним, не спрашивая.
  3. Teams нет вообще — сразу спрашиваем: «Включить субтитры Windows?»
  4. Звонок Teams есть, а панели нет 20 с — спрашиваем то же.
  5. Ответ «Нет» — больше не спрашиваем до следующего старта.
  6. Работаем на субтитрах Windows, а в Teams появилась панель — переходим на Teams.
  7. Субтитры шли и пропали: звонок Teams ещё идёт — через 20 с спрашиваем снова;
     Teams закрыт (встреча кончилась) — не спрашиваем.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

from .base import Event

SEARCH_EVERY = 3.0          # как часто искать источник
RECHECK_TEAMS = 6.0         # работая на субтитрах Windows, как часто проверять Teams
ASK_AFTER = 20.0            # сколько ждать панель Teams, прежде чем предложить субтитры Windows
FAST, SLOW = 0.3, 0.8       # опрос: пока текст идёт / в тишине
QUIET_AFTER = 5.0


class Selector:
    """Чистая логика без потоков: step() вызывается в цикле. Источники подставляются (в тестах — поддельные)."""

    def __init__(self, teams, windows, on_event: Callable[[Event], None],
                 on_state: Callable[[str, str], None] = lambda source, status: None,
                 on_ask: Callable[[], None] = lambda: None):
        self.teams, self.windows = teams, windows
        self.on_event, self.on_state, self.on_ask = on_event, on_state, on_ask
        self.active = None                  # источник, с которым работаем сейчас
        self.asked = self.declined = False
        self.had_source = False             # в этом запуске субтитры уже шли
        self.want_windows = False           # пользователь ответил «Да»
        self.interval = FAST
        self._started = self._next_search = self._next_teams = None
        self._last_text = 0.0
        self._state = ("", "")

    # ---------- ответы пользователя (вызываются из окна)
    def answer(self, yes: bool) -> bool:
        if not yes:
            self.declined = True
            return True
        self.want_windows = True
        self._next_search = None            # искать окно субтитров сразу
        return self.windows.launch()

    # ---------- внутреннее
    def _set_state(self, source: str, status: str) -> None:
        if (source, status) != self._state:
            self._state = (source, status)
            self.on_state(source, status)

    def _use(self, source, now: float) -> None:
        self.active, self._last_text, self.interval = source, now, FAST
        self.had_source = True
        self._next_teams = now + RECHECK_TEAMS
        self._set_state(source.name, "ok")

    def _search(self, now: float) -> None:
        if self._next_search is not None and now < self._next_search:
            return
        self._next_search = now + SEARCH_EVERY
        if self.teams.find():
            return self._use(self.teams, now)
        if self.windows.find():
            return self._use(self.windows, now)
        self._set_state("", self.teams.status)
        no_teams = self.teams.status == "no_window" and not self.had_source
        waited = now - self._started >= ASK_AFTER and self.teams.status != "no_window"
        if (not self.asked and not self.declined and not self.want_windows
                and self.teams.status != "panel_empty" and (no_teams or waited)):
            self.asked = True
            self.on_ask()

    def step(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        if self._started is None:
            self._started = now
        if self.active is None:
            self.interval = FAST
            return self._search(now)
        events = self.active.poll(now)
        if events is None:                  # источник пропал
            self.active, self._next_search = None, None
            self._started, self.asked = now, False
            self._set_state("", "lost")
            return
        for ev in events:
            self._last_text = now
            self.on_event(ev)
        self.interval = FAST if now - self._last_text < QUIET_AFTER else SLOW
        if self.active is self.windows and now >= self._next_teams:
            self._next_teams = now + RECHECK_TEAMS
            if self.teams.find():           # в Teams появились субтитры — там есть имена
                self._use(self.teams, now)


class CaptureLoop(threading.Thread):
    """Фоновый поток: подключает UI Automation и крутит Selector.step()."""

    def __init__(self, make_selector: Callable[[object], Selector], on_error: Callable[[str], None] = print):
        super().__init__(name="capture", daemon=True)
        self.make_selector, self.on_error = make_selector, on_error
        self.selector: Selector | None = None
        self._stop_flag = threading.Event()

    def stop(self) -> None:
        self._stop_flag.set()

    def run(self) -> None:
        try:
            from . import uia
            auto = uia.load()
        except Exception as e:  # noqa: BLE001
            self.on_error(f"UI Automation недоступна: {e}")
            return
        with auto.UIAutomationInitializerInThread():
            self.selector = self.make_selector(auto)
            while not self._stop_flag.is_set():
                try:
                    self.selector.step()
                except Exception as e:  # noqa: BLE001 — не даём потоку умереть
                    self.on_error(f"capture: {type(e).__name__}: {str(e)[:150]}")
                    self.selector.active = None
                self._stop_flag.wait(self.selector.interval)
