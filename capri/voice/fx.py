"""Фильтры для голоса Капри: робот, цифра, эхо, двойник, радио, глитч.

Работают с готовым звуком (int16, моно) прямо перед воспроизведением — подходят и к Edge, и к Piper.
Все силы эффектов — от 0 до 100. Настраиваются в меню «🎛 Голос Капри…».
"""
from __future__ import annotations

import numpy as np

FX_KEYS = ("robot", "digital", "echo", "double", "radio", "glitch")

PRESETS: dict[str, dict[str, int]] = {
    "clean":    {"robot": 0, "digital": 0, "echo": 0, "double": 0, "radio": 0, "glitch": 0},
    "cyber":    {"robot": 18, "digital": 15, "echo": 12, "double": 30, "radio": 0, "glitch": 12},
    "hologram": {"robot": 8, "digital": 0, "echo": 35, "double": 45, "radio": 10, "glitch": 5},
    "radio":    {"robot": 0, "digital": 10, "echo": 0, "double": 0, "radio": 70, "glitch": 8},
    "glitchy":  {"robot": 25, "digital": 35, "echo": 10, "double": 20, "radio": 0, "glitch": 45},
    "demon":    {"robot": 45, "digital": 20, "echo": 30, "double": 60, "radio": 0, "glitch": 15},
}


def _delay(x: np.ndarray, samples: int) -> np.ndarray:
    if samples <= 0:
        return x
    out = np.zeros_like(x)
    out[samples:] = x[:-samples]
    return out


def apply(audio: np.ndarray, sr: int, fx: dict | None, seed: int | None = None) -> np.ndarray:
    """int16 → int16 с эффектами. Пустые/нулевые настройки — звук не меняется."""
    fx = {k: max(0, min(100, int((fx or {}).get(k, 0)))) / 100 for k in FX_KEYS}
    if audio.size == 0 or not any(fx.values()):
        return audio
    x = audio.astype(np.float32) / 32768.0
    peak_in = float(np.max(np.abs(x))) or 1.0
    rng = np.random.default_rng(seed)

    if fx["glitch"]:
        # глитч: изредка короткий кусочек повторяется «заиканием» 2–3 раза
        out, i, n = [], 0, len(x)
        while i < n:
            if rng.random() < 0.06 * fx["glitch"] and i > sr // 4:
                ln = int(sr * rng.uniform(0.03, 0.07))
                piece = x[i:i + ln]
                out.extend([piece] * int(rng.integers(2, 4)))
                i += ln
            else:
                step = sr // 20
                out.append(x[i:i + step])
                i += step
        x = np.concatenate(out)

    if fx["double"]:
        # двойник: та же фраза с задержкой 18 и 31 мс — голос «в два слоя», как у ИИ
        x = x + 0.45 * fx["double"] * (_delay(x, int(sr * 0.018)) + 0.6 * _delay(x, int(sr * 0.031)))

    if fx["robot"]:
        # кольцевая модуляция — металлический «роботный» призвук
        t = np.arange(len(x), dtype=np.float32) / sr
        carrier = np.sin(2 * np.pi * 55.0 * t)
        x = x * (1 - fx["robot"]) + x * carrier * fx["robot"] * 1.4

    if fx["radio"]:
        # радио/рация: оставляем 300–3400 Гц и слегка перегружаем
        spec = np.fft.rfft(x)
        freqs = np.fft.rfftfreq(len(x), 1 / sr)
        band = (freqs > 300) & (freqs < 3400)
        mask = np.where(band, 1.0, 1 - fx["radio"])
        x = np.fft.irfft(spec * mask, n=len(x)).astype(np.float32)
        x = np.tanh(x * (1 + 4 * fx["radio"])) / np.tanh(1 + 4 * fx["radio"])

    if fx["digital"]:
        # цифровое «зерно»: меньше бит и реже отсчёты
        bits = 16 - 11 * fx["digital"]
        q = 2 ** (bits - 1)
        hold = 1 + int(5 * fx["digital"])
        x = np.round(x * q) / q
        if hold > 1:
            x = np.repeat(x[::hold], hold)[: len(x)]

    if fx["echo"]:
        # эхо: три затухающих повтора через 0.17 с
        d = int(sr * 0.17)
        tail = np.zeros(len(x) + 3 * d, dtype=np.float32)
        tail[: len(x)] += x
        for k in range(1, 4):
            tail[k * d: k * d + len(x)] += x * (0.45 * fx["echo"]) ** k
        x = tail

    peak = float(np.max(np.abs(x))) or 1.0
    x = x * (min(1.0, peak_in * 1.05) / peak) * 0.98   # громкость как была, без хрипа
    return (x * 32767).astype(np.int16)
