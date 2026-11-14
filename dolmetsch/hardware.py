"""Класс железа: A — без видеокарты NVIDIA, B — 6–12 ГБ видеопамяти, C — 16 ГБ и больше."""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass


@dataclass
class Hardware:
    cls: str            # A | B | C
    cuda: bool
    vram_gb: float
    threads: int


def _vram_gb() -> float:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return max(float(x) for x in out.stdout.split()) / 1024
    except Exception:  # noqa: BLE001
        return 0.0


def detect() -> Hardware:
    cuda = False
    try:
        import ctranslate2
        cuda = ctranslate2.get_cuda_device_count() > 0
    except Exception:  # noqa: BLE001
        pass
    vram = _vram_gb() if cuda else 0.0
    cls = "A" if not cuda or vram < 5.5 else ("C" if vram >= 15.5 else "B")
    return Hardware(cls, cuda, round(vram, 1), os.cpu_count() or 4)


def live_interval(hw: Hardware, setting: str = "auto") -> float | None:
    """Как часто переводить недописанную фразу. None — не переводить (ноутбук: бережём процессор)."""
    if setting == "off":
        return None
    if setting == "on":
        return {"A": 2.5, "B": 1.5, "C": 0.5}[hw.cls]
    return {"A": None, "B": 1.5, "C": 0.5}[hw.cls]
