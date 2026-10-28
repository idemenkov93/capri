"""Капри замечает, что ты запустил игру, и переходит в игровой режим.

Как узнаём игру (раз в 2 секунды, это микросекунды): смотрим, какая программа сейчас на переднем плане.
  * известные игры — по имени файла (cs2.exe, dota2.exe…);
  * остальные — по папке: steamapps\\common, Epic Games, Riot Games, GOG, Ubisoft, EA, Xbox;
  * лаунчеры, установщики, античиты, Wallpaper Engine и т.п. — не игры.
Игра «идёт», пока её процесс жив (даже если ты свернул её в Discord). Закрыл игру — режим выключается.

Два режима:
  strict — для CS2 (и того, что ты допишешь в config.yaml → games.strict): Капри прячется, выгружает модели
           из видеокарты, слушает только короткие команды (vosk на процессоре), ставит себе низкий приоритет;
  soft   — для остальных игр: Капри прячется и выгружает локальную модель, вопросы — через Claude.
Мы ничего не внедряем в игру и не жмём за тебя кнопки: только смотрим, какое окно активно.
"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field

# exe → (как называть, режим). Режим можно поменять в config.yaml.
KNOWN = {
    "cs2.exe": ("Counter-Strike 2", "strict"),
    "dota2.exe": ("Dota 2", "soft"),
    "valorant-win64-shipping.exe": ("Valorant", "soft"),
    "r5apex.exe": ("Apex Legends", "soft"),
    "r5apex_dx12.exe": ("Apex Legends", "soft"),
    "fortniteclient-win64-shipping.exe": ("Fortnite", "soft"),
    "league of legends.exe": ("League of Legends", "soft"),
    "overwatch.exe": ("Overwatch 2", "soft"),
    "rocketleague.exe": ("Rocket League", "soft"),
    "tslgame.exe": ("PUBG", "soft"),
    "rainbowsix.exe": ("Rainbow Six Siege", "soft"),
    "cyberpunk2077.exe": ("Cyberpunk 2077", "soft"),
    "gta5.exe": ("GTA V", "soft"),
    "gta5_enhanced.exe": ("GTA V", "soft"),
    "eldenring.exe": ("Elden Ring", "soft"),
    "witcher3.exe": ("Ведьмак 3", "soft"),
    "bg3.exe": ("Baldur's Gate 3", "soft"),
    "bg3_dx11.exe": ("Baldur's Gate 3", "soft"),
    "destiny2.exe": ("Destiny 2", "soft"),
    "eft.exe": ("Escape from Tarkov", "soft"),
    "escapefromtarkov.exe": ("Escape from Tarkov", "soft"),
    "deadlock.exe": ("Deadlock", "soft"),
    "project8.exe": ("Deadlock", "soft"),
    "helldivers2.exe": ("Helldivers 2", "soft"),
    "minecraft.windows.exe": ("Minecraft", "soft"),
}
GAME_DIRS = ("\\steamapps\\common\\", "\\epic games\\", "\\riot games\\", "\\gog galaxy\\games\\", "\\gog games\\",
             "\\ubisoft game launcher\\games\\", "\\ea games\\", "\\electronic arts\\", "\\xboxgames\\",
             "\\battle.net\\games\\", "\\wargaming.net\\games\\", "\\games\\")
# в папках игр живут и не-игры
NOT_GAMES = ("launcher", "crash", "setup", "unins", "redist", "dxsetup", "easyanticheat", "battleye", "beservice",
             "wallpaper32", "wallpaper64", "wallpaper engine", "steamvr", "vrmonitor", "vrserver", "vrcompositor",
             "losslessscaling", "obs64", "blender", "aseprite", "soundpad", "steam.exe", "steamwebhelper",
             "epicgameslauncher", "riotclient", "galaxyclient", "ubisoftconnect", "eadesktop", "origin.exe",
             "battle.net.exe", "agent.exe", "updater", "update.exe", "config", "settings", "report", "helper")


TOGGLE_TEXT = {   # ключ в data/state.json → (выключено, включено)
    "game_assist": ("Игровой помощник выключен: в играх веду себя как обычно.",
                    "Игровой помощник включён: замечу игру и не буду мешать."),
    "cs2_tournament": ("Турнирный режим выключен: таймер бомбы и подсказки снова работают.",
                       "Турнирный режим: в CS2 молчу — ни таймера бомбы, ни подсказок. Отвечаю только на команды."),
    "cs2_bomb_timer": ("Таймер бомбы выключен.", "Таймер бомбы включён."),
    "cs2_econ": ("Подсказки по экономике выключены.", "Подсказки по экономике включены."),
}


@dataclass
class GameInfo:
    exe: str            # cs2.exe
    name: str           # Counter-Strike 2
    mode: str           # strict | soft
    pid: int = 0
    path: str = ""
    started: float = field(default_factory=time.monotonic)

    @property
    def is_cs2(self) -> bool:
        return self.exe == "cs2.exe"


def _pretty(exe: str, path: str) -> str:
    """Имя игры по папке: ...\\steamapps\\common\\Hades II\\Hades2.exe → Hades II."""
    low = path.lower()
    for d in GAME_DIRS:
        i = low.find(d)
        if i >= 0:
            rest = path[i + len(d):].replace("/", "\\").split("\\")
            if rest and rest[0]:
                return rest[0]
    return os.path.splitext(exe)[0]


def classify(path: str, cfg: dict | None = None) -> GameInfo | None:
    """Путь к exe → игра или None. cfg — раздел games из config.yaml."""
    cfg = cfg or {}
    if not path:
        return None
    exe = os.path.basename(path.replace("/", "\\").split("\\")[-1]).lower()
    ignore = [str(x).lower() for x in cfg.get("ignore") or []]
    if exe in ignore:
        return None
    strict = [str(x).lower() for x in cfg.get("strict") or ["cs2.exe"]]
    extra = [str(x).lower() for x in cfg.get("extra") or []]
    if exe in KNOWN:
        name, mode = KNOWN[exe]
    elif exe in extra:
        name, mode = os.path.splitext(exe)[0], "soft"
    else:
        low = path.lower().replace("/", "\\")
        if not any(d in low for d in GAME_DIRS):
            return None
        if any(w in exe for w in NOT_GAMES) or not exe.endswith(".exe"):
            return None
        name, mode = _pretty(exe, path), "soft"
    if exe in strict:
        mode = "strict"
    return GameInfo(exe=exe, name=name, mode=mode, path=path)


# ------------------------------------------------------------------ Windows: какое окно впереди
def foreground() -> tuple[int, int, str]:
    """(окно, pid, путь к exe) программы на переднем плане."""
    if sys.platform != "win32":
        return 0, 0, ""
    import ctypes
    from ctypes import wintypes
    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    h = u.GetForegroundWindow()
    if not h:
        return 0, 0, ""
    pid = wintypes.DWORD()
    u.GetWindowThreadProcessId(h, ctypes.byref(pid))
    if pid.value == os.getpid():
        return h, pid.value, ""
    hp = k.OpenProcess(0x1000, False, pid.value)          # PROCESS_QUERY_LIMITED_INFORMATION
    if not hp:
        return h, pid.value, ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        path = buf.value if k.QueryFullProcessImageNameW(hp, 0, buf, ctypes.byref(size)) else ""
    finally:
        k.CloseHandle(hp)
    return h, pid.value, path


def alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        import psutil
        return psutil.pid_exists(pid)
    except ImportError:
        return True


def set_low_priority(low: bool) -> None:
    """Пока идёт CS2, Капри уступает процессор игре."""
    try:
        import psutil
        p = psutil.Process()
        if sys.platform == "win32":
            p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if low else psutil.NORMAL_PRIORITY_CLASS)
        else:
            p.nice(10 if low else 0)
    except Exception:  # noqa: BLE001 — нет psutil или нельзя поднять приоритет обратно — не страшно
        pass


class Watcher:
    """Опрос без Qt (Qt-таймер дергает poll()): возвращает ('start', game) / ('stop', game) / None."""

    def __init__(self, cfg: dict | None = None, fg=foreground, is_alive=alive):
        self.cfg = cfg or {}
        self.current: GameInfo | None = None
        self._fg, self._alive = fg, is_alive

    def poll(self):
        if self.current:
            if not self._alive(self.current.pid):
                g, self.current = self.current, None
                return "stop", g
            return None
        _h, pid, path = self._fg()
        g = classify(path, self.cfg)
        if g:
            g.pid = pid
            self.current = g
            return "start", g
        return None


def human_duration(seconds: float) -> str:
    m = int(seconds // 60)
    if m < 60:
        return f"{m} мин"
    return f"{m // 60} ч {m % 60:02d} мин"
