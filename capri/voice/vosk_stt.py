"""Распознавание фразы через vosk — на процессоре, почти без нагрузки. Нужен в строгом игровом режиме (CS2):
Whisper на видеокарте во время игры дёргает кадры, а vosk-модель для «Капри» и так уже загружена."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .stt import SAMPLE_RATE


class VoskTranscriber:
    device = "vosk"

    def __init__(self, models_dir: Path, shared_model=None):
        self.models_dir = Path(models_dir)
        self._model = shared_model

    def _load(self):
        if self._model is None:
            from vosk import Model, SetLogLevel

            from .wake import model_path
            SetLogLevel(-1)
            self._model = Model(str(model_path(self.models_dir)))
        return self._model

    def warmup(self) -> None:
        self._load()

    def unload(self) -> None:
        pass

    def transcribe(self, audio: np.ndarray) -> tuple[str, str]:
        if audio.size < SAMPLE_RATE * 0.3:
            return "", ""
        from vosk import KaldiRecognizer
        rec = KaldiRecognizer(self._load(), SAMPLE_RATE)
        pcm = (np.clip(audio.reshape(-1), -1.0, 1.0) * 32767).astype(np.int16).tobytes()
        for i in range(0, len(pcm), 32000):
            rec.AcceptWaveform(pcm[i:i + 32000])
        text = json.loads(rec.FinalResult()).get("text", "")
        return text.strip(), "ru" if text else ""
