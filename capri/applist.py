"""Какие программы есть на компьютере и как понять «открой ютуб мюзик» или «открой дискорд».

Список берётся из меню «Пуск» (PowerShell Get-StartApps: обычные программы, приложения из Store и сайты-приложения
вроде YouTube Music) и кэшируется в data/apps.json. Запуск — через shell:AppsFolder, как из самого «Пуск».
"""
from __future__ import annotations

import difflib
import json
import re
import subprocess
import sys
from pathlib import Path

NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

# как это обычно говорят по-русски → как пишется в названии программы
ALIASES = {
    "ютуб": "youtube", "ютьюб": "youtube", "мюзик": "music", "музыка": "music", "стим": "steam", "дискорд": "discord",
    "телеграм": "telegram", "телеграмм": "telegram", "хром": "chrome", "гугл": "google", "фаерфокс": "firefox",
    "файрфокс": "firefox", "мозилла": "firefox", "эдж": "edge", "ворд": "word", "эксель": "excel", "ексель": "excel",
    "пауэрпоинт": "powerpoint", "поверпоинт": "powerpoint", "тимс": "teams", "аутлук": "outlook", "спотифай": "spotify",
    "код": "code", "блокнот": "notepad", "калькулятор": "calculator",
    "проводник": "file explorer", "терминал": "terminal", "павершелл": "powershell", "пауэршелл": "powershell",
    "повершелл": "powershell", "повершел": "powershell", "павершел": "powershell", "повышал": "powershell",
    "повышил": "powershell", "пауэр шелл": "powershell", "паур шел": "powershell", "обс": "obs", "зум": "zoom", "вотсап": "whatsapp", "ватсап": "whatsapp",
    "вайбер": "viber", "скайп": "skype", "эпик": "epic", "гейм": "games", "геймс": "games", "баттл": "battle",
    "нет": "net", "нвидиа": "nvidia", "докер": "docker", "гит": "git", "постман": "postman", "путти": "putty",
    "пакет": "packet", "трейсер": "tracer", "циско": "cisco", "виртуалбокс": "virtualbox", "вмвар": "vmware",
    "филезилла": "filezilla", "кипасс": "keepass", "гранола": "granola", "клод": "claude", "онедрайв": "onedrive",
    "настройки": "settings", "параметры": "settings", "диспетчер": "task manager", "задач": "", "камера": "camera",
    "фото": "photos", "почта": "mail", "календарь": "calendar", "магазин": "store", "кс": "counter-strike",
    "контра": "counter-strike", "юбисофт": "ubisoft", "ориджин": "origin", "иа": "ea", "гог": "gog",
    "мессенджер": "messenger", "нотион": "notion", "обсидиан": "obsidian", "вирт": "virt", "винбокс": "winbox",
    "мобакстерм": "mobaxterm", "ведьмак": "witcher", "киберпанк": "cyberpunk", "валорант": "valorant", "апекс": "apex",
    "геогессер": "geoguessr", "рейзер": "razer", "логитек": "logitech", "риот": "riot", "экситлаг": "exitlag",
    "фейсит": "faceit", "влс": "vlc", "винрар": "winrar", "иксбокс": "xbox", "пэйнт": "paint", "паинт": "paint",
    "питон": "python", "оллама": "ollama", "шиндоус": "windows", "безопасность": "security", "вайршарк": "wireshark", "анидеск": "anydesk", "тимвьюер": "teamviewer",
    "фигма": "figma", "блендер": "blender", "фотошоп": "photoshop", "премьер": "premiere", "давинчи": "davinci",
}
GENERIC = {"desktop", "app", "client", "launcher", "windows", "microsoft", "google", "new", "beta", "x64", "x86",
           "for", "the", "classic", "preview"}
SKIP = re.compile(r"uninstall|удал|deinstall|деинстал|reset preferences|readme|help|справк|documentation|license|release notes|website", re.I)
TR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
              ["a", "b", "v", "g", "d", "e", "yo", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u",
               "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))


def norm(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9а-яё]+", " ", s.lower().replace("ё", "е")).split())


PHRASES = {
    # админские инструменты Windows — по-русски
    "просмотр событий": "event viewer", "журнал событий": "event viewer", "редактор реестра": "registry editor",
    "реестр": "registry editor", "службы": "services", "планировщик заданий": "task scheduler", "планировщик": "task scheduler",
    "брандмауэр": "windows defender firewall", "фаервол": "windows defender firewall", "панель управления": "control panel",
    "командная строка": "command prompt", "командную строку": "command prompt", "управление компьютером": "computer management",
    "монитор ресурсов": "resource monitor", "монитор производительности": "performance monitor",
    "сведения о системе": "system information", "конфигурация системы": "system configuration",
    "удаленный рабочий стол": "remote desktop connection", "удалённый рабочий стол": "remote desktop connection",
    "очистка диска": "disk clean up", "локальная политика безопасности": "local security policy",
    "ножницы": "snipping tool", "гит баш": "git bash", "человек паук": "spider man", "балдурс гейт": "baldur s gate",
    "вс код": "visual studio code", "vs code": "visual studio code", "vscode": "visual studio code", "вскоде": "visual studio code", "вскод": "visual studio code", "вижуал студио код": "visual studio code",
           "диспетчер задач": "task manager", "эпик геймс": "epic games", "баттл нет": "battle.net"}


def to_latin(spoken: str) -> str:
    """«ютуб мюзик» → «youtube music», «пакет трейсер» → «packet tracer», неизвестное — транслитом."""
    t = norm(spoken)
    for ru, en in PHRASES.items():
        t = re.sub(rf"\b{ru}\b", en, t)
    out = []
    for w in t.split():
        if w in ALIASES:
            if ALIASES[w]:
                out.append(ALIASES[w])
        elif re.search(r"[а-я]", w):
            out.append("".join(TR.get(c, c) for c in w))
        else:
            out.append(w)
    return " ".join(out)


def score(query: str, name: str) -> float:
    """Насколько фраза похожа на название: целиком и по словам (каждое слово фразы должно найтись в названии)."""
    q = norm(to_latin(query))
    n = norm(re.split(r"\s[-–—:|]\s|\(", name)[0]) or norm(name)   # «Spotify - Music and Podcasts» → «spotify»
    # «Telegram Desktop», «Google Chrome», «Windows PowerShell» — служебные слова не мешают точному совпадению
    core = " ".join(w for w in n.split() if w not in GENERIC) or n
    if q == core:
        return 1.0
    if not q or not n:
        return 0.0
    if q == n:
        return 1.0
    whole = difflib.SequenceMatcher(None, q, n).ratio()
    nt, qt = n.split(), q.split()
    per_word = [max((difflib.SequenceMatcher(None, w, x).ratio() for x in nt), default=0) for w in qt]
    words = sum(per_word) / len(per_word)
    extra = max(0, len(nt) - len(qt))
    if extra:  # в названии есть лишние слова («cisco packet tracer», «youtube music») — чуть ниже точного совпадения
        words = min(words, 0.92 - 0.02 * (extra - 1))
    return max(whole, words if min(per_word) >= 0.6 else 0)


class AppIndex:
    def __init__(self, cache: Path):
        self.cache = Path(cache)
        self.apps: list[dict] = []
        try:
            self.apps = json.loads(self.cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass

    def refresh(self) -> int:
        """Перечитать меню «Пуск» (около секунды). Вызывается в фоне при запуске."""
        if sys.platform != "win32":
            return len(self.apps)
        cmd = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
               "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress")
        r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, timeout=60,
                           creationflags=NO_WINDOW)
        data = json.loads(r.stdout.decode("utf-8", "replace") or "[]")
        data = data if isinstance(data, list) else [data]
        apps = [{"name": a["Name"], "id": a["AppID"]} for a in data
                if a.get("Name") and a.get("AppID") and not SKIP.search(a["Name"])]
        if apps:
            self.apps = apps
            try:
                self.cache.parent.mkdir(parents=True, exist_ok=True)
                self.cache.write_text(json.dumps(apps, ensure_ascii=False, indent=1), encoding="utf-8")
            except OSError:
                pass
        return len(self.apps)

    def find(self, spoken: str, threshold: float = 0.82) -> dict | None:
        best, best_s = None, 0.0
        for a in self.apps:
            s = score(spoken, a["name"])
            # при равенстве — более короткое название («YouTube» лучше, чем «YouTube Studio»)
            if s > best_s or (s == best_s and best and len(a["name"]) < len(best["name"])):
                best, best_s = a, s
        return best if best_s >= threshold else None

    def names(self) -> list[str]:
        return sorted(a["name"] for a in self.apps)


def launch(app_id: str) -> None:
    """Запуск как из меню «Пуск» — работает и для обычных программ, и для приложений из Store."""
    subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app_id}"], creationflags=NO_WINDOW)
