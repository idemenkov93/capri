"""Общее для тестов: поддельное дерево UI Automation (без Windows и без Teams)."""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
FIXTURES = Path(__file__).parent / "fixtures"


class Node:
    """Поддельный элемент окна: те же свойства и методы, что у пакета uiautomation."""
    _n = 0

    def __init__(self, typ, name="", aid="", cls="", pid=0):
        Node._n += 1
        self.ControlTypeName, self.Name, self.AutomationId, self.ClassName = typ, name, aid, cls
        self.ProcessId, self.kids, self.parent, self.rid = pid, [], None, [7, Node._n]
        self.dead = False

    def add(self, *kids):
        for k in kids:
            k.parent = self
            self.kids.append(k)
        return self

    def _alive(self):
        if self.dead:
            raise RuntimeError("элемент исчез")

    def GetChildren(self):
        self._alive()
        return list(self.kids)

    def GetParentControl(self):
        return self.parent

    def GetLastChildControl(self):
        self._alive()
        return self.kids[-1] if self.kids else None

    def GetPreviousSiblingControl(self):
        sibs = self.parent.kids
        i = sibs.index(self)
        return sibs[i - 1] if i > 0 else None

    def GetRuntimeId(self):
        return self.rid

    def find(self, pred):
        for c, _d in walk(self, True):
            if pred(c):
                return c


def walk(ctrl, includeTop=False, maxDepth=99, depth=0):
    if includeTop:
        yield ctrl, depth
    if depth < maxDepth:
        for k in list(ctrl.kids):
            yield from walk(k, True, maxDepth, depth + 1)


class FakeAuto:
    """Поддельный модуль uiautomation: рабочий стол с окнами."""

    def __init__(self, *windows):
        self.desktop = Node("PaneControl").add(*windows)

    def GetRootControl(self):
        return self.desktop

    def WalkControl(self, ctrl, includeTop=False, maxDepth=99):
        return walk(ctrl, includeTop, maxDepth)


def load_tree(name: str, pid: int = 7) -> Node:
    """Дерево из файла-образца: [глубина, тип, имя, id, класс]."""
    rows = json.loads((FIXTURES / name).read_text(encoding="utf-8"))["nodes"]
    stack = []
    for depth, typ, text, aid, cls in rows:
        node = Node(typ, text, aid, cls, pid)
        del stack[depth:]
        if stack:
            stack[-1].add(node)
        stack.append(node)
        if depth == 0:
            root = node
    return root


def caption_list(win: Node) -> Node:
    return win.find(lambda c: c.ClassName.startswith("fui-ChatMessageCompact__body")).parent


def add_caption(lst: Node, speaker: str, text: str) -> Node:
    """Добавить реплику так, как это делает Teams: аватар, затем блок с именем и текстом."""
    body = Node("GroupControl", cls="fui-ChatMessageCompact__body ___10lx575").add(
        Node("TextControl", speaker), Node("TextControl", text))
    lst.add(Node("GroupControl", cls="___87nhvx0 fau6ura"), body)
    return body


PROCS = {7: "ms-teams.exe", 8: "livecaptions.exe", 9: "notepad.exe"}


@pytest.fixture
def teams_win():
    return load_tree("teams_call.json")


@pytest.fixture
def get_proc():
    return lambda pid: PROCS.get(pid, "")


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


_status = {"code": 0}


def pytest_sessionfinish(session, exitstatus):
    _status["code"] = int(exitstatus)


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config):
    """Windows CI: выходим сразу после итогов. Иначе Qt иногда роняет процесс при завершении,
    хотя все тесты прошли (та же защита, что в Capri)."""
    if os.name == "nt" and os.environ.get("CI"):
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(_status["code"])
