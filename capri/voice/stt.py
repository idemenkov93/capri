"""Распознавание речи локально через faster-whisper. Сам определяет язык."""
from __future__ import annotations

import numpy as np

SAMPLE_RATE = 16_000


class Recorder:
    """Запись с микрофона: start() … stop() → numpy float32, 16 кГц, моно."""

    def __init__(self):
        import sounddevice as sd  # импорт здесь, чтобы Капри работала и без голосового модуля

        self._sd = sd
        self._chunks: list[np.ndarray] = []
        self._stream = None

    def start(self) -> None:
        self._chunks = []
        self._stream = self._sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="float32",
            callback=lambda data, *_: self._chunks.append(data.copy()),
        )
        self._stream.start()

    def stop(self) -> np.ndarray:
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if not self._chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(self._chunks)[:, 0]


class Transcriber:
    def __init__(self, model_size: str = "small", languages: list[str] | None = None, download_root: str | None = None):
        self.model_size = model_size
        self.languages = languages or ["ru", "de", "en", "he"]
        self.download_root = download_root
        self._model = None

    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            # int8 на процессоре — быстро и мало памяти; модель скачается один раз
            self._model = WhisperModel(self.model_size, device="cpu", compute_type="int8",
                                       download_root=self.download_root)
        return self._model

    def warmup(self) -> None:
        self._load()

    def _pick_language(self, audio: np.ndarray) -> str | None:
        """Выбираем язык только из разрешённых (чтобы whisper не «услышал» португальский)."""
        model = self._load()
        try:
            _, _, probs = model.detect_language(audio)
        except (AttributeError, TypeError, ValueError):
            return None
        allowed = [(lang, p) for lang, p in probs if lang in self.languages]
        return max(allowed, key=lambda x: x[1])[0] if allowed else None

    def transcribe(self, audio: np.ndarray) -> tuple[str, str]:
        """Вернуть (текст, язык)."""
        if audio.size < SAMPLE_RATE * 0.3:  # меньше 0.3 сек — это не речь
            return "", ""
        model = self._load()
        lang = self._pick_language(audio)
        segments, info = model.transcribe(audio, language=lang, beam_size=1, vad_filter=True)
        text = " ".join(s.text.strip() for s in segments).strip()
        return text, (lang or info.language)
