"""Кто говорит: группировка по голосу, имена, память голосов между уроками, «я — Herr Müller»."""
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from capri.live import commands  # noqa: E402
from capri.live.session import LiveSession, Settings, intro_name  # noqa: E402
from capri.live.speakers import Speakers, fbank, trim_silence  # noqa: E402


def voice(seed, noise=0.25):
    """Векторы «одного голоса»: общий центр + шум."""
    rng = np.random.default_rng(seed)
    base = rng.standard_normal(256)

    def sample(k):
        v = base + noise * np.random.default_rng(seed * 100 + k).standard_normal(256) * np.linalg.norm(base) / 16
        return (v / np.linalg.norm(v)).astype(np.float32)
    return sample


def test_online_clustering_and_short_phrases():
    a, b = voice(1), voice(2)
    sp = Speakers()
    got = [sp.assign(x).id for x in (a(1), b(1), a(2), a(3), b(2))]
    assert got == [1, 2, 1, 1, 2]
    assert sp.assign(None).id == 2                      # короткое «ja» — тот же, кто говорил последним


def test_rename_merge_and_memory(tmp_path):
    a, b = voice(1), voice(2)
    f = tmp_path / "voices.json"
    sp = Speakers(voices_file=f, same=0.995)            # слишком строго — один человек распался на двух
    s1, s2 = sp.assign(a(1)), sp.assign(a(2))
    assert s1.id != s2.id
    sp.rename("спикер 1", "Herr Müller")
    merged = sp.rename("2", "Herr Müller")              # «спикер 2 — это тоже Herr Müller» → один человек
    assert merged.id == 1 and len(sp.speakers) == 1 and sp.resolve(2).name == "Herr Müller"
    sp.assign(b(1))
    assert sp.rename("Спикер 2", "Anna").name == "Anna"
    # следующий урок: учителя узнаём сразу
    nxt = Speakers(voices_file=f)
    assert nxt.assign(a(7)).name == "Herr Müller" and nxt.speakers[0].known
    assert nxt.assign(b(5)).name == "Anna"
    assert nxt.assign(voice(3)(1)).name == "Спикер 3"          # новый голос — без имени
    assert nxt.forget("Herr Müller") and "Herr Müller" not in Speakers(voices_file=f).memory


def test_intro_names():
    assert intro_name("Guten Morgen, ich bin Herr Müller.") == "Herr Müller"
    assert intro_name("Mein Name ist Anna.") == "Anna"
    assert intro_name("Ich bin Informatiker.") == ""
    assert intro_name("Ich bin fertig.") == ""


def test_fbank_and_trim():
    t = np.arange(16000) / 16000
    a = np.concatenate([np.zeros(16000), 0.3 * np.sin(2 * np.pi * 200 * t), np.zeros(16000)]).astype(np.float32)
    assert len(trim_silence(a)) < len(a) * 0.6
    f = fbank(a)
    assert f.shape[1] == 80 and 290 <= f.shape[0] <= 300 and abs(float(f.mean())) < 1e-3


@pytest.mark.parametrize("phrase,params", [
    ("спикер 2 — это Herr Müller", {"do": "rename", "ref": "2", "name": "Herr Müller"}),
    ("первый спикер это учитель", {"do": "rename", "ref": "1", "name": "учитель"}),
    ("спикер 3 это тоже Anna", {"do": "rename", "ref": "3", "name": "Anna"}),
    ("кто говорит", {"do": "who"}),
    ("забудь голос Anna", {"do": "forget_voice", "name": "Anna"}),
])
def test_phrases(phrase, params):
    assert commands.route(phrase).actions[0].params == params


def test_session_labels_speakers():
    import time

    from tests.test_live import FakeAgent, FakeWhisper, silence, speech
    from capri.live import translate as T
    a, b = voice(1), voice(2)
    seq = iter([a(1), b(1)])
    lines = []
    s = LiveSession(lambda: FakeWhisper(), Settings(names=["Igor"]), on_line=lines.append, on_translation=lambda i, t: None,
                    translator=T.Translator(FakeAgent(), "ru"), speakers=Speakers(),
                    embedder_getter=lambda: (lambda audio: next(seq)))
    s.start()
    audio = np.concatenate([silence(0.5), speech(1.2), silence(1.0), speech(1.5), silence(1.0)])
    for i in range(0, len(audio), 1600):
        s.push("system", audio[i:i + 1600])
    t0 = time.monotonic()
    while len(lines) < 2 and time.monotonic() - t0 < 5:
        time.sleep(0.02)
    s.stop()
    assert [(x.spk, x.speaker) for x in lines] == [(1, "Спикер 1"), (2, "Спикер 2")]


def test_bar_shows_and_renames_speakers():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from capri.ui.subtitles import SubtitleBar
    b = SubtitleBar()
    b.add_line(1, "system", "Guten Morgen.", "de", spk=1, speaker="Спикер 1")
    b.add_line(2, "mic", "Hallo.", "de")
    assert "Спикер 1:" in b.html and "Ты:" in b.html
    b.rename_speakers({1: "Herr Müller"})
    assert "Herr Müller:" in b.html
    b.show_history()
    got = []
    b.rename_requested.connect(got.append)
    assert 'href="spk:1"' in b._entry_html(1)


def test_controller_rename_after_lesson_rewrites_transcript(tmp_path):
    import datetime as dt

    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from capri.live import notes as N
    from capri.live.controller import LiveController
    from capri.live.session import Line

    class Prefs(dict):
        def set(self, k, v):
            self[k] = v

    class App:
        prefs, cfg = Prefs(), {"user_name": "Игорь"}
    c = LiveController(App())
    a, b = voice(1), voice(2)
    c.speakers = Speakers(voices_file=tmp_path / "voices.json")
    s1, s2 = c.speakers.assign(a(1)), c.speakers.assign(b(1))
    lines = [Line(1, "system", "Guten Morgen.", "de", 0, 2, spk=1, speaker=s1.name),
             Line(2, "system", "Hallo!", "de", 3, 4, spk=2, speaker=s2.name), Line(3, "mic", "Ja.", "de", 5, 6, speaker="Ты")]
    c.started = dt.datetime(2026, 11, 9, 9, 0)
    c.folder = N.save_session(lines, c.started, tmp_path)
    c._last_lines = lines
    assert "Запомнила голос: Herr Müller" in c.handle({"do": "rename", "ref": "1", "name": "Herr Müller"})
    text = (c.folder / "транскрипт.md").read_text(encoding="utf-8")
    assert "Herr Müller:** Guten Morgen." in text and "Спикер 2:** Hallo!" in text
    assert "Не нашла" in c.handle({"do": "rename", "ref": "7", "name": "X"})
    assert c.handle({"do": "who"}).startswith("Сейчас говорит Спикер 2")
