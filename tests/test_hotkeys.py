"""1.9: горячие клавиши — таблица действий, сочетания без пересечений, переезд со старых, точное совпадение."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from capri import hotkeys as H  # noqa: E402


def test_defaults_have_no_conflicts_and_all_parse():
    keys = H.resolve(None)
    assert set(keys) == {a.id for a in H.ACTIONS}
    assert H.problems(keys) == {}
    assert all(H.parse(v) for v in keys.values())
    assert len(set(keys.values())) == len(keys)


def test_defaults_match_config():
    from capri.config import DEFAULTS
    hk = DEFAULTS["hotkeys"]
    assert {a.id: a.default for a in H.ACTIONS} == {k: v for k, v in hk.items() if k != "enabled"}


def test_old_defaults_move_to_new_but_own_choice_stays():
    old = {"translate": "Ctrl+Shift+T", "chat": "ctrl+shift+space", "convert": "Ctrl+Shift+Q", "answer": "Ctrl+Shift+1"}
    keys = H.resolve(old)
    assert keys["translate"] == "Win+Alt+X" and keys["chat"] == "Win+Alt+O" and keys["convert"] == "Win+Alt+V"
    assert H.resolve({"translate": "Ctrl+Shift+T", "v": 2})["translate"] == "Ctrl+Shift+T"      # выбрал сам в таблице
    assert H.resolve({"translate": "Win+Alt+Y"})["translate"] == "Win+Alt+Y"
    assert H.resolve({"mentor": ""})["mentor"] == ""                                            # выключено


def test_norm():
    assert H.norm("alt + win + x") == "Win+Alt+X"
    assert H.norm("win+ctrl") == "Ctrl+Win"
    assert H.norm("ctrl+shift+f5") == "Ctrl+Shift+F5"


def test_problems_found():
    keys = H.resolve(None)
    keys.update(translate="Ctrl+Shift+T", explain="Win+Alt+C", screen="Win+Alt+R", mentor="X", hint="Win+Alt+Банан")
    p = H.problems(keys)
    assert "закрытую вкладку" in p["translate"]
    assert "уже занято" in p.get("explain", "") + p.get("clipboard", "")
    assert "Game Bar" in p["screen"]
    assert "Ctrl, Alt или Win" in p["mentor"]
    assert p["hint"] == "непонятная клавиша"


def test_exact_match_ignores_extra_modifiers():
    down = {0x5B, 0x12, 0x58}                                      # Win+Alt+X
    assert H.HotkeyPoller.exact(H.parse("Win+Alt+X"), lambda v: v in down)
    assert not H.HotkeyPoller.exact(H.parse("Win+Alt+X"), lambda v: v in down | {0x10})       # + Shift — не наше
    assert not H.HotkeyPoller.exact(H.parse("Alt+X"), lambda v: v in down)                     # + Win — не наше
    assert not H.HotkeyPoller.exact([], lambda v: True)


def test_poller_set_keys_live():
    p = H.HotkeyPoller(H.resolve(None))
    assert p._codes["translate"] == H.parse("Win+Alt+X") and p.dict_keys == H.parse("Ctrl+Win")
    p.set_keys({**H.resolve(None), "translate": "", "dictate": "Ctrl+Win"})
    assert "translate" not in p._codes


def test_dialog_capture_and_save():
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication
    from capri.ui.hotkeys_dialog import HotkeysDialog
    QApplication.instance() or QApplication([])
    d = HotkeysDialog(H.resolve(None))
    got = []
    d.saved.connect(got.append)
    ed = d.edits["translate"]
    ed.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Y, Qt.MetaModifier | Qt.AltModifier))
    assert ed.text() == "Win+Alt+Y"
    d.edits["explain"].keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_C, Qt.MetaModifier | Qt.AltModifier))
    assert "уже занято" in d.warns["explain"].text() + d.warns["clipboard"].text() and not d.b_save.isEnabled()
    d._reset("explain")
    assert d.b_save.isEnabled()
    d.edits["mentor"].keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Backspace, Qt.NoModifier))
    d._save()
    assert got[0]["translate"] == H.norm("Win+Alt+Y") and got[0]["mentor"] == "" and got[0]["explain"] == "Win+Alt+E"


@pytest.mark.parametrize("text,word", [("Gruppenrichtlinie", True), ("die Freigabe", True), ("Active Directory (AD)", True),
                                       ("Das ist ein langer Satz mit vielen Wörtern.", False), ("a\nb", False)])
def test_is_word(text, word):
    from capri.textui import is_word
    assert is_word(text) is word


def test_chat_command_opens_table():
    from capri import agent as AG
    r = AG.Agent._text_cmd(object.__new__(AG.Agent), "настрой горячие клавиши")
    assert r.actions[0].kind == "hotkeys"
    assert AG.Agent._text_cmd(object.__new__(AG.Agent), "хоткейсы").actions[0].kind == "hotkeys"
