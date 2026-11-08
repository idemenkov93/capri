"""Волна 3: админ-аптечка, проверка сайтов, PowerShell-подсказчик."""
import base64
import copy
import os
import time
from pathlib import Path

import httpx
import pytest

from capri import admin, admin_cmds, sites
from capri.agent import Agent
from capri.applist import AppIndex
from capri.config import DEFAULTS


class Sys:
    def run(self, cmd):
        return {}


def agent(ask=None):
    a = Agent(copy.deepcopy(DEFAULTS), llm=object(), claude=lambda t, c: "C", system=Sys(), apps=AppIndex(Path("/x")))
    if ask:
        a.ask_text = ask
    return a


# ------------------------------------------------------------------ фразы → проверки
@pytest.mark.parametrize("phrase,fn,args", [
    ("какой у меня ip", "my_ip", ()), ("мой айпи", "my_ip", ()),
    ("проверь интернет", "check_internet", ()), ("почему не работает интернет?", "check_internet", ()),
    ("пинг google.com", "ping_host", ("google.com",)), ("трассировка до 8.8.8.8", "traceroute", ("8.8.8.8",)),
    ("открыт ли порт 443 на example.com", "port_open", ("example.com", 443)),
    ("пароль от вайфая", "wifi_password", ()), ("какие устройства в сети", "lan_devices", ()),
    ("что грузит компьютер", "top_processes", ()), ("почему тормозит комп", "top_processes", ()),
    ("здоровье дисков", "disk_health", ()), ("сколько работает компьютер", "uptime", ()),
    ("сводка железа", "hardware", ()), ("ошибки за сегодня", "errors_today", ()),
    ("почему компьютер перезагрузился", "why_reboot", ()), ("обновления windows", "updates", ()),
    ("статус службы spooler", "service_status", ("spooler",)), ("защитник работает", "defender", ()),
    ("брандмауэр", "firewall", ()), ("кто залогинен", "who_logged_in", ()), ("неудачные входы", "failed_logons", ()),
])
def test_phrases_route_to_checks(monkeypatch, phrase, fn, args):
    called = []
    monkeypatch.setattr(admin, fn, lambda *a: called.append(a) or admin.Result("коротко", "подробно", "Get-Thing", "T"))
    r = agent().handle(phrase)
    assert called == [args] and r.text == "коротко"
    assert r.actions[0].kind == "popup" and r.actions[0].params["command"] == "Get-Thing"


def test_capri_update_is_not_windows_update():
    r = agent().handle("проверь обновления")
    assert r.actions[0].kind == "check_updates"


@pytest.mark.parametrize("phrase,code", [("очисти временные файлы", "clean_temp"), ("сбрось dns", "flush_dns"),
                                         ("полный сброс сети", "reset_network"), ("очисти очередь печати", "print_queue")])
def test_changes_need_yes(phrase, code):
    r = agent().handle(phrase)
    act = r.actions[0]
    assert act.kind == "admin_do" and act.needs_confirm and act.params["what"] == code


def test_restart_service_needs_yes():
    act = agent().handle("перезапусти службу Spooler").actions[0]
    assert act.kind == "admin_do" and act.params == {"what": "restart_service", "name": "Spooler"} and act.needs_confirm


def test_snapins():
    assert agent().handle("открой диспетчер устройств").actions[0].params["target"] == "devmgmt.msc"
    assert agent().handle("открой управление дисками").actions[0].params["target"] == "diskmgmt.msc"
    assert agent().handle("открой просмотр событий").actions[0].params["target"] == "eventvwr.msc"
    assert admin.snapin("службы") == "services.msc" and admin.snapin("ютуб") is None


def test_not_windows_says_so(monkeypatch):
    monkeypatch.setattr(admin.sys, "platform", "linux")
    r = agent().handle("ошибки за сегодня")
    assert r.route == "error" and "Windows" in r.text


# ------------------------------------------------------------------ разбор вывода Windows
@pytest.mark.parametrize("key", ["Key Content", "Содержимое ключа", "Schlüsselinhalt"])
def test_wifi_password_any_language(monkeypatch, key):
    outs = {"interfaces": "    Name : WLAN\n    SSID                   : Дом 5G\n    BSSID : aa\n",
            "profile": f"Profile\n    Security key           : Present\n    {key}            : s3cret pass\n"}
    monkeypatch.setattr(admin, "run_cmd", lambda args, timeout=60: outs["interfaces" if "interfaces" in args else "profile"])
    r = admin.wifi_password()
    assert "Дом 5G" in r.details and "s3cret pass" in r.details and "s3cret" not in r.say


def test_ping_parse_ru_en(monkeypatch):
    monkeypatch.setattr(admin, "run_cmd", lambda *a, **k: "Ответ от 1.1.1.1: число байт=32 время=12мс TTL=57\n"
                                                         "Reply from 1.1.1.1: bytes=32 time=14ms TTL=57\n")
    assert admin.ping("1.1.1.1", 2) == (2, 13.0)


def test_internet_diagnosis_isp_down(monkeypatch):
    monkeypatch.setattr(admin, "ping", lambda host, n=4: (2, 3.0) if host == "192.168.1.1" else (0, None))
    monkeypatch.setattr(admin.socket, "getaddrinfo", lambda *a: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(httpx, "head", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("x")))
    r = admin.check_internet("192.168.1.1")
    assert "провайдер" in r.say and "✅ **Роутер" in r.details and "❌ **Интернет" in r.details


def test_internet_ok(monkeypatch):
    monkeypatch.setattr(admin, "ping", lambda host, n=4: (n, 9.0))
    monkeypatch.setattr(admin.socket, "getaddrinfo", lambda *a: [])
    monkeypatch.setattr(httpx, "head", lambda *a, **k: httpx.Response(200))
    assert admin.check_internet("10.0.0.1").say.startswith("Интернет в порядке")


def test_elevated_script_is_base64_and_safe():
    w = admin.elevated_wrapper("Restart-Service 'Spooler'; $x = \"a\"", "C:\\Temp\\it's.txt")
    b64 = w.split("'-EncodedCommand','")[1].split("'")[0]
    inner = base64.b64decode(b64).decode("utf-16-le")
    assert "Restart-Service 'Spooler'; $x = \"a\"" in inner and "it''s.txt" in inner and "-Verb RunAs" in w


def test_clean_temp_only_old_files(monkeypatch, tmp_path):
    monkeypatch.setenv("TEMP", str(tmp_path))
    old, new = tmp_path / "old.tmp", tmp_path / "new.tmp"
    old.write_bytes(b"x" * 2048)
    new.write_bytes(b"y")
    past = time.time() - 3 * 86400
    os.utime(old, (past, past))
    r = admin.clean_temp()
    assert not old.exists() and new.exists() and "1 временн" in r.say


# ------------------------------------------------------------------ сайты
def test_site_normalize():
    assert sites.normalize("example.com") == "https://example.com"
    assert sites.normalize("http://10.0.0.5:8080/health") == "http://10.0.0.5:8080/health"
    assert sites.normalize("компьютер") is None


def test_site_check_and_reply(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Response(200))
    monkeypatch.setattr(sites, "ssl_days_left", lambda host, port=443, timeout=6: 9)
    r = agent().handle("проверь сайт example.com")
    assert "example.com работает" in r.text and "скоро кончится" in r.text and r.actions[0].kind == "popup"
    monkeypatch.setattr(httpx, "get", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectTimeout("x")))
    r = agent().handle("работает ли сайт example.com?")
    assert "не работает" in r.text and r.route == "error"


def test_site_watch_phrases():
    r = agent().handle("следи за сайтом example.com")
    assert r.actions[0].params == {"url": "https://example.com", "on": True}
    r = agent().handle("перестань следить за example.com")
    assert r.actions[0].params == {"url": "https://example.com", "on": False}


# ------------------------------------------------------------------ PowerShell-подсказчик
def test_powershell_helper(monkeypatch, tmp_path):
    sheet = tmp_path / "PowerShell.md"
    monkeypatch.setattr(admin_cmds, "cheatsheet_path", lambda: sheet)
    ans = '{"command": "Get-LocalUser | Where-Object Enabled", "explain": "Список включённых пользователей.", "admin": false, "danger": ""}'
    a = agent(ask=lambda task, images=None: ans)
    r = a.handle("как в powershell показать всех включённых пользователей")
    act = r.actions[0]
    assert act.kind == "ps_helper" and act.params["command"] == "Get-LocalUser | Where-Object Enabled"
    assert "в буфере" in r.text
    a.handle("как в powershell показать всех включённых пользователей")       # второй раз — без дубля
    text = sheet.read_text(encoding="utf-8")
    assert text.count("Get-LocalUser | Where-Object Enabled") == 1 and "## Показать всех" in text


def test_parse_ps_from_markdown():
    d = admin_cmds.parse_ps("Вот:\n```powershell\nGet-Service | Where Status -eq Running\n```\nПоказывает службы.")
    assert d["command"] == "Get-Service | Where Status -eq Running" and "Показывает" in d["explain"]
