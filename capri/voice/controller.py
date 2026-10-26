"""Связка голоса с интерфейсом: запись → распознавание → (ответ) → озвучка, всё в фоновых потоках."""
from __future__ import annotations

import importlib.util
from typing import Callable

from PySide6.QtCore import QObject, QThread, Signal

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

    def __init__(self, cfg: dict):
        super().__init__()
        from .stt import Recorder, Transcriber
        from .tts import EdgeSpeaker, FallbackSpeaker, Speaker

        v = cfg["voice"]
        self.cfg = v
        self.recorder = Recorder()
        self.stt = Transcriber(v["whisper_model"], v["languages"], str(expand(v["models_dir"]) / "whisper"),
                               preferred=v.get("preferred_language", "ru"))
        piper = None
        if importlib.util.find_spec("piper") is not None:
            piper = Speaker(v["piper_voices"], expand(v["models_dir"]) / "piper")
        self.speaker = piper
        if v.get("tts_engine", "edge") == "edge" and importlib.util.find_spec("edge_tts") is not None:
            edge = EdgeSpeaker(v["edge_voices"], v.get("edge_rate", "+0%"), v.get("edge_pitch", "+0Hz"))
            self.speaker = FallbackSpeaker(edge, piper)
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

    # микрофон ---------------------------------------------------------------
    def toggle_record(self):
        if self.speaker:
            self.speaker.stop()  # если Капри говорит — замолкает, когда начинаешь говорить ты
        if not self.recording:
            try:
                self.recorder.start()
            except Exception as e:  # noqa: BLE001
                self.error.emit(f"Микрофон не работает: {e}")
                return
            self.recording = True
            self.state.emit("LISTENING")
            return
        self.recording = False
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
