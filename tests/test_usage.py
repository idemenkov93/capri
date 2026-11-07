"""Расход ресурсов: запись в CSV, сводка, отчёт, команды."""
import copy
import zipfile
from pathlib import Path

from capri import usage
from capri.agent import Agent
from capri.applist import AppIndex
from capri.config import DEFAULTS


def row(t, ram, vram=0, model="", game="", used="", total=""):
    return {"time": t, "capri_ram_mb": ram, "capri_cpu": 2.0, "ollama_ram_mb": 700, "model_vram_mb": vram,
            "model": model, "gpu_used_mb": used, "gpu_total_mb": total, "gpu_load": "", "sys_ram_pct": 60,
            "cpu_total": 10, "game": game}


def test_summary_over_all_time(tmp_path):
    p = tmp_path / "usage.csv"
    assert "Пока нечего" in usage.summary(p)
    for i in range(120):
        usage.append(p, row(f"2026-11-01 10:{i % 60:02d}", 1000 + i, 5200 if i < 60 else 0, "qwen3:8b" if i < 60 else "",
                            "Counter-Strike 2" if i >= 60 else "", 6000, 8192))
    s = usage.summary(p)
    assert "около 2 ч" in s and "максимум 1,1 ГБ" in s and "qwen3:8b" in s and "5,1 ГБ" in s
    assert "из 8,0 ГБ" in s and "В играх (1 ч)" in s


def test_rotation(tmp_path, monkeypatch):
    monkeypatch.setattr(usage, "MAX_BYTES", 300)
    p = tmp_path / "usage.csv"
    for i in range(10):
        usage.append(p, row("2026-11-01 10:00", 900))
    assert p.with_suffix(".old.csv").exists() and len(usage._rows(p)) == 10


def test_sample_runs_without_ollama_and_gpu():
    r = usage.sample("", "http://127.0.0.1:9")
    assert r["capri_ram_mb"] > 0 and set(usage.FIELDS) == set(r)


def test_report_zip(tmp_path):
    (tmp_path / "capri.log").write_text("лог", encoding="utf-8")
    usage.append(tmp_path / "usage.csv", row("2026-11-01 10:00", 900))
    out = usage.make_report(tmp_path, tmp_path, extra="Капри 1.0")
    names = zipfile.ZipFile(out).namelist()
    assert {"capri.log", "usage.csv", "железо.txt"} <= set(names)


class Sys:
    def run(self, cmd):
        return {}


def test_voice_commands(monkeypatch, tmp_path):
    import capri.config as cfgmod
    monkeypatch.setattr(cfgmod, "expand", lambda p: tmp_path / Path(p).name)
    a = Agent(copy.deepcopy(DEFAULTS), llm=object(), claude=lambda t, c: "", system=Sys(), apps=AppIndex(Path("/x")))
    assert "Пока нечего" in a.handle("Капри, сколько ты ешь ресурсов?").text
    assert "Пока нечего" in a.handle("статистика ресурсов").text
    r = a.handle("собери отчёт")
    assert r.actions[0].kind == "make_report"


def test_update_voice_command():
    a = Agent(copy.deepcopy(DEFAULTS), llm=object(), claude=lambda t, c: "", system=Sys(), apps=AppIndex(Path("/x")))
    for phrase in ("обнови капри", "обновись", "проверь обновления"):
        assert a.handle(phrase).actions[0].kind == "check_updates", phrase


def test_cpu_is_measured_between_samples():
    usage.sample("", "http://127.0.0.1:9")
    x = 0
    for i in range(3_000_000):
        x += i * i
    assert usage.sample("", "http://127.0.0.1:9")["capri_cpu"] > 0
