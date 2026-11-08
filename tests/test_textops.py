"""Волна 2: буфер, перевод, письма, QR, диктовка — без настоящих Gemini/Claude."""
import copy
import datetime as dt
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from capri import textops as T
from capri.agent import Agent
from capri.applist import AppIndex
from capri.config import DEFAULTS

APP = QApplication.instance() or QApplication([])
KEEP = []


class Sys:
    def run(self, cmd):
        return {}


def test_parse_and_explained():
    r = T.explained('Вот:\n```json\n{"kind":"letter","lang":"de","title":"Jobcenter","answer":"Нужна справка",'
                    '"deadline":"2026-11-20","text":"Sehr geehrter…"}\n```')
    assert r["kind"] == "letter" and r["deadline"] == "2026-11-20" and r["text"].startswith("Sehr")
    assert T.explained("просто текст без JSON")["answer"] == "просто текст без JSON"
    assert T.explained('{"kind":"x","deadline":"20.11"}')["kind"] == "text"          # мусор → безопасные значения
    rp = T.reply_parsed('{"subject":"Re: Termin","body":"Hallo","attach":"Lebenslauf"}')
    assert rp == {"subject": "Re: Termin", "body": "Hallo", "attach": ["Lebenslauf"]}


def test_links_and_qr(tmp_path):
    assert "сокращённая" in T.link_risk("https://bit.ly/abc") and "http" in T.link_risk("http://example.de")
    assert "IP" in T.link_risk("https://192.168.0.1/login") and "punycode" in T.link_risk("https://xn--80ak6aa92e.com")
    assert T.link_risk("https://www.arbeitsagentur.de/jobsuche/") == "" and T.host_of("https://www.xing.com/jobs") == "xing.com"
    p = str(tmp_path / "qr.png")
    T.qr_make("https://example.de/termin", p)
    assert T.qr_decode(p) == ["https://example.de/termin"]


def test_prompts_mention_what_matters():
    assert "немецкий" in T.translate_prompt("hi", "de") and "уже на языке «русский»" in T.translate_prompt("hi", "ru", auto=True)
    assert "с немецкого на иврит" in T.translate_prompt("Hallo", "he", "de")
    assert T.parse_translation('{"from": "de", "to": "he", "text": "שלום"}') == ("de", "he", "שלום")
    assert T.parse_translation("просто текст", "ru") == ("", "ru", "просто текст")
    rp = T.reply_prompt("Sehr geehrter Herr…", "согласен на четверг", "de")
    assert "согласен на четверг" in rp and "attach" in rp and "B1" in rp


def agent(answer):
    a = Agent(copy.deepcopy(DEFAULTS), llm=object(), claude=lambda t, c: answer, system=Sys(), apps=AppIndex(Path("/x")),
              gemini_ask=lambda *x, **k: answer)
    return a


class FakeApp:
    def __init__(self, answer):
        self.agent = agent(answer)
        self.voice = None
        self.reminders = []

    def add_reminder(self, at, text):
        self.reminders.append((at, text))


def wait(ops):
    import time
    for j in list(ops.jobs):
        j.wait(5000)                         # поток закончил — его сигнал ждёт в очереди главного потока
    for _ in range(50):
        APP.processEvents()
        time.sleep(0.002)


def test_clipboard_letter_flow(tmp_path, monkeypatch):
    from capri.textui import TextOps
    fake = FakeApp('{"kind":"letter","lang":"de","title":"Письмо Jobcenter","answer":"Просят справку до 20.11",'
                   '"deadline":"2030-11-20","text":""}')
    monkeypatch.setattr(fake.agent, "_workdir", lambda: tmp_path)
    ops = TextOps(fake)
    KEEP.append(ops)
    APP.clipboard().setText("Sehr geehrter Herr Demenkov, bitte senden Sie uns …")
    ops.explain_clipboard()
    wait(ops)
    labels = [ops.popup.buttons.itemAt(i).widget().text() for i in range(ops.popup.buttons.count())
              if ops.popup.buttons.itemAt(i).widget()]
    assert "Перевести" in labels and "✉ Ответить" in labels and any("Напомнить 20.11" in x for x in labels)
    assert "справку" in ops.popup.body.toPlainText()
    ops.remind(dt.date(2030, 11, 20))
    assert dt.datetime.fromtimestamp(fake.reminders[0][0]) == dt.datetime(2030, 11, 19, 10, 0)


def test_reply_goes_to_clipboard(tmp_path, monkeypatch):
    from capri.textui import TextOps
    fake = FakeApp('{"subject":"Re: Termin","body":"Guten Tag, Donnerstag passt mir.","attach":["Lebenslauf"]}')
    ops = TextOps(fake)
    KEEP.append(ops)
    ops.reply("согласен на четверг", letter="Bitte bestätigen Sie den Termin.")
    wait(ops)
    assert ops.popup.subject.text() == "Re: Termin" and "Donnerstag" in ops.popup.reply.toPlainText()
    assert APP.clipboard().text().startswith("Guten Tag") and "Lebenslauf" in ops.popup.warn.text()


def test_clipboard_link_and_qr_image(tmp_path, monkeypatch):
    from capri.textui import TextOps
    fake = FakeApp("")
    monkeypatch.setattr(fake.agent, "_workdir", lambda: tmp_path)
    ops = TextOps(fake)
    KEEP.append(ops)
    APP.clipboard().setText("https://bit.ly/xyz")
    ops.explain_clipboard()
    assert "bit.ly" in ops.popup.title.text().lower() and "сокращённая" in ops.popup.warn.text()
    p = str(tmp_path / "q.png")
    T.qr_make("https://example.de/x", p)
    APP.clipboard().setImage(QImage(p))
    ops.explain_clipboard()
    assert "example.de" in ops.popup.title.text().lower()


def test_translate_shows_language_buttons():
    from capri.textui import TextOps
    fake = FakeApp('{"from": "de", "to": "ru", "text": "Привет, мир"}')
    ops = TextOps(fake)
    KEEP.append(ops)
    ops.translate("Hallo Welt")
    wait(ops)
    pop = ops.popup
    assert pop.body.toPlainText() == "Привет, мир"
    assert pop.cb_to.currentData() == "ru" and pop.cb_from.currentText() == "Авто (Немецкий)" and pop.cb_from.isVisible()
    asked = []
    ops.translate = lambda text, target="", source="": asked.append((text, target, source))
    pop.cb_to.setCurrentIndex(pop.cb_to.findData("he"))
    pop._lang_changed()                                      # «с немецкого → на иврит»
    pop._swap()
    assert asked == [("Hallo Welt", "he", ""), ("Hallo Welt", "de", "he")]


def test_ask_text_falls_back_to_local_model():
    class Local:
        model = "qwen3:8b"

        def chat(self, messages, tools=None, **k):
            return {"content": "<think>x</think>Перевод", "tool_calls": []}
    a = Agent(copy.deepcopy(DEFAULTS), llm=Local(), claude=lambda t, c: "C", system=Sys(), apps=AppIndex(Path("/x")))
    a.cfg["brain"]["provider"] = "ollama"
    assert a.ask_text("переведи") == "Перевод"


def test_hotkey_parse():
    from capri import hotkeys
    assert hotkeys.parse("Ctrl+Shift+T") == [0x11, 0x10, 0x54]
    assert hotkeys.parse("Ctrl+Win") == [0x11, 0x5B] and hotkeys.parse("Ctrl+Банан") == []


def test_reminders_persist_and_fire(monkeypatch, tmp_path):
    from tests.test_eco import quiet_app
    app = quiet_app(monkeypatch)
    monkeypatch.setattr(app.prefs, "path", tmp_path / "state.json")
    fired = []
    monkeypatch.setattr(app, "timer_fired", lambda text, prefix="": fired.append(text))
    app.add_reminder(0, "Срок по письму")
    app.add_reminder(4_000_000_000, "потом")
    app._check_reminders()
    assert fired == ["Срок по письму"] and len(app.prefs.get("reminders")) == 1
    assert app.text.popup is not None and app.hotkeys is not None
