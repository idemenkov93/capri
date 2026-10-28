"""Температура и загрузка видеокарты NVIDIA — через nvml.dll из драйвера (без запуска nvidia-smi).

Один вызов — доли миллисекунды, так что опрос раз в 30 секунд во время игры ничего не стоит.
Нет NVIDIA или драйвера — read() вернёт None, и Капри просто промолчит про температуру.
"""
from __future__ import annotations

import ctypes
import sys
import threading

_lib = None
_handle = None
_lock = threading.Lock()
_failed = False


def _init() -> bool:
    global _lib, _handle, _failed
    if _handle is not None:
        return True
    if _failed:
        return False
    try:
        if sys.platform == "win32":
            import os
            names = [os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "nvml.dll"),
                     r"C:\Program Files\NVIDIA Corporation\NVSMI\nvml.dll", "nvml.dll"]
        else:
            names = ["libnvidia-ml.so.1"]
        for n in names:
            try:
                _lib = ctypes.CDLL(n)
                break
            except OSError:
                continue
        if _lib is None or _lib.nvmlInit_v2() != 0:
            raise OSError("nvml")
        h = ctypes.c_void_p()
        if _lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(h)) != 0:
            raise OSError("nvml device")
        _handle = h
        return True
    except (OSError, AttributeError):
        _failed = True
        return False


class _Util(ctypes.Structure):
    _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]


def read() -> dict | None:
    """{'temp': 71, 'load': 97} или None."""
    with _lock:
        if not _init():
            return None
        t = ctypes.c_uint()
        if _lib.nvmlDeviceGetTemperature(_handle, 0, ctypes.byref(t)) != 0:   # 0 — датчик GPU
            return None
        u = _Util()
        load = u.gpu if _lib.nvmlDeviceGetUtilizationRates(_handle, ctypes.byref(u)) == 0 else None
        return {"temp": int(t.value), "load": load}


class Stats:
    """Копит замеры за игру: максимум и среднее."""

    def __init__(self):
        self.temps: list[int] = []

    def add(self, sample: dict | None) -> None:
        if sample and sample.get("temp"):
            self.temps.append(int(sample["temp"]))

    def summary(self) -> str:
        if not self.temps:
            return ""
        mx, avg = max(self.temps), round(sum(self.temps) / len(self.temps))
        warn = " Горячо — проверь пыль и обдув." if mx >= 85 else ""
        return f"Видеокарта: максимум {mx}°, в среднем {avg}°.{warn}"

    @property
    def hot(self) -> bool:
        return bool(self.temps) and max(self.temps) >= 85
