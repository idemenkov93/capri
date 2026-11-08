"""Бесплатные облачные «мозги»: Groq, OpenRouter (DeepSeek и др., только :free), Mistral."""
import copy
from pathlib import Path

import httpx
import pytest

from capri.agent import Agent
from capri.applist import AppIndex
from capri.config import DEFAULTS
from capri.llm import claude_code, cloud, gemini


class Sys:
    def run(self, cmd):
        return {}


def agent(claude=None, gem=None, cloud_ask=None, keys=(), monkeypatch=None):
    if monkeypatch is not None:
        monkeypatch.setattr(cloud, "get_key", lambda code, cfg=None: "k" if code in keys else None)
        monkeypatch.setattr(gemini, "get_key", lambda cfg=None: None)
    a = Agent(copy.deepcopy(DEFAULTS), llm=object(), claude=claude or (lambda t, c: "C"), system=Sys(),
              apps=AppIndex(Path("/x")), gemini_ask=gem, cloud_ask=cloud_ask)
    a.game = None
    return a


# ------------------------------------------------------------------ ключи
def test_parse_key_by_prefix_and_by_word():
    assert cloud.parse_key("gsk_" + "a" * 40)[0].code == "groq"
    assert cloud.parse_key("ключ openrouter sk-or-v1-" + "b" * 40)[0].code == "openrouter"
    assert cloud.parse_key("ключ для дипсика sk-or-v1-" + "b" * 40)[0].code == "openrouter"
    p, key = cloud.parse_key("ключ mistral " + "Z9" * 16)
    assert p.code == "mistral" and key == "Z9" * 16
    assert cloud.parse_key("Z9" * 16) is None                     # ключ без примет — только со словом
    assert cloud.parse_key("ключ groq " + "Z9" * 16) is None      # у Groq ключ всегда gsk_
    assert cloud.parse_key("открой ютуб") is None


def test_mask_hides_key():
    m = cloud.mask("gsk_abcdefghijklmnopqrstuvwxyz1234")
    assert m.startswith("gsk_ab") and m.endswith("1234") and "mnop" not in m


def test_provider_words():
    assert cloud.provider_for("дипсик").code == "openrouter"
    assert cloud.provider_for("deepseek").code == "openrouter"
    assert cloud.provider_for("грок").code == "groq"
    assert cloud.provider_for("мистраль").code == "mistral"
    assert cloud.provider_for("gemini") is None


# ------------------------------------------------------------------ только бесплатное
def test_openrouter_picks_only_free_models():
    models = [{"id": "deepseek/deepseek-v4-pro", "pricing": {"prompt": "0.000001", "completion": "0.000002"}},
              {"id": "deepseek/deepseek-v4-flash:free", "pricing": {"prompt": "0", "completion": "0"}},
              {"id": "meta-llama/llama-3.3-70b-instruct:free"}]
    assert cloud.pick_model("openrouter", models) == "deepseek/deepseek-v4-flash:free"
    paid_only = [{"id": "deepseek/deepseek-v4-pro", "pricing": {"prompt": "0.1", "completion": "0.1"}}]
    assert cloud.pick_model("openrouter", paid_only) == "openrouter/free"


def test_openrouter_refuses_paid_model_from_config(monkeypatch):
    monkeypatch.setattr(cloud, "get_key", lambda code, cfg=None: "sk-or-x")
    called = []
    monkeypatch.setattr(httpx, "post", lambda *a, **k: called.append(1))
    with pytest.raises(cloud.CloudError, match="платная"):
        cloud.ask("openrouter", "привет", {"model": "deepseek/deepseek-v4-pro"})
    assert not called


def test_groq_prefers_big_model():
    models = [{"id": "llama-3.1-8b-instant"}, {"id": "llama-3.3-70b-versatile"}, {"id": "whisper-large-v3"}]
    assert cloud.pick_model("groq", models) == "llama-3.3-70b-versatile"


def test_ask_sends_openai_format_and_strips_thinking(monkeypatch):
    monkeypatch.setattr(cloud, "get_key", lambda code, cfg=None: "gsk_x")
    seen = {}

    def post(url, json, headers, timeout):
        seen.update(url=url, body=json, auth=headers["Authorization"])
        return httpx.Response(200, json={"choices": [{"message": {"content": "<think>хм</think>Привет!"}}]})
    monkeypatch.setattr(httpx, "post", post)
    assert cloud.ask("groq", "привет", {"model": "llama-3.3-70b-versatile"}, system="будь краткой") == "Привет!"
    assert seen["url"].endswith("/chat/completions") and seen["auth"] == "Bearer gsk_x"
    assert seen["body"]["messages"][0] == {"role": "system", "content": "будь краткой"}


@pytest.mark.parametrize("status,words", [(429, "лимит"), (402, "платить не будем"), (401, "не принял ключ")])
def test_ask_errors_are_human(monkeypatch, status, words):
    monkeypatch.setattr(cloud, "get_key", lambda code, cfg=None: "gsk_x")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(status, json={"error": {"message": "x"}}))
    with pytest.raises(cloud.CloudError, match=words):
        cloud.ask("groq", "привет", {"model": "m"})


# ------------------------------------------------------------------ в агенте
def test_order_adds_connected_free_brains_after_claude_and_gemini(monkeypatch):
    a = agent(keys=("groq", "mistral"), monkeypatch=monkeypatch,
              gem=lambda *a, **k: "G")
    assert a._order() == ["claude", "gemini", "groq", "mistral", "ollama"]


def test_claude_limit_falls_back_to_groq_when_no_gemini(monkeypatch):
    def claude(t, c):
        raise claude_code.ClaudeLimit("лимит")
    a = agent(claude=claude, cloud_ask=lambda code, t, cfg, images=None, system="": f"ответ {code}",
              keys=("groq",), monkeypatch=monkeypatch)
    monkeypatch.setattr(a, "gemini_ready", lambda: False)
    r = a.handle("объясни подробно, что такое DNS")
    assert r.route == "groq" and r.text.startswith("Лимит Claude кончился — ответил Groq.")


def test_chat_brain_forces_cloud(monkeypatch):
    asked = []
    a = agent(cloud_ask=lambda code, t, cfg, images=None, system="": asked.append(code) or "ответ",
              keys=("openrouter",), monkeypatch=monkeypatch)
    a.chat_brain = "openrouter"
    r = a.handle("что такое VLAN?", typed=True)
    assert r.route == "openrouter" and asked == ["openrouter"]


def test_chat_brain_without_key_says_how(monkeypatch):
    a = agent(cloud_ask=None, keys=(), monkeypatch=monkeypatch)
    a.chat_brain = "mistral"
    r = a.handle("что такое VLAN?", typed=True)
    assert r.route == "error" and "как подключить mistral" in r.text


def test_how_to_connect_deepseek_and_grok(monkeypatch):
    a = agent(keys=(), monkeypatch=monkeypatch)
    r = a.handle("как подключить дипсик")
    assert "openrouter.ai/keys" in r.text and "платный" in r.text and r.actions[0].params["url"].startswith("https://openrouter")
    r = a.handle("как подключить грок")
    assert "console.groq.com" in r.text
    r = a.handle("как подключить grok")
    assert "платный" in r.text and "Groq" in r.text


def test_key_from_chat_is_checked_saved_and_masked(monkeypatch):
    saved = {}
    monkeypatch.setattr(cloud, "check_key", lambda code, key: "llama-3.3-70b-versatile")
    monkeypatch.setattr(cloud, "set_key", lambda code, key: saved.update({code: key}))
    a = agent(keys=(), monkeypatch=monkeypatch)
    key = "gsk_" + "Q" * 40
    r = a.handle(f"ключ groq {key}")
    assert saved == {"groq": key} and "Groq подключён" in r.text and key not in r.text


def test_games_use_http_brains_first(monkeypatch):
    class Game:
        active, strict = True, True

        def busy_round(self):
            return False

        def context(self):
            return ""
    a = agent(keys=("groq",), monkeypatch=monkeypatch)
    a.game = Game()
    assert a._order() == ["groq"]                      # CS2: только HTTP, без Claude и локальной


# ------------------------------------------------------------------ чат: «Ещё ▾» и вид
def test_chat_more_menu_and_look(qapp=None):
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from capri.ui.chat import ChatWindow
    w = ChatWindow()
    got, quick, looks = [], [], []
    w.brain_changed.connect(got.append)
    w.quick.connect(quick.append)
    w.look_changed.connect(looks.append)
    w.set_brain_available({"groq": (True, "ok"), "openrouter": (False, "нет ключа"), "mistral": (False, "")})
    w.more_actions["groq"].trigger()
    assert got == ["groq"] and w.more_btn.isChecked() and w.more_btn.text() == "Groq ▾"
    w.more_actions["openrouter"].trigger()            # не подключён → инструкция, выбор не меняется
    assert quick == ["как подключить openrouter"] and got == ["groq"]
    assert "подключить" in w.more_actions["openrouter"].text()
    w.brain_buttons["auto"].click()
    assert w.more_btn.text() == "Ещё ▾" and got[-1] == "auto"
    w.set_look("capri")
    assert w.look_buttons["capri"].isChecked() and not w.look_buttons["glow"].isChecked()
    w.look_buttons["glow"].click()
    assert looks == ["glow"]
    app.processEvents()
