import json

from capri.i18n import TEXTS, t
from capri.state import State
from capri.voice.wake import is_wake


def test_wake_phrases():
    for s in ["привет капри", "Привет, Капри!", "ну привет кэпри", "эй капри", "hey capri", "привет капли"]:
        assert is_wake(s), s
    for s in ["привет", "капри", "брюки капри", "привет как дела", ""]:
        assert not is_wake(s), s


def test_auto_stop_logic():
    from capri.voice.stt import Recorder
    r = Recorder.__new__(Recorder)
    r.auto, r._elapsed, r._heard, r._quiet_for = True, 0.0, False, 0.0
    for _ in range(10):
        r.track(0.5, 0.1)          # секунда речи
    assert not r.should_stop
    for _ in range(13):
        r.track(0.01, 0.1)         # 1.3 с тишины
    assert r.should_stop
    r.auto, r._elapsed, r._heard, r._quiet_for = True, 0.0, False, 0.0
    for _ in range(61):
        r.track(0.0, 0.1)          # молчит 6 секунд — отбой
    assert r.should_stop


def test_state_persists(tmp_path):
    s = State(tmp_path / "state.json")
    assert s.get("language") == "ru"
    s.set("language", "en")
    s.set("input_device", 3)
    again = State(tmp_path / "state.json")
    assert again.get("language") == "en" and again.get("input_device") == 3
    assert json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))["language"] == "en"


def test_every_text_has_both_languages():
    for key, entry in TEXTS.items():
        assert entry.get("ru") and entry.get("en"), key
    assert t("mic_set", "en", name="USB") == "Microphone: USB"


def test_short_wake_word():
    assert is_wake("капри открой ютуб", short=True)
    assert is_wake("ну капри", short=True)
    assert not is_wake("капри открой ютуб", short=False)
    for s in ["капли воды", "это каприз", "капризная погода"]:
        assert not is_wake(s, short=True), s


def test_sentence_split():
    from capri.voice.tts import split_sentences
    parts = split_sentences("Ну привет. Опять ты? Ладно, говори, что надо, у меня процессы скучают. DNS — это книга. Всё.")
    assert len(parts) == 2 and parts[0].startswith("Ну привет.")
    assert split_sentences("Один") == ["Один"]


def test_strict_keyword_confidence_during_video():
    from capri.voice.wake import keyword_hit
    words = [{"word": "капри", "conf": 0.88}]
    assert keyword_hit(words, short=True)                       # обычно — хватает
    assert not keyword_hit(words, short=True, min_conf=0.93)    # во время видео — нужно увереннее
    assert keyword_hit([{"word": "капри", "conf": 0.97}], short=True, min_conf=0.93)
