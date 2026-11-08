"""Волна 4: субтитры урока — нарезка речи, Whisper (заглушка), перевод пачками, сигнал на имя, конспект, команды."""
import copy
import datetime as dt
import os
import time
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from capri.live import commands, notes as N, translate as T  # noqa: E402
from capri.live.capture import resample  # noqa: E402
from capri.live.segmenter import SR, Chunk, Segmenter  # noqa: E402
from capri.live.session import LiveSession, Settings, clean, is_echo, trigger_regex  # noqa: E402


def speech(sec, amp=0.2, f=220):
    t = np.arange(int(sec * SR)) / SR
    return (amp * np.sin(2 * np.pi * f * t)).astype(np.float32)


def silence(sec, amp=0.0005):
    return (np.random.default_rng(0).standard_normal(int(sec * SR)) * amp).astype(np.float32)


# ------------------------------------------------------------------ нарезка по паузам
def test_segmenter_cuts_on_pauses():
    seg = Segmenter("system")
    audio = np.concatenate([silence(1), speech(1.5), silence(1), speech(2.0), silence(1), speech(0.1), silence(1)])
    chunks = []
    for i in range(0, len(audio), 1600):                  # кусками по 0,1 с, как из звуковой карты
        chunks += seg.feed(audio[i:i + 1600])
    assert len(chunks) == 2                                # щелчок 0,1 с — выброшен
    assert 1.5 <= chunks[0].end - chunks[0].start <= 2.6 and 0.6 < chunks[0].start < 1.1
    assert 2.0 <= len(chunks[1].audio) / SR <= 3.0


def test_segmenter_long_speech_is_split():
    seg = Segmenter("system", max_len=4.0)
    chunks = seg.feed(np.concatenate([silence(0.5), speech(10)]))
    assert len(chunks) == 2 and all(len(c.audio) / SR <= 4.1 for c in chunks)
    assert seg.partial() is not None and seg.speaking


def test_resample_48k_to_16k():
    x = np.ones(48000, np.float32)
    assert len(resample(x, 48000)) == 16000
    assert len(resample(np.ones(44100, np.float32), 44100)) in (16000, 16001)


# ------------------------------------------------------------------ фильтры
def test_hallucinations_dropped():
    assert clean("Untertitel der Amara.org-Community") == ""
    assert clean("Vielen Dank fürs Zuschauen!") == ""
    assert clean("...") == ""
    assert clean("  Heute  sprechen wir über DNS. ") == "Heute sprechen wir über DNS."


def test_trigger_names_and_words():
    rx = trigger_regex(["Igor", "Ihor", "Prüfung", "AD"])
    assert rx.search("Igor, kannst du das erklären?").group(1) == "Igor"
    assert rx.search("Morgen schreiben wir die Prüfungen").group(1).lower().startswith("prüfung")
    assert rx.search("Igorevich") is None and rx.search("ADAC") is None
    assert trigger_regex([]) is None


def test_echo_gate():
    mic = Chunk("mic", np.zeros(10), 10.0, 12.0)
    assert is_echo(mic, [(9.5, 11.8)])
    assert not is_echo(mic, [(5, 6), (11.9, 13)])


# ------------------------------------------------------------------ перевод
def test_translate_prompt_and_parse():
    p = T.build_prompt(["Guten Morgen.", "Heute DNS."], ["Hallo zusammen."])
    assert "1. Guten Morgen." in p and "Hallo zusammen." in p and "русский" in p and "(2)" in p
    assert T.parse('```json\n["Доброе утро.", "Сегодня DNS."]\n```', 2) == ["Доброе утро.", "Сегодня DNS."]
    assert T.parse("1. Доброе утро.\n2. Сегодня DNS.", 2) == ["Доброе утро.", "Сегодня DNS."]
    assert T.parse('["один"]', 2) == ["один", ""]
    with pytest.raises(ValueError):
        T.parse("не знаю", 2)


class FakeAgent:
    def __init__(self, ready=("groq",), fail=()):
        self._ready, self.fail, self.asked = set(ready), set(fail), []

    def ready(self, p):
        return p in self._ready

    def _ccfg(self, p):
        return {}

    def _gcfg(self):
        return {}

    def cloud_ask(self, p, prompt, cfg, images=None, system=""):
        self.asked.append(p)
        if p in self.fail:
            raise RuntimeError("лимит")
        n = prompt.count("\n", prompt.index("Фразы:"), prompt.index("Ответ")) - 1
        return str([f"перевод {i}" for i in range(1, n + 1)]).replace("'", '"')

    gemini_ask = None


def test_translator_falls_back_and_remembers():
    a = FakeAgent(ready=("groq", "openrouter"), fail=("groq",))
    tr = T.Translator(a, "ru")
    assert tr.order() == ["groq", "openrouter", "local"]
    assert tr.translate(["a", "b"], []) == ["перевод 1", "перевод 2"] and tr.last_used == "openrouter"
    tr.translate(["c"], [])
    assert a.asked == ["groq", "groq", "groq", "openrouter", "openrouter"]   # обе модели Groq пробовали, потом не трогаем


def test_groq_big_model_limit_switches_to_fast_model():
    class A(FakeAgent):
        def cloud_ask(self, p, prompt, cfg, images=None, system=""):
            self.asked.append(cfg.get("model"))
            if cfg.get("model") == "openai/gpt-oss-120b":
                raise RuntimeError("лимит Groq пока исчерпан")
            return '["ok"]'
    a = A(ready=("groq",))
    tr = T.Translator(a, "ru")
    assert tr.translate(["x"], []) == ["ok"] and tr.last_used == "groq openai/gpt-oss-20b"
    tr.translate(["y"], [])
    assert a.asked == ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "openai/gpt-oss-20b"]


# ------------------------------------------------------------------ вся сессия с заглушкой Whisper
class Seg:
    def __init__(self, text):
        self.text, self.no_speech_prob, self.avg_logprob = text, 0.1, -0.3


class FakeWhisper:
    def __init__(self):
        self.n = 0

    def transcribe(self, audio, **kw):
        self.n += 1
        texts = {1: "Guten Morgen zusammen.", 2: "Igor, hast du die Hausaufgabe?", 3: "Ja, habe ich."}
        return iter([Seg(texts.get(self.n, "Weiter."))]), type("I", (), {"language": "de"})()


def run_session(settings, feed):
    events = {"lines": [], "tr": {}, "trig": []}
    model = FakeWhisper()
    s = LiveSession(lambda: model, settings, on_line=events["lines"].append,
                    on_translation=lambda i, t: events["tr"].__setitem__(i, t),
                    on_trigger=events["trig"].append, translator=T.Translator(FakeAgent(), "ru"))
    s.start()
    for source, audio in feed:
        for i in range(0, len(audio), 1600):
            s.push(source, audio[i:i + 1600])
            time.sleep(0.001)
    t0 = time.monotonic()
    while (len(events["tr"]) < len([x for x in events["lines"] if x.source == "system"]) or not events["lines"]) \
            and time.monotonic() - t0 < 5:
        time.sleep(0.02)
    time.sleep(0.2)
    s.stop()
    return s, events


def test_session_lines_translation_and_name_signal():
    st = Settings(names=["Igor"], triggers=["Prüfung"])
    sys_audio = np.concatenate([silence(0.5), speech(1.2), silence(1.0), speech(1.5), silence(1.0)])
    s, ev = run_session(st, [("system", sys_audio)])
    texts = [x.text for x in ev["lines"]]
    assert texts == ["Guten Morgen zusammen.", "Igor, hast du die Hausaufgabe?"]
    assert [x.trigger for x in ev["trig"]] == ["Igor"]
    assert set(ev["tr"].values()) <= {"перевод 1", "перевод 2"} and len(ev["tr"]) == 2


def test_my_own_name_from_mic_is_not_a_signal():
    st = Settings(names=["Igor"])
    s, ev = run_session(st, [("mic", np.concatenate([silence(0.5), speech(1.0), silence(1.0), speech(1.0), silence(1)]))])
    assert [x.source for x in ev["lines"]] == ["mic", "mic"] and ev["trig"] == []


# ------------------------------------------------------------------ конспект и уроки
class L:
    def __init__(self, i, src, text, tr="", trig=""):
        self.id, self.source, self.text, self.translation, self.trigger = i, src, text, tr, trig
        self.start, self.end, self.lang = i * 5.0, i * 5.0 + 3, "de"


def test_transcript_and_notes(tmp_path):
    lines = [L(1, "system", "Heute: DNS.", "Сегодня: DNS."), L(2, "system", "Igor, bitte.", "Игорь, пожалуйста.", "Igor"),
             L(3, "mic", "Ja.")]
    folder = N.save_session(lines, dt.datetime(2026, 11, 9, 9, 0), tmp_path)
    md = (folder / "транскрипт.md").read_text(encoding="utf-8")
    assert "**[00:00:05] Они:** Heute: DNS." in md and "> Сегодня: DNS." in md and "🔔 Igor" in md and "Ты:** Ja." in md

    asked = []

    def ask(prompt):
        asked.append(prompt)
        return ("# DNS und Namensauflösung\n\n## Коротко\nПро DNS.\n\n## Что повторить\n- DNS\nYT: DNS einfach erklärt\n"
                if "конспект" in prompt else "выжимка")
    out = N.make_notes(ask, "x" * 100)
    assert len(asked) == 1 and "[DNS einfach erklärt](https://www.youtube.com/results?search_query=DNS+einfach+erkl" in out
    asked.clear()
    N.make_notes(ask, "\n".join("строка урока " * 30 for _ in range(200)))
    assert len(asked) >= 3 and "часть 1 из" in asked[0]                    # длинный — по частям, потом итог

    new = N.write_notes(folder, out)
    assert new.name == "2026-11-09 0900 — DNS und Namensauflösung" and (new / "конспект.md").exists()
    assert "DNS und Namensauflösung" in (tmp_path / "Индекс.md").read_text(encoding="utf-8")
    hits = N.search("dns", tmp_path)
    assert hits and hits[0][1] == "DNS und Namensauflösung"
    assert N.latest(tmp_path) == new


def test_notes_pdf(tmp_path):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    pdf = N.md_to_pdf("# Тема\n\n| Deutsch | Русский |\n|---|---|\n| Benutzer | пользователь |\n", tmp_path / "к.pdf")
    assert pdf.read_bytes()[:4] == b"%PDF"


# ------------------------------------------------------------------ фразы
@pytest.mark.parametrize("phrase,params", [
    ("включи субтитры", {"do": "start", "lang": None}),
    ("субтитры с английского", {"do": "start", "lang": "en"}),
    ("переводи урок", {"do": "start", "lang": None}),
    ("переводи собеседование", {"do": "start", "lang": None}),
    ("стоп субтитры", {"do": "stop"}),
    ("хватит переводить", {"do": "stop"}),
    ("сделай конспект", {"do": "notes"}),
    ("сигнал на слово Prüfung", {"do": "add_word", "word": "Prüfung"}),
    ("убери слово Prüfung", {"do": "del_word", "word": "Prüfung"}),
    ("какие слова слушаешь", {"do": "list_words"}),
    ("выключи сигналы", {"do": "signals", "on": False}),
    ("не переводи меня", {"do": "mic", "on": False}),
    ("повтори прошлый урок", {"do": "last"}),
    ("что мы проходили по DNS?", {"do": "search", "term": "DNS"}),
])
def test_phrases(phrase, params):
    r = commands.route(phrase)
    assert r.actions[0].kind == "live" and r.actions[0].params == params


def test_unrelated_phrase_not_taken():
    assert commands.route("субтитры к фильму скачай") is None
    assert commands.route("переведи буфер") is None


def test_agent_routes_live_before_other_rules():
    from capri.agent import Agent
    from capri.applist import AppIndex
    from capri.config import DEFAULTS

    class Sys:
        def run(self, cmd):
            return {}
    a = Agent(copy.deepcopy(DEFAULTS), llm=object(), claude=lambda t, c: "C", system=Sys(), apps=AppIndex(Path("/x")))
    assert a.handle("включи субтитры").actions[0].kind == "live"


# ------------------------------------------------------------------ сценарий «учёба»
def test_study_scenario_gets_subtitles_once():
    from capri import scenarios
    old = scenarios.NEW_TEXT_11.replace("    - subtitles: on            # живые субтитры урока с переводом на русский\n", "") \
                               .replace("    - subtitles: off\n", "")
    assert "subtitles" not in old
    new = scenarios._add_subtitles(old)
    assert new.count("subtitles: on") == 1 and new.count("subtitles: off") == 1 and "# + 1.5" in new
    assert scenarios._add_subtitles(new) == new
    import yaml
    st = yaml.safe_load(new)["учёба"]
    assert {"subtitles": "on"} in st["steps"] or {"subtitles": True} in st["steps"]


def test_scenario_step_becomes_action(monkeypatch):
    from capri import scenarios
    r = scenarios.Runner(lambda *a: None, None, lambda a: None)
    r.run([{"subtitles": "de"}])
    assert r.subtitles == "de"
    r.run([{"subtitles": False}])
    assert r.subtitles == "off"


# ------------------------------------------------------------------ окно субтитров
def test_subtitle_bar_render():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from capri.ui.subtitles import SubtitleBar
    b = SubtitleBar()
    b.add_line(1, "system", "Guten Morgen.", "de")
    assert "Guten Morgen." in b.html and "…" in b.html
    b.set_translation(1, "Доброе утро.")
    b.add_line(2, "system", "Igor, bitte!", "de", trigger="Igor")
    b.set_partial("system", "Und jetzt")
    html_ = b.html
    assert "Доброе утро." in html_ and "🔔" in html_ and "Und jetzt" in html_ and b.flash > 0
    for i in range(3, 20):
        b.add_line(i, "mic", f"фраза {i}", "ru")
    assert "Guten Morgen." not in b.html          # на экране только последние
    # окно больше — шрифт крупнее, текст на всю ширину
    b.resize(800, 190)
    small = b.sizes()[0]
    b.resize(1400, 400)
    b.render()
    assert b.sizes()[0] > small and b._doc.textWidth() == b.text_rect().width() > 1300
    b.show_history()
    assert "Guten Morgen." in b.history.toPlainText()
    b.zoom(4)
    assert b.font_size == 24
