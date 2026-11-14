"""Tracker: из снимков «хвоста» панели субтитров делает события partial / final.

Teams держит всю речь одного человека в одной реплике и дописывает её (до 600 символов).
Поэтому мы помним, сколько символов реплики уже отдали в перевод (emitted), и отдаём дальше
только новое — по законченным предложениям, не дожидаясь, пока человек замолчит.
"""
from __future__ import annotations

import re
import time
from collections import deque
from difflib import SequenceMatcher

from .base import Event, Item, norm, sentence_ends


class Tracker:
    def __init__(self, stable: float = 1.2):
        self.stable = stable                 # столько секунд тишины — и недописанный хвост считаем готовым
        self.cur: dict | None = None         # реплика, которая сейчас дописывается
        self.seen: deque = deque(maxlen=80)  # уже отданные реплики — чтобы не перевести дважды
        self.primed = False

    # ---------- сравнение реплик
    @staticmethod
    def _key(speaker: str, text: str) -> tuple[str, str]:
        return (speaker, norm(text)[:60])

    def _same(self, it: Item) -> bool:
        """Это та же реплика, что мы ведём? Сначала по началу текста, потом «на глаз» (Teams правит слова)."""
        c = self.cur
        if c is None or it.speaker != c["speaker"]:
            return False
        a, b = norm(it.text), norm(c["text"])
        n = min(len(a), len(b), 25)
        if n and a[:n] == b[:n]:
            return True
        m = min(len(a), len(b), 80)
        return m >= 12 and SequenceMatcher(None, a[:m], b[:m]).ratio() >= 0.75

    # ---------- работа с текущей репликой
    @staticmethod
    def _reanchor(old: str, new: str, emitted: int) -> int:
        """Teams переписал уже отданную часть — находим в новом тексте место, до которого всё отдано."""
        if new[:emitted] == old[:emitted]:
            return emitted
        tail = old[max(0, emitted - 24):emitted]
        pos = new.rfind(tail, 0, emitted + 40) if tail.strip() else -1
        if pos >= 0:
            return pos + len(tail)
        ends = [e for e in sentence_ends(new) if e <= emitted + 15]
        return ends[-1] if ends else min(emitted, len(new))

    def _absorb(self, text: str, now: float) -> bool:
        c = self.cur
        if text == c["text"]:
            return False
        c["emitted"] = self._reanchor(c["text"], text, c["emitted"])
        c["text"], c["changed"] = text, now
        return True

    def _finish(self, out: list[Event], now: float, upto: int | None = None) -> None:
        """Отдать в перевод всё от emitted до upto (или до конца реплики)."""
        c = self.cur
        end = len(c["text"]) if upto is None else upto
        rest = c["text"][c["emitted"]:end].strip(" ,;:-–")
        if re.search(r"\w", rest):
            out.append(Event("final", c["speaker"], rest, now))
        c["emitted"] = max(c["emitted"], end)

    def _early(self, out: list[Event], now: float) -> None:
        """Законченные предложения отдаём сразу: после точки уже идут новые слова."""
        c = self.cur
        text = c["text"]
        ends = [e for e in sentence_ends(text, c["emitted"]) if re.search(r"\w", text[e:])]
        if ends:
            self._finish(out, now, ends[-1])

    def _rest(self) -> str:
        c = self.cur
        return c["text"][c["emitted"]:].strip()

    # ---------- главный метод
    def update(self, items: list[Item], now: float | None = None) -> list[Event]:
        now = time.monotonic() if now is None else now
        items = [it for it in items if it.text.strip()]
        out: list[Event] = []

        if not self.primed:                  # первый снимок: что уже было на экране — не переводим
            if not items:
                return out
            self.primed = True
            for it in items[:-1]:
                self.seen.append(self._key(it.speaker, it.text))
            last = items[-1]
            self.cur = {"speaker": last.speaker, "text": last.text, "emitted": len(last.text), "changed": now}
            return out

        c = self.cur
        if not items:
            if c and self._rest() and now - c["changed"] >= self.stable:
                self._finish(out, now)
            return out

        idx = next((i for i in range(len(items) - 1, -1, -1) if self._same(items[i])), None) if c else None
        if c is not None and idx is not None:
            changed = self._absorb(items[idx].text, now)
            if idx == len(items) - 1:                         # человек ещё говорит
                before = len(out)
                self._early(out, now)
                if self._rest() and now - c["changed"] >= self.stable:
                    self._finish(out, now)                    # замолчал — отдаём хвост
                if changed or len(out) > before:
                    out.append(Event("partial", c["speaker"], self._rest(), now))
                return out
            self._finish(out, now)                            # реплика закончилась, дальше идут новые
            self.seen.append(self._key(c["speaker"], c["text"]))
            new = items[idx + 1:]
        elif c is None:
            new = items[-2:]
        else:                                                 # текущая реплика исчезла из хвоста
            self._finish(out, now)
            self.seen.append(self._key(c["speaker"], c["text"]))
            new = items[-3:]

        new = [it for it in new if self._key(it.speaker, it.text) not in self.seen]
        if not new:
            self.cur = None
            return out
        for it in new[:-1]:                                   # целиком готовые реплики
            out.append(Event("final", it.speaker, it.text.strip(), now))
            self.seen.append(self._key(it.speaker, it.text))
        last = new[-1]
        self.cur = {"speaker": last.speaker, "text": last.text, "emitted": 0, "changed": now}
        self._early(out, now)
        out.append(Event("partial", last.speaker, self._rest(), now))
        return out
