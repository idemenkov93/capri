"""Озвучка ответов. Два движка:

- edge  — нейроголоса Microsoft (живые, с интонацией, есть иврит). Нужен интернет:
          текст ответа уходит в сервис Microsoft для синтеза.
- piper — полностью локально, офлайн, но звучит заметно «роботнее».
Если edge недоступен (нет интернета), Капри сама переключится на piper.
"""
from __future__ import annotations

import subprocess
import sys
import wave
from pathlib import Path

import httpx
import numpy as np

VOICES_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main"


def voice_url(name: str, ext: str) -> str:
    """ru_RU-irina-medium → .../ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx"""
    lang_region, speaker, quality = name.split("-", 2)
    lang = lang_region.split("_")[0]
    return f"{VOICES_URL}/{lang}/{lang_region}/{speaker}/{quality}/{name}{ext}"


def ensure_voice(name: str, folder: Path) -> Path:
    """Скачать голос Piper один раз (≈60 МБ) и вернуть путь к .onnx."""
    folder.mkdir(parents=True, exist_ok=True)
    onnx = folder / f"{name}.onnx"
    for ext in (".onnx.json", ".onnx"):
        target = folder / f"{name}{ext}"
        if target.exists() and target.stat().st_size > 0:
            continue
        tmp = target.with_suffix(target.suffix + ".part")
        with httpx.stream("GET", voice_url(name, ext), follow_redirects=True, timeout=60) as r:
            r.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in r.iter_bytes(1 << 16):
                    f.write(chunk)
        tmp.replace(target)
    return onnx


def decode_mp3(data: bytes) -> tuple[np.ndarray, int]:
    """MP3 → int16 моно через PyAV (он уже стоит вместе с faster-whisper)."""
    import io

    import av

    with av.open(io.BytesIO(data)) as container:
        resampler = av.AudioResampler(format="s16", layout="mono")
        rate = container.streams.audio[0].rate
        frames = []
        for frame in container.decode(audio=0):
            for out in resampler.resample(frame):
                frames.append(out.to_ndarray().reshape(-1))
    return (np.concatenate(frames) if frames else np.zeros(0, np.int16)), rate


def play_blocking(sd, audio, sr: int, should_stop) -> None:
    """Проиграть звук своим потоком вывода, кусками по 0.1 с, проверяя «стоп» между кусками.
    Не трогает глобальный sd.play/sd.wait — раньше они иногда «зависали», и Капри навсегда оставалась в SPEAKING."""
    import numpy as np
    audio = np.ascontiguousarray(audio.reshape(-1, 1))
    step = max(1, sr // 10)
    with sd.OutputStream(samplerate=sr, channels=1, dtype=audio.dtype.name) as out:
        for i in range(0, len(audio), step):
            if should_stop():
                out.abort()
                return
            out.write(audio[i:i + step])


def split_sentences(text: str, min_len: int = 40) -> list[str]:
    """Режем на предложения, но слишком короткие склеиваем с соседним («Ну привет.» + следующее)."""
    import re
    raw = [p.strip() for p in re.split(r"(?<=[.!?…])\s+", text) if p.strip()]
    out: list[str] = []
    for p in raw:
        if out and len(out[-1]) < min_len:
            out[-1] += " " + p
        else:
            out.append(p)
    return out or [text]


class EdgeSpeaker:
    """Нейроголоса Microsoft через edge-tts."""

    def __init__(self, voices: dict[str, str], rate: str = "+0%", pitch: str = "+0Hz"):
        import sounddevice as sd

        self._sd = sd
        self.voices, self.rate, self.pitch = voices, rate, pitch
        self.fx: dict = {}   # фильтры голоса (см. fx.py), меняются из «🎛 Голос Капри…»
        self._stopped = False

    def warmup(self, lang: str) -> None:
        pass

    def _synth(self, text: str, voice: str):
        import edge_tts

        from .fx import apply
        comm = edge_tts.Communicate(text, voice, rate=self.rate, pitch=self.pitch, connect_timeout=5, receive_timeout=20)
        mp3 = b"".join(c["data"] for c in comm.stream_sync() if c.get("type") == "audio")
        audio, sr = decode_mp3(mp3)
        return apply(audio, sr, self.fx), sr

    def say(self, text: str, lang: str) -> None:
        """По предложениям: первое начинает звучать сразу, следующее синтезируется, пока играет текущее."""
        from concurrent.futures import ThreadPoolExecutor

        voice = self.voices.get(lang) or self.voices.get("en")
        parts = split_sentences(text)
        self._stopped = False
        with ThreadPoolExecutor(max_workers=1) as ex:
            nxt = ex.submit(self._synth, parts[0], voice)
            for i in range(len(parts)):
                audio, sr = nxt.result()
                if i + 1 < len(parts):
                    nxt = ex.submit(self._synth, parts[i + 1], voice)
                if self._stopped:
                    break
                play_blocking(self._sd, audio, sr, lambda: self._stopped)
                if self._stopped:
                    break

    def stop(self) -> None:
        self._stopped = True


class FallbackSpeaker:
    """Сначала пробуем основной движок, при ошибке (например, нет интернета) — запасной."""

    def __init__(self, primary, backup):
        self.primary, self.backup = primary, backup

    def warmup(self, lang: str) -> None:
        for s in (self.primary, self.backup):
            if s:
                try:
                    s.warmup(lang)
                except Exception:  # noqa: BLE001
                    pass

    def say(self, text: str, lang: str) -> None:
        try:
            self.primary.say(text, lang)
        except Exception:  # noqa: BLE001
            if not self.backup:
                raise
            self.backup.say(text, lang)

    def stop(self) -> None:
        for s in (self.primary, self.backup):
            if s:
                s.stop()


class Speaker:
    def __init__(self, voices: dict[str, str], folder: Path):
        import sounddevice as sd

        self._sd = sd
        self.voices = voices
        self.folder = folder
        self._stopped = False
        self.fx: dict = {}
        self._loaded: dict[str, object] = {}

    def _piper(self, lang: str):
        if lang not in self._loaded:
            from piper import PiperVoice

            self._loaded[lang] = PiperVoice.load(str(ensure_voice(self.voices[lang], self.folder)))
        return self._loaded[lang]

    def warmup(self, lang: str) -> None:
        if lang in self.voices:
            self._piper(lang)

    def _synthesize(self, voice, text: str) -> tuple[np.ndarray, int]:
        # piper-tts ≥ 1.3: synthesize() отдаёт кусочки звука
        try:
            chunks = list(voice.synthesize(text))
            if chunks and hasattr(chunks[0], "audio_int16_array"):
                audio = np.concatenate([c.audio_int16_array for c in chunks])
                return audio, chunks[0].sample_rate
        except TypeError:
            pass
        # старые версии piper-tts: пишем в WAV в памяти
        import io

        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            voice.synthesize(text, wav)
        buf.seek(0)
        with wave.open(buf, "rb") as wav:
            rate = wav.getframerate()
            audio = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)
        return audio, rate

    def say(self, text: str, lang: str) -> None:
        """Проговорить текст (блокирует поток до конца фразы)."""
        if not text:
            return
        if lang in self.voices:
            audio, rate = self._synthesize(self._piper(lang), text)
            from .fx import apply
            audio = apply(audio, rate, self.fx)
            self._stopped = False
            play_blocking(self._sd, audio, rate, lambda: self._stopped)
        elif sys.platform == "win32":
            windows_say(text, lang)

    def stop(self) -> None:
        self._stopped = True


def windows_say(text: str, lang: str) -> None:
    """Запасной вариант: встроенный голос Windows для нужного языка (если установлен)."""
    culture = {"he": "he-IL", "ru": "ru-RU", "de": "de-DE", "en": "en-US"}.get(lang, "en-US")
    script = (
        "[Console]::InputEncoding = [Text.Encoding]::UTF8;"
        "Add-Type -AssemblyName System.Speech;"
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        f"$v = $s.GetInstalledVoices() | Where-Object {{ $_.VoiceInfo.Culture.Name -eq '{culture}' }} | Select-Object -First 1;"
        "if ($v) { $s.SelectVoice($v.VoiceInfo.Name); $s.Speak([Console]::In.ReadToEnd()) }"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        input=text, text=True, encoding="utf-8", capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
