"""Распознавание речи локально через faster-whisper. Сам определяет язык."""
from __future__ import annotations

import numpy as np

SAMPLE_RATE = 16_000


class Recorder:
    """Запись с микрофона: start() … stop() → numpy float32, 16 кГц, моно.

    auto=True — для «Привет, Капри»: should_stop становится True, когда ты договорил
    (пауза ~1.2 с после речи), если долго молчишь или говоришь дольше 12 секунд.
    """

    SPEECH_LEVEL = 0.12     # громкость (0..1), выше которой считаем, что это речь
    SILENCE_AFTER = 1.2     # секунд тишины после речи — значит, договорил
    NO_SPEECH = 6.0         # если за столько секунд не начал говорить — отбой
    MAX_LEN = 12.0

    def __init__(self, device=None):
        import sounddevice as sd  # импорт здесь, чтобы Капри работала и без голосового модуля

        self._sd = sd
        self.device = device
        self._chunks: list[np.ndarray] = []
        self._stream = None
        self.level = 0.0  # текущая громкость 0..1 — для индикатора в интерфейсе
        self.auto = False
        self._elapsed = 0.0
        self._heard = False
        self._quiet_for = 0.0

    def start(self, auto: bool = False) -> None:
        self._chunks = []
        self.auto, self._elapsed, self._heard, self._quiet_for = auto, 0.0, False, 0.0
        self._stream = self._sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="float32", device=self.device,
            callback=self._on_audio,
        )
        self._stream.start()

    def _on_audio(self, data, *_):
        self._chunks.append(data.copy())
        self.level = float(min(1.0, np.sqrt(np.mean(data ** 2)) * 12))
        self.track(self.level, len(data) / SAMPLE_RATE)

    def track(self, level: float, seconds: float) -> None:
        """Учёт речи и тишины (вынесено отдельно, чтобы проверить без микрофона)."""
        self._elapsed += seconds
        if level >= self.SPEECH_LEVEL:
            self._heard, self._quiet_for = True, 0.0
        else:
            self._quiet_for += seconds

    @property
    def should_stop(self) -> bool:
        if not self.auto:
            return False
        if self._elapsed >= self.MAX_LEN:
            return True
        if self._heard:
            return self._quiet_for >= self.SILENCE_AFTER
        return self._elapsed >= self.NO_SPEECH

    def stop(self) -> np.ndarray:
        self.level = 0.0
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if not self._chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(self._chunks)[:, 0]


class Transcriber:
    def __init__(self, model_size: str = "small", languages: list[str] | None = None,
                 download_root: str | None = None, preferred: str | None = "ru"):
        self.model_size = model_size
        self.preferred = preferred
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
        # основной язык получает фору: короткие фразы whisper иногда путает (русское «как дела» → немецкий)
        allowed = [(lang, p * (2.0 if lang == self.preferred else 1.0)) for lang, p in probs if lang in self.languages]
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
