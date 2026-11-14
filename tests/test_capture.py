"""Источники субтитров на поддельном дереве: структура окна Teams взята из разведки 14.11.2026."""
import types

from conftest import FakeAuto, Node, add_caption, caption_list
from dolmetsch.capture import uia
from dolmetsch.capture.selector import ASK_AFTER, Selector
from dolmetsch.capture.teams import TeamsSource
from dolmetsch.capture.wincaptions import WindowsCaptionsSource


def finals(events):
    return [(e.speaker, e.text) for e in events if e.kind == "final"]


def teams(win, get_proc):
    return TeamsSource(auto=FakeAuto(win), get_proc=get_proc)


def test_finds_caption_list_by_button_ids_not_by_words(teams_win, get_proc):
    teams_win.Name = "Daily: Netzwerk/DNS | Microsoft Teams"
    src = teams(teams_win, get_proc)
    assert src.find() and src.status == "ok"
    assert src._list is caption_list(teams_win)
    assert src.title == "Daily Netzwerk DNS"     # название встречи — для имени папки, без запрещённых знаков


def test_reads_newest_replies_not_oldest(teams_win, get_proc):
    """Ошибка Capri: читались первые строки истории и подпись ползунка. Здесь — только хвост."""
    src = teams(teams_win, get_proc)
    src.find()
    lst = caption_list(teams_win)
    assert src.poll(0) == []                     # первый снимок: что было на экране — не переводим
    add_caption(lst, "Hans Müller", "Guten Morgen zusammen. Wir")
    ev = src.poll(1)
    assert finals(ev) == [("Hans Müller", "Guten Morgen zusammen.")]
    assert all("Untertitelfenster" not in e.text for e in ev)


def test_fast_path_reads_only_current_text(teams_win, get_proc):
    src = teams(teams_win, get_proc)
    src.find()
    lst = caption_list(teams_win)
    src.poll(0)
    body = add_caption(lst, "Hans Müller", "Erster Satz")
    src.poll(1)
    calls = {"n": 0}
    original = Node.GetChildren

    def counting(self):
        calls["n"] += 1
        return original(self)
    Node.GetChildren = counting
    try:
        body.kids[1].Name = "Erster Satz ist fertig. Zweiter"
        ev = src.poll(1.3)
    finally:
        Node.GetChildren = original
    assert finals(ev) == [("Hans Müller", "Erster Satz ist fertig.")]
    assert calls["n"] == 0                       # реплики не перечитывались — прочитан один текст


def test_reply_with_name_only_waits_for_text(teams_win, get_proc):
    src = teams(teams_win, get_proc)
    src.find()
    lst = caption_list(teams_win)
    src.poll(0)
    body = Node("GroupControl", cls="fui-ChatMessageCompact__body x").add(Node("TextControl", "Hans Müller"))
    lst.add(body)
    assert finals(src.poll(1)) == []
    body.add(Node("TextControl", "Jetzt kommt der Text. Und"))
    assert finals(src.poll(1.3)) == [("Hans Müller", "Jetzt kommt der Text.")]


def test_chat_message_with_word_subtitles_is_not_a_caption_panel(get_proc):
    """9 ноября Capri приняла за субтитры сообщение в чате со словом «субтитров»."""
    chat = Node("WindowControl", "Chat | Microsoft Teams", cls="TeamsWebView", pid=7).add(
        Node("GroupControl", "закинуть гранолу с автопереводом субтитров", "message-body-1", "fui-ChatMyMessage__body").add(
            Node("TextControl", "закинуть гранолу с автопереводом субтитров")))
    src = teams(chat, get_proc)
    assert not src.find() and src.status == "no_panel"


def test_panel_without_replies_and_no_teams(teams_win, get_proc):
    lst = caption_list(teams_win)
    lst.kids = [k for k in lst.kids if not k.ClassName.startswith("fui-ChatMessageCompact__body")]
    src = teams(teams_win, get_proc)
    assert not src.find() and src.status == "panel_empty"
    src = TeamsSource(auto=FakeAuto(Node("WindowControl", "Notepad", pid=9)), get_proc=get_proc)
    assert not src.find() and src.status == "no_window"


def test_panel_closed_returns_none(teams_win, get_proc):
    src = teams(teams_win, get_proc)
    src.find()
    src.poll(0)
    caption_list(teams_win).dead = True
    assert src.poll(1) is None and src.status == "no_panel"


def test_fast_search_is_trusted_and_falls_back(teams_win):
    auto = FakeAuto(teams_win)
    ids = ["captions-panel-dismiss-button"]
    assert uia.find_by_ids_fast(auto, teams_win, ids) == (None, False)      # быстрый путь недоступен
    assert uia.find_by_ids(auto, teams_win, ids, allow_slow=False).AutomationId == ids[0]
    # быстрый путь сработал и ничего не нашёл — медленный не запускается, если его не разрешили
    client = types.SimpleNamespace(IUIAutomation=types.SimpleNamespace(
        CreatePropertyCondition=lambda *a: 1, CreateOrCondition=lambda *a: 1))
    auto._AutomationClient = types.SimpleNamespace(instance=lambda: client)
    auto.Control = types.SimpleNamespace(CreateControlFromElement=lambda e: e)
    teams_win.Element = types.SimpleNamespace(FindFirst=lambda scope, cond: None)
    assert uia.find_by_ids(auto, teams_win, ids, allow_slow=False) is None
    assert uia.find_by_ids(auto, teams_win, ids, allow_slow=True) is not None


# ---------- субтитры Windows
def live_window(text=""):
    block = Node("TextControl", text, "CaptionsTextBlock")
    return Node("WindowControl", "Live captions", pid=8).add(Node("PaneControl").add(block)), block


def test_windows_captions_source(get_proc):
    win, block = live_window("Vorher.")
    src = WindowsCaptionsSource(auto=FakeAuto(win), get_proc=get_proc)
    assert src.find() and src.poll(0) == []
    block.Name = "Vorher. Jetzt geht es los. Wir sehen"
    assert finals(src.poll(1)) == [("", "Jetzt geht es los.")]

    class Closed:                                 # окно субтитров закрыли: чтение бросает ошибку
        @property
        def Name(self):
            raise RuntimeError("элемент исчез")
    src._text = Closed()
    assert src.poll(2) is None and src.status == "no_window"


def test_windows_captions_fallback_to_longest_text(get_proc):
    win = Node("WindowControl", "Live captions", pid=8).add(
        Node("TextControl", "kurz"), Node("TextControl", "Das ist der lange Text der Untertitel."))
    src = WindowsCaptionsSource(auto=FakeAuto(win), get_proc=get_proc)
    assert src.find() and src._text.Name.startswith("Das ist")


# ---------- выбор источника
class Src:
    def __init__(self, name, status="no_window"):
        self.name, self.status, self.found, self.events, self.launched, self.title = name, status, False, [], 0, ""

    def find(self):
        return self.found

    def poll(self, now):
        return self.events

    def launch(self):
        self.launched += 1
        return True


def make():
    t, w, log = Src("Teams"), Src("Субтитры Windows"), []
    sel = Selector(t, w, on_event=log.append, on_state=lambda s, st: log.append((s, st)),
                   on_ask=lambda: log.append("ask"))
    return t, w, sel, log


def test_no_teams_asks_at_once_and_yes_launches_windows_captions():
    t, w, sel, log = make()
    sel.step(0)
    assert "ask" in log
    assert sel.answer(True) and w.launched == 1
    w.found = True
    sel.step(0.1)
    assert sel.active is w and log[-1] == ("Субтитры Windows", "ok")
    sel.step(10)
    assert log.count("ask") == 1


def test_teams_without_panel_asks_after_wait_and_no_is_remembered():
    t, w, sel, log = make()
    t.status = "no_panel"
    sel.step(0)
    sel.step(10)
    assert "ask" not in log
    sel.step(ASK_AFTER + 1)
    assert log.count("ask") == 1
    sel.answer(False)
    for now in range(30, 200, 10):
        sel.step(now)
    assert log.count("ask") == 1 and w.launched == 0


def test_panel_on_but_silent_does_not_ask():
    t, w, sel, log = make()
    t.status = "panel_empty"
    for now in range(0, 100, 5):
        sel.step(now)
    assert "ask" not in log


def test_open_windows_captions_are_used_without_asking_and_teams_wins_back():
    t, w, sel, log = make()
    w.found = True
    sel.step(0)
    assert sel.active is w and "ask" not in log
    t.found = True
    sel.step(1)
    assert sel.active is w                        # Teams перепроверяется не каждый шаг
    sel.step(10)
    assert sel.active is t


def test_lost_source_goes_back_to_search_and_polling_slows_in_silence():
    t, w, sel, log = make()
    t.found = True
    sel.step(0)
    assert sel.active is t and sel.interval == 0.3
    sel.step(20)
    assert sel.interval == 0.8
    t.events, t.found = None, False
    sel.step(21)
    assert sel.active is None and log[-1] == ("", "lost")


def test_after_loss_asks_again_only_while_teams_call_is_running():
    t, w, sel, log = make()
    t.found = True
    sel.step(0)
    t.events, t.found, t.status = None, False, "no_panel"     # субтитры пропали, звонок идёт
    sel.step(1)
    sel.step(2)
    assert "ask" not in log
    sel.step(2 + ASK_AFTER + 3)
    assert log.count("ask") == 1
    t2, w2, sel2, log2 = make()
    t2.found = True
    sel2.step(0)
    t2.events, t2.found, t2.status = None, False, "no_window"  # встреча закончилась, Teams закрыт
    for now in range(1, 120, 5):
        sel2.step(now)
    assert "ask" not in log2
