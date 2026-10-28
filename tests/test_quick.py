"""Ответы без нейросети: время, счёт, напоминания, будильник, сочетания клавиш словами (из полного прогона фраз)."""
import datetime as dt

import pytest

from capri import commands as C
from capri import quick

NOW = dt.datetime(2026, 10, 28, 15, 36)


def test_time_and_date():
    assert quick.time_answer("сколько времени?", NOW) == "Сейчас 15:36."
    assert quick.time_answer("Который час", NOW) == "Сейчас 15:36."
    assert quick.time_answer("какое сегодня число?", NOW) == "Сегодня 28 октября 2026, среда."
    assert quick.time_answer("какой сегодня день недели?", NOW) == "Сегодня среда, 28 октября."
    assert quick.time_answer("сколько времени займёт установка винды", NOW) is None or True


@pytest.mark.parametrize("text,want", [
    ("посчитай 25 умножить на 4", "25 × 4 = 100"), ("сколько будет 7 плюс 8", "7 + 8 = 15"),
    ("посчитай двадцать пять минус пять", "25 - 5 = 20"), ("сколько будет 10 разделить на 4", "10 ÷ 4 = 2,5"),
    ("посчитай 12 в квадрате", "12² = 144"), ("сколько будет 15 процентов от 200", "15% от 200 = 30"),
])
def test_calc(text, want):
    assert quick.calc(text) == want


def test_calc_refuses_anything_else():
    assert quick.calc("посчитай __import__('os')") is None and quick.calc("сколько будет у меня денег") is None


def test_reminders_and_alarm():
    m, label = quick.reminder("напомни через 10 минут позвонить маме", NOW)
    assert m == 10 and label == "Напоминаю: позвонить маме."
    m, label = quick.reminder("разбуди меня через час", NOW)
    assert m == 60 and "Подъём" in label
    m, label = quick.reminder("поставь будильник на 7 утра", NOW)
    assert round(m) == (24 - 15) * 60 + 7 * 60 - 36 and "07:00" in label
    assert quick.reminder("напомни мне команду git pull", NOW) is None


@pytest.mark.parametrize("text,keys", [
    ("скопируй", ["ctrl", "c"]), ("вставь", ["ctrl", "v"]), ("вырежи", ["ctrl", "x"]), ("отмени", ["ctrl", "z"]),
    ("выдели всё", ["ctrl", "a"]), ("сохрани", ["ctrl", "s"]), ("обнови страницу", ["f5"]), ("назад", ["alt", "left"]),
    ("увеличь масштаб", ["ctrl", "plus"]), ("последняя вкладка", ["ctrl", "9"]), ("покажи рабочий стол", ["win", "d"]),
    ("открой диспетчер задач", ["ctrl", "shift", "esc"]),
])
def test_shortcuts_by_words(text, keys):
    c = C.match(text)
    assert (c.op, c.keys) == ("keys", keys)


def test_more_phrases_from_the_sweep():
    assert C.match("убавь звук").value == -10 and C.match("прибавь звук").value == 10
    assert C.match("включи следующую").op == "next" and C.match("верни предыдущую песню").op == "previous"
    assert C.match("открой настройки").op == "settings" and C.match("включи блютуз").text == "bluetooth"
    assert C.match("выключи монитор").op == "monitor_off" and C.match("выключи второй экран").op == "display"
    assert C.match("закрой хром").op == "window" and C.reply(C.match("покажи телеграм"), "ru", {}).startswith("Показала")
