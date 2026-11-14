"""Tracker: реплики Teams → события partial / final."""
from dolmetsch.capture.base import Item, sentence_ends
from dolmetsch.capture.rolling import RollingTracker, split_sentences
from dolmetsch.capture.tracker import Tracker


def finals(events):
    return [e.text for e in events if e.kind == "final"]


def primed(text="Alt."):
    t = Tracker()
    t.update([Item("Anna", text)], 0)
    return t


def test_first_snapshot_is_not_translated():
    t = Tracker()
    assert t.update([Item("Anna", "Das war schon vorher da."), Item("Ben", "Und das auch")], 0) == []


def test_sentence_goes_out_as_soon_as_next_one_starts():
    t = primed()
    assert finals(t.update([Item("Anna", "Alt."), Item("Ben", "Guten Morgen zusammen")], 1)) == []
    ev = t.update([Item("Anna", "Alt."), Item("Ben", "Guten Morgen zusammen. Wir fangen")], 1.3)
    assert finals(ev) == ["Guten Morgen zusammen."]
    assert ev[-1].kind == "partial" and ev[-1].text == "Wir fangen"


def test_long_reply_is_sent_sentence_by_sentence_without_duplicates():
    t = primed()
    text, got = "", []
    for i, s in enumerate(["Erstens prüfen wir den Server.", "Zweitens schauen wir auf die Firewall.",
                           "Drittens testen wir den Client.", "Und dann"]):
        text = (text + " " + s).strip()
        got += finals(t.update([Item("Anna", "Alt."), Item("Ben", text)], 1 + i * 0.3))
    assert got == ["Erstens prüfen wir den Server.", "Zweitens schauen wir auf die Firewall.",
                   "Drittens testen wir den Client."]


def test_silence_finishes_the_tail():
    t = primed()
    t.update([Item("Anna", "Alt."), Item("Ben", "Hat jemand eine Frage")], 1)
    assert finals(t.update([Item("Anna", "Alt."), Item("Ben", "Hat jemand eine Frage")], 1.5)) == []
    assert finals(t.update([Item("Anna", "Alt."), Item("Ben", "Hat jemand eine Frage")], 2.3)) == ["Hat jemand eine Frage"]
    assert finals(t.update([Item("Anna", "Alt."), Item("Ben", "Hat jemand eine Frage")], 5)) == []


def test_new_speaker_finishes_previous_reply():
    t = primed()
    t.update([Item("Anna", "Alt."), Item("Ben", "Ich bin fertig")], 1)
    ev = t.update([Item("Ben", "Ich bin fertig"), Item("Anna", "Danke")], 1.3)
    assert finals(ev) == ["Ich bin fertig"] and ev[-1].speaker == "Anna"
    assert finals(t.update([Item("Ben", "Ich bin fertig"), Item("Anna", "Danke")], 3)) == ["Danke"]


def test_abbreviations_and_numbers_do_not_split():
    assert sentence_ends("Das ist z. B. der 3. Schritt bzw. Nr. 5 hier. Ok") == [45]
    t = primed()
    ev = t.update([Item("Anna", "Alt."), Item("Ben", "Nehmen wir z. B. den Server")], 1)
    assert finals(ev) == []


def test_teams_rewrites_already_sent_part():
    t = primed()
    t.update([Item("Anna", "Alt."), Item("Ben", "wir prüfen den server. Dann")], 1)
    ev = t.update([Item("Anna", "Alt."), Item("Ben", "Wir prüfen den Server. Dann kommt die Firewall. Und")], 1.3)
    assert finals(ev) == ["Dann kommt die Firewall."]


def test_same_speaker_new_reply():
    t = primed()
    t.update([Item("Anna", "Alt."), Item("Ben", "Erste Aussage ohne Punkt")], 1)
    ev = t.update([Item("Ben", "Erste Aussage ohne Punkt"), Item("Ben", "Zweite Aussage beginnt")], 1.3)
    assert finals(ev) == ["Erste Aussage ohne Punkt"]


# ---------- субтитры Windows: бегущее окно текста
def test_split_sentences():
    assert split_sentences("Eins. Zwei! Und drei") == (["Eins.", "Zwei!"], "Und drei")


def test_rolling_emits_new_sentences_once():
    t = RollingTracker()
    assert t.update("Das stand schon da. Und", 0) == []
    ev = t.update("Das stand schon da. Und jetzt geht es los. Wir sehen", 1)
    assert finals(ev) == ["Und jetzt geht es los."] and ev[-1].text == "Wir sehen"
    # старые строки уехали из окна, слово поправили — повторов нет
    assert finals(t.update("Und jetzt geht es los. Wir sehen uns das an. Danach kommt", 2)) == ["Wir sehen uns das an."]
    assert finals(t.update("und jetzt geht es los. Wir sehen uns das an. Danach kommt", 2.2)) == []


def test_rolling_waits_until_sentence_is_stable_then_flushes_on_silence():
    t = RollingTracker()
    t.update("", 0)
    assert finals(t.update("Guten Morgen.", 1)) == []
    assert finals(t.update("Guten Morgen.", 2.3)) == ["Guten Morgen."]
    t.update("Guten Morgen. Wir fangen jetzt", 3)
    ev = t.update("Guten Morgen. Wir fangen jetzt", 5.6)          # тишина: отдаём недописанное
    assert finals(ev) == ["Wir fangen jetzt"]
    assert finals(t.update("Guten Morgen. Wir fangen jetzt an mit dem Thema. Also weiter", 6)) == ["an mit dem Thema."]
