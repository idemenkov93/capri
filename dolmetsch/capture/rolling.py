"""RollingTracker: для субтитров Windows. Там нет отдельных реплик — одно «бегущее» окно текста:
новые слова дописываются в конец, старые строки уезжают из начала. Имён говорящих нет.

Правило: законченное предложение отдаём в перевод, когда после него уже идёт следующее
или оно не менялось stable секунд. Недописанный конец показываем серым.
"""
from __future__ import annotations

import re
import time
from collections import deque
from difflib import SequenceMatcher

from .base import Event, norm, sentence_ends


def split_sentences(text: str) -> tuple[list[str], str]:
    """Текст → (законченные предложения, недописанный остаток)."""
    sents, start = [], 0
    for end in sentence_ends(text):
        piece = text[start:end].strip()
        if piece:
            sents.append(piece)
        start = end
    return sents, text[start:].strip()


class RollingTracker:
    def __init__(self, stable: float = 1.2, idle: float = 2.5):
        self.stable, self.idle = stable, idle
        self.seen: deque = deque(maxlen=60)      # уже отданные предложения
        self.pending: dict[str, float] = {}      # предложение → когда впервые увидели
        self.rest, self.rest_changed = "", 0.0
        self.flushed = ""                        # начало фразы, отданное по тишине, без точки
        self.primed = False

    def _known(self, key: str) -> bool:
        if key in self.seen:
            return True
        return any(SequenceMatcher(None, key, old).ratio() > 0.9 for old in list(self.seen)[-6:])

    def _cut_flushed(self, sentence: str) -> str:
        """Часть предложения уже ушла в перевод по тишине — отдаём только продолжение."""
        if self.flushed and norm(sentence).startswith(self.flushed):
            words = sentence.split()[len(self.flushed.split()):]
            self.flushed = ""
            return " ".join(words)
        return sentence

    def update(self, text: str, now: float | None = None) -> list[Event]:
        now = time.monotonic() if now is None else now
        text = re.sub(r"\s+", " ", text or "").strip()
        sents, rest = split_sentences(text)
        out: list[Event] = []

        if not self.primed:                      # что уже было в окне при старте — не переводим
            self.primed = True
            self.seen.extend(norm(s) for s in sents)
            self.rest, self.rest_changed = rest, now
            return out

        keys = []
        for i, s in enumerate(sents):
            key = norm(s)
            keys.append(key)
            if not key or self._known(key):
                continue
            first = self.pending.setdefault(key, now)
            followed = i < len(sents) - 1 or len(rest.split()) >= 2
            if followed or now - first >= self.stable:
                self.pending.pop(key, None)
                self.seen.append(key)
                piece = self._cut_flushed(s)
                if re.search(r"\w", piece):
                    out.append(Event("final", "", piece, now))
        for key in [k for k in self.pending if k not in keys]:
            del self.pending[key]                # предложение переписали или оно уехало

        if rest != self.rest:
            self.rest, self.rest_changed = rest, now
            if self.flushed and not norm(rest).startswith(self.flushed) \
                    and not any(k.startswith(self.flushed) for k in keys):
                self.flushed = ""                # фразу переписали — забываем отданное начало
            out.append(Event("partial", "", rest, now))
        elif rest and now - self.rest_changed >= self.idle and norm(rest) != self.flushed:
            self.flushed = norm(rest)            # тишина: отдаём недописанное как есть
            out.append(Event("final", "", rest, now))
            out.append(Event("partial", "", "", now))
        return out
