"""«Привет, Капри» — офлайн-распознавание фразы пробуждения через vosk (маленькая русская модель).

Слушает микрофон в своём потоке и испускает woke, когда слышит:
«привет капри», «эй капри», «окей капри», «hey capri», «hallo capri»… (vosk иногда пишет «кэпри», «капли»).
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
MODEL_NAME = "vosk-model-small-ru-0.22"


def is_wake(text: str) -> bool:
    return bool(WAKE_RX.search(text.lower().replace("ё", "е")))


def model_path(models_dir: Path) -> Path:
    return Path(models_dir) / MODEL_NAME


def wake_available(models_dir: Path) -> tuple[bool, str]:
    import importlib.util
    if importlib.util.find_spec("vosk") is None:
        return False, "vosk"
    if not (model_path(models_dir) / "am").is_dir():
        return False, "model"
    return True, ""


class WakeListener(QThread):
    woke = Signal(str)       # что услышал
    failed = Signal(str)

    def __init__(self, models_dir: Path, device=None):
        super().__init__()
        self.models_dir = Path(models_dir)
        self.device = device
        self.paused = False      # пока Капри сама слушает или говорит — не реагируем
        self._stop = False

    def stop(self):
        self._stop = True
        self.wait(2000)

    def run(self):
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
        rec = KaldiRecognizer(model, rate)
        audio: queue.Queue[bytes] = queue.Queue(maxsize=100)

        def cb(indata, frames, t, status):  # noqa: ARG001
            try:
                audio.put_nowait(bytes(indata))
            except queue.Full:
                pass

        last = 0.0
        try:
            with sd.RawInputStream(samplerate=rate, blocksize=4000, dtype="int16", channels=1,
                                   device=self.device, callback=cb):
                while not self._stop:
                    try:
                        data = audio.get(timeout=0.5)
                    except queue.Empty:
                        continue
                    if self.paused:
                        rec.Reset()
                        continue
                    if rec.AcceptWaveform(data):
                        text = json.loads(rec.Result()).get("text", "")
                    else:
                        text = json.loads(rec.PartialResult()).get("partial", "")
                    if text and is_wake(text) and time.monotonic() - last > 2.5:
                        last = time.monotonic()
                        rec.Reset()
                        self.woke.emit(text)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"микрофон: {e}")
