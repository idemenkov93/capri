"""Звук для субтитров: всё, что играет на компьютере (WASAPI loopback — Teams, Zoom, YouTube), и микрофон.

Библиотека soundcard (чистый Python + cffi). Когда на компьютере тишина, Windows вообще не отдаёт звук —
soundcard в этом случае сам досыпает нули, так что паузы между фразами видны.
"""
from __future__ import annotations

import threading
import time
import warnings
from typing import Callable

import numpy as np

from .segmenter import SR


def resample(x: np.ndarray, sr_from: int, sr_to: int = SR) -> np.ndarray:
    if sr_from == sr_to or len(x) == 0:
        return x.astype(np.float32, copy=False)
    k = sr_from / sr_to
    if abs(k - round(k)) < 1e-6:                      # 48 000 → 16 000: среднее по 3 — заодно фильтр
        k = int(round(k))
        n = len(x) // k * k
        return x[:n].reshape(-1, k).mean(axis=1).astype(np.float32)
    t = np.arange(0, len(x), k)
    return np.interp(t, np.arange(len(x)), x).astype(np.float32)


def to_mono(block: np.ndarray) -> np.ndarray:
    return block.mean(axis=1) if block.ndim == 2 else block


class Capture:
    """Один источник в отдельном потоке. on_audio(source, frames16k)."""

    def __init__(self, source: str, on_audio: Callable[[str, np.ndarray], None], on_error: Callable[[str], None]):
        self.source, self.on_audio, self.on_error = source, on_audio, on_error
        self.stop_flag = threading.Event()
        self.paused = False
        self.thread = threading.Thread(target=self._run, name=f"capture-{source}", daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_flag.set()

    def _device(self):
        import soundcard as sc
        if self.source == "system":
            spk = sc.default_speaker()
            return sc.get_microphone(id=str(spk.name), include_loopback=True)
        return sc.default_microphone()

    def _run(self):
        try:
            import sys
            if sys.platform == "win32":
                import ctypes
                ctypes.windll.ole32.CoInitializeEx(None, 0)       # COM в этом потоке (иначе soundcard падает)
            warnings.filterwarnings("ignore", message=".*discontinuity.*")
        except Exception:  # noqa: BLE001
            pass
        sr = 48000
        while not self.stop_flag.is_set():
            try:
                dev = self._device()
                with dev.recorder(samplerate=sr, blocksize=sr // 50) as rec:
                    while not self.stop_flag.is_set():
                        # soundcard сам возвращает нули, когда Windows молчит, — паузы между фразами видны
                        data = rec.record(numframes=None)
                        if data is not None and len(data):
                            self._emit(resample(to_mono(np.asarray(data, np.float32)), sr))
                        else:
                            time.sleep(0.01)
            except Exception as e:  # noqa: BLE001 — сменили наушники, устройство пропало: пробуем заново
                self.on_error(f"{self.source}: {type(e).__name__}: {str(e)[:120]}")
                if self.stop_flag.wait(2.0):
                    break

    def _emit(self, frames: np.ndarray):
        if not self.paused and len(frames):
            self.on_audio(self.source, frames)
