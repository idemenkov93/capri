"""Мастер настройки, подбор модели под железо, скины, обновления."""
from pathlib import Path

import pytest
import yaml

from capri import hardware as H
from capri import installer, skins, updates
from capri.setup_config import REMOVE, apply
from capri.ui.sprites import classify

ROOT = Path(__file__).resolve().parent.parent
LAPTOP = H.Hardware("Intel(R) Core(TM) i5-10310U", 8, 32, [H.GPU("Intel(R) UHD Graphics", "intel", 0.1)])
PC_1070 = H.Hardware("i7-6700K", 8, 16, [H.GPU("NVIDIA GeForce GTX 1070", "nvidia", 8.0)])
RTX_4090 = H.Hardware("Ryzen 9", 32, 64, [H.GPU("NVIDIA GeForce RTX 4090", "nvidia", 24.0)])


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def pick(hw):
    return next(p for p in H.presets(hw) if p.recommended)


# ------------------------------------------------------------------ железо → модель
def test_laptop_without_gpu_gets_medium_cpu_model():
    ps = H.presets(LAPTOP)
    assert [p.model for p in ps] == ["qwen3:1.7b", H.Q4B, "qwen3:8b"]
    assert pick(LAPTOP).model == H.Q4B and not pick(LAPTOP).on_gpu
    assert not ps[2].fits                         # 8b на процессоре ноутбука — не советуем
    assert H.ollama_options(pick(LAPTOP)) == {"num_ctx": 4096}


def test_gtx_1070_gets_8b_on_gpu():
    p = pick(PC_1070)
    assert p.model == "qwen3:8b" and p.on_gpu and H.ollama_options(p)["num_gpu"] == 99


def test_big_gpu_gets_bigger_models():
    assert pick(RTX_4090).model == "qwen3:32b"


def test_vram_matters_not_series():
    rtx3050 = H.Hardware("x", 16, 32, [H.GPU("NVIDIA GeForce RTX 3050", "nvidia", 4.0)])
    assert pick(rtx3050).model == H.Q4B      # новее 1070, но памяти меньше — модель проще


def test_nvidia_smi_parse_and_integrated():
    assert H.parse_nvidia_smi("NVIDIA GeForce GTX 1070, 8192\n") == [H.GPU("NVIDIA GeForce GTX 1070", "nvidia", 8.0)]
    assert H.GPU("Intel(R) UHD Graphics", "intel").integrated
    assert not H.GPU("Intel(R) Arc(TM) A770", "intel", 16).integrated
    assert LAPTOP.best_gpu is None and "встроенная" in LAPTOP.summary()


# ------------------------------------------------------------------ config.yaml без потери комментариев
def test_config_edit_keeps_comments():
    text = (ROOT / "config.example.yaml").read_text(encoding="utf-8")
    out = apply(text, {("ollama", "model"): "qwen3:4b", ("ollama", "options", "num_gpu"): 99,
                       ("voice", "whisper_model"): "base", ("city",): "Berlin", ("avatar", "skin"): "x"})
    assert out.count("\n#") >= text.count("\n#") - 1     # комментарии на месте (num_gpu раскомментирован)
    assert "model: qwen3:4b          # RU/DE/EN/HE" in out
    d = yaml.safe_load(out)
    assert d["ollama"]["model"] == "qwen3:4b" and d["ollama"]["options"]["num_gpu"] == 99
    assert d["voice"]["whisper_model"] == "base" and d["city"] == "Berlin" and d["avatar"]["skin"] == "x"
    back = yaml.safe_load(apply(out, {("ollama", "options", "num_gpu"): REMOVE}))
    assert "num_gpu" not in back["ollama"]["options"]


def test_wizard_writes_config(tmp_path):
    from capri.ui.setup_wizard import config_values, save_config
    path = save_config(config_values(pick(LAPTOP), "Аня", "Berlin", claude=False), tmp_path / "config.yaml")
    d = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert d["user_name"] == "Аня" and d["ollama"]["model"] == H.Q4B and d["claude_code"]["enabled"] is False
    assert "num_gpu" not in (d["ollama"].get("options") or {})


def test_wizard_runs_through(tmp_path, monkeypatch, qapp):
    from capri.ui import setup_wizard as W
    monkeypatch.setattr(W, "config_path", lambda: tmp_path / "config.yaml")
    monkeypatch.setattr(W.hardware, "detect", lambda: LAPTOP)
    calls = []
    monkeypatch.setattr(installer, "ollama_exe", lambda: None)
    monkeypatch.setattr(installer, "claude_exe", lambda: None)
    monkeypatch.setattr(installer, "install_ollama", lambda url, pr: calls.append("ollama"))
    monkeypatch.setattr(installer, "pull_model", lambda url, m, pr: calls.append(m))
    monkeypatch.setattr(installer, "install_vosk", lambda d, pr: calls.append("vosk"))
    monkeypatch.setattr(installer, "install_whisper", lambda m, d, pr: (_ for _ in ()).throw(RuntimeError("нет сети")))
    monkeypatch.setattr(installer, "install_claude", lambda pr: calls.append("claude"))
    w = W.SetupWizard()
    w.detect.wait(5000)
    qapp.processEvents()
    w.name.setText("Тест")
    w._next()                      # → железо
    w._next()                      # → умный помощник (Claude не установлен — по умолчанию «только локально»)
    assert w._brain() == "ollama" and w.cb_vision.isChecked()
    w.brain_radio["claude"].setChecked(True)
    assert not w.cb_vision.isChecked() and w.gem_box.isHidden()
    w._next()                      # → что поставить
    w._next()                      # «Установить»
    w.worker.wait(5000)
    qapp.processEvents()
    assert calls == ["ollama", H.Q4B, "vosk", "claude"]
    assert w.worker.failed == ["Распознавание речи"] and not w.retry.isHidden()
    d = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert d["user_name"] == "Тест" and d["brain"]["provider"] == "claude" and d["claude_code"]["enabled"] is True
    w.close()


def test_pypi_wheel_pick(monkeypatch):
    class R:
        def json(self):
            win = [{"filename": "x-win_amd64.whl", "url": "U"}]
            return {"releases": {"9.1.0": win, "10.0.0": [{"filename": "y-win_amd64.whl", "url": "NEW"}],
                                 "9.0.0": [{"filename": "z-manylinux.whl", "url": "L"}]}}
    monkeypatch.setattr(installer.httpx, "get", lambda *a, **k: R())
    assert installer.wheel_url("nvidia-cudnn-cu12", "10") == "U"
    assert installer.wheel_url("nvidia-cudnn-cu12") == "NEW"


# ------------------------------------------------------------------ скины и танцы
def _gif(p: Path):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"GIF89a")


def test_skins(tmp_path, monkeypatch):
    monkeypatch.setattr(skins, "PROJECT_ROOT", tmp_path)
    _gif(tmp_path / "skins" / "neo" / "desktop" / "idle.gif")
    _gif(tmp_path / "skins" / "neo" / "hud" / "talk.gif")
    _gif(tmp_path / "skins" / "flat" / "dance1.gif")
    _gif(tmp_path / "skins" / "_old" / "idle.gif")
    (tmp_path / "skins" / "empty").mkdir()
    assert skins.list_skins() == ["flat", "neo"]
    assert skins.source("neo", "desktop").endswith("desktop") and skins.source("neo", "hud").endswith("hud")
    assert skins.source("flat", "hud").endswith("flat")
    assert skins.source(skins.STAR, "desktop") == ""
    assert skins.source("пропал", "desktop") is None and skins.source("", "desktop") is None


def test_app_prefers_chosen_skin(tmp_path, monkeypatch):
    from capri.app import CapriApp
    monkeypatch.setattr(skins, "PROJECT_ROOT", tmp_path)
    _gif(tmp_path / "skins" / "neo" / "idle.gif")
    assert CapriApp._avatar_source({}, "desktop", "neo") == str(tmp_path / "skins" / "neo")


@pytest.mark.parametrize("name,kind", [("dance1", "dance"), ("capri_dancing", "dance"), ("танец2", "dance"),
                                       ("idle", "idle"), ("walk_left", "walk")])
def test_dance_files(name, kind):
    assert classify(name) == kind


def test_versions():
    assert updates.newer("1.0.0", "0.9.0") and updates.newer("0.10.0", "0.9.9")
    assert not updates.newer("0.9.0", "0.9.0") and not updates.newer("", "0.9.0")


def test_avatar_plays_own_dance(tmp_path, qapp):
    from PIL import Image

    from capri.ui.avatar import Avatar
    for n in ("idle", "dance1", "dance2"):
        Image.new("RGBA", (20, 30), (255, 0, 0, 255)).save(tmp_path / f"{n}.png")
    a = Avatar(100, str(tmp_path), 12, wander=False)
    assert a.sprite_kind() == "idle"
    a.set_dance(0.8)
    assert a.own_dance() and a.sprite_kind() == "dance"
    a.grab()                                   # рисуется без ошибок
    for _ in range(40):
        a.set_dance(0.0)
    assert a.sprite_kind() == "idle"


def test_wizard_gemini_needs_working_key(tmp_path, monkeypatch, qapp):
    from capri.llm import gemini
    from capri.ui import setup_wizard as W
    monkeypatch.setattr(W, "config_path", lambda: tmp_path / "config.yaml")
    monkeypatch.setattr(W.hardware, "detect", lambda: LAPTOP)
    monkeypatch.setattr(installer, "claude_exe", lambda: None)
    monkeypatch.setattr(gemini, "get_key", lambda cfg=None: None)
    saved = []
    monkeypatch.setattr(gemini, "set_key", saved.append)
    w = W.SetupWizard()
    w.detect.wait(5000)
    qapp.processEvents()
    w._go(W.BRAIN)
    w.brain_radio["gemini_free"].setChecked(True)
    assert not w.gem_box.isHidden()
    w._next()
    assert w.pages.currentIndex() == W.BRAIN                 # без ключа не пускает
    monkeypatch.setattr(gemini, "check_key", lambda k, tier: {"free": "gemini-flash-lite-latest"}[tier])
    w.gem_key.setText("AIzaTEST")
    w._next()
    assert w.pages.currentIndex() == W.COMPONENTS and w.gem_model == "gemini-flash-lite-latest"
    assert w.cb_claude.isHidden()
    for name in ("install_ollama", "pull_model", "install_vosk", "install_whisper"):
        monkeypatch.setattr(installer, name, lambda *a: None)
    w._next()
    w.worker.wait(5000)
    qapp.processEvents()
    d = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert d["brain"]["provider"] == "gemini" and d["brain"]["gemini"]["model"] == "gemini-flash-lite-latest"
    assert saved == ["AIzaTEST"] and "AIzaTEST" not in (tmp_path / "config.yaml").read_text(encoding="utf-8")
    w.close()


def test_no_thinking_only_models_in_presets():
    """qwen3:4b и qwen3:30b в Ollama — «думающие» 2507: рассуждают вслух и не отключаются. Их не предлагаем."""
    for hw in (LAPTOP, PC_1070, RTX_4090):
        for p in H.presets(hw):
            assert p.model not in ("qwen3:4b", "qwen3:30b"), p.model
