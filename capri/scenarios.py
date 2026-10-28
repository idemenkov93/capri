"""Сценарии: одной фразой — несколько действий. «Игровой режим», «ночной режим», «режим работы».

Сценарии лежат в data/scenarios.yaml (создаётся при первом запуске из DEFAULT ниже) — их можно править
и добавлять свои. Шаги выполняются по порядку:

    - open: discord        # программа (как в «открой …») или сайт
      screen: 2            # 1 — основной экран, 2 — второй
      place: left          # max | left | right | top | bottom | center
    - browser: [https://www.youtube.com, https://www.netflix.com]   # одно окно браузера, вкладки по порядку
    - volume: 20           # громкость, %
    - brightness: 40       # яркость, %
    - nightlight: on       # стандартный «Ночной свет» Windows: on | off
    - break_reminder: 180  # напоминать отдохнуть каждые N минут (0 — выключить)
    - say: "текст"         # что сказать, когда всё готово
    - display: external    # как Win+P: extend | internal | external | clone
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

import yaml

DEFAULT_TEXT = """# Сценарии Капри: скажи «Капри, игровой режим» — и она сделает всё по списку.
# Можно менять и добавлять свои. После правки перезапусти Капри (или скажи «перечитай сценарии»).
# Шаги:
#   open: программа или сайт    screen: 1 (основной) / 2 (второй)    place: max | left | right | top | bottom | center
#   browser: [ссылка1, ссылка2]  — одно окно браузера, вкладки по порядку
#   volume: 20 · brightness: 40 · nightlight: on/off · break_reminder: 180 (минут, 0 — выключить) · say: "текст"
#   display: extend (оба экрана) | internal (только основной) | external (только второй) | clone — как Win+P
# Если в сценарии есть screen: 2, а второй экран выключен через Win+P, — Капри сама включит оба экрана.

игровой:
  phrases: [игровой режим, режим игры, гейминг, поиграем, game mode]
  steps:
    - open: discord
      screen: 2
      place: left
    - open: telegram
      screen: 2
      place: right
    - open: steam
      screen: 1
      place: max
    - say: "Игровой режим. Дискорд и телеграм на втором экране, Стим на основном. Удачной катки, чумба."

ночной:
  phrases: [ночной режим, режим ночи, вечерний режим, night mode]
  off_phrases: [выключи ночной режим, дневной режим, отключи ночной режим]
  steps:
    - display: external      # основной экран гаснет, остаётся только второй
    - nightlight: on
    - volume: 20
    - browser: [https://www.youtube.com, https://www.netflix.com]
    - say: "Ночной режим. Основной экран погасила, второй тёплый, звук 20, ютуб и нетфликс открыты. Не засиживайся."
  off_steps:
    - display: extend
    - nightlight: off
    - say: "Ночной режим выключила: оба экрана снова работают."

работа:
  phrases: [режим работы, рабочий режим, работаем, за работу, work mode]
  off_phrases: [выключи режим работы, хватит работать, конец работы, закончили работу]
  steps:
    - open: youtube music
      screen: 2
      place: max
    - open: vs code
      screen: 1
      place: max
    - break_reminder: 180
    - say: "Режим работы. Музыка на втором экране, VS Code на основном. Каждые три часа напомню размяться."
  off_steps:
    - break_reminder: 0
    - say: "Режим работы выключила. Напоминания про отдых больше не будет."
"""

DEFAULT = yaml.safe_load(DEFAULT_TEXT)
# файл с примерами из прошлой версии (без шагов display) — если его не меняли, обновляем сами
OLD_DEFAULTS = {"7f67238c0e41d7f4"}


def path() -> Path:
    from .config import expand
    return expand("data/scenarios.yaml")


def load() -> dict:
    p = path()
    try:
        import hashlib
        if p.exists() and hashlib.sha256(p.read_text(encoding="utf-8").encode()).hexdigest()[:16] in OLD_DEFAULTS:
            p.write_text(DEFAULT_TEXT, encoding="utf-8")
    except OSError:
        pass
    if not p.exists():
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(DEFAULT_TEXT, encoding="utf-8")
        except OSError:
            return DEFAULT
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return data if isinstance(data, dict) else DEFAULT
    except (OSError, yaml.YAMLError):
        return DEFAULT


def _norm(text: str) -> str:
    t = text.lower().replace("ё", "е")
    t = re.sub(r"^\s*(?:привет\s*,?\s*)?(?:капри|capri)[\s,!.:-]*", "", t)
    t = re.sub(r"[.!?,]+", " ", t)
    t = re.sub(r"^(?:включи|запусти|активируй|давай|врубай|вруби|start|activate)\s+", "", t.strip())
    t = re.sub(r"^(?:в\s+)?", "", t)
    return " ".join(t.split())


def match(text: str, data: dict | None = None) -> tuple[str, bool] | None:
    """«включи игровой режим» → ("игровой", True); «выключи ночной режим» → ("ночной", False)."""
    data = data if data is not None else load()
    raw = " ".join(re.sub(r"[.!?,]+", " ", text.lower().replace("ё", "е")).split())
    raw = re.sub(r"^(?:привет\s+)?(?:капри|capri)\s+", "", raw)
    t = _norm(text)
    for name, sc in data.items():
        if not isinstance(sc, dict):
            continue
        for ph in sc.get("off_phrases") or []:
            if raw == ph.lower() or raw.endswith(" " + ph.lower()):
                return name, False
        if re.match(r"^(?:выключи|выключить|выключай|отключи|отключить|останови|stop|disable)\s+", raw):
            if any(raw.endswith(ph.lower()) for ph in sc.get("phrases") or [name]):
                return name, False
            continue
        for ph in sc.get("phrases") or [f"{name} режим"]:
            if t == ph.lower():
                return name, True
    return None


# ------------------------------------------------------------------ выполнение шагов
def browser_exe() -> str | None:
    """Chrome (или Edge) — чтобы открыть несколько вкладок одним окном."""
    if sys.platform == "win32":
        import winreg
        for exe in ("chrome.exe", "msedge.exe"):
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}") as k:
                        return winreg.QueryValue(k, None)
                except OSError:
                    continue
    return shutil.which("chrome") or shutil.which("google-chrome") or shutil.which("msedge")


class Runner:
    """Выполняет сценарий в рабочем потоке Капри. resolve(name) → Action для «открой …» (берётся у агента)."""

    def __init__(self, resolve, system, execute):
        self.resolve, self.system, self.execute = resolve, system, execute
        self.notes: list[str] = []            # что Капри поменяла сама (включила второй экран и т.п.)

    def run(self, steps: list) -> tuple[list[str], list[str], int | None, str]:
        """→ (что сделано, что не вышло, напоминание в минутах или None, что сказать)."""
        done, failed, reminder, say = [], [], None, ""
        notes = []
        from . import control
        wants_two = any(isinstance(st, dict) and int(st.get("screen") or 1) >= 2 for st in steps or [])
        switches = any(isinstance(st, dict) and "display" in st for st in steps or [])
        if wants_two and not switches:
            try:
                if len(control.monitors()) < 2:
                    if control.connected_displays() >= 2:
                        control.switch_display("extend")      # второй экран выключен через Win+P — включаем
                        notes.append("включила второй экран")
                    else:
                        notes.append("второго экрана нет — разложила всё на одном")
            except Exception:  # noqa: BLE001
                pass
        for step in steps or []:
            if not isinstance(step, dict):
                continue
            try:
                if "open" in step:
                    title = self._open(str(step["open"]), int(step.get("screen", 1)), str(step.get("place", "max")))
                    done.append(title)
                elif "browser" in step:
                    urls = step["browser"] if isinstance(step["browser"], list) else [step["browser"]]
                    self._browser([str(u) for u in urls], step.get("screen"), str(step.get("place", "max")))
                    done.append("вкладки: " + ", ".join(re.sub(r"^https?://(www\.)?", "", u).split("/")[0] for u in urls))
                elif "display" in step:
                    mode = str(step["display"]).lower()
                    if mode not in ("extend", "internal", "external", "clone"):
                        raise RuntimeError(f"не знаю режим экрана «{mode}»")
                    if mode in ("external", "extend") and control.connected_displays() < 2 and len(control.monitors()) < 2:
                        failed.append("второй экран не подключён — оставила как есть")
                    else:
                        control.switch_display(mode)
                        done.append({"extend": "оба экрана", "internal": "только основной экран",
                                     "external": "только второй экран", "clone": "экраны дублируются"}[mode])
                elif "volume" in step:
                    from .commands import Command
                    self.system.run(Command("volume_set", float(step["volume"])))
                    done.append(f"громкость {step['volume']}%")
                elif "brightness" in step:
                    from .commands import Command
                    self.system.run(Command("brightness_set", float(step["brightness"])))
                    done.append(f"яркость {step['brightness']}%")
                elif "nightlight" in step:
                    on = str(step["nightlight"]).lower() in ("on", "true", "1", "вкл", "да", "yes")
                    from . import control
                    if control.night_light(on):
                        done.append("ночной свет " + ("вкл" if on else "выкл"))
                    else:
                        failed.append("ночной свет (открыла настройки — нажми переключатель)")
                elif "break_reminder" in step:
                    reminder = int(step["break_reminder"] or 0)
                elif "say" in step:
                    say = str(step["say"])
            except Exception as e:  # noqa: BLE001 — один шаг не ломает весь сценарий
                failed.append(f"{next(iter(step))}: {e}")
        self.notes = notes
        return done, failed, reminder, say

    def _open(self, name: str, screen: int, place: str) -> str:
        from . import control
        before = control.find_app_window(name, strict=True)
        act = self.resolve(name)
        if act is None:
            raise RuntimeError(f"не знаю, что такое «{name}»")
        if not before:
            self.execute(act)
        h = before or control.wait_window(name, timeout=25)
        if not h:
            raise RuntimeError(f"{name} не открылся за 25 секунд")
        control.place_window(h, screen, place)
        if not before:
            # Discord и Steam сначала показывают маленькое окно загрузки, потом открывают главное — ставим и его
            time.sleep(3.0)
            h2 = control.find_app_window(name, strict=True)
            if h2 and h2 != h:
                control.place_window(h2, screen, place)
                h = h2
        return control.window_title(h) or name

    def _browser(self, urls: list[str], screen, place: str) -> None:
        exe = browser_exe()
        if not exe:
            for u in urls:
                webbrowser.open_new_tab(u)
            return
        subprocess.Popen([exe, "--new-window", *urls])
        if screen:
            from . import control
            time.sleep(2.0)
            h = control.find_app_window("chrome" if "chrome" in exe.lower() else "msedge.exe", strict=True)
            if h:
                control.place_window(h, int(screen), place)
