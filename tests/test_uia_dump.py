"""Проверка uia_dump.py на поддельном дереве (без Windows и без Teams)."""
import sys, types, argparse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dolmetsch.tools import uia_dump as U

class Rect:
    left, top, right, bottom = 10, 20, 300, 400

class C:
    _n = 0
    def __init__(self, typ, name="", aid="", cls="", kids=(), pid=0, rid=True):
        C._n += 1
        self.ControlTypeName, self.Name, self.AutomationId, self.ClassName = typ, name, aid, cls
        self.kids, self.ProcessId, self.BoundingRectangle = list(kids), pid, Rect()
        self._rid = [42, C._n] if rid else None
    def GetChildren(self): return self.kids
    def GetRuntimeId(self):
        if self._rid is None: raise RuntimeError("нет")
        return self._rid
    def GetPropertyValue(self, pid): return "log" if pid == 30101 and "caption-list" in self.AutomationId else ""

def walk(c, includeTop=False, maxDepth=99, depth=0):
    if includeTop: yield c, depth
    if depth < maxDepth:
        for k in c.kids:
            yield from walk(k, True, maxDepth, depth + 1)

def build(rid=True):
    C._n = 0
    line1 = C("TextControl", "Guten Morgen zusammen, heute", rid=rid)
    timer = C("TextControl", "00:12:34", rid=rid)
    caps = C("ListControl", "", "caption-list", "fui-x", rid=rid, kids=[
        C("GroupControl", "", cls="item", rid=rid, kids=[C("TextControl", "Hans Müller", rid=rid), line1])])
    chat = C("GroupControl", "", "chat", rid=rid, kids=[C("TextControl", "СЕКРЕТНОЕ сообщение из чата про зарплату", rid=rid)])
    btn = C("ButtonControl", "Live-Untertitel deaktivieren", "cc-btn", rid=rid)
    win = C("WindowControl", "Daily mit Frau Schmidt | Microsoft Teams", cls="TeamsWebView", pid=7, rid=rid,
            kids=[C("PaneControl", rid=rid, kids=[chat, btn, timer, C("GroupControl", "", "stage", rid=rid, kids=[caps])])])
    other = C("WindowControl", "Notepad", pid=9, rid=rid)
    return win, other, caps, line1, timer

def run_case(tmp, rid, show):
    win, other, caps, line1, timer = build(rid)
    auto = types.SimpleNamespace(WalkControl=walk, GetRootControl=lambda: C("PaneControl", kids=[win, other]))
    old_proc, old_sleep = U.proc_name, U.time.sleep
    U.proc_name = lambda pid: {7: "ms-teams.exe", 9: "notepad.exe"}.get(pid, "")
    step = {"n": 0}
    def sleep(_):
        step["n"] += 1
        line1.Name += " sprechen wir über Active Directory."
        timer.Name = f"00:12:{35 + step['n']}"
        if step["n"] == 2:
            caps.kids.append(C("GroupControl", "", cls="item", rid=rid, kids=[
                C("TextControl", "Anna Becker", rid=rid), C("TextControl", "Ich habe eine Frage dazu.", rid=rid)]))
    U.time.sleep = sleep
    args = argparse.Namespace(snapshots=3, pause=1, max_nodes=8000, max_depth=70, show_text=show, also=[])
    logs = []
    try:
        path = U.run(auto, args, tmp, log=logs.append)
    finally:                                   # подмены не должны утечь в другие тесты
        U.proc_name, U.time.sleep = old_proc, old_sleep
    return path.read_text(encoding="utf-8"), logs

def test_hidden_text_and_container(tmp_path):
    for rid in (True, False):
        text, logs = run_case(tmp_path, rid, show=False)
        for secret in ("СЕКРЕТНОЕ", "зарплат", "Guten Morgen", "Hans", "Müller", "Anna", "Frage", "Schmidt", "Daily"):
            assert secret not in text, (rid, secret)
        assert "'Live-Untertitel deaktivieren'" in text and "★" in text
        assert "| Microsoft Teams" in text and "Notepad" not in text
        assert "вероятный блок субтитров" in text
        summary = text.split("## Надписи")[0]
        assert "ListControl[id='caption-list'" in summary.split("вероятный блок")[1].splitlines()[1], summary
        assert "повторное чтение этого блока" in summary
        assert "Δ2" in text and " +" in text
        assert "похоже на имя" in text and "конец предложения" in text and "время" in text
        assert any("блок субтитров найден" in l for l in logs)

def test_show_text_only_for_changed(tmp_path):
    text, _ = run_case(tmp_path, True, show=True)
    assert "Guten Morgen zusammen" in text and "Ich habe eine Frage" in text
    assert "СЕКРЕТНОЕ" not in text          # чат не менялся — остаётся скрытым

def test_no_teams(tmp_path):
    auto = types.SimpleNamespace(WalkControl=walk, GetRootControl=lambda: C("PaneControl", kids=[C("WindowControl", "Notepad", pid=9)]))
    old_proc = U.proc_name
    U.proc_name = lambda pid: "notepad.exe"
    args = argparse.Namespace(snapshots=1, pause=0, max_nodes=10, max_depth=5, show_text=False, also=[])
    try:
        assert U.run(auto, args, tmp_path, log=lambda *_: None) is None
    finally:
        U.proc_name = old_proc
