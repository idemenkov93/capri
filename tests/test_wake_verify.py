"""Ложные «Капри» (учитель на уроке, видео) и громкая музыка во время команды."""
import os

import numpy as np

from capri.voice import duck
from capri.voice.stt import Transcriber

KEEP = []


class Seg:
    def __init__(self, text):
        self.text = text


class Model:
    def __init__(self, text):
        self.text = text

    def transcribe(self, audio, **k):
        return [Seg(self.text)], None


def test_whisper_double_checks_the_name():
    t = Transcriber()
    for heard, ok in (("Капри, следующий трек", True), ("Каприо", True), ("Привет, Капри!", True),
                      ("ist wie gesagt gar nicht so kompliziert", False), ("капитан сказал", False), ("Adresse geändert", False)):
        t._model = Model(heard)
        assert t.heard_name(np.zeros(16000 * 2, np.float32))[0] is ok, heard


class Vol:
    def __init__(self, v):
        self.v = v

    def GetMasterVolume(self):
        return self.v

    def SetMasterVolume(self, v, ctx):
        self.v = v


class Sess:
    def __init__(self, v):
        self.SimpleAudioVolume = Vol(v)


def test_music_is_quieter_while_listening(monkeypatch):
    music, video = Sess(0.8), Sess(0.5)
    monkeypatch.setattr(duck.Ducker, "_sessions", staticmethod(lambda: [music, video]))
    d = duck.Ducker()
    d.duck()
    d._pool.submit(lambda: None).result()
    assert round(music.SimpleAudioVolume.v, 2) == 0.2 and round(video.SimpleAudioVolume.v, 3) == 0.125
    video.SimpleAudioVolume.v = 0.9                  # сам сделал видео громче, пока я слушала, — не трогаю
    d.close()
    assert music.SimpleAudioVolume.v == 0.8 and video.SimpleAudioVolume.v == 0.9


class FakeVoice:
    def __init__(self, heard):
        self.stt = Transcriber()
        self.stt._model = Model(heard)
        self.recording = False
        self.log = []

    def quiet(self):
        pass

    def listen_once(self):
        self.recording = True
        self.log.append("listen")

    def cancel_record(self):
        self.recording = False
        self.log.append("cancel")

    def chime(self):
        self.log.append("chime")

    def _run(self, fn, ok, fail=None):
        ok(fn())


def _app(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    import capri.app as appmod
    from capri.applist import AppIndex
    monkeypatch.setattr(appmod.CapriApp, "_init_voice", lambda self: None)
    monkeypatch.setattr(appmod.CapriApp, "start_dancing", lambda self: None)
    monkeypatch.setattr(AppIndex, "refresh", lambda self: 0)
    app = appmod.CapriApp()
    KEEP.append(app)
    app.warmup.wait(10_000)
    app.game._poll_timer.stop()

    class Wake:
        last_audio = np.zeros(16000 * 3, np.float32)
    app.wake = Wake()
    asked = []
    monkeypatch.setattr(app, "on_message", asked.append)
    return app, asked


def test_false_wake_is_dropped(monkeypatch):
    app, asked = _app(monkeypatch)
    app.voice = FakeVoice("ist wie gesagt gar nicht so kompliziert")
    app.on_wake("капри")
    assert app.voice.log == ["listen", "cancel"]            # без «дзынь» и без ответа
    app.on_transcribed("Gleichzeitig starten wir wieder.", "de")
    assert not asked
    app.voice = FakeVoice("Капри, следующий трек")
    app.on_wake("капри")
    assert app.voice.log == ["listen", "chime"]
    app.on_transcribed("Следующий трек.", "ru")
    assert asked == ["Следующий трек."]
