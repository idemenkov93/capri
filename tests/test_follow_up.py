"""Капри спросила — слушает ответ и понимает его: «На сколько?» → «на 10 минут», «Включить?» → «да»."""
import copy
import os
from pathlib import Path

import numpy as np

from capri.agent import Agent, Reply
from capri.applist import AppIndex
from capri.config import DEFAULTS

KEEP = []


class LLM:
    def __init__(self, text):
        self.text = text

    def chat(self, messages, tools=True, on_token=None, should_stop=None):
        return {"content": self.text}


class Sys:
    def run(self, cmd):
        return {}


def agent(llm_text="ок"):
    return Agent(copy.deepcopy(DEFAULTS), llm=LLM(llm_text), claude=lambda t, c: "", system=Sys(), apps=AppIndex(Path("/x")))


def test_timer_question_and_answer():
    a = agent()
    r = a.handle("Капри, поставь таймер")
    assert r.text == "На сколько?" and r.ask
    r = a.handle("на десять минут")
    assert r.actions[0].kind == "timer" and r.actions[0].params["minutes"] == 10
    a.handle("таймер")
    assert a.handle("пять").actions[0].params["minutes"] == 5          # просто число — минуты
    a.handle("таймер")
    assert a.handle("не надо").text.startswith("Окей") and a.pending is None


def test_answer_that_is_not_an_answer_is_a_new_command():
    a = agent()
    a.handle("таймер")
    r = a.handle("громкость 30")
    assert r.route == "rules" and "30" in r.text and a.pending is None


def test_confirm_yes_and_no():
    a = agent()
    done = []
    a.ask("confirm", do=lambda: done.append(1) or Reply("Включаю.", route="rules"), no="Окей, не включаю.")
    assert a.handle("Да, давай").text == "Включаю." and done == [1]
    a.ask("confirm", do=lambda: done.append(2) or Reply("x"), no="Окей, не включаю.")
    assert a.handle("нет") .text == "Окей, не включаю." and done == [1]


def test_question_from_the_model_opens_the_mic():
    assert agent("Что именно проверить — диск или память?").handle("проверь что-нибудь").ask
    assert not agent("Готово. Держи.").handle("расскажи анекдот").ask


class FakeVoice:
    def __init__(self):
        self.recording, self._speaking, self.log = False, 0, []
        from capri.voice.stt import Transcriber
        self.stt = Transcriber()

    def speak(self, text):
        self.log.append(("speak", text))

    def chime(self):
        self.log.append("chime")

    def listen_once(self):
        self.log.append("listen")

    def quiet(self):
        pass


def test_app_listens_after_asking(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    import capri.app as appmod
    from capri.applist import AppIndex as AI
    monkeypatch.setattr(appmod.CapriApp, "_init_voice", lambda self: None)
    monkeypatch.setattr(appmod.CapriApp, "start_dancing", lambda self: None)
    monkeypatch.setattr(AI, "refresh", lambda self: 0)
    app = appmod.CapriApp()
    KEEP.append(app)
    app.warmup.wait(10_000)
    if hasattr(app, "game"):
        app.game._poll_timer.stop()
    app.voice = FakeVoice()
    app.last_input_was_voice = True
    app.on_reply(Reply("На сколько?", route="rules", ask=True))
    assert ("speak", "На сколько?") in app.voice.log and "listen" not in app.voice.log
    app.on_voice_state("IDLE")                 # договорила
    import time
    end = time.time() + 2
    while "listen" not in app.voice.log and time.time() < end:
        QApplication.processEvents()
        time.sleep(0.02)
    assert app.voice.log[-2:] == ["chime", "listen"]
    app.agent.ask("timer")
    app.on_transcribed("", "ru")               # промолчал — молча забыла вопрос
    assert app.agent.pending is None
    assert np is not None


def _fake_uia(monkeypatch):
    import contextlib
    import sys
    import types
    m = types.ModuleType("uiautomation")
    m.UIAutomationInitializerInThread = contextlib.nullcontext
    monkeypatch.setitem(sys.modules, "uiautomation", m)


def test_track_not_on_screen_is_searched_and_asked(monkeypatch):
    from capri import control
    _fake_uia(monkeypatch)
    clicked = []
    monkeypatch.setattr(control, "ytm_window", lambda: 42)
    monkeypatch.setattr(control, "focus", lambda h: h)
    monkeypatch.setattr(control, "context_window", lambda: ("YouTube Music", "chrome.exe"))
    monkeypatch.setattr(control, "page_links", lambda **k: [("Риплей Микс", "ctrl-a")])
    monkeypatch.setattr(control, "ytm_search", lambda h, q: [("High Forever", "c1"), ("Petite Meller", "c2")])
    monkeypatch.setattr(control, "ytm_click", lambda h, name: clicked.append(name) or True)
    a = agent()
    r = a.handle("Воспроизведи High Forever")
    assert r.text == "Нашла «High Forever». Включить?" and r.ask and not clicked
    r = a.handle("да")
    assert r.text == "Включаю «High Forever»." and clicked == ["High Forever"]


def test_track_on_screen_is_clicked(monkeypatch):
    from capri import control
    _fake_uia(monkeypatch)
    clicked = []
    monkeypatch.setattr(control, "ytm_window", lambda: 42)
    monkeypatch.setattr(control, "focus", lambda h: h)
    monkeypatch.setattr(control, "context_window", lambda: ("YouTube Music", "chrome.exe"))
    monkeypatch.setattr(control, "page_links", lambda **k: [("Лампочки", "c-lamp"), ("Solitude", "c-sol")])
    monkeypatch.setattr(control, "in_content_area", lambda h, c: True)
    monkeypatch.setattr(control, "click_element", clicked.append)
    r = agent().handle("включи Лампочки")
    assert r.text == "Включаю «Лампочки»." and clicked == ["c-lamp"]


def test_youtube_and_youtube_music_do_not_mix(monkeypatch):
    from capri import control
    _fake_uia(monkeypatch)
    searched = []
    monkeypatch.setattr(control, "ytm_window", lambda: 42)
    monkeypatch.setattr(control, "focus", lambda h: h)
    monkeypatch.setattr(control, "page_links", lambda **k: [])
    monkeypatch.setattr(control, "ytm_search", lambda h, q: searched.append(q) or [("High Forever", "c1")])
    # сейчас перед тобой YouTube в браузере — YouTube Music не трогаем, ищем на YouTube
    monkeypatch.setattr(control, "context_window", lambda: ("Котики - YouTube - Google Chrome", "chrome.exe"))
    r = agent().handle("воспроизведи High Forever")
    assert not searched and "YouTube Music" not in r.text
    r = agent().handle("включи High Forever на ютубе")
    assert not searched and r.text.startswith("Ищу на YouTube")
    # сказал прямо «в ютуб мюзик» — ищем там, даже если окно не перед глазами
    r = agent().handle("воспроизведи High Forever в ютуб мюзик")
    assert searched == ["High Forever"] and r.ask

    r = agent().handle("Включи Лампочки в ютуб мюзик")
    assert searched[-1] == "Лампочки" and r.text.startswith("Нашла")
