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
        self.stt_override = None        # строгий игровой режим: vosk вместо Whisper
        self._cache: dict[str, tuple] = {}   # заранее записанные фразы (таймер бомбы)
        self.recording = False
        self._speaking = 0
        self.last_lang = "ru"
        self._jobs: list[_Job] = []

    # фоновые задачи ---------------------------------------------------------
    def _run(self, fn, on_ok=None, on_fail=None):
        """Фоновая задача. Ответ возвращается в главный поток: сигналы потока подключены к методам этого
        QObject, поэтому Qt доставляет их через очередь главного потока (раньше лямбды выполнялись прямо
        в фоновом потоке и трогали окна оттуда — отсюда «зависания» и вечное SPEAKING)."""
        job = _Job(fn)
        job.on_ok, job.on_fail = on_ok, on_fail or self.error.emit
        job.ok.connect(self._job_ok)
        job.fail.connect(self._job_fail)
        job.finished.connect(self._job_done)
        self._jobs.append(job)
        job.start()

    def _job_ok(self, result):
        job = self.sender()
        if job is not None and job.on_ok:
            job.on_ok(result)

    def _job_fail(self, err):
        job = self.sender()
        if job is not None:
            job.on_fail(err)

    def _job_done(self):
        job = self.sender()
        if job in self._jobs:
            self._jobs.remove(job)
            job.deleteLater()

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

    SPEAK_SLACK = 30.0      # секунд сверх ~0.12 с на символ: синтез + проигрывание
    chime_volume = 0.7   # 0…1 — громкость «дзынь» после «Капри»

    def chime(self) -> None:
        """Заметный «дзынь-дзынь» — услышала имя и слушаю (~0.25 с, распознаванию не мешает)."""
        try:
            import numpy as np
            import sounddevice as sd
            sr = 24000

            def note(freq: float, dur: float):
                t = np.arange(int(sr * dur)) / sr
                wave = np.sin(2 * np.pi * freq * t) + 0.35 * np.sin(2 * np.pi * 2 * freq * t)   # обертон — слышно даже под музыку
                env = np.minimum(1.0, t / 0.008) * np.exp(-t * 9)                                   # быстрый старт, мягкое затухание
                return wave * env

            tone = np.concatenate([note(988, 0.11), note(1480, 0.15)])
            tone = tone / np.abs(tone).max() * max(0.05, min(1.0, self.chime_volume))
            sd.play(tone.astype(np.float32), sr)
        except Exception:  # noqa: BLE001
            pass

    def listen_once(self) -> None:
        """Слушать одну фразу и самой остановиться, когда договоришь (после «Привет, Капри»)."""
        if self.recording:
            return
        self.toggle_record(auto=True)

    def cancel_record(self) -> None:
        """Ложное «Капри» — тихо перестать слушать, ничего не распознавая."""
        if not self.recording:
            return
        self.recording = False
        self._auto_timer.stop()
        self.recorder.stop()
        self.state.emit("IDLE")

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
            from ..log import log
            log.info("stt [%s/%s]: «%s»", getattr(stt, "device", "?"), lang, text)
            self.state.emit("IDLE")
            if lang:
                self.last_lang = lang
            self.transcribed.emit(text, lang)

        def failed(err):
            from ..log import log
            log.error("stt: %s", err)
            self.state.emit("IDLE")
            self.error.emit(f"Не получилось распознать: {err}")

        stt = self.stt_override or self.stt
        self._run(lambda: stt.transcribe(audio), done, failed)

    # заранее записанные фразы -------------------------------------------------
    def precache(self, phrases: dict[str, str], lang: str = "ru") -> None:
        """Озвучить фразы заранее (в меню игры), чтобы в раунде только проиграть их — без сети и без нагрузки."""
        todo = {k: v for k, v in phrases.items() if k not in self._cache}
        if not todo:
            return

        def work():
            for key, text in todo.items():
                audio = None
                if self.edge:
                    try:
                        audio = self.edge._synth(text, self.edge.voices.get(lang) or self.edge.voices.get("en"))
                    except Exception:  # noqa: BLE001 — нет интернета: запишем голосом Piper
                        audio = None
                if audio is None and self.piper:
                    try:
                        audio = self.piper._synthesize(self.piper._piper(lang), text)
                    except Exception:  # noqa: BLE001
                        audio = None
                if audio is not None:
                    self._cache[key] = audio
            return len(self._cache)
        self._run(work, on_fail=lambda e: None)

    def play_cached(self, key: str, fallback: str = "") -> None:
        """Проиграть записанную фразу сразу (перебивает текущую речь). Нет записи — обычная озвучка."""
        item = self._cache.get(key)
        if item is None:
            if fallback:
                self.speak(fallback)
            return
        import threading

        import sounddevice as sd

        from .tts import play_blocking
        if self.speaker:
            self.speaker.stop()
        audio, sr = item
        threading.Thread(target=lambda: play_blocking(sd, audio, sr, lambda: False), daemon=True).start()

    # озвучка ----------------------------------------------------------------
    def speak(self, text: str):
        clean = clean_for_speech(text, self.cfg.get("max_speech_chars", 600))
        if not clean:
            return
        lang = detect_lang(clean, fallback=self.last_lang)
        if self.speaker is None and lang != "he":
            return
        self._speaking += 1
        self.state.emit("SPEAKING")
        once = {"done": False}

        def finished():
            if once["done"]:
                return
            once["done"] = True
            self._speaking = max(0, self._speaking - 1)
            if not self._speaking and not self.recording:
                self.state.emit("IDLE")

        def watchdog():
            # озвучка застряла (сеть, звуковое устройство) — не держим Капри в SPEAKING навсегда
            if not once["done"]:
                from ..log import log
                log.warning("tts: фраза не закончилась за %.0f с — снимаю «говорю»", limit)
                if self.speaker:
                    self.speaker.stop()
                finished()
        limit = self.SPEAK_SLACK + len(clean) * 0.12
        QTimer.singleShot(int(limit * 1000), watchdog)

        def say():
            from ..log import log
            log.debug("tts: говорю %d символов [%s]", len(clean), lang)
            if self.speaker:
                self.speaker.say(clean, lang)
            else:
                from .tts import windows_say
                windows_say(clean, lang)

        def failed(e):
            from ..log import log
            log.error("tts: %s", e)
            finished()
            self.error.emit(f"Озвучка не сработала: {e}")

        self._run(say, lambda _: finished(), failed)
