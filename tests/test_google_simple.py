import copy
import datetime as dt
from pathlib import Path

import pytest

from capri import google_simple as S
from capri.agent import Agent
from capri.applist import AppIndex
from capri.config import DEFAULTS

TZ = dt.timezone(dt.timedelta(hours=1))
ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:1
DTSTART:20261027T140000Z
DTEND:20261027T150000Z
SUMMARY:Собеседование XCOM
END:VEVENT
BEGIN:VEVENT
UID:2
DTSTART;VALUE=DATE:20261027
DTEND;VALUE=DATE:20261028
SUMMARY:Выходной
END:VEVENT
BEGIN:VEVENT
UID:3
DTSTART:20261005T070000Z
DTEND:20261005T080000Z
RRULE:FREQ=WEEKLY;BYDAY=TU
SUMMARY:Weiterbildung
END:VEVENT
END:VCALENDAR
""".encode("utf-8")


def test_ics_agenda_with_recurring_events():
    items = S.agenda_from_ics(ICS, dt.date(2026, 10, 27), TZ)
    assert items == [("весь день", "Выходной"), ("08:00", "Weiterbildung"), ("15:00", "Собеседование XCOM")]
    assert S.agenda_from_ics(ICS, dt.date(2026, 10, 28), TZ) == []


def test_ical_url_check_and_add_link():
    assert S.valid_ical_url("https://calendar.google.com/calendar/ical/me%40gmail.com/private-abc/basic.ics")
    assert not S.valid_ical_url("https://calendar.google.com/calendar/u/0/r")
    ev = {"title": "Врач", "start": dt.datetime(2026, 10, 27, 10, 0, tzinfo=TZ),
          "end": dt.datetime(2026, 10, 27, 11, 0, tzinfo=TZ), "all_day": False}
    url = S.calendar_add_url(ev)
    assert "action=TEMPLATE" in url and "text=%D0%92%D1%80%D0%B0%D1%87" in url and "20261027T090000Z/20261027T100000Z" in url


class FakeIMAP:
    def __init__(self, host):
        assert host == "imap.gmail.com"

    def login(self, a, p):
        assert p == "abcdabcdabcdabcd"

    def select(self, box, readonly):
        assert readonly

    def search(self, *a):
        return "OK", [b"1 2 3 4"]

    def fetch(self, mid, what):
        assert "PEEK" in what           # не помечаем прочитанным
        return "OK", [(b"", b"From: =?utf-8?b?0J7Qu9GM0LPQsA==?= <olga@x.de>\r\nSubject: Termin morgen\r\n\r\n")]

    def logout(self):
        pass


def test_unread_imap():
    s = S.unread_imap("me@gmail.com", "abcd abcd abcd abcd", imap_cls=FakeIMAP)
    assert s.startswith("Новые письма: от Ольга: «Termin morgen»") and "Всего непрочитанных: 4" in s


def test_local_todo(tmp_path):
    t = S.Todo(tmp_path / "todo.md")
    t.add("Купить хлеб")
    t.add("Bewerbung schreiben")
    assert t.open_items() == ["Купить хлеб", "Bewerbung schreiben"]
    assert t.done("хлеб") == "Купить хлеб"
    assert t.open_items() == ["Bewerbung schreiben"]


@pytest.fixture
def simple_agent(tmp_path, monkeypatch):
    import capri.config as cfgmod
    monkeypatch.setattr(cfgmod, "PROJECT_ROOT", tmp_path)
    secrets = {}
    monkeypatch.setattr(S, "get_secret", lambda n: secrets.get(n))

    class LLM:
        def chat(self, *a, **k):
            raise AssertionError("не должно доходить до модели")
    a = Agent(copy.deepcopy(DEFAULTS), llm=LLM(), claude=lambda t, c: "", apps=AppIndex(Path("/x")))
    return a, secrets


def test_simple_mode_asks_to_connect_then_works(simple_agent, monkeypatch):
    a, secrets = simple_agent
    r = a.handle("есть новые письма?")
    assert r.actions[0].kind == "connect_mail"
    assert a.handle("что у меня завтра").actions[0].kind == "connect_calendar"
    assert a.handle("подключи гугл").actions[0].kind == "connect_simple"
    secrets.update(ical_url="https://calendar.google.com/calendar/ical/x/basic.ics", gmail_address="me@gmail.com",
                   gmail_app_password="abcdabcdabcdabcd")
    monkeypatch.setattr(S, "fetch_agenda", lambda url, day, tz: [("15:00", "Собеседование")])
    monkeypatch.setattr(S, "unread_imap", lambda addr, pw: "Новые письма: от Ольга.")
    assert a.handle("что у меня завтра").text == "Завтра: 15:00 — Собеседование."
    assert a.handle("проверь почту").text == "Новые письма: от Ольга."
    r = a.handle("добавь в календарь завтра в 15:00 собеседование")
    assert r.actions[0].kind == "open_url" and "calendar.google.com" in r.actions[0].params["url"]
    assert "Купить молоко" in a.handle("добавь задачу купить молоко").text
    assert "Купить молоко" in a.handle("какие у меня задачи").text
    assert "Вычеркнула" in a.handle("отметь задачу молоко").text


@pytest.mark.parametrize("text", ["Сколько у меня не прочитанных и мейлов?", "есть ли новые имейлы", "что пришло на почту", "any new emails?"])
def test_mail_questions_go_to_mail(simple_agent, text):
    a, _ = simple_agent
    assert a.handle(text).actions[0].kind == "connect_mail"      # не подключено → предлагает подключить, а не болтает


def test_tool_call_text_is_stripped():
    class LLM:
        def chat(self, *a, **k):
            return {"content": "Проверяю… (вызывает инструмент open_app с параметром \\"почта\\") Ты в курсе?"}
    a = Agent(copy.deepcopy(DEFAULTS), llm=LLM(), claude=lambda t, c: "", apps=AppIndex(Path("/x")))
    assert a.handle("как дела").text == "Проверяю… Ты в курсе?"
