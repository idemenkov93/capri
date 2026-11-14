"""Router: решает, кто переводит.

Профили:
  «Урок»    (lesson)    — только офлайн. Текст никуда не уходит.
  «Важное»  (important) — DeepL, при сбое офлайн.
  auto — в будни в часы уроков «Урок», в остальное время «Важное» (если есть ключ DeepL).
"""
from __future__ import annotations

import datetime as dt
import time
from collections import OrderedDict

from .base import Result, TranslateError, Translator

PROFILE_NAMES = {"lesson": "Урок", "important": "Важное"}
RETRY_AFTER = 30.0          # после сбоя DeepL пробуем его снова через столько секунд


class Router:
    def __init__(self, offline: Translator, online: Translator, profile: str = "auto",
                 lesson_hours: dict | None = None, state: dict | None = None, clock=time.monotonic,
                 today=dt.datetime.now):
        self.offline, self.online = offline, online
        self.profile = profile                        # auto | lesson | important
        self.lesson_hours = lesson_hours or {"days": [0, 1, 2, 3, 4], "from": 9, "to": 17}
        self.state = state if state is not None else {}   # сохраняется в настройках: счётчик символов DeepL
        self.clock, self.today = clock, today
        self.cache: OrderedDict = OrderedDict()
        self.online_retry_at = 0.0
        self.last_error = ""

    # ---------- профиль
    def active_profile(self) -> str:
        if self.profile in ("lesson", "important"):
            return self.profile
        now = self.today()
        h = self.lesson_hours
        in_lesson = now.weekday() in h["days"] and h["from"] <= now.hour < h["to"]
        return "lesson" if in_lesson or not self.online.ready() else "important"

    def toggle(self) -> str:
        """Переключить профиль вручную (горячая клавиша). Возвращает новый профиль."""
        self.profile = "important" if self.active_profile() == "lesson" else "lesson"
        return self.profile

    # ---------- квота DeepL
    def _month(self) -> str:
        return f"{self.today():%Y-%m}"

    def _online_allowed(self) -> bool:
        return (self.online.ready() and self.state.get("blocked_month") != self._month()
                and self.clock() >= self.online_retry_at)

    def _count(self, chars: int) -> None:
        if self.state.get("month") != self._month():
            self.state["month"], self.state["chars"] = self._month(), 0
        self.state["chars"] = self.state.get("chars", 0) + chars

    def chars_this_month(self) -> int:
        return self.state.get("chars", 0) if self.state.get("month") == self._month() else 0

    # ---------- перевод
    def _order(self) -> list[Translator]:
        if self.active_profile() == "important" and self._online_allowed():
            return [self.online, self.offline]
        return [self.offline]

    def translate(self, texts: list[str], context: str = "") -> list[Result]:
        """Перевести пачку. Никогда не бросает ошибку: при полном отказе вернёт пустой перевод и причину."""
        results: list[Result | None] = [None] * len(texts)
        todo = []
        for i, text in enumerate(texts):
            hit = self.cache.get(text)
            if hit:
                results[i] = Result(hit[0], hit[1], 0)
            else:
                todo.append(i)
        if not todo:
            return results
        batch, error = [texts[i] for i in todo], ""
        for engine in self._order():
            started = self.clock()
            try:
                if not engine.ready():
                    raise TranslateError("model" if engine is self.offline else "auth",
                                         f"{engine.name}: не готов")
                out = engine.translate(batch, context)
            except TranslateError as e:
                error = str(e)
                if engine is self.online:
                    if e.kind == "quota":
                        self.state["blocked_month"] = self._month()
                    self.online_retry_at = self.clock() + RETRY_AFTER
                continue
            ms = int((self.clock() - started) * 1000)
            if engine is self.online:
                self._count(sum(len(t) for t in batch))
            for i, ru in zip(todo, out):
                results[i] = Result(ru, engine.name, ms // max(1, len(todo)))
                self.cache[texts[i]] = (ru, engine.name)
                if len(self.cache) > 200:
                    self.cache.popitem(last=False)
            self.last_error = error            # сбой первого переводчика остаётся виден в строке состояния
            return results
        self.last_error = error or "нет переводчика"
        for i in todo:
            results[i] = Result("", "", 0, self.last_error)
        return results
