"""«Привет, Капри» — офлайн-распознавание фразы пробуждения через vosk (маленькая русская модель).

Слушает микрофон в своём потоке и испускает woke, когда слышит:
«привет капри», «эй капри», «окей капри», «hey capri», «hallo capri»… (vosk иногда пишет «кэпри», «капли»),
а в коротком режиме (по умолчанию) — просто «Капри».
"""
from __future__ import annotations

import json
import queue
import re
import time
from pathlib import Path

from PySide6.QtCore import QThread, Signal

WAKE_RX = re.compile(
    r"(привет|прив|эй|окей|ок|хей|hey|hi|hallo|okay)\s*,?\s*(капри|кэпри|кепри|капре|каприз|капр|кап ри|кабри|капли|capri)",
    re.I,
)
# короткий режим: просто «Капри» отдельным словом (без «капли», «каприз» — их vosk слышит в обычной речи)
SHORT_RX = re.compile(r"(?<![а-яa-z])(капри|кэпри|кепри|capri)(?![а-яa-z])", re.I)
MODEL_NAME = "vosk-model-small-ru-0.22"


KEYWORDS = ["капри", "привет капри", "эй капри", "окей капри", "привет"]


def keyword_hit(words: list[dict], short: bool, min_conf: float = 0.8) -> bool:
    """Результат «ключевого» распознавателя: [{'word': 'капри', 'conf': 0.97}, …]."""
    for i, w in enumerate(words):
        if w.get("word") == "капри" and w.get("conf", 0) >= min_conf:
            prev = words[i - 1]["word"] if i else ""
            if short or prev in ("привет", "эй", "окей"):
                return True
    return False


def is_wake(text: str, short: bool = False) -> bool:
    t = text.lower().replace("ё", "е")
    return bool(WAKE_RX.search(t) or (short and SHORT_RX.search(t)))


def model_path(models_dir: Path) -> Path:
    return Path(models_dir) / MODEL_NAME


def wake_available(models_dir: Path) -> tuple[bool, str]:
    import importlib.util
    if importlib.util.find_spec("vosk") is None:
        return False, "vosk"
    if not (model_path(models_dir) / "am").is_dir():
        return False, "model"
    return True, ""


class _Reopen(Exception):
    pass


class WakeListener(QThread):
    woke = Signal(str)       # что услышал
    failed = Signal(str)

    def __init__(self, models_dir: Path, device=None, short: bool = True):
        super().__init__()
        self.short = short       # True — хватит просто «Капри»
        self.strict = False      # True — в колонках идёт видео/речь: «Капри» засчитываем только при очень уверенном распознавании
        self.models_dir = Path(models_dir)
        self.device = device
        self._paused = False     # пока Капри сама слушает или говорит — не реагируем
        self._paused_at = 0.0
        self._stop = False
        self._last_hit = 0.0
        self.model = None
        self.last_audio = None   # 3 секунды звука до «Капри» — для проверки Whisper-ом (float32, 16 кГц)

    @property
    def paused(self) -> bool:
        # страховка: «пауза» дольше 45 с — это зависшее состояние (озвучка не сообщила, что закончила), снимаем
        if self._paused and time.monotonic() - self._paused_at > 45:
            from ..log import log
            log.warning("wake: пауза висела больше 45 с — снимаю")
            self._paused = False
        return self._paused

    @paused.setter
    def paused(self, value: bool) -> None:
        if value and not self._paused:
            self._paused_at = time.monotonic()
        self._paused = value

    def stop(self):
        self._stop = True
        self.wait(2000)

    def run(self):
        from ..log import log
        try:
            import sounddevice as sd
            from vosk import KaldiRecognizer, Model, SetLogLevel
        except ImportError as e:
            self.failed.emit(f"нет пакета {e.name}")
            return
        SetLogLevel(-1)
        try:
            model = Model(str(model_path(self.models_dir)))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"модель vosk не загрузилась: {e}")
            return
        rate = 16000
        # два слушателя на одном звуке:
        #  free — обычное распознавание всей речи (ловит «привет капри» внутри фразы);
        #  kw   — распознаёт ТОЛЬКО слова «капри / привет / эй…», остальное → [unk]. Так имя ловится намного надёжнее.
        free = KaldiRecognizer(model, rate)
        kw = KaldiRecognizer(model, rate, json.dumps(KEYWORDS + ["[unk]"], ensure_ascii=False))
        kw.SetWords(True)
        audio: queue.Queue[bytes] = queue.Queue(maxsize=100)

        def cb(indata, frames, t, status):  # noqa: ARG001
            try:
                audio.put_nowait(bytes(indata))
            except queue.Full:
                pass

        last = 0.0
        log.info("wake: слушаю (устройство %s, короткий режим %s)", self.device, self.short)
        while not self._stop:
            try:
                self._listen(sd, rate, cb, audio, free, kw, log, last)
            except _Reopen as r:
                log.warning("wake: %s — переоткрываю микрофон", r)
                free.Reset()
                kw.Reset()
                time.sleep(1.0)
            except Exception as e:  # noqa: BLE001
                log.exception("wake: ошибка")
                self.failed.emit(f"микрофон: {e}")
                return

    def _listen(self, sd, rate, cb, audio, free, kw, log, last):
        """Один «сеанс» микрофона. Если звук перестал приходить — бросаем _Reopen, и run() откроет его заново."""
        import json
        import collections

        import numpy as np
        ring: collections.deque[bytes] = collections.deque(maxlen=15)     # 15 × 0.2 с = 3 с
        got, beat_at, silent_since = 0, time.monotonic(), time.monotonic()
        with sd.RawInputStream(samplerate=rate, blocksize=3200, dtype="int16", channels=1,
                               device=self.device, callback=cb):
                while not self._stop:
                    now = time.monotonic()
                    if now - beat_at > 60:                      # раз в минуту — «я жива» в журнал
                        log.debug("wake: жива, кусков звука за минуту %d, пауза %s", got, self._paused)
                        got, beat_at = 0, now
                    try:
                        data = audio.get(timeout=0.5)
                    except queue.Empty:
                        if now - silent_since > 5:
                            raise _Reopen("5 с нет звука с микрофона")
                        continue
                    got += 1
                    silent_since = now
                    ring.append(data)
                    if self.paused:
                        free.Reset()
                        kw.Reset()
                        continue
                    hit = ""
                    if free.AcceptWaveform(data):
                        text = json.loads(free.Result()).get("text", "")
                        if text:
                            log.debug("wake: слышу «%s»", text)
                    else:
                        text = json.loads(free.PartialResult()).get("partial", "")
                    # во время видео одиночное «капри» из обычного распознавания не считаем (оно часто «слышит» его в речи),
                    # а «привет/эй капри» — считаем всегда
                    if text and is_wake(text, self.short and not self.strict):
                        hit = text
                    if kw.AcceptWaveform(data):
                        words = json.loads(kw.Result()).get("result", [])
                        if keyword_hit(words, self.short, min_conf=0.93 if self.strict else 0.8):
                            hit = hit or " ".join(w["word"] for w in words)
                    if hit and time.monotonic() - self._last_hit > 2.0:
                        self._last_hit = time.monotonic()
                        free.Reset()
                        kw.Reset()
                        log.info("wake: сработало на «%s»", hit)
                        self.last_audio = np.frombuffer(b"".join(ring), np.int16).astype(np.float32) / 32768.0
                        self.woke.emit(hit)
