import datetime as dt

import pytest

from capri import google_api as G
from capri.when import parse_day, parse_time

NOW = dt.datetime(2026, 10, 26, 19, 58, tzinfo=dt.timezone(dt.timedelta(hours=1)))


@pytest.mark.parametrize("text,day", [("завтра", 27), ("послезавтра", 28), ("в пятницу", 30), ("28 октября", 28),
                                      ("через 3 дня", 29), ("сегодня", 26), ("30.10", 30)])
def test_days(text, day):
    assert parse_day(text, NOW.date()).day == day


@pytest.mark.parametrize("text,hm", [("в 15:00", (15, 0)), ("в 3 часа дня", (15, 0)), ("в 9 утра", (9, 0)),
                                     ("в 7 вечера", (19, 0)), ("um 14.30", (14, 30))])
def test_times(text, hm):
    assert parse_time(text) == hm


def test_event_parsing():
    ev = G.Google.parse_event("в пятницу в 3 часа дня созвон с Ольгой", NOW)
    assert ev["title"] == "Созвон с Ольгой" and ev["start"].hour == 15 and ev["start"].day == 30 and not ev["all_day"]
    ev = G.Google.parse_event("послезавтра день рождения мамы", NOW)
    assert ev["all_day"] and ev["title"] == "День рождения мамы"
    assert isinstance(G.Google.parse_event("встречу с другом", NOW), str)


class Exec:
    def __init__(self, v):
        self.v = v

    def execute(self):
        return self.v


class FakeGmail:
    def users(self):
        return self

    def messages(self):
        return self

    def list(self, **k):
        return Exec({"resultSizeEstimate": 5, "messages": [{"id": "1"}, {"id": "2"}]})

    def get(self, userId, id, **k):
        subj = {"1": "Einladung zum Vorstellungsgespräch", "2": "Rechnung"}[id]
        return Exec({"payload": {"headers": [{"name": "From", "value": f'"XCOM IT" <hr@x{id}.de>'}, {"name": "Subject", "value": subj}]}})


class FakeCal:
    inserted = []

    def events(self):
        return self

    def list(self, **k):
        return Exec({"items": [{"summary": "Собеседование", "start": {"dateTime": "2026-10-27T15:00:00+01:00"}},
                               {"summary": "Выходной", "start": {"date": "2026-10-27"}}]})

    def insert(self, calendarId, body):
        FakeCal.inserted.append(body)
        return Exec({})


class FakeTasks:
    added = []

    def tasks(self):
        return self

    def list(self, **k):
        return Exec({"items": [{"title": "Купить хлеб"}, {"title": "Bewerbung schreiben"}]})

    def insert(self, tasklist, body):
        FakeTasks.added.append(body["title"])
        return Exec({})


@pytest.fixture
def google(tmp_path):
    g = G.Google(tmp_path)
    g._services = {"gmail": FakeGmail(), "calendar": FakeCal(), "tasks": FakeTasks()}
    return g


def test_mail_calendar_tasks(google):
    mail = google.unread()
    assert "XCOM IT" in mail and "Vorstellungsgespräch" in mail and "примерно 5" in mail
    assert google.agenda("что у меня завтра", NOW) == "Завтра: 15:00 — Собеседование; весь день — Выходной."
    assert "Купить хлеб" in google.tasks()
    google.add_task("Позвонить маме")
    assert FakeTasks.added[-1] == "Позвонить маме"
    google.add_event(G.Google.parse_event("завтра в 10:00 врач", NOW))
    assert FakeCal.inserted[-1]["summary"] == "Врач" and FakeCal.inserted[-1]["start"]["dateTime"].startswith("2026-10-27T10:00")


def test_agent_routes_google(google):
    import copy
    from pathlib import Path

    from capri.agent import Agent
    from capri.applist import AppIndex
    from capri.config import DEFAULTS

    class LLM:
        def chat(self, *a, **k):
            raise AssertionError("не должно доходить до модели")
    a = Agent(copy.deepcopy(DEFAULTS), llm=LLM(), claude=lambda t, c: "", apps=AppIndex(Path("/x")))
    a.google = google
    assert "XCOM" in a.handle("Капри, есть новые письма?").text
    assert a.handle("какие у меня задачи").text.startswith("В списке дел")
    assert "Купить молоко" in a.handle("добавь задачу купить молоко").text
    r = a.handle("добавь в календарь завтра в 15:00 собеседование")
    assert r.actions[0].kind == "gcal_add" and r.actions[0].needs_confirm


def test_not_connected_message(tmp_path, monkeypatch):
    monkeypatch.setattr(G.Path, "home", staticmethod(lambda: tmp_path / "nohome"))
    g = G.Google(tmp_path / "data")
    ok, why = g.ready()
    assert not ok and ("client_secret" in why or "пакеты" in why)


def test_finds_downloaded_key(tmp_path, monkeypatch):
    import json
    home = tmp_path / "home"
    (home / "Downloads").mkdir(parents=True)
    (home / "Downloads" / "client_secret_123-abc.apps.googleusercontent.com.json").write_text(
        json.dumps({"installed": {"client_id": "x"}}), encoding="utf-8")
    monkeypatch.setattr(G.Path, "home", staticmethod(lambda: home))
    g = G.Google(tmp_path / "data")
    assert g._find_client_file() and g.client_file.exists()
