"""Сколько Капри ест: раз в минуту пишем в data/usage.csv память и процессор Капри и Ollama, видеопамять.

Видеопамять: NVIDIA — через nvml.dll из драйвера (занято/всего, загрузка); AMD и Intel — через счётчики Windows
(«GPU Adapter Memory → Dedicated Usage», только «занято»). Сколько видеопамяти держит модель — спрашиваем саму Ollama.
«сколько ты ешь ресурсов» — сводка; меню → «📦 Отчёт для разработчика» — zip с журналом и этими цифрами (для тестов у друзей).
"""
from __future__ import annotations

import csv
import ctypes
import datetime as dt
import sys
import threading
import time
from pathlib import Path

FIELDS = ["time", "capri_ram_mb", "capri_cpu", "ollama_ram_mb", "model_vram_mb", "model",
          "gpu_used_mb", "gpu_total_mb", "gpu_load", "sys_ram_pct", "cpu_total", "game"]
MAX_BYTES = 5_000_000
OLLAMA_NAMES = ("ollama", "llama-server", "ollama_llama_server")


# ------------------------------------------------------------------ видеопамять
class _Pdh:
    """Счётчики Windows: сколько выделенной видеопамяти занято (любая видеокарта)."""

    def __init__(self):
        from ctypes import wintypes
        self.pdh = ctypes.windll.pdh
        self.query = ctypes.c_void_p()
        self.counter = ctypes.c_void_p()
        if self.pdh.PdhOpenQueryW(None, None, ctypes.byref(self.query)) != 0:
            raise OSError("pdh query")
        if self.pdh.PdhAddEnglishCounterW(self.query, r"\GPU Adapter Memory(*)\Dedicated Usage", None,
                                          ctypes.byref(self.counter)) != 0:
            raise OSError("pdh counter")
        self.pdh.PdhCollectQueryData(self.query)

        class Value(ctypes.Structure):
            _fields_ = [("CStatus", wintypes.DWORD), ("largeValue", ctypes.c_longlong)]

        class Item(ctypes.Structure):
            _fields_ = [("szName", wintypes.LPWSTR), ("FmtValue", Value)]
        self.Item = Item

    def used_mb(self) -> int | None:
        from ctypes import wintypes
        if self.pdh.PdhCollectQueryData(self.query) != 0:
            return None
        size, count = wintypes.DWORD(0), wintypes.DWORD(0)
        self.pdh.PdhGetFormattedCounterArrayW(self.counter, 0x400, ctypes.byref(size), ctypes.byref(count), None)
        if not size.value:
            return None
        buf = (ctypes.c_byte * size.value)()
        if self.pdh.PdhGetFormattedCounterArrayW(self.counter, 0x400, ctypes.byref(size), ctypes.byref(count),
                                                 buf) != 0:
            return None
        items = ctypes.cast(buf, ctypes.POINTER(self.Item))
        total = sum(items[i].FmtValue.largeValue for i in range(count.value) if items[i].FmtValue.CStatus in (0, 1))
        return int(total / 2**20)


_pdh: _Pdh | None = None
_pdh_failed = False


def gpu_memory() -> dict:
    """{'used': МБ, 'total': МБ или None, 'load': % или None}; пусто — не узнать."""
    global _pdh, _pdh_failed
    from . import gpu
    try:
        m = gpu.memory()
        if m:
            return m
    except Exception:  # noqa: BLE001
        pass
    if sys.platform != "win32" or _pdh_failed:
        return {}
    try:
        if _pdh is None:
            _pdh = _Pdh()
        used = _pdh.used_mb()
        return {"used": used, "total": None, "load": None} if used is not None else {}
    except Exception:  # noqa: BLE001 — нет счётчиков (удалённый рабочий стол, старая Windows)
        _pdh_failed = True
        return {}


def ollama_models(url: str = "http://127.0.0.1:11434") -> tuple[int, str]:
    """(видеопамять под модели, МБ; имена загруженных моделей)."""
    try:
        import httpx
        r = httpx.get(f"{url.rstrip('/')}/api/ps", timeout=2)
        ms = r.json().get("models") or []
        return int(sum(m.get("size_vram", 0) for m in ms) / 2**20), "+".join(m.get("name", "") for m in ms)
    except Exception:  # noqa: BLE001 — Ollama не запущена
        return 0, ""


# ------------------------------------------------------------------ запись
def sample(game: str = "", ollama_url: str = "http://127.0.0.1:11434") -> dict:
    import psutil
    me = psutil.Process()
    ram = me.memory_info().rss
    cpu = me.cpu_percent(None)
    for ch in me.children(recursive=True):          # дочерние процессы Капри (снимок игры, Claude)
        try:
            ram += ch.memory_info().rss
        except psutil.Error:
            pass
    oll = 0
    for p in psutil.process_iter(["name", "memory_info"]):
        name = (p.info.get("name") or "").lower()
        if name.startswith(OLLAMA_NAMES) and p.info.get("memory_info"):
            oll += p.info["memory_info"].rss
    vram, model = ollama_models(ollama_url)
    g = gpu_memory()
    return {"time": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "capri_ram_mb": int(ram / 2**20),
            "capri_cpu": round(cpu / (psutil.cpu_count() or 1), 1), "ollama_ram_mb": int(oll / 2**20),
            "model_vram_mb": vram, "model": model, "gpu_used_mb": g.get("used") or "",
            "gpu_total_mb": g.get("total") or "", "gpu_load": "" if g.get("load") is None else g.get("load"),
            "sys_ram_pct": psutil.virtual_memory().percent, "cpu_total": psutil.cpu_percent(None), "game": game}


def append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > MAX_BYTES:
        path.replace(path.with_suffix(".old.csv"))
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in FIELDS})


class Recorder(threading.Thread):
    """Фоновая запись раз в минуту. game() — имя текущей игры или ''."""

    def __init__(self, path: Path, game=lambda: "", ollama_url: str = "http://127.0.0.1:11434", every: float = 60):
        super().__init__(name="usage", daemon=True)
        self.path, self.game, self.url, self.every = Path(path), game, ollama_url, every
        self.stop = threading.Event()

    def run(self):
        try:
            import psutil
            psutil.Process().cpu_percent(None)          # первый замер процессора всегда 0 — прогреваем
            psutil.cpu_percent(None)
        except Exception:  # noqa: BLE001
            return
        while not self.stop.wait(self.every):
            try:
                append(self.path, sample(self.game() or "", self.url))
            except Exception:  # noqa: BLE001 — статистика не должна ронять Капри
                from .log import log
                log.exception("usage: не записалось")


# ------------------------------------------------------------------ сводка
def _rows(path: Path) -> list[dict]:
    rows = []
    for p in (path.with_suffix(".old.csv"), path):
        if p.exists():
            with p.open(encoding="utf-8") as f:
                rows += list(csv.DictReader(f))
    return rows


def _num(rows, key) -> list[float]:
    out = []
    for r in rows:
        try:
            out.append(float(r.get(key) or ""))
        except ValueError:
            pass
    return out


def _gb(mb: float) -> str:
    return f"{mb / 1024:.1f}".replace(".", ",") + " ГБ"


def summary(path: Path) -> str:
    rows = _rows(Path(path))
    if not rows:
        return "Пока нечего показать: расход ресурсов записываю раз в минуту — загляни через пару минут."
    hours = len(rows) / 60
    first = rows[0]["time"][:10]
    parts = [f"Записи с {first}, всего около {hours:.0f} ч работы." if hours >= 1
             else f"Записей пока мало: {len(rows)} мин."]
    ram, cpu = _num(rows, "capri_ram_mb"), _num(rows, "capri_cpu")
    if ram:
        parts.append(f"Капри: память в среднем {_gb(sum(ram) / len(ram))}, максимум {_gb(max(ram))}; "
                     f"процессор в среднем {sum(cpu) / len(cpu):.0f}%, максимум {max(cpu):.0f}%." if cpu else "")
    oram, vram = _num(rows, "ollama_ram_mb"), [v for v in _num(rows, "model_vram_mb") if v > 0]
    if vram or any(oram):
        models = sorted({r["model"] for r in rows if r.get("model")})
        parts.append(f"Локальная модель{' (' + ', '.join(models) + ')' if models else ''}: "
                     f"видеопамять до {_gb(max(vram) if vram else 0)}, обычная память до {_gb(max(oram or [0]))}.")
    used, total = _num(rows, "gpu_used_mb"), _num(rows, "gpu_total_mb")
    if used:
        parts.append(f"Видеокарта всего: занято до {_gb(max(used))}" + (f" из {_gb(max(total))}." if total else "."))
    games = [r for r in rows if r.get("game")]
    if games:
        g_ram = _num(games, "capri_ram_mb")
        parts.append(f"В играх ({len(games) / 60:.0f} ч): Капри в среднем {_gb(sum(g_ram) / len(g_ram))}, "
                     f"процессор {sum(_num(games, 'capri_cpu')) / len(games):.1f}%.")
    return " ".join(p for p in parts if p)


def hardware() -> str:
    """Короткое описание железа — для отчёта."""
    lines = []
    try:
        import platform

        import psutil
        lines.append(f"Windows: {platform.platform()}")
        lines.append(f"Процессор: {platform.processor()} ({psutil.cpu_count()} потоков)")
        lines.append(f"Память: {_gb(psutil.virtual_memory().total / 2**20)}")
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import hardware as hw
        for g in hw.detect().gpus:
            lines.append(f"Видеокарта: {g.name} ({g.vendor}), видеопамять {g.vram_gb or '?'} ГБ")
    except Exception:  # noqa: BLE001
        pass
    return "\n".join(lines)


def make_report(data_dir: Path, dest: Path, extra: str = "") -> Path:
    """zip: журнал, расход ресурсов, железо. Ключи и пароли в журнал не пишутся."""
    import zipfile
    data_dir = Path(data_dir)
    out = Path(dest) / f"Capri-отчёт-{dt.datetime.now():%Y%m%d-%H%M}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name in ("capri.log", "capri.log.1", "usage.csv", "usage.old.csv", "selftest.txt", "pc_report.txt",
                     "cs2_matches.jsonl"):
            p = data_dir / name
            if p.exists():
                z.write(p, name)
        z.writestr("железо.txt", hardware() + "\n\n" + summary(data_dir / "usage.csv") + ("\n\n" + extra if extra else ""))
    return out
