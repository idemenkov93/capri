"""Связка голоса с интерфейсом: запись → распознавание → (ответ) → озвучка, всё в фоновых потоках."""
from __future__ import annotations

import importlib.util
from typing import Callable

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from ..config import expand
from .lang import clean_for_speech, detect_lang


def voice_available() -> tuple[bool, str]:
    missing = [m for m in ("sounddevice", "faster_whisper") if importlib.util.find_spec(m) is None]
    if missing:
        return False, "Голосовой модуль не установлен: " + ", ".join(missing)
    return True, ""


class _Job(QThread):
    ok = Signal(object)
    fail = Signal(str)

    def __init__(self, fn: Callable[[], object]):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            self.ok.emit(self.fn())
        except Exception as e:  # noqa: BLE001 — показываем любую ошибку в чате
            self.fail.emit(str(e))


class VoiceController(QObject):
    state = Signal(str)               # LISTENING | RECOGNIZING | SPEAKING | IDLE
    transcribed = Signal(str, str)    # текст, язык
    error = Signal(str)

    def __init__(self, cfg: dict, device=None):
        super().__init__()
        from .stt import Recorder, Transcriber
        from .tts import EdgeSpeaker, FallbackSpeaker, Speaker

        v = cfg["voice"]
        self.cfg = v
        self.recorder = Recorder(device)
        self._auto_timer = QTimer(self, interval=100, timeout=self._check_auto_stop)
        self.stt = Transcriber(v["whisper_model"], v["languages"], str(expand(v["models_dir"]) / "whisper"),
                               preferred=v.get("preferred_language", "ru"), device=v.get("whisper_device", "auto"))
        piper = None
        if importlib.util.find_spec("piper") is not None:
            piper = Speaker(v["piper_voices"], expand(v["models_dir"]) / "piper")
        self.piper, self.edge = piper, None
        self.speaker = piper
        if v.get("tts_engine", "edge") == "edge" and importlib.util.find_spec("edge_tts") is not None:
            self.edge = EdgeSpeaker(dict(v["edge_voices"]), v.get("edge_rate", "+0%"), v.get("edge_pitch", "+0Hz"))
            self.speaker = FallbackSpeaker(self.edge, piper)
        self.recording = False
        self.last_lang = "ru"
        self._jobs: list[_Job] = []

    # фоновые задачи ---------------------------------------------------------
    def _run(self, fn, on_ok=None, on_fail=None):
        job = _Job(fn)
        if on_ok:
            job.ok.connect(on_ok)
        job.fail.connect(on_fail or self.error.emit)
        job.finished.connect(lambda: self._jobs.remove(job))
        self._jobs.append(job)
        job.start()

    def warmup(self):
        """Скачать/загрузить модели заранее, чтобы первая фраза не ждала."""
        def load():
            self.stt.warmup()
            if self.speaker:
                self.speaker.warmup("ru")
        self._run(load, on_fail=lambda e: self.error.emit(f"Не удалось загрузить голосовые модели: {e}"))

    # настройка голоса -------------------------------------------------------
    def tune(self) -> dict:
        """Текущие голоса Edge, скорость, высота и фильтры."""
        e = self.edge
        return {"voices": dict(e.voices) if e else dict(self.cfg["edge_voices"]),
                "rate": e.rate if e else self.cfg.get("edge_rate", "+0%"),
                "pitch": e.pitch if e else self.cfg.get("edge_pitch", "+0Hz"),
                "fx": dict(getattr(self.piper or e, "fx", {}) or {})}

    def apply_tune(self, tune: dict) -> None:
        if self.edge:
            self.edge.voices.update(tune.get("voices") or {})
            self.edge.rate = tune.get("rate", self.edge.rate)
            self.edge.pitch = tune.get("pitch", self.edge.pitch)
            self.edge.fx = dict(tune.get("fx") or {})
        if self.piper:
            self.piper.fx = dict(tune.get("fx") or {})

    def say_now(self, text: str, lang: str) -> None:
        """Проговорить пробную фразу именно этим языком (для настройки голоса)."""
        if self.speaker:
            self.speaker.stop()
        self.last_lang = lang
        self.speak(text)

    # микрофон ---------------------------------------------------------------
    def set_device(self, device) -> None:
        self.recorder.device = device

    def quiet(self) -> None:
        """Замолчать: оборвать озвучку."""
        if self.speaker:
            self.speaker.stop()

    def listen_once(self) -> None:
        """Слушать одну фразу и самой остановиться, когда договоришь (после «Привет, Капри»)."""
        if self.recording:
            return
        self.toggle_record(auto=True)

    def _check_auto_stop(self):
        if self.recording and self.recorder.should_stop:
            self.toggle_record()

    def toggle_record(self, auto: bool = False):
        if self.speaker:
            self.speaker.stop()  # если Капри говорит — замолкает, когда начинаешь говорить ты
        if not self.recording:
            try:
                self.recorder.start(auto=auto)
            except Exception as e:  # noqa: BLE001
                self.error.emit(f"Микрофон не работает: {e}")
                return
            if auto:
                self._auto_timer.start()
            self.recording = True
            self.state.emit("LISTENING")
            return
        self.recording = False
        self._auto_timer.stop()
        audio = self.recorder.stop()
        self.state.emit("RECOGNIZING")

        def done(result):
            text, lang = result
            self.state.emit("IDLE")
            if lang:
                self.last_lang = lang
            self.transcribed.emit(text, lang)

        def failed(err):
            self.state.emit("IDLE")
            self.error.emit(f"Не получилось распознать: {err}")

        self._run(lambda: self.stt.transcribe(audio), done, failed)

    # озвучка ----------------------------------------------------------------
    def speak(self, text: str):
        clean = clean_for_speech(text, self.cfg.get("max_speech_chars", 600))
        if not clean:
            return
        lang = detect_lang(clean, fallback=self.last_lang)
        if self.speaker is None and lang != "he":
            return
        self.state.emit("SPEAKING")

        def say():
            if self.speaker:
                self.speaker.say(clean, lang)
            else:
                from .tts import windows_say
                windows_say(clean, lang)

        self._run(say, lambda _: self.state.emit("IDLE"),
                  lambda e: (self.state.emit("IDLE"), self.error.emit(f"Озвучка не сработала: {e}")))
