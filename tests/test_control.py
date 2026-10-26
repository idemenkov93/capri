import copy
from pathlib import Path

import pytest

from capri import commands as C
from capri import control
from capri.agent import Agent
from capri.applist import AppIndex
from capri.config import DEFAULTS


@pytest.mark.parametrize("want,name,ok", [
    ("сохранить", "Сохранить", True), ("сохранить", "Save", True), ("настройки", "Settings", True),
    ("файл", "File", True), ("отправить", "Отправить сообщение", True), ("сохранить", "Сохранить как...", True),
    ("далее", "Weiter", True), ("сохранить", "Удалить", False), ("войти", "Sign in", True), ("ок", "OK", True),
])
def test_button_names(want, name, ok):
    assert (control.name_score(want, name) >= 0.72) == ok


def test_risky_buttons_need_yes():
    for n in ("Удалить", "Delete account", "Отправить", "Send", "Оплатить", "Kaufen", "Sign out"):
        assert control.RISKY.search(n), n
    for n in ("Сохранить", "Settings", "Далее", "OK"):
        assert not control.RISKY.search(n), n


@pytest.mark.parametrize("text,op", [
    ("Капри, напечатай Привет, как дела?", "type"), ("прокрути вниз", "scroll"), ("двойной клик", "click"),
    ("сверни окно", "window"), ("закрой окно", "window"), ("нажми кнопку Сохранить", "click_name"),
    ("кликни по Файл", "click_name"), ("открой меню Вид", "click_name"), ("нажми энтер", "keys"),
])
def test_control_phrases(text, op):
    assert C.match(text).op == op


def test_typed_text_keeps_case_and_enter():
    assert C.match("Капри, напечатай Привет, Мир!").text == "Привет, Мир!"
    assert C.match("напечатай git status и нажми энтер").text == "git status\n"
    long = "напечатай " + "длинное письмо " * 10
    assert C.match(long).op == "type"


def test_risky_click_asks_first():
    class Sys:
        def run(self, cmd):
            return {"confirm": True, "found": "Удалить"}

    class LLM:
        def chat(self, *a, **k):
            raise AssertionError
    a = Agent(copy.deepcopy(DEFAULTS), llm=LLM(), claude=lambda t, c: "", system=Sys(), apps=AppIndex(Path("/x")))
    r = a.handle("нажми кнопку удалить")
    assert r.text == "Нашла «Удалить». Нажать?" and r.actions[0].kind == "click_confirmed" and r.actions[0].needs_confirm
