"""1.9.2: по итогам проверки на ноутбуке без видеокарты."""
import copy
from pathlib import Path

import pytest

from capri import mentor
from capri.agent import Agent
from capri.applist import AppIndex
from capri.config import DEFAULTS
from capri.live import commands, glossary as G


class Sys:
    def run(self, cmd):
        return {}


class Local:
    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools=None, on_token=None, should_stop=None, **k):
        self.calls += 1
        return {"content": "локально", "tool_calls": []}


def agent(local):
    cfg = copy.deepcopy(DEFAULTS)
    cfg["brain"]["provider"] = "gemini"           # как у Игоря: Gemini выбран, но ключ не сохранился
    a = Agent(cfg, llm=local, claude=lambda t, c: "C", system=Sys(), apps=AppIndex(Path("/x")),
              gemini_ask=lambda *x, **k: "G")
    a.ready = lambda p: p in ("groq", "ollama")
    a.cloud_ask = lambda p, prompt, cfg, images=None, system="": "быстро из Groq"
    a._ccfg = lambda p: {}
    return a


def test_laptop_simple_question_goes_to_fast_cloud(monkeypatch):
    monkeypatch.setitem(mentor._HW, "gpu", False)
    local = Local()
    r = agent(local).handle("объясни разницу между NTFS и Freigabe-правами")
    assert "быстро из Groq" in r.text and r.route == "groq" and local.calls == 0


def test_pc_with_gpu_keeps_local(monkeypatch):
    local = Local()
    r = agent(local).handle("привет, как дела")
    assert r.text == "локально" and local.calls == 1


def test_only_local_choice_is_respected(monkeypatch):
    monkeypatch.setitem(mentor._HW, "gpu", False)
    local = Local()
    a = agent(local)
    a.cfg["brain"]["provider"] = "ollama"
    assert a.handle("привет, как дела").text == "локально"


def test_ollama_commands():
    a = agent(Local())
    assert a._text_cmd("установи ollama").actions[0].kind == "ollama"
    assert a._text_cmd("запусти олламу").actions[0].kind == "ollama"


def test_not_running_text(monkeypatch):
    from capri import installer
    from capri.llm import ollama_client
    monkeypatch.setattr(installer, "ollama_exe", lambda: None)
    assert "установи ollama" in ollama_client.not_running_text()
    monkeypatch.setattr(installer, "ollama_exe", lambda: "C:/ollama.exe")
    assert "запусти ollama" in ollama_client.not_running_text()


@pytest.mark.parametrize("err,saved", [("Gemini не отвечает: The read operation timed out", True),
                                       ("Gemini: ошибка 503: UNAVAILABLE high demand", True),
                                       ("Gemini не принял ключ (400)", False)])
def test_gemini_key_saved_when_google_is_busy(monkeypatch, err, saved):
    from capri.llm import gemini

    def boom(*a, **k):
        raise gemini.GeminiError(err)
    monkeypatch.setattr(gemini, "list_models", boom)
    monkeypatch.setattr(gemini, "ask", boom)
    if saved:
        assert gemini.check_key("AIzaX", "free")
    else:
        with pytest.raises(gemini.GeminiError):
            gemini.check_key("AIzaX", "free")


# ------------------------------------------------------------------ бестиарий «положил рядом»
@pytest.fixture
def dirs(tmp_path, monkeypatch):
    import capri.config as C
    root = tmp_path / "Docs" / "Capri" / "Уроки"
    root.mkdir(parents=True)
    proj = tmp_path / "CapriApp"
    (proj / "data").mkdir(parents=True)
    monkeypatch.setattr(C, "PROJECT_ROOT", proj)
    monkeypatch.setattr(G, "csv_dir", lambda: proj / "data" / "glossary")
    monkeypatch.setattr(G.N, "lessons_dir", lambda: root)
    G._cache["key"] = None
    return root, proj


def test_bestiary_next_to_capri_is_found_but_random_csv_is_not(dirs):
    root, proj = dirs
    (proj / "Bestiarium_IT-Admin.csv").write_text("Deutsch;Русский;Заметка\nDHCP;DHCP;выдаёт IP\n", encoding="utf-8")
    (proj / "data" / "usage.csv").write_text("2026-11-08 17:33,874,1.8,3175\n", encoding="utf-8")
    (root.parent / "Бестиарий 2026-11-08.csv").write_text("Deutsch;Русский\nAlt;старое\n", encoding="utf-8")
    terms = G.load(root)
    assert terms == {"DHCP": "DHCP"}


def test_find_in_bestiary_not_found_says_so(dirs):
    from capri.study import route

    class Ag:
        prefs = None
    r = route(Ag(), "найди в бестиарии NTFS")
    assert r is not None and "нет" in r.text and "импортируй бестиарий" in r.text
    assert route(Ag(), "что такое NTFS") is None          # «что такое» без словаря — мозгу


@pytest.mark.parametrize("phrase,params", [
    (r"импортируй бестиарий E:\Project-Capri\Bestiarium_IT-Admin.csv",
     {"do": "import_glossary", "path": r"E:\Project-Capri\Bestiarium_IT-Admin.csv"}),
    ("импортируй бестиарий", {"do": "import_glossary"}),
    ("субтитры быстрее", {"do": "asr_model", "value": "base"}),
    ("распознавание точнее", {"do": "asr_model", "value": "small"}),
    ("субтитры авто", {"do": "asr_model", "value": "auto"}),
])
def test_commands(phrase, params):
    assert commands.route(phrase).actions[0].params == params
