"""Субтитры прямо из Teams: когда в звонке включены «Live-Untertitel», Teams сам пишет, КТО говорит и ЧТО сказал.
Капри читает это окно (как экранный диктор, через UI Automation — только чтение, ничего не нажимает) и переводит.
Плюсы: имена настоящие, текст точнее, Whisper не грузит видеокарту — перевод приходит быстрее.

Нет субтитров в Teams (не включены / не Teams) — Капри сама слушает звук, как раньше.
Структуру окна Teams Microsoft меняет — поэтому при старте Капри сохраняет «скелет» окна в data\\teams_uia.txt
(только названия элементов, без звука и картинок) — он попадает в «собери отчёт», по нему чиним разбор.

Включить в Teams: во время звонка «…» → «Sprache und Sprachausgabe» → «Live-Untertitel aktivieren»
(или Ctrl+Shift+U в новом Teams).
"""
from __future__ import annotations

import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

CAPTION_RX = re.compile(r"live.?untertitel|untertitel|live.?caption|closed.?caption|captions?|субтитр", re.I)
TEAMS_PROCS = {"ms-teams.exe", "teams.exe", "msteams.exe"}
SENT_END = re.compile(r"[.!?…](?=\s|$)")


@dataclass
class Item:
    speaker: str
    text: str


@dataclass
class Event:
    kind: str           # partial | final
    speaker: str
    text: str


def _norm(s: str) -> str:
    return re.sub(r"\W+", " ", s.lower()).strip()


@dataclass
class Tracker:
    """Снимки окна субтитров (последние N реплик; последняя ещё дописывается) → события partial/final.
    Каждая фраза уходит в перевод один раз: когда реплика закончилась, текст не менялся stable секунд
    или набралось законченное предложение."""
    stable: float = 1.2
    long: int = 160                                  # длинный монолог режем по концу предложения
    cur: dict | None = None
    seen: deque = field(default_factory=lambda: deque(maxlen=80))

    def _key(self, it: Item) -> tuple[str, str]:
        return (it.speaker, _norm(it.text)[:60])

    def _same(self, it: Item) -> bool:
        c = self.cur
        if c is None or it.speaker != c["speaker"]:
            return False
        a, b = _norm(it.text), _norm(c["text"])
        n = min(len(a), len(b), 25)
        return n > 0 and a[:n] == b[:n]

    def _finish(self, text: str, out: list[Event], upto: int | None = None):
        c = self.cur
        end = len(text) if upto is None else upto
        rest = text[c["emitted"]:end].strip(" ,;:-–")
        if re.search(r"\w", rest):
            out.append(Event("final", c["speaker"], rest))
        c["emitted"] = max(c["emitted"], end)

    def update(self, items: list[Item], now: float | None = None) -> list[Event]:
        now = time.monotonic() if now is None else now
        items = [it for it in items if it.text.strip()]
        out: list[Event] = []
        c = self.cur
        if not items:
            if c and now - c["changed"] >= self.stable and len(c["text"]) > c["emitted"]:
                self._finish(c["text"], out)
            return out
        idx = next((i for i in range(len(items) - 1, -1, -1) if self._same(items[i])), None) if c else None
        if c is not None and idx is not None:
            it = items[idx]
            if idx == len(items) - 1:                         # всё ещё говорит
                if it.text != c["text"]:
                    c["text"], c["changed"] = it.text, now
                    # длинный монолог: законченные предложения — сразу в перевод
                    if len(it.text) - c["emitted"] > self.long:
                        ends = [m.end() for m in SENT_END.finditer(it.text) if m.end() > c["emitted"] + 20]
                        if ends:
                            self._finish(it.text, out, ends[-1])
                    out.append(Event("partial", it.speaker, it.text[c["emitted"]:].strip()))
                elif now - c["changed"] >= self.stable and len(c["text"]) > c["emitted"]:
                    self._finish(c["text"], out)
                    out.append(Event("partial", it.speaker, ""))
                return out
            self._finish(it.text, out)                        # реплика закончилась — дальше новые
            self.seen.append(self._key(it))
            new = items[idx + 1:]
        elif c is None:
            new = items[-2:]                                  # старт: старую историю не переводим
        else:                                                 # текущая уехала из окна / Teams её переписал
            self._finish(c["text"], out)
            self.seen.append((c["speaker"], _norm(c["text"])[:60]))
            new = [it for it in items if self._key(it) not in self.seen][-3:]
        new = [it for it in new if self._key(it) not in self.seen]
        if not new:
            self.cur = None
            return out
        for it in new[:-1]:
            out.append(Event("final", it.speaker, it.text.strip()))
            self.seen.append(self._key(it))
        last = new[-1]
        self.cur = {"speaker": last.speaker, "text": last.text, "emitted": 0, "changed": now}
        out.append(Event("partial", last.speaker, last.text.strip()))
        return out


def items_from_texts(groups: list[list[str]]) -> list[Item]:
    """Тексты по репликам: [[«Herr Müller», «Guten Morgen zusammen»], [«Ok, danke»]] → Item.
    Реплика без имени — продолжение предыдущего говорящего."""
    out: list[Item] = []
    for g in groups:
        g = [t.strip() for t in g if t and t.strip()]
        g = [t for i, t in enumerate(g) if i == 0 or t != g[i - 1]]
        if not g:
            continue
        if len(g) >= 2 and _looks_like_name(g[0]):
            out.append(Item(g[0], " ".join(g[1:])))
        elif out:
            out.append(Item(out[-1].speaker, " ".join(g)))
        else:
            out.append(Item("", " ".join(g)))
    return out


def _looks_like_name(s: str) -> bool:
    s = re.sub(r"\s*\((?:Gast|Guest|Extern|External|гость|[^)]{0,30})\)\s*$", "", s.strip())
    words = s.split()
    return 1 <= len(words) <= 5 and len(s) <= 60 and not SENT_END.search(s[-1:]) and \
        all(w[:1].isupper() or not w[:1].isalpha() or w.lower() in ("von", "van", "de", "der", "zu") for w in words)


def clean_name(s: str) -> str:
    return re.sub(r"\s*\((?:Gast|Guest|Extern|External|гость)\)\s*$", "", s.strip(), flags=re.I)


# ====================================================================== Windows: UI Automation
class TeamsCaptions:
    """Поток: ищет окно Teams с субтитрами, читает реплики, отдаёт события.
    on_event(Event), on_state(bool active) — из фонового потока."""

    def __init__(self, on_event: Callable[[Event], None], on_state: Callable[[bool], None] = lambda a: None,
                 dump_path: Path | None = None, interval: float = 0.35):
        self.on_event, self.on_state = on_event, on_state
        self.dump_path, self.interval = dump_path, interval
        self.stop_flag = threading.Event()
        self.active = False
        self.tracker = Tracker()
        self._thread: threading.Thread | None = None
        self._dumped = False
        self.error = ""

    def start(self):
        self._thread = threading.Thread(target=self._run, name="teams-captions", daemon=True)
        self._thread.start()

    def stop(self):
        self.stop_flag.set()

    def _set_active(self, on: bool):
        if on != self.active:
            self.active = on
            self.on_state(on)

    def _run(self):
        try:
            import uiautomation as auto
        except Exception as e:  # noqa: BLE001
            self.error = f"нет uiautomation: {e}"
            return
        with auto.UIAutomationInitializerInThread():
            box, last_seen, next_find, started = None, 0.0, 0.0, time.monotonic()
            while not self.stop_flag.is_set():
                now = time.monotonic()
                try:
                    if box is None and now >= next_find:
                        next_find = now + 3.0
                        box = self._find_box(auto)
                        if box is None and not self._dumped and now - started > 12:
                            self._dump(auto, None)            # Teams есть, а субтитров не нашла — сохраним скелет
                    if box is not None:
                        groups = self._read(auto, box)
                        if groups is None:                    # окно закрылось / перестроилось
                            box = None
                        else:
                            last_seen = now
                            self._set_active(True)
                            for ev in self.tracker.update(items_from_texts(groups), now):
                                self.on_event(ev)
                    if self.active and now - last_seen > 20:
                        self._set_active(False)               # субтитры выключили — снова слушаем звук
                except Exception as e:  # noqa: BLE001 — UIA капризный: пробуем дальше
                    self.error = str(e)[:200]
                    box = None
                self.stop_flag.wait(self.interval)

    # ---------- поиск
    def _teams_windows(self, auto) -> list:
        out = []
        try:
            import psutil
        except Exception:  # noqa: BLE001
            psutil = None
        for w in auto.GetRootControl().GetChildren():
            name = w.Name or ""
            proc = ""
            if psutil is not None:
                try:
                    proc = psutil.Process(w.ProcessId).name().lower()
                except Exception:  # noqa: BLE001
                    proc = ""
            if proc in TEAMS_PROCS or "microsoft teams" in name.lower():
                out.append(w)
        return out

    def _find_box(self, auto):
        for w in self._teams_windows(auto):
            seen = 0
            for c, depth in auto.WalkControl(w, maxDepth=60):
                seen += 1
                if seen > 6000:
                    break
                label = f"{c.Name or ''} {c.AutomationId or ''} {c.ClassName or ''}"
                if CAPTION_RX.search(label) and c.ControlTypeName not in ("ButtonControl", "MenuItemControl",
                                                                         "CheckBoxControl", "TextControl"):
                    if self._read(auto, c):
                        if not self._dumped:
                            self._dump(auto, c, w)
                        return c
        return None

    # ---------- чтение
    def _read(self, auto, box) -> list[list[str]] | None:
        try:
            kids = box.GetChildren()
        except Exception:  # noqa: BLE001
            return None
        # контейнер-обёртка с одним ребёнком — спускаемся до списка реплик
        for _ in range(6):
            if len(kids) == 1:
                kids = kids[0].GetChildren()
            else:
                break
        groups = []
        for k in kids[-12:]:
            texts = []
            for c, depth in auto.WalkControl(k, includeTop=True, maxDepth=8):
                if c.ControlTypeName in ("TextControl", "HyperlinkControl") and c.Name:
                    texts.append(c.Name)
                if len(texts) > 12:
                    break
            if not texts and k.Name:
                texts = [k.Name]
            if texts:
                groups.append(texts)
        return groups

    def _dump(self, auto, box, win=None):
        """Скелет окна Teams → data/teams_uia.txt (только типы и названия элементов)."""
        if self.dump_path is None:
            return
        self._dumped = True
        lines = [f"# Teams UIA {time.strftime('%Y-%m-%d %H:%M')} — субтитры {'найдены' if box else 'НЕ найдены'}"]
        roots = [box] if box is not None else self._teams_windows(auto)
        if win is not None:
            lines.append(f"окно: {win.Name!r} {win.ClassName!r}")
        n = 0
        for r in roots:
            for c, depth in auto.WalkControl(r, includeTop=True, maxDepth=60):
                n += 1
                if n > 5000:
                    break
                name = c.Name or ""
                if box is None and c.ControlTypeName in ("TextControl", "EditControl", "DocumentControl"):
                    name = f"<текст {len(name)} симв.>" if name else ""   # чаты и сообщения в отчёт не попадают
                lines.append(f"{'  ' * min(depth, 30)}{c.ControlTypeName} name={name[:90]!r} "
                             f"id={(c.AutomationId or '')[:60]!r} cls={(c.ClassName or '')[:60]!r}")
        try:
            self.dump_path.parent.mkdir(parents=True, exist_ok=True)
            self.dump_path.write_text("\n".join(lines), encoding="utf-8")
        except OSError:
            pass
