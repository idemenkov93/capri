"""Источник А: субтитры Teams (Live-Untertitel) через UI Automation.

Как устроена панель (разведка 14.11.2026, новый Teams):
  панель «Liveuntertitel»
    ├─ кнопки с постоянными id: captions-panel-dismiss-button, closed-captions-pop-out-button, …
    └─ список реплик
         └─ реплика: группа с классом fui-ChatMessageCompact__body…
              ├─ текст 1: имя говорящего
              └─ текст 2: что сказал (дописывается, до ~600 символов)

Главное правило скорости: читаем только ХВОСТ списка (последние реплики), а не всю историю.
Правила поиска лежат в PROFILE — поменяет Microsoft вёрстку, правим только его.
"""
from __future__ import annotations

import re
import time

from . import uia
from .base import Event, Item
from .tracker import Tracker

PROFILE = {
    "procs": ["ms-teams.exe", "teams.exe", "msteams.exe"],
    # кнопки панели субтитров: id не зависят от языка Teams
    "anchor_ids": ["captions-panel-dismiss-button", "closed-captions-pop-out-button",
                   "captions-settings-menu-trigger-button-non-overflow"],
    "item_class": "fui-ChatMessageCompact__body",
    "tail": 3,                      # сколько последних реплик читаем
}


class TeamsSource:
    name = "Teams"

    def __init__(self, profile: dict | None = None, auto=None, get_proc=None):
        self.profile = profile or PROFILE
        self.auto = auto                     # подставляется в потоке чтения (или в тестах)
        self.get_proc = get_proc
        self.tracker = Tracker()
        self.status = "no_window"            # no_window | no_panel | panel_empty | ok
        self.title = ""                      # название встречи — для имени папки с текстом
        self.read_ms: list[float] = []       # сколько длилось каждое чтение (для журнала)
        self._list = None                    # список реплик
        self._last_rid = None                # «номер» последнего элемента списка
        self._older: list[Item] = []         # реплики перед текущей (уже не меняются)
        self._cur_speaker = ""
        self._cur_text = None                # элемент с текстом текущей реплики
        self._full_at = 0.0
        self._attempt = 0

    # ---------- поиск
    def windows(self) -> list:
        return uia.top_windows(self.auto, set(self.profile["procs"]), self.get_proc)

    def find(self) -> bool:
        """Найти список реплик. Дорогая операция: вызывается раз в несколько секунд."""
        self._attempt += 1
        self._drop()
        wins = self.windows()
        if not wins:
            self.status = "no_window"
            return False
        self.status = "no_panel"
        for win in wins:
            # медленный обход всего окна — не каждый раз, а на первой и каждой третьей попытке
            anchor = uia.find_by_ids(self.auto, win, self.profile["anchor_ids"],
                                     allow_slow=self._attempt % 3 == 1)
            if anchor is None:
                continue
            self.status = "panel_empty"      # панель включена, но реплик ещё нет
            node = anchor
            for _ in range(6):               # поднимаемся от кнопки, пока внутри не найдётся реплика
                node = uia.safe(lambda: node.GetParentControl())
                if node is None:
                    break
                item = uia.first_with_class(self.auto, node, self.profile["item_class"])
                if item is not None:
                    self._list = uia.safe(lambda: item.GetParentControl())
                    if self._list is not None:
                        self.status = "ok"
                        self.title = self._title(win)
                        return True
        return False

    @staticmethod
    def _title(win) -> str:
        name = str(uia.safe(lambda: win.Name, ""))
        parts = [p.strip() for p in name.split("|")]
        if len(parts) >= 2 and "teams" in parts[-1].lower():
            name = " | ".join(parts[:-1])
        return re.sub(r"\s+", " ", re.sub(r'[\\/:*?"<>|]+', " ", name)).strip()[:60]

    def _drop(self) -> None:
        self._list = self._last_rid = self._cur_text = None
        self._older = []

    # ---------- чтение
    def _item(self, ctrl) -> Item | None:
        names = [str(uia.safe(lambda: k.Name, "")).strip() for k in ctrl.GetChildren()]
        names = [n for n in names if n]
        if len(names) < 2:                   # пока есть только имя — текста ещё нет, ждём
            return None
        return Item(names[0], " ".join(names[1:]))

    def _read_tail(self) -> list[Item] | None:
        """Полное чтение хвоста: последние N реплик, от старых к новым."""
        prefix, found = self.profile["item_class"], []
        ctrl, steps = self._list.GetLastChildControl(), 0
        if ctrl is None:
            return None
        self._last_rid = ctrl.GetRuntimeId()
        while ctrl is not None and len(found) < self.profile["tail"] and steps < 12:
            if str(ctrl.ClassName or "").startswith(prefix):
                found.append(ctrl)
            ctrl, steps = ctrl.GetPreviousSiblingControl(), steps + 1
        if not found:
            return None
        items = [it for it in (self._item(c) for c in reversed(found)) if it]
        # запоминаем элемент с текстом последней реплики: дальше читаем только его
        kids = found[0].GetChildren()
        newest_has_text = len(kids) >= 2 and bool(items)
        self._cur_text = kids[-1] if newest_has_text else None
        self._cur_speaker = items[-1].speaker if items else ""
        self._older = items[:-1] if newest_has_text else items
        return items

    def _read(self, now: float) -> list[Item] | None:
        """Быстрый путь: если новых реплик нет — читаем один текст (3 запроса вместо 20)."""
        last = self._list.GetLastChildControl()
        if last is None:
            return None
        if (self._cur_text is not None and now - self._full_at < 5.0
                and last.GetRuntimeId() == self._last_rid):
            text = str(self._cur_text.Name or "").strip()
            if text:
                return self._older + [Item(self._cur_speaker, text)]
        self._full_at = now
        return self._read_tail()

    def poll(self, now: float | None = None) -> list[Event] | None:
        """Один опрос. None — панель пропала, нужно искать заново."""
        now = time.monotonic() if now is None else now
        if self._list is None:
            return None
        started = time.perf_counter()
        try:
            items = self._read(now)
        except Exception:  # noqa: BLE001 — элемент исчез, Teams перестроил панель
            items = None
        self.read_ms.append((time.perf_counter() - started) * 1000)
        if items is None:
            self._drop()
            self.status = "no_panel"
            return None
        return self.tracker.update(items, now)
