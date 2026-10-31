"""Умный помощник: Claude, Gemini (бесплатный/платный) или только локально — и что, если выбранного нет."""
import copy
from pathlib import Path

import pytest

from capri import screen
from capri.agent import Agent
from capri.applist import AppIndex
from capri.config import DEFAULTS
from capri.llm import claude_code, gemini


class FakeLLM:
    def __init__(self, message=None):
        self.message = message or {"content": "локальный ответ"}
        self.images = []

    def chat(self, messages, tools=None, on_token=None, should_stop=None):
        return self.message

    def ask_images(self, prompt, images, model):
        self.images.append((model, [Path(i).name for i in images]))
        return '{"x": 10, "y": 20}' if "JSON" in prompt else "на экране ошибка DNS"


class NoSystem:
    def run(self, cmd):
        return {"error": f"не нашла в окне кнопку «{cmd.text}»"}


@pytest.fixture
def no_claude(monkeypatch):
    monkeypatch.setattr(claude_code, "find_claude", lambda c: None)
    monkeypatch.setattr(gemini, "get_key", lambda cfg=None: None)


def agent(provider="claude", llm=None, gem=None, vision="", tmp=None):
    cfg = copy.deepcopy(DEFAULTS)
    cfg["brain"]["provider"] = provider
    cfg["brain"]["vision_model"] = vision
    if tmp:
        cfg["claude_code"]["workdir"] = str(tmp)
    return Agent(cfg, llm=llm or FakeLLM(), system=NoSystem(), apps=AppIndex(Path("/x")), gemini=gem)


def test_without_claude_or_gemini_complex_task_is_answered_locally(no_claude):
    a = agent("claude")
    assert a.provider() == "ollama"
    r = a.handle("напиши скрипт на PowerShell для бэкапа папки")
    assert r.route == "local" and "локальный ответ" in r.text and "Claude/Gemini не подключены" in r.text


def test_local_only_by_choice_has_no_excuse_note(no_claude):
    r = agent("ollama").handle("напиши скрипт на PowerShell для бэкапа папки")
    assert r.route == "local" and "не подключены" not in r.text


def test_falls_back_to_gemini_when_claude_missing(no_claude, monkeypatch):
    monkeypatch.setattr(gemini, "get_key", lambda cfg=None: "AIzaX")
    assert agent("claude").provider() == "gemini"


def test_gemini_answers_complex_task(no_claude):
    seen = []
    a = agent("gemini", gem=lambda task, cfg, images=None, system="": seen.append((task, images, system)) or "ответ Gemini")
    r = a.handle("напиши скрипт на PowerShell для бэкапа папки")
    assert r.route == "gemini" and r.text == "ответ Gemini"
    assert "Капри" in seen[0][2] and not seen[0][1]          # характер Капри — системной инструкцией


def test_local_model_cannot_loop_into_missing_helper(no_claude):
    llm = FakeLLM({"content": "сама справлюсь", "tool_calls": [{"function": {"name": "ask_claude", "arguments": {"task": "x"}}}]})
    r = agent("ollama", llm=llm).handle("расскажи про DNS")
    assert r.route == "local" and r.text == "сама справлюсь"


def test_screen_question_goes_to_gemini_with_images(no_claude, monkeypatch, tmp_path):
    monkeypatch.setattr(screen, "capture", lambda wd: (["screens/window.png", "screens/screen.png"], "PowerShell"))
    seen = []
    a = agent("gemini", gem=lambda task, cfg, images=None, system="": seen.append(images) or "Это ошибка DNS", tmp=tmp_path)
    r = a.handle("посмотри что за ошибка на экране")
    assert r.route == "gemini" and [p.name for p in seen[0]] == ["window.png", "screen.png"]


def test_screen_question_with_claude_mentions_files(monkeypatch, tmp_path):
    monkeypatch.setattr(screen, "capture", lambda wd: (["screens/window.png"], ""))
    asked = []
    cfg = copy.deepcopy(DEFAULTS)
    cfg["claude_code"]["workdir"] = str(tmp_path)
    a = Agent(cfg, llm=FakeLLM(), claude=lambda t, c: asked.append(t) or "ок", system=NoSystem(), apps=AppIndex(Path("/x")))
    assert a.handle("посмотри что за ошибка на экране").route == "claude"
    assert "screens/window.png" in asked[0].replace("\\", "/") and "Read" in asked[0]


def test_screen_locally_needs_vision_model(no_claude, monkeypatch, tmp_path):
    monkeypatch.setattr(screen, "capture", lambda wd: (["screens/s.png"], ""))
    r = agent("ollama", tmp=tmp_path).handle("посмотри что на экране")
    assert r.route == "error" and "Мастер настройки" in r.text
    llm = FakeLLM()
    r = agent("ollama", llm=llm, vision="qwen3-vl:4b", tmp=tmp_path).handle("посмотри что на экране")
    assert r.text == "на экране ошибка DNS" and llm.images == [("qwen3-vl:4b", ["s.png"])]


def test_vision_click_with_local_model(no_claude, monkeypatch, tmp_path):
    from capri import control
    monkeypatch.setattr(screen, "screenshot_for_locate", lambda wd: ("screens/l.png", 0.5, 0, 0, 800, 450))
    clicks = []
    monkeypatch.setattr(control, "click", lambda button="left", double=False, at=None: clicks.append(at))
    r = agent("ollama", vision="qwen3-vl:4b", tmp=tmp_path).handle("нажми кнопку скип")
    assert clicks == [(20, 40)] and r.route == "local"


# ------------------------------------------------------------------ Gemini API
MODELS = ["gemini-3.6-flash", "gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-3.1-pro-preview",
          "gemini-flash-latest", "gemini-flash-lite-latest", "gemini-pro-latest", "gemini-embedding-2"]


def test_pick_model_prefers_latest_aliases():
    assert gemini.pick_model(MODELS, "free") == "gemini-flash-lite-latest"
    assert gemini.pick_model(MODELS, "free_smart") == "gemini-flash-latest"
    assert gemini.pick_model(MODELS, "paid") == "gemini-pro-latest"


def test_pick_model_without_aliases_takes_newest():
    plain = [m for m in MODELS if "latest" not in m]
    assert gemini.pick_model(plain, "free_smart") == "gemini-3.8-flash"
    assert gemini.pick_model(plain, "free") == "gemini-3.5-flash-lite"
    assert gemini.pick_model(plain, "paid") == "gemini-3.1-pro-preview"
    assert gemini.pick_model(["gemini-3.8-flash"], "paid") == "gemini-3.8-flash"   # Pro нет → лучшая Flash


class Resp:
    def __init__(self, code, data=None, text=""):
        self.status_code, self._data, self.text = code, data or {}, text

    def json(self):
        return self._data


def test_gemini_ask_sends_images_and_reads_text(monkeypatch, tmp_path):
    img = tmp_path / "s.png"
    img.write_bytes(b"\x89PNG")
    sent = {}

    def post(url, json, headers, timeout):
        sent.update(url=url, body=json, headers=headers)
        return Resp(200, {"candidates": [{"content": {"parts": [{"text": "думаю…", "thought": True}, {"text": "Ответ"}]}}]})
    monkeypatch.setattr(gemini.httpx, "post", post)
    out = gemini.ask("вопрос", {"model": "gemini-flash-latest", "api_key": "K"}, images=[img], system="ты Капри")
    assert out == "Ответ" and sent["headers"]["x-goog-api-key"] == "K"
    assert sent["url"].endswith("/models/gemini-flash-latest:generateContent")
    parts = sent["body"]["contents"][0]["parts"]
    assert parts[0]["text"] == "вопрос" and parts[1]["inline_data"]["mime_type"] == "image/png"
    assert sent["body"]["system_instruction"]["parts"][0]["text"] == "ты Капри"


@pytest.mark.parametrize("code,word", [(429, "Лимит"), (403, "ключ"), (404, "мастер")])
def test_gemini_errors_are_human(monkeypatch, code, word):
    monkeypatch.setattr(gemini.httpx, "post", lambda *a, **k: Resp(code, text="x"))
    with pytest.raises(gemini.GeminiError, match=word):
        gemini.ask("q", {"api_key": "K"})


def test_gemini_without_key(monkeypatch):
    monkeypatch.setattr(gemini, "get_key", lambda cfg=None: None)
    with pytest.raises(gemini.GeminiError, match="aistudio"):
        gemini.ask("q", {})
