"""Письменные вопросы: ответ текстом, кнопки «копировать», выбор «мозга», модель для текста."""
import copy
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication

from capri.agent import Agent
from capri.applist import AppIndex
from capri.config import DEFAULTS
from capri.llm.ollama_client import OllamaClient

APP = QApplication.instance() or QApplication([])
KEEP = []


class Sys:
    def run(self, cmd):
        return {}


class Local:
    def __init__(self, name="local"):
        self.name, self.calls = name, []

    def chat(self, messages, tools=None, on_token=None, should_stop=None, **k):
        self.calls.append(messages)
        return {"content": self.name, "tool_calls": []}


def agent(**kw):
    return Agent(copy.deepcopy(DEFAULTS), llm=kw.pop("llm", Local()), system=Sys(), apps=AppIndex(Path("/x")), **kw)


def test_chat_brain_routes_typed_questions():
    seen = []
    a = agent(claude=lambda t, c: seen.append("claude") or "C", gemini_ask=lambda *x, **k: seen.append("gemini") or "G")
    a.chat_brain = "gemini"
    assert a.handle("объясни, что такое VLAN", typed=True).text == "G"
    a.chat_brain = "claude"
    assert a.handle("объясни, что такое VLAN", typed=True).text == "C"
    a.chat_brain = "local"
    assert a.handle("объясни, что такое VLAN", typed=True).text == "local"
    assert a.handle("громкость 20", typed=True).route == "rules"          # команды — по-прежнему команды
    assert seen == ["gemini", "claude"]


def test_forced_brain_without_key_says_how():
    import capri.llm.gemini as G
    a = Agent(copy.deepcopy(DEFAULTS), llm=Local(), claude=lambda t, c: "C", system=Sys(), apps=AppIndex(Path("/x")))
    a.chat_brain = "gemini"
    G_get = G.get_key
    G.get_key = lambda cfg=None: None
    try:
        r = a.handle("объясни, что такое VLAN", typed=True)
    finally:
        G.get_key = G_get
    assert r.route == "error" and "как подключить gemini" in r.text


def test_text_model_for_typed_questions(monkeypatch):
    a = Agent(copy.deepcopy(DEFAULTS), llm=OllamaClient("http://x", "qwen3:8b"), claude=lambda t, c: "C", system=Sys(),
              apps=AppIndex(Path("/x")))
    assert a._text_llm() is a.llm                                        # не выбрана — та же
    a.cfg["ollama"]["text_model"] = "qwen3:14b"
    t = a._text_llm()
    assert t.model == "qwen3:14b" and t.timeout >= 300 and a._text_llm() is t


def test_copy_links_and_clipboard():
    from capri.ui.chat import ChatWindow
    w = ChatWindow()
    KEEP.append(w)
    html = w.copy_links("Вот команда:\n```powershell\nGet-ADUser -Filter *\n```\nи ещё\n```\nipconfig /all\n```")
    assert "копировать ответ" in html and "код 1" in html and "код 2" in html
    w._link(QUrl("copy:1"))
    assert APP.clipboard().text() == "Get-ADUser -Filter *"
    w.add_capri("**Привет**", "local")
    w.set_brain("claude")
    assert w.brain_buttons["claude"].isChecked()


def test_hud_has_brain_switch_and_copy():
    from capri.ui.hud import HudWindow
    h = HudWindow()
    KEEP.append(h)
    got = []
    h.brain_changed.connect(got.append)
    h.brain_buttons["gemini"].click()
    assert got == ["gemini"]
    h.add_capri("ответ", "local")
    h.btn_copy.click()
    assert APP.clipboard().text() == "ответ"


def test_typed_replies_are_not_spoken_by_default(tmp_path):
    from capri.state import State
    import json
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"speak_typed": True}), encoding="utf-8")
    assert State(p).get("speak_typed") is False


def test_quick_buttons_completer_and_availability():
    from capri.ui.hud import HudWindow
    h = HudWindow()
    KEEP.append(h)
    got = []
    h.quick.connect(got.append)
    h.quick_buttons["📋 Буфер"].click()
    acts = h.quick_buttons["🔕"].menu().actions()
    acts[1].trigger()
    assert got == ["что в буфере", "не беспокой меня час"]
    assert h.input.completer() is not None and "переведи буфер" in h.input.completer().model().stringList()
    h.set_brain_available({"gemini": (False, "нет ключа"), "claude": (True, "ok")})
    assert not h.brain_buttons["gemini"].isEnabled() and h.brain_buttons["gemini"].toolTip() == "нет ключа"
    h.set_dnd_label("до 18:00")
    assert "18:00" in h.status.text()


def test_app_quick_action_runs_command(monkeypatch):
    from tests.test_eco import quiet_app
    app = quiet_app(monkeypatch)
    app.warmup.wait(10_000)
    seen = []
    monkeypatch.setattr(app, "on_message", seen.append)
    app.chat.quick.emit("что в буфере")
    assert seen == ["что в буфере"] and app.last_input_was_voice is False
    app.refresh_brain_buttons()
