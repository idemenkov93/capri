"""Что за компьютер и какую модель ему поставить.

Для языковой модели важна не серия видеокарты (10xx/20xx/…), а её видеопамять: RTX 3050 на 4 ГБ
медленнее GTX 1070 на 8 ГБ. Поэтому смотрим память, а уже по ней предлагаем три варианта:
быстрая (глупее), средняя, умная (медленнее). Без видеокарты всё считает процессор.
"""
from __future__ import annotations

import os
import platform
import re
import subprocess
import sys
from dataclasses import dataclass, field

NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


@dataclass
class GPU:
    name: str
    vendor: str            # nvidia | amd | intel | other
    vram_gb: float = 0.0   # 0 — неизвестно или встроенная (общая с оперативкой)

    @property
    def integrated(self) -> bool:
        n = self.name.lower()
        return self.vendor == "intel" and "arc" not in n or "radeon(tm) graphics" in n or "vega" in n and "rx" not in n


@dataclass
class Hardware:
    cpu: str = ""
    cores: int = 1
    ram_gb: float = 0.0
    gpus: list[GPU] = field(default_factory=list)
    os: str = ""

    @property
    def nvidia(self) -> GPU | None:
        return max((g for g in self.gpus if g.vendor == "nvidia"), key=lambda g: g.vram_gb, default=None)

    @property
    def best_gpu(self) -> GPU | None:
        """Видеокарта, на которой может работать модель (NVIDIA; Radeon RX 6000/7000 — Ollama тоже умеет)."""
        if self.nvidia:
            return self.nvidia
        amd = [g for g in self.gpus if g.vendor == "amd" and not g.integrated and g.vram_gb >= 4]
        return max(amd, key=lambda g: g.vram_gb, default=None)

    @property
    def vram_gb(self) -> float:
        g = self.best_gpu
        return g.vram_gb if g else 0.0

    def summary(self) -> str:
        gpu = self.best_gpu
        if gpu:
            g = f"{gpu.name}, {gpu.vram_gb:.0f} ГБ"
        elif self.gpus:
            g = f"{self.gpus[0].name} (встроенная — модель будет на процессоре)"
        else:
            g = "нет (модель будет на процессоре)"
        return f"Процессор: {self.cpu} ({self.cores} потоков)\nОперативная память: {self.ram_gb:.0f} ГБ\nВидеокарта: {g}"


def _run(cmd: list[str], timeout: int = 15) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
        return r.stdout.decode("utf-8", "replace").strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _vendor(name: str) -> str:
    n = name.lower()
    if "nvidia" in n or "geforce" in n or "quadro" in n or "rtx" in n:
        return "nvidia"
    if "amd" in n or "radeon" in n:
        return "amd"
    if "intel" in n:
        return "intel"
    return "other"


def parse_nvidia_smi(out: str) -> list[GPU]:
    """«NVIDIA GeForce GTX 1070, 8192» → GPU(…, 8.0)."""
    gpus = []
    for line in out.splitlines():
        if "," not in line:
            continue
        name, mem = line.rsplit(",", 1)
        try:
            gpus.append(GPU(name.strip(), "nvidia", round(float(mem.strip()) / 1024, 1)))
        except ValueError:
            continue
    return gpus


def _windows_gpus() -> list[GPU]:
    """Все видеоадаптеры из реестра: там точный объём памяти (в WMI он обрезан до 4 ГБ)."""
    ps = (r"Get-ItemProperty 'HKLM:\SYSTEM\ControlSet001\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}\0*' "
          r"-ErrorAction SilentlyContinue | ForEach-Object { '{0}|{1}' -f $_.DriverDesc, $_.'HardwareInformation.qwMemorySize' }")
    out = _run(["powershell", "-NoProfile", "-Command", ps])
    gpus = []
    for line in out.splitlines():
        name, _, mem = line.partition("|")
        name = name.strip()
        if not name or "basic display" in name.lower() or "remote" in name.lower():
            continue
        try:
            vram = round(int(mem) / 1024 ** 3, 1)
        except ValueError:
            vram = 0.0
        gpus.append(GPU(name, _vendor(name), vram))
    return gpus


def _ram_gb() -> float:
    try:
        import psutil
        return round(psutil.virtual_memory().total / 1024 ** 3, 1)
    except Exception:  # noqa: BLE001
        pass
    if sys.platform == "win32":
        import ctypes

        class MS(ctypes.Structure):
            _fields_ = [("len", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                        ("avail", ctypes.c_ulonglong), ("tp", ctypes.c_ulonglong), ("ap", ctypes.c_ulonglong),
                        ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong), ("ave", ctypes.c_ulonglong)]
        m = MS(len=ctypes.sizeof(MS))
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return round(m.total / 1024 ** 3, 1)
    try:
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024 ** 3, 1)
    except (ValueError, OSError, AttributeError):
        return 0.0


def _cpu_name() -> str:
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
                return re.sub(r"\s+", " ", winreg.QueryValueEx(k, "ProcessorNameString")[0]).strip()
        except OSError:
            pass
    return platform.processor() or platform.machine()


def detect() -> Hardware:
    hw = Hardware(cpu=_cpu_name(), cores=os.cpu_count() or 1, ram_gb=_ram_gb(), os=platform.platform())
    nv = parse_nvidia_smi(_run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"]))
    others = _windows_gpus() if sys.platform == "win32" else []
    hw.gpus = nv + [g for g in others if g.vendor != "nvidia" or not nv]
    return hw


# ------------------------------------------------------------------ варианты модели
@dataclass
class Preset:
    level: str             # fast | medium | smart
    model: str             # имя в Ollama
    download_gb: float
    whisper: str           # модель распознавания речи
    on_gpu: bool           # модель целиком влезает в видеокарту
    fits: bool             # будет работать с нормальной скоростью
    note: str = ""
    recommended: bool = False

    @property
    def title(self) -> str:
        return {"fast": "Быстрая", "medium": "Средняя", "smart": "Умная"}[self.level]


# размер скачивания и сколько видеопамяти нужно модели (с запасом под контекст 4096)
MODELS = {
    "qwen3:1.7b": (1.4, 2.2), "qwen3:4b": (2.5, 3.6), "qwen3:8b": (5.2, 6.2),
    "qwen3:14b": (9.3, 10.8), "qwen3:30b": (19.0, 21.0),
    "qwen3-vl:4b": (3.3, 4.5), "qwen3-vl:8b": (6.1, 7.5),
}


def vision_model(hw: "Hardware") -> str:
    """Локальная модель, которая видит картинки: побольше, если хватает видеопамяти."""
    return "qwen3-vl:8b" if hw.vram_gb >= 8 else "qwen3-vl:4b"


def presets(hw: Hardware) -> list[Preset]:
    vram = hw.vram_gb
    if vram >= 20:
        names = ("qwen3:8b", "qwen3:14b", "qwen3:30b")
    elif vram >= 7:
        names = ("qwen3:4b", "qwen3:8b", "qwen3:14b")
    else:
        names = ("qwen3:1.7b", "qwen3:4b", "qwen3:8b")
    whisper_gpu = hw.nvidia is not None and hw.nvidia.vram_gb >= 2
    out = []
    for level, name in zip(("fast", "medium", "smart"), names):
        dl, need = MODELS[name]
        on_gpu = vram >= need
        if on_gpu:
            fits, note = True, "целиком в видеокарте — быстро"
        elif vram >= need * 0.6:
            fits, note = True, "не влезает в видеокарту целиком — часть на процессоре, медленнее"
        else:
            ram_ok = hw.ram_gb >= need * 1.5
            slow = name in ("qwen3:8b", "qwen3:14b", "qwen3:30b")
            fits = ram_ok and not (slow and hw.cores <= 8)
            note = "на процессоре — " + ("очень медленно" if slow else "терпимо" if name == "qwen3:4b" else "быстро")
            if not ram_ok:
                note += ", мало оперативной памяти"
        whisper = "small" if whisper_gpu or level != "fast" else "base"
        out.append(Preset(level, name, dl, whisper, on_gpu, fits, note))
    # рекомендуем самую умную из тех, что работают в видеокарте; без видеокарты — среднюю, если тянет
    gpu_ok = [p for p in out if p.on_gpu]
    pick = gpu_ok[-1] if gpu_ok else (out[1] if out[1].fits else out[0])
    pick.recommended = True
    return out


def ollama_options(p: Preset) -> dict:
    """num_gpu 99 = «всё в видеокарту» — только если модель туда правда влезает (иначе Ollama решит сама)."""
    return {"num_gpu": 99, "num_ctx": 4096} if p.on_gpu else {"num_ctx": 4096}
