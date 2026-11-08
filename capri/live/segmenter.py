"""Режем поток звука на фразы по паузам. Без ИИ: громкость (RMS) против уровня шума, который подстраивается сам.

Фраза закончилась, когда тишина ≥ end_silence (0,6 с), или когда она длиннее max_len (9 с — дольше Whisper
ждать незачем, перевод запоздает). Короче min_speech — кашель, щелчок: выбрасываем.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

SR = 16000
WIN = 480                      # 30 мс


@dataclass
class Chunk:
    source: str                # "system" — звук компьютера (учитель, собеседник), "mic" — ты
    audio: np.ndarray
    start: float               # секунды от начала сессии
    end: float
    rms: float = 0.0


@dataclass
class Segmenter:
    source: str
    min_speech: float = 0.45
    end_silence: float = 0.6
    max_len: float = 9.0
    pre_roll: float = 0.25
    floor: float = 0.004       # тише этого — всегда тишина
    noise: float = 0.002
    _buf: list = field(default_factory=list)
    _pre: list = field(default_factory=list)
    _rest: np.ndarray = field(default_factory=lambda: np.zeros(0, np.float32))
    _speaking: bool = False
    _silence: int = 0
    _t: float = 0.0            # время конца уже обработанного звука
    _start: float = 0.0
    _loud: list = field(default_factory=list)

    def threshold(self) -> float:
        return max(self.floor, self.noise * 3.0)

    def feed(self, frames: np.ndarray) -> list[Chunk]:
        out: list[Chunk] = []
        data = np.concatenate([self._rest, frames.astype(np.float32, copy=False)])
        n = len(data) // WIN * WIN
        self._rest = data[n:]
        for i in range(0, n, WIN):
            w = data[i:i + WIN]
            rms = float(np.sqrt(np.mean(w * w)) + 1e-9)
            loud = rms > self.threshold()
            if not self._speaking:
                self._pre.append(w)
                self._pre = self._pre[-int(self.pre_roll * SR / WIN) or 1:]
                if loud:
                    self._speaking, self._silence = True, 0
                    self._buf = list(self._pre)
                    self._start = self._t - (len(self._pre) - 1) * WIN / SR
                    self._loud = [rms]
                else:
                    self.noise = 0.95 * self.noise + 0.05 * rms     # уровень фона — только в тишине
            else:
                self._buf.append(w)
                if loud:
                    self._silence = 0
                    self._loud.append(rms)
                else:
                    self._silence += 1
                length = len(self._buf) * WIN / SR
                if self._silence * WIN / SR >= self.end_silence or length >= self.max_len:
                    if (c := self._finish()) is not None:
                        out.append(c)
            self._t += WIN / SR
        return out

    def _finish(self) -> Chunk | None:
        audio = np.concatenate(self._buf) if self._buf else np.zeros(0, np.float32)
        speech = len(self._loud) * WIN / SR
        start, end = self._start, self._t + WIN / SR
        rms = float(np.mean(self._loud)) if self._loud else 0.0
        self._buf, self._pre, self._loud = [], [], []
        self._speaking, self._silence = False, 0
        if speech < self.min_speech:
            return None
        return Chunk(self.source, audio, start, end, rms)

    def flush(self) -> list[Chunk]:
        if self._speaking:
            c = self._finish()
            return [c] if c else []
        return []

    def partial(self, min_len: float = 1.2) -> np.ndarray | None:
        """Что говорится прямо сейчас (для «живой» строки), если фраза уже длиннее min_len."""
        if self._speaking and len(self._buf) * WIN / SR >= min_len:
            return np.concatenate(self._buf)
        return None

    @property
    def speaking(self) -> bool:
        return self._speaking
