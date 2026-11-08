"""1.9: бестиарий из CSV (поиск, «что такое …», перевод терминов) и субтитры прямо из Teams."""
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from capri.live import commands, glossary as G  # noqa: E402
from capri.live import translate as T  # noqa: E402
from capri.live.session import LiveSession, Settings  # noqa: E402
from capri.live.teams import Tracker, clean_name, items_from_texts  # noqa: E402

CSV = ("﻿Deutsch;Русский;Заметка;Источник;Тема\n"
       '"Active Directory (AD)";"Active Directory";"Каталог пользователей и компьютеров";"Kurs";"AD"\n'
       '"Freigabe- vs. NTFS-Berechtigung";"права общего ресурса vs. NTFS";"Действует самое строгое";"Kurs";"AD"\n'
       '"Gruppenrichtlinie (GPO)";"групповая политика";"";"Kurs";"AD"\n'
       '"DNS";"DNS";"Имя → IP";"Kurs";"Netzwerk"\n'
       '".internal / .test";"домен .internal";"вместо .local";"Granola";"Netzwerk"\n')


@pytest.fixture
def gl(tmp_path, monkeypatch):
    d = tmp_path / "data" / "glossary"
    monkeypatch.setattr(G, "csv_dir", lambda: d)
    G._cache["key"] = None
    src = tmp_path / "Bestiarium.csv"
    src.write_text(CSV, encoding="utf-8")
    root = tmp_path / "Уроки"
    root.mkdir()
    monkeypatch.setattr(G.N, "lessons_dir", lambda: root)
    return src, root, d


def test_import_csv_and_find(gl):
    src, root, d = gl
    assert G.import_file(src) == 5 and (d / "Bestiarium.csv").exists()
    assert G.count(root) == 5
    assert G.find("AD", root=root)[0]["ru"] == "Active Directory"
    assert G.find("gpo", root=root)[0]["de"] == "Gruppenrichtlinie (GPO)"
    assert G.find("групповая политика", root=root)[0]["de"].startswith("Gruppen")
    assert G.find("Freigabe", root=root)[0]["de"].startswith("Freigabe-")
    assert G.find("zzz", root=root) == []


def test_own_dictionary_wins_over_csv(gl):
    src, root, d = gl
    G.import_file(src)
    G.add("DNS", "служба имён", "", root)
    assert G.load(root)["DNS"] == "служба имён"


def test_csv_without_header_and_comma(tmp_path, gl):
    p = tmp_path / "w.csv"
    p.write_text("Freigabe,общий ресурс\nDrucker,принтер,заметка\n", encoding="cp1251")
    rows = G.read_csv(p)
    assert rows[0] == {"de": "Freigabe", "ru": "общий ресурс", "note": "", "topic": ""} and rows[1]["note"] == "заметка"


def test_variants():
    assert G.variants("Active Directory (AD)") == ["Active Directory", "AD"]
    assert G.variants("Freigabe- vs. NTFS-Berechtigung") == ["Freigabe", "NTFS-Berechtigung"]
    assert G.variants("die Freigabe") == ["Freigabe"]


def test_relevant_short_terms_whole_word_and_stems(gl):
    src, root, d = gl
    G.import_file(src)
    rel = G.relevant(["Wir legen die Freigaben an und prüfen das Konto im AD.", "Die Gruppenrichtlinien greifen."],
                     G.load(root))
    assert set(rel) == {"Active Directory (AD)", "Freigabe- vs. NTFS-Berechtigung", "Gruppenrichtlinie (GPO)"}
    assert "DNS" not in G.relevant(["Die Adresse ist neu."], G.load(root))      # «AD» в «Adresse» не ловим


def test_what_is_from_glossary_offline(gl):
    from capri.study import route
    src, root, d = gl
    G.import_file(src)

    class Ag:
        prefs = None
    r = route(Ag(), "что такое GPO?")
    assert r.route == "rules" and "групповая политика" in r.actions[0].params["text"]
    assert route(Ag(), "что такое любовь") is None                             # нет в словаре — дальше мозгу
    r = route(Ag(), "найди в бестиарии NTFS")
    assert r is not None and "NTFS" in r.actions[0].params["text"]


@pytest.mark.parametrize("phrase,params", [
    ("импортируй бестиарий", {"do": "import_glossary"}),
    ("загрузи словарь курса", {"do": "import_glossary"}),
    ("в тимсе меня зовут Ihor Demenkov", {"do": "teams_name", "name": "Ihor Demenkov"}),
    ("не читай субтитры тимс", {"do": "source", "value": "audio"}),
    ("читай субтитры из teams", {"do": "source", "value": "auto"}),
])
def test_new_phrases(phrase, params):
    r = commands.route(phrase)
    assert r.actions[0].kind == "live" and r.actions[0].params == params


# ------------------------------------------------------------------ Teams
def test_items_from_texts():
    it = items_from_texts([["Herr Müller", "Guten Morgen"], ["Und weiter."], ["Ihor Demenkov (Gast)", "Ja"]])
    assert [(i.speaker, i.text) for i in it] == [("Herr Müller", "Guten Morgen"), ("Herr Müller", "Und weiter."),
                                                 ("Ihor Demenkov (Gast)", "Ja")]
    assert clean_name("Ihor Demenkov (Gast)") == "Ihor Demenkov"


def ev(t, groups, now):
    return [(e.kind, e.speaker, e.text) for e in t.update(items_from_texts(groups), now) if e.kind == "final"]


def test_tracker_finals_once():
    t = Tracker()
    assert ev(t, [["Herr Müller", "Guten Morgen"]], 0) == []
    assert ev(t, [["Herr Müller", "Guten Morgen zusammen."]], 0.5) == []
    assert ev(t, [["Herr Müller", "Guten Morgen zusammen."]], 2.0) == [("final", "Herr Müller", "Guten Morgen zusammen.")]
    assert ev(t, [["Herr Müller", "Guten Morgen zusammen. Heute AD"]], 2.4) == []
    assert ev(t, [["Herr Müller", "Guten Morgen zusammen. Heute AD"], ["Anna Weber", "Frage"]], 3) == \
        [("final", "Herr Müller", "Heute AD")]
    assert ev(t, [["Anna Weber", "Frage"]], 5) == [("final", "Anna Weber", "Frage")]
    assert ev(t, [["Anna Weber", "Frage"]], 9) == []                             # дважды не отдаём


def test_tracker_long_monologue_split_by_sentences():
    t = Tracker(long=40)
    t.update(items_from_texts([["Herr Müller", "Erst"]]), 0)
    long = "Erst legen wir den Benutzer an. Dann die Gruppe. Dann prüfen wir die Rechte und"
    out = ev(t, [["Herr Müller", long]], 0.3)
    assert out == [("final", "Herr Müller", "Erst legen wir den Benutzer an. Dann die Gruppe.")]


def test_tracker_start_ignores_old_history():
    t = Tracker()
    ev(t, [["A B", "eins"], ["A B", "zwei"], ["C D", "drei"], ["C D", "vier"]], 0)
    assert ev(t, [["C D", "vier"]], 2) == [("final", "C D", "vier")]


class TrAgent:
    def ready(self, p):
        return p == "groq"

    def _ccfg(self, p):
        return {"model": "x"}

    def cloud_ask(self, p, prompt, cfg, images=None, system=""):
        n = prompt.count("\n", prompt.index("Фразы:"), prompt.index("Ответ")) - 1
        return str(["перевод"] * n).replace("'", '"')


def test_session_external_lines_bypass_whisper():
    got, tr = [], {}
    s = LiveSession(lambda: (_ for _ in ()).throw(AssertionError("Whisper не нужен")),
                    Settings(names=["Igor", "Ihor"]), on_line=got.append,
                    on_translation=lambda i, t: tr.__setitem__(i, t), translator=T.Translator(TrAgent(), "ru"))
    s.start()
    s.external = True
    s.push("system", __import__("numpy").zeros(1600, "float32"))               # звук игнорируется
    a = s.external_line("Herr Müller", "Ihor, haben Sie eine Frage?")
    b = s.external_line("Ihor Demenkov", "Nein, danke.")
    t0 = time.monotonic()
    while len(tr) < 2 and time.monotonic() - t0 < 3:
        time.sleep(0.02)
    s.stop()
    assert a.speaker == "Herr Müller" and a.source == "system" and a.trigger
    assert b.speaker == "Ты" and b.source == "mic" and not b.trigger
    assert tr == {a.id: "перевод", b.id: "перевод"}
