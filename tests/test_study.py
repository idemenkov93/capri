"""Волна 4 (окончание): языковые помощники, сводка, подсказка на уроке, тренажёр, импорт Granola и материалов."""
import copy
import datetime as dt
import json
import os
import zipfile
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from capri import study  # noqa: E402
from capri.agent import Agent  # noqa: E402
from capri.applist import AppIndex  # noqa: E402
from capri.config import DEFAULTS  # noqa: E402
from capri.live import importer, notes as N  # noqa: E402


class Sys:
    def run(self, cmd):
        return {}


class Prefs(dict):
    def set(self, k, v):
        self[k] = v


def agent(answers=None):
    a = Agent(copy.deepcopy(DEFAULTS), llm=object(), claude=lambda t, c: "C", system=Sys(), apps=AppIndex(Path("/x")))
    a.prefs = Prefs()
    a.asked = []

    def ask(prompt, images=None):
        a.asked.append(prompt)
        if callable(answers):
            return answers(prompt)
        return answers.pop(0) if isinstance(answers, list) else (answers or "**Ich habe den Server neu gestartet.**\nОбычный вариант.")
    a.ask_text = ask
    return a


# ------------------------------------------------------------------ язык
def test_how_to_say_german_and_hebrew():
    a = agent()
    r = a.handle("как сказать по-немецки я перезагрузил сервер")
    assert "по-немецки" in a.asked[0] and "я перезагрузил сервер" in a.asked[0]
    assert r.text == "Ich habe den Server neu gestartet." and r.actions[0].params["copy"] == r.text
    a.handle("как сказать на иврите спасибо")
    assert "иврит" in a.asked[1] and "транслитерац" in a.asked[1]
    a.handle("как будет «Störung» по-английски?")
    assert "по-английски" in a.asked[2]


def test_fix_phrase():
    a = agent("**Правильно:** Ich habe gestern den Server neu gestartet.\n**Ошибки:** der → den")
    r = a.handle("исправь мою фразу: Ich habe gestern der Server neu gestartet")
    assert "der Server neu gestartet" in a.asked[0] and r.actions[0].params["copy"].startswith("Ich habe gestern den")


def test_word_of_day_is_stable_and_offline():
    assert study.word_of_day(dt.date(2026, 11, 9)) == study.word_of_day(dt.date(2026, 11, 9))
    assert study.word_of_day(dt.date(2026, 11, 9)) != study.word_of_day(dt.date(2026, 11, 10))
    r = agent().handle("слово дня")
    assert r.text.startswith("Слово дня:") and r.actions[0].kind == "popup"


# ------------------------------------------------------------------ сводка
def test_course_end_and_morning(monkeypatch, tmp_path):
    a = agent()
    a.handle("курс заканчивается 29.01.2027")
    assert a.prefs["course_end"] == "2027-01-29"
    folder = tmp_path / "2026-11-07 0900 — DNS"
    folder.mkdir()
    (folder / "конспект.md").write_text("# DNS\n\n## Что задали\n- Aufgabe 3 zu DNS bis Montag\n\n## Что повторить\n- DNS\n",
                                        encoding="utf-8")
    monkeypatch.setattr(N, "lessons_dir", lambda: tmp_path)
    from capri import weather
    monkeypatch.setattr(weather, "forecast", lambda *a, **k: "Мюнхен: +5°, облачно")
    a.prefs["reminders"] = [{"at": dt.datetime.combine(dt.date.today(), dt.time(15, 0)).timestamp(), "text": "позвонить Ольге"}]
    r = a.handle("утренняя сводка")
    text = r.actions[0].params["text"]
    assert "+5°" in text and "Aufgabe 3 zu DNS bis Montag" in text and "15:00 позвонить Ольге" in text
    assert "До конца Weiterbildung" in text and "Слово дня" in text and "задали" in r.text


def test_homework_parse():
    assert study.homework_of("## Что задали\n- Ничего не задали\n") == ""
    assert study.homework_of("## Что задали\n- A\n- B\n## Вопросы") == "A; B"


# ------------------------------------------------------------------ подсказка на уроке
def test_lesson_hint_uses_recent_lines():
    from capri.live.session import Line

    class Live:
        session = None
        _last_lines = [Line(1, "system", "Igor, was ist ein DHCP-Server?", "de", 0, 2, speaker="Herr Müller")]

        def prefs(self):
            return {"language": "de"}
    a = agent("**Вопрос:** что такое DHCP\n**Ответ (немецкий, B1):** Ein DHCP-Server verteilt IP-Adressen.")
    a.live = Live()
    r = a.handle("подскажи ответ")
    assert "Herr Müller: Igor, was ist ein DHCP-Server?" in a.asked[0]
    assert r.text == "Подсказка — в окне." and "DHCP-Server verteilt" in r.actions[0].params["text"]
    a.live = type("L", (), {"session": None, "_last_lines": [], "prefs": lambda s: {}})()
    assert a.handle("что мне ответить").route == "error"


# ------------------------------------------------------------------ тренажёр
def test_interview_training_flow():
    ev = json.dumps({"score": 2, "topic": "DHCP", "good": "есть идея", "missing": "аренда адреса",
                     "german": "der → den", "model": "Ein DHCP-Server vergibt IP-Adressen automatisch."})

    def answers(prompt):
        if "Составь" in prompt:
            return '["Was ist DHCP?", "Was ist DNS?"]'
        return ev
    a = agent(answers)
    r = a.handle("тренируй собеседование")
    assert "Вопрос 1/2:** Was ist DHCP?" in r.text and a.trainer is not None
    r = a.handle("Der DHCP gibt Adressen")              # ответ — не команда
    assert "★★☆☆☆" in r.text and "Вопрос 2/2:** Was ist DNS?" in r.text and "Ein DHCP-Server vergibt" in r.text
    r = a.handle("DNS macht Namen zu IP")
    assert "Готово: средняя оценка 2.0" in r.text and a.trainer is None
    assert a.prefs["weak_topics"] == {"DHCP": 2}
    r = a.handle("повтори слабые темы")
    assert "DHCP" in a.asked[-1]


def test_training_stop_and_fallback_questions():
    a = agent(lambda p: (_ for _ in ()).throw(RuntimeError("нет сети")))
    r = a.handle("тренируй собеседование")
    assert "Вопрос 1/5" in r.text                         # свои вопросы, если помощник недоступен
    r = a.handle("стоп тренировка")
    assert a.trainer is None and "Готово" in r.text


def test_cards_and_client_roleplay():
    a = agent(lambda p: '["Что делает chmod?", "Что такое sudo?", "Что такое ssh?"]' if "Составь" in p else "{}")
    r = a.handle("спроси меня 3 вопроса по Linux")
    assert "Карточки: 3 вопросов" in r.text and "русский" in a.asked[0]
    a = agent(lambda p: json.dumps({"client": "Ja, danke, jetzt geht es!", "tip": "Скажи «Einen Moment bitte».",
                                    "solved": True}))
    r = a.handle("тренируй клиента")
    assert "Клиент:" in r.text
    r = a.handle("Haben Sie den Rechner neu gestartet?")
    assert "jetzt geht es" in r.text and "Разговор закончен" in r.text and a.trainer is None


def test_unrelated_training_phrase_not_taken():
    assert study.route(agent(), "тренировка по футболу") is None


# ------------------------------------------------------------------ импорт Granola
def granola_cache(path: Path):
    state = {"documents": {
        "d1": {"id": "d1", "title": "Netzwerk Grundlagen", "created_at": "2026-10-15T07:00:00Z",
               "notes_markdown": "- VLAN trennt Netze", "last_viewed_panel": {"content": {"type": "doc", "content": [
                   {"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": "Zusammenfassung"}]},
                   {"type": "paragraph", "content": [{"type": "text", "text": "Heute: VLAN und Subnetze."}]}]}}},
        "d2": {"id": "d2", "title": "Gelöscht", "deleted_at": "2026-10-16", "notes_markdown": "x"},
        "d3": {"id": "d3", "title": "Leer", "created_at": "2026-10-17T07:00:00Z"}},
        "transcripts": {"d1": [{"text": "Guten Morgen", "source": "system", "start_timestamp": "2026-10-15T07:00:05Z"},
                               {"text": "Hallo", "source": "microphone", "start_timestamp": "2026-10-15T07:00:09Z"}]}}
    path.write_text(json.dumps({"cache": json.dumps({"state": state})}), encoding="utf-8")
    return path


def test_import_granola_once(tmp_path):
    cache = granola_cache(tmp_path / "cache-v3.json")
    root = tmp_path / "Уроки"
    assert importer.import_granola(cache, root) == (1, 2)
    [folder] = [p for p in root.iterdir() if p.is_dir()]
    assert folder.name.endswith("— Netzwerk Grundlagen [Granola]")
    md = (folder / "конспект.md").read_text(encoding="utf-8")
    assert "## Zusammenfassung" in md and "Heute: VLAN und Subnetze." in md and "## Мои заметки" in md
    tr = (folder / "транскрипт.md").read_text(encoding="utf-8")
    assert "Они:** Guten Morgen" in tr and "Ты:** Hallo" in tr
    assert importer.import_granola(cache, root) == (0, 2)                  # второй раз — ничего нового
    assert N.search("VLAN", root)[0][1] == "Netzwerk Grundlagen"


# ------------------------------------------------------------------ импорт файлов
def make_docx(path: Path, paras):
    W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(f'<w:p><w:r><w:t>{p}</w:t></w:r></w:p>' for p in paras)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>')


def make_pptx(path: Path, slides):
    A = "http://schemas.openxmlformats.org/drawingml/2006/main"
    with zipfile.ZipFile(path, "w") as z:
        for i, text in enumerate(slides, 1):
            z.writestr(f"ppt/slides/slide{i}.xml", f'<p:sld xmlns:p="x" xmlns:a="{A}"><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:sld>')


def test_import_folder_and_search(tmp_path):
    src = tmp_path / "Zukunftsmotor"
    (src / "Woche 3").mkdir(parents=True)
    make_docx(src / "Woche 3" / "Active Directory.docx", ["Eine OU ist eine Organisationseinheit.", "GPO = Gruppenrichtlinie"])
    make_pptx(src / "DNS.pptx", ["DNS übersetzt Namen", "Forward Lookup Zone"])
    (src / "notiz.txt").write_text("Subnetz 255.255.255.0", encoding="utf-8")
    (src / "~$temp.docx").write_bytes(b"x")
    root = tmp_path / "Уроки"
    done, skipped, errors = importer.import_folder(src, root)
    assert (done, skipped, errors) == (3, 0, [])
    assert importer.import_folder(src, root)[:2] == (0, 3)                 # не менялись — пропуск
    hits = importer.search_materials("organisationseinheit", root)
    assert hits and hits[0][1] == "Active Directory.docx" and "OU" in hits[0][2][0]
    assert "Слайд 2" in (root / "Материалы" / "Zukunftsmotor" / "DNS.pptx.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("phrase,params", [
    ("импортируй гранолу", {"do": "import_granola"}),
    ("импортируй материалы курса", {"do": "import_folder"}),
    ("обнови материалы", {"do": "import_refresh"}),
    ("объясни по моим урокам VLAN", {"do": "explain", "term": "VLAN"}),
    ("объясни DHCP по моим урокам", {"do": "explain", "term": "DHCP"}),
])
def test_import_phrases(phrase, params):
    from capri.live import commands
    assert commands.route(phrase).actions[0].params == params


# ------------------------------------------------------------------ 💬 быстрый ответ
def test_quick_answer_on_subtitles():
    from capri.live.session import Line

    class Live:
        session = None
        _last_lines = [Line(1, "system", "Hat jemand eine Frage?", "de", 0, 2, speaker="Herr Müller")]

        def prefs(self):
            return {"language": "de"}
    a = agent('{"say": "Können Sie das bitte langsamer wiederholen?", "easy": "Bitte langsamer.", "ru": "Повторите медленнее"}')
    a.live = Live()
    r = a.handle("помоги ответить: я не понял, повторите медленнее")
    assert "Herr Müller: Hat jemand eine Frage?" in a.asked[0] and "немецки" in a.asked[0]
    assert r.text == "Können Sie das bitte langsamer wiederholen?"
    kinds = [x.kind for x in r.actions]
    assert kinds == ["popup", "live_answer"] and r.actions[0].params["copy"] == r.text


def test_subtitle_bar_shows_answer():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from capri.ui.subtitles import SubtitleBar
    b = SubtitleBar()
    b.add_line(1, "system", "Hat jemand eine Frage?", "de")
    b.show_answer("Können Sie das bitte wiederholen?", "Повторите, пожалуйста")
    assert "💬 Скажи так" in b.html and "Können Sie das bitte wiederholen?" in b.html


# ------------------------------------------------------------------ словарь курса
def test_glossary_from_file_and_notes_goes_into_translation(tmp_path):
    from capri.live import glossary as G
    from capri.live import translate as T
    lesson = tmp_path / "2026-11-09 0900 — AD"
    lesson.mkdir()
    (lesson / "конспект.md").write_text("# AD\n\n## Термины\n| Deutsch | Русский | Что это |\n|---|---|---|\n"
                                        "| die Freigabe | общий ресурс | папка в сети |\n| der Benutzer | юзер | |\n", encoding="utf-8")
    G.add("der Benutzer", "пользователь", "учётка", tmp_path)          # свой словарь важнее конспекта
    G.add("der Benutzer", "дубль", "", tmp_path)                        # второй раз не добавляется
    terms = G.load(tmp_path)
    assert terms["die Freigabe"] == "общий ресурс" and terms["der Benutzer"] == "пользователь"
    rel = G.relevant(["Die Freigaben auf dem Server prüfen."], terms)
    assert rel == {"die Freigabe": "общий ресурс"}
    p = T.build_prompt(["Die Freigaben prüfen."], [], "ru", rel)
    assert "die Freigabe — общий ресурс" in p
    assert (tmp_path / "Словарь.md").read_text(encoding="utf-8").count("Benutzer") == 1
