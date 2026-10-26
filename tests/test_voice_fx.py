import os

import numpy as np
import pytest

from capri.persona import system_prompt
from capri.voice.fx import PRESETS, apply


def tone(sr=24000, sec=1.0):
    t = np.arange(int(sr * sec)) / sr
    return (np.sin(2 * np.pi * 220 * t) * 12000).astype(np.int16)


def test_clean_is_untouched():
    a = tone()
    assert apply(a, 24000, PRESETS["clean"]) is a
    assert apply(a, 24000, None) is a


@pytest.mark.parametrize("name", list(PRESETS))
def test_presets_keep_level_and_type(name):
    a = tone()
    out = apply(a, 24000, PRESETS[name], seed=1)
    assert out.dtype == np.int16 and out.size >= a.size * 0.9
    assert np.abs(out).max() <= 32767
    assert np.abs(out).max() <= np.abs(a).max() * 1.1   # не громче оригинала, без хрипа


def test_echo_makes_it_longer():
    a = tone()
    assert apply(a, 24000, {"echo": 50}).size > a.size


def test_attitudes():
    rude = system_prompt("Игорь", "rude")
    assert "ГРУБАЯ" in rude and "Без мата" in rude and "Игорь" in rude
    assert "ГРУБАЯ" not in system_prompt("Игорь", "sassy")
    assert "МЯГКАЯ" in system_prompt("Игорь", "soft")


def test_voice_lab_dialog():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841
    from capri.ui.voice_lab import VoiceLab

    played = []
    tune = {"voices": {"ru": "ru-RU-SvetlanaNeural", "en": "en-US-AvaNeural"}, "rate": "+8%", "pitch": "+6Hz", "fx": {}}
    dlg = VoiceLab(tune, "ru", lambda tn, text, lang: played.append((tn, text, lang)))
    dlg.preset.setCurrentIndex(dlg.preset.findData("cyber"))
    dlg.rate.setValue(-10)
    dlg.voice.setCurrentText("ru-RU-DariyaNeural")
    dlg._test()
    tn, text, lang = played[0]
    assert lang == "ru" and text
    assert tn["rate"] == "-10%" and tn["pitch"] == "+6Hz"
    assert tn["voices"]["ru"] == "ru-RU-DariyaNeural" and tn["voices"]["en"] == "en-US-AvaNeural"
    assert tn["fx"] == PRESETS["cyber"]
    assert tune["voices"]["ru"] == "ru-RU-SvetlanaNeural"   # оригинал не испорчен до «Сохранить»


def test_music_detection_and_beat():
    from capri.music import Beat, is_music
    assert is_music("Chrome._crx_cinhimbnkkaeohfgghhklpknlkffjgod", "", "")          # приложение YouTube Music
    assert is_music("Spotify.exe", "", "")
    assert is_music("chrome.exe", "Discovery", "Daft Punk")                           # music.youtube.com во вкладке: есть альбом
    assert not is_music("chrome.exe", "", "Linus Tech Tips")                          # обычный ролик на YouTube
    assert is_music("chrome.exe", "", "", any_media=True)
    b = Beat()
    kicks = 0
    t = 0.0
    for i in range(200):                                  # 120 BPM: удар каждые 0.5 с, между ними тихо
        t += 0.05
        level, k = b.push(0.9 if i % 10 == 0 else 0.1, t)
        kicks += k
    assert 18 <= kicks <= 20 and 0 < level <= 1


def test_avatar_dance_and_effects():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from PySide6.QtGui import QPixmap
    from capri.ui.avatar import Avatar
    a = Avatar(120, "", 12, wander=False)
    a.set_dance(0.8)
    assert a.dance > 0
    a.kick()
    import math
    assert abs(abs(math.sin(a._dance_t)) - 1) < 1e-6        # на ударе — пик прыжка
    for _ in range(40):
        a.set_dance(0.0)
    assert a.dance == 0.0
    for st in Avatar.TP_STYLES:                            # каждый телепорт рисуется без ошибок
        a._start_teleport(("ground", None, 0, 0))
        a._tp_style = st
        pm = QPixmap(a.size())
        a.render(pm)
