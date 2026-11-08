"""Волна 3: админ-аптечка. Сеть, система, Windows, безопасность — локально, через PowerShell и psutil.

Каждая проверка возвращает Result: коротко (это Капри скажет) + подробно (окно у курсора) + сама команда
PowerShell (кнопка «📜 Команда» — посмотреть и выучить). Что меняет систему (сброс сети, очередь печати, временные
файлы, перезапуск службы) — только после «Да», а где нужны права администратора — Windows сама спросит (UAC).
"""
from __future__ import annotations

import json
import re
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field

from .convert import flags


@dataclass
class Result:
    say: str                          # коротко — голосом и в чат
    details: str = ""                 # подробно — в окне (markdown)
    command: str = ""                 # PowerShell, которым это делается
    title: str = ""
    danger: dict = field(default_factory=dict)      # {"what": код, "ask": вопрос} — нужно «Да»


class AdminError(RuntimeError):
    pass


# ====================================================================== запуск PowerShell
PS_PREFIX = "$ProgressPreference='SilentlyContinue';[Console]::OutputEncoding=[Text.Encoding]::UTF8;"


def ps(script: str, timeout: float = 40) -> str:
    if sys.platform != "win32":
        raise AdminError("это работает только в Windows")
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
                        PS_PREFIX + script], capture_output=True, timeout=timeout, creationflags=flags())
    out = r.stdout.decode("utf-8", "replace").strip()
    if r.returncode and not out:
        raise AdminError((r.stderr.decode("utf-8", "replace") or f"код {r.returncode}").strip()[:300])
    return out


def ps_json(script: str, timeout: float = 40):
    out = ps(f"{script} | ConvertTo-Json -Depth 4 -Compress", timeout)
    if not out:
        return []
    data = json.loads(out)
    return data if isinstance(data, list) else [data]


def run_cmd(args: list[str], timeout: float = 60) -> str:
    """ipconfig, netsh, tracert… — их вывод на языке Windows; читаем в кодировке консоли (OEM)."""
    r = subprocess.run(args, capture_output=True, timeout=timeout, creationflags=flags())
    raw = r.stdout + r.stderr
    for enc in ("utf-8", "cp866", "cp850", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def elevated_wrapper(script: str, out_file: str) -> str:
    """Команда, которая запускает script от администратора. Скрипт передаём в base64 (-EncodedCommand) —
    так никакие кавычки и $ внутри не сломаются."""
    import base64
    inner = PS_PREFIX + f"& {{ {script} }} *> '{out_file.replace(chr(39), chr(39) * 2)}'"
    b64 = base64.b64encode(inner.encode("utf-16-le")).decode()
    return ("$p = Start-Process powershell -Verb RunAs -WindowStyle Hidden -Wait -PassThru -ArgumentList "
            f"'-NoProfile','-ExecutionPolicy','Bypass','-EncodedCommand','{b64}'; exit $p.ExitCode")


def run_elevated(script: str, timeout: float = 120) -> str:
    """С правами администратора: Windows покажет окно «Разрешить?». Вывод — через временный файл."""
    import tempfile
    from pathlib import Path
    out = Path(tempfile.gettempdir()) / f"capri-admin-{int(time.time() * 1000)}.txt"
    wrapper = elevated_wrapper(script, str(out))
    try:
        ps(wrapper, timeout)
    except AdminError as e:
        if "отмен" in str(e).lower() or "canceled" in str(e).lower() or "abgebrochen" in str(e).lower():
            raise AdminError("ты нажал «Нет» в окне Windows — ничего не меняла") from None
        raise
    try:
        return out.read_text(encoding="utf-8", errors="replace").strip() if out.exists() else ""
    finally:
        out.unlink(missing_ok=True)


# ====================================================================== сеть
def my_ip() -> Result:
    cmd = "Get-NetIPConfiguration | Where-Object IPv4DefaultGateway"
    rows = ps_json(cmd + " | ForEach-Object { [pscustomobject]@{ Name=$_.InterfaceAlias; "
                   "IP=($_.IPv4Address.IPAddress -join ', '); Gateway=($_.IPv4DefaultGateway.NextHop -join ', '); "
                   "DNS=(($_.DNSServer | Where-Object AddressFamily -eq 2 | ForEach-Object { $_.ServerAddresses }) -join ', ') } }")
    public = public_ip()
    lines = [f"**{r['Name']}** — IP {r['IP']}, шлюз {r['Gateway']}, DNS {r['DNS'] or '—'}" for r in rows]
    if public:
        lines.append(f"**Внешний IP** (как тебя видит интернет): {public}")
    main = rows[0]["IP"] if rows else "—"
    say = f"Твой IP {main}" + (f", внешний {public}." if public else ".")
    return Result(say, "\n\n".join(lines) or "Нет активных подключений.", cmd + "\n(Invoke-RestMethod api.ipify.org — внешний IP)",
                  "Мой IP")


def public_ip() -> str:
    try:
        import httpx
        return httpx.get("https://api.ipify.org", timeout=5).text.strip()
    except Exception:  # noqa: BLE001
        return ""


def ping(host: str, count: int = 4) -> tuple[int, float | None]:
    """→ (сколько ответов, средняя задержка мс). Через ping.exe — работает и без прав."""
    out = run_cmd(["ping", "-n" if sys.platform == "win32" else "-c", str(count), "-w" if sys.platform == "win32" else "-W",
                   "1500" if sys.platform == "win32" else "2", host], timeout=count * 3 + 5)
    times = [float(x.replace(",", ".")) for x in re.findall(r"(?:time|время|Zeit)[=<]\s*([\d.,]+)\s*(?:ms|мс)", out, re.I)]
    return len(times), (sum(times) / len(times) if times else None)


def check_internet(gateway: str | None = None) -> Result:
    """Шаг за шагом: роутер → интернет по IP → DNS → сайты. По первому сбою понятно, где беда."""
    steps = []
    if gateway is None and sys.platform == "win32":
        try:
            gws = ps_json("(Get-NetIPConfiguration | Where-Object IPv4DefaultGateway).IPv4DefaultGateway | Select-Object NextHop")
            gateway = gws[0]["NextHop"] if gws else ""
        except Exception:  # noqa: BLE001
            gateway = ""
    ok_gw = None
    if gateway:
        n, ms = ping(gateway, 2)
        ok_gw = n > 0
        steps.append(("Роутер " + gateway, ok_gw, f"{ms:.0f} мс" if ms else "не отвечает"))
    n, ms = ping("1.1.1.1", 3)
    ok_ip = n > 0
    steps.append(("Интернет (1.1.1.1)", ok_ip, f"{ms:.0f} мс, ответов {n}/3" if ms else "не отвечает"))
    try:
        t0 = time.monotonic()
        socket.getaddrinfo("google.com", 443)
        ok_dns, dns_txt = True, f"{(time.monotonic() - t0) * 1000:.0f} мс"
    except OSError:
        ok_dns, dns_txt = False, "имена сайтов не находятся"
    steps.append(("DNS (google.com)", ok_dns, dns_txt))
    ok_web = False
    try:
        import httpx
        t0 = time.monotonic()
        httpx.head("https://www.google.com", timeout=6)
        ok_web, web_txt = True, f"{(time.monotonic() - t0) * 1000:.0f} мс"
    except Exception as e:  # noqa: BLE001
        web_txt = type(e).__name__
    steps.append(("Сайты (https)", ok_web, web_txt))
    if ok_web:
        say = "Интернет в порядке." + (f" Задержка {ms:.0f} мс." if ms else "")
    elif ok_gw is False:
        say = "Роутер не отвечает: проверь кабель или Wi-Fi, перезагрузи роутер."
    elif not ok_ip:
        say = "До роутера достаю, дальше — нет. Похоже, нет интернета у провайдера: перезагрузи роутер, потом звони провайдеру."
    elif not ok_dns:
        say = "Интернет есть, но не работает DNS. Скажи «сбрось сеть» или поставь DNS 1.1.1.1."
    else:
        say = "Интернет по IP есть, а сайты не открываются — проверь прокси, VPN или антивирус."
    details = "\n".join(f"{'✅' if ok else '❌'} **{name}** — {txt}" for name, ok, txt in steps)
    return Result(say, details, "ping <шлюз>; ping 1.1.1.1; Resolve-DnsName google.com; Invoke-WebRequest https://www.google.com",
                  "Проверка интернета")


def ping_host(host: str) -> Result:
    n, ms = ping(host, 4)
    say = f"{host}: ответов {n} из 4" + (f", в среднем {ms:.0f} мс." if ms else " — не отвечает.")
    return Result(say, say, f"Test-Connection {host} -Count 4   (или: ping {host})", "Пинг")


def traceroute(host: str) -> Result:
    out = run_cmd(["tracert", "-d", "-h", "20", "-w", "800", host] if sys.platform == "win32" else ["traceroute", "-n", host],
                  timeout=120)
    hops = [ln.strip() for ln in out.splitlines() if re.match(r"^\s*\d+\s", ln)]
    lost = sum(1 for h in hops if h.count("*") >= 3)
    say = f"До {host} {len(hops)} прыжков" + (f", {lost} не ответили — это нормально, если в конце дошло." if lost else ".")
    return Result(say, "```\n" + "\n".join(hops) + "\n```", f"tracert -d {host}   (Test-NetConnection {host} -TraceRoute)",
                  "Трассировка")


def port_open(host: str, port: int, timeout: float = 3) -> Result:
    t0 = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            ok = True
    except OSError:
        ok = False
    ms = (time.monotonic() - t0) * 1000
    say = f"Порт {port} на {host} {'открыт' if ok else 'закрыт или недоступен'}" + (f" ({ms:.0f} мс)." if ok else ".")
    return Result(say, say, f"Test-NetConnection {host} -Port {port}", "Порт")


def wifi_password() -> Result:
    """Пароль Wi-Fi, к которому подключён этот компьютер (твой же, сохранённый в Windows)."""
    iface = run_cmd(["netsh", "wlan", "show", "interfaces"])
    m = re.search(r"^\s*SSID\s*:\s*(.+)$", iface, re.M)
    if not m:
        return Result("Сейчас компьютер не подключён к Wi-Fi (или подключён кабелем).", command="netsh wlan show interfaces",
                      title="Пароль Wi-Fi")
    ssid = m.group(1).strip()
    prof = run_cmd(["netsh", "wlan", "show", "profile", f"name={ssid}", "key=clear"])
    k = re.search(r"^\s*(?:Key Content|Содержимое ключа|Schlüsselinhalt|Contenu de la clé)\s*:\s*(.+)$", prof, re.M)
    if not k:
        return Result(f"Сеть {ssid}, но пароль Windows не показала (сеть без пароля или корпоративная).",
                      command=f'netsh wlan show profile name="{ssid}" key=clear', title="Пароль Wi-Fi")
    pw = k.group(1).strip()
    return Result(f"Сеть {ssid}. Пароль — в окне.", f"**Сеть:** {ssid}\n\n**Пароль:** `{pw}`",
                  f'netsh wlan show profile name="{ssid}" key=clear', "Пароль Wi-Fi")


def lan_devices() -> Result:
    """Кто в моей сети: быстрый пинг всей /24 (параллельно), потом таблица ARP (IP ↔ MAC) и имена."""
    from concurrent.futures import ThreadPoolExecutor
    info = ps_json("Get-NetIPConfiguration | Where-Object IPv4DefaultGateway | Select-Object -First 1 | ForEach-Object "
                   "{ [pscustomobject]@{ IP=$_.IPv4Address[0].IPAddress; Prefix=$_.IPv4Address[0].PrefixLength } }")
    if not info:
        return Result("Не вижу подключения к сети.", title="Устройства в сети")
    me = info[0]["IP"]
    base = me.rsplit(".", 1)[0]
    with ThreadPoolExecutor(64) as ex:
        list(ex.map(lambda i: run_cmd(["ping", "-n", "1", "-w", "300", f"{base}.{i}"], 5), range(1, 255)))
    arp = run_cmd(["arp", "-a"])
    found = []
    for ip, mac in re.findall(rf"({re.escape(base)}\.\d+)\s+([0-9a-f]{{2}}(?:-[0-9a-f]{{2}}){{5}})", arp, re.I):
        if mac.lower().startswith("ff-ff") or ip.endswith(".255"):
            continue
        try:
            name = socket.gethostbyaddr(ip)[0]
        except OSError:
            name = ""
        found.append((ip, mac, name))
    found.sort(key=lambda r: int(r[0].rsplit(".", 1)[1]))
    lines = [f"`{ip}`  {mac}  {name}" for ip, mac, name in found] + [f"`{me}`  — это ты"]
    return Result(f"В сети {len(found) + 1} устройств, список в окне.", "\n\n".join(lines),
                  f"1..254 | % {{ ping -n 1 -w 300 {base}.$_ }}; arp -a", "Устройства в сети")


# ====================================================================== система
def top_processes(n: int = 6) -> Result:
    import psutil
    procs = list(psutil.process_iter(["name", "memory_info"]))
    for p in procs:
        try:
            p.cpu_percent(None)
        except psutil.Error:
            pass
    time.sleep(1.0)
    rows = []
    for p in procs:
        if (p.info.get("name") or "").lower() in ("system idle process", "idle", "memory compression") or p.pid == 0:
            continue
        try:
            rows.append((p.info["name"] or "?", p.cpu_percent(None) / (psutil.cpu_count() or 1),
                         (p.info["memory_info"].rss if p.info["memory_info"] else 0) / 2**20))
        except psutil.Error:
            continue
    by_cpu = sorted(rows, key=lambda r: -r[1])[:n]
    by_ram = sorted(rows, key=lambda r: -r[2])[:n]
    cpu, ram = psutil.cpu_percent(0.2), psutil.virtual_memory()
    top = by_cpu[0] if by_cpu else ("—", 0, 0)
    say = f"Процессор {cpu:.0f} %, память {ram.percent:.0f} %. Больше всех грузит {top[0]}: {top[1]:.0f} % процессора."
    det = ("**Процессор:**\n" + "\n".join(f"• {nm} — {c:.0f} %" for nm, c, _ in by_cpu) +
           "\n\n**Память:**\n" + "\n".join(f"• {nm} — {m:.0f} МБ" for nm, _, m in by_ram))
    return Result(say, det, "Get-Process | Sort-Object CPU -Descending | Select-Object -First 6 Name,CPU,WS", "Что грузит ПК")


def disks() -> Result:
    import psutil
    rows = []
    for part in psutil.disk_partitions(all=False):
        if "cdrom" in part.opts or not part.fstype:
            continue
        try:
            u = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        rows.append((part.mountpoint, u.free / 2**30, u.total / 2**30, u.percent))
    low = [r for r in rows if r[1] < 10 or r[3] > 90]
    say = "; ".join(f"диск {m.rstrip(chr(92))} — свободно {f:.0f} ГБ из {t:.0f}" for m, f, t, _ in rows) + "."
    if low:
        say += " Места мало — скажи «очисти временные файлы»."
    det = "\n".join(f"{'⚠' if (f < 10 or p > 90) else '•'} **{m}** свободно {f:.1f} ГБ из {t:.0f} ГБ (занято {p:.0f} %)"
                    for m, f, t, p in rows)
    return Result(say[0].upper() + say[1:], det, "Get-PSDrive -PSProvider FileSystem", "Место на дисках")


def disk_health() -> Result:
    cmd = "Get-PhysicalDisk | Select-Object FriendlyName,MediaType,HealthStatus,OperationalStatus,Size"
    rows = ps_json(cmd)
    bad = [r for r in rows if str(r.get("HealthStatus")) not in ("Healthy", "0")]
    say = "Все диски здоровы." if not bad else f"Внимание: {', '.join(r['FriendlyName'] for r in bad)} — проблемы! Сделай бэкап."
    det = "\n".join(f"{'✅' if r not in bad else '❌'} **{r['FriendlyName']}** ({r.get('MediaType')}, "
                    f"{int(r.get('Size') or 0) / 1e9:.0f} ГБ) — {r.get('HealthStatus')}" for r in rows)
    return Result(say, det, cmd, "Здоровье дисков")


def uptime() -> Result:
    import psutil
    sec = time.time() - psutil.boot_time()
    d, h, m = int(sec // 86400), int(sec % 86400 // 3600), int(sec % 3600 // 60)
    txt = (f"{d} дн. " if d else "") + f"{h} ч {m} мин"
    tip = " Давно не перезагружался — перезагрузка часто лечит странности." if d >= 7 else ""
    return Result(f"Компьютер работает {txt}.{tip}", command="(Get-Date) - (Get-CimInstance Win32_OperatingSystem).LastBootUpTime",
                  title="Время работы")


def hardware() -> Result:
    cmd = ("$os=Get-CimInstance Win32_OperatingSystem; $cs=Get-CimInstance Win32_ComputerSystem; "
           "$cpu=Get-CimInstance Win32_Processor | Select-Object -First 1; $bb=Get-CimInstance Win32_BaseBoard; "
           "[pscustomobject]@{ OS=$os.Caption; Build=$os.BuildNumber; PC=$cs.Name; Model=\"$($cs.Manufacturer) $($cs.Model)\"; "
           "CPU=$cpu.Name.Trim(); Cores=$cpu.NumberOfCores; Threads=$cpu.NumberOfLogicalProcessors; "
           "RAM=[math]::Round($cs.TotalPhysicalMemory/1GB); Board=\"$($bb.Manufacturer) $($bb.Product)\"; "
           "GPU=(Get-CimInstance Win32_VideoController | ForEach-Object Name) -join ', ' }")
    r = ps_json(cmd)[0]
    det = (f"**Windows:** {r['OS']} (сборка {r['Build']})\n\n**Компьютер:** {r['PC']} — {r['Model']}\n\n"
           f"**Процессор:** {r['CPU']} — {r['Cores']} ядер / {r['Threads']} потоков\n\n**Память:** {r['RAM']} ГБ\n\n"
           f"**Видеокарта:** {r['GPU']}\n\n**Материнская плата:** {r['Board']}")
    return Result(f"{r['CPU']}, {r['RAM']} гигабайт памяти, {r['GPU'].split(',')[0]}. Подробно — в окне.", det,
                  "Get-CimInstance Win32_OperatingSystem, Win32_ComputerSystem, Win32_Processor, Win32_VideoController, Win32_BaseBoard",
                  "Сводка железа")


# ====================================================================== Windows
def errors_today() -> Result:
    cmd = ("Get-WinEvent -FilterHashtable @{LogName='System','Application'; Level=1,2; StartTime=(Get-Date).Date} "
           "-MaxEvents 200 -ErrorAction SilentlyContinue")
    rows = ps_json(cmd + " | Select-Object ProviderName,Id,LevelDisplayName,@{n='Time';e={$_.TimeCreated.ToString('HH:mm')}},"
                         "@{n='Msg';e={($_.Message -split \"`n\")[0]}}")
    if not rows:
        return Result("Сегодня ошибок в журнале нет.", command=cmd, title="Ошибки за сегодня")
    groups: dict[tuple, list] = {}
    for r in rows:
        groups.setdefault((r.get("ProviderName"), r.get("Id")), []).append(r)
    top = sorted(groups.items(), key=lambda kv: -len(kv[1]))[:8]
    det = "\n\n".join(f"**{prov}** (ID {eid}) ×{len(v)}, последняя в {v[0].get('Time')}\n{(v[0].get('Msg') or '')[:200]}"
                      for (prov, eid), v in top)
    return Result(f"Сегодня {len(rows)} ошибок, разных — {len(groups)}. Чаще всех {top[0][0][0]}. Список в окне.", det, cmd,
                  "Ошибки за сегодня")


REBOOT_IDS = {41: "Компьютер выключился внезапно (питание, перегрев или зависание) — Kernel-Power 41",
              6008: "Предыдущее выключение было неожиданным — 6008",
              1074: "Перезагрузку запустила программа или человек — 1074",
              1001: "Был синий экран (BugCheck) — 1001"}


def why_reboot() -> Result:
    cmd = "Get-WinEvent -FilterHashtable @{LogName='System'; Id=41,1074,6008,1001} -MaxEvents 6 -ErrorAction SilentlyContinue"
    rows = ps_json(cmd + " | Select-Object Id,@{n='Time';e={$_.TimeCreated.ToString('dd.MM HH:mm')}},"
                         "@{n='Msg';e={($_.Message -split \"`n\")[0]}}")
    if not rows:
        return Result("В журнале нет записей о перезагрузках.", command=cmd, title="Почему перезагрузился")
    last = rows[0]
    reason = REBOOT_IDS.get(int(last["Id"]), str(last["Id"]))
    extra = ""
    if int(last["Id"]) == 1074 and (m := re.search(r"(?:process|процесс|Prozess)\s+(\S+)", last.get("Msg") or "", re.I)):
        extra = f" Кто: {m.group(1).rsplit(chr(92), 1)[-1]}."
    det = "\n\n".join(f"**{r['Time']}** — {REBOOT_IDS.get(int(r['Id']), r['Id'])}\n{(r.get('Msg') or '')[:180]}" for r in rows)
    return Result(f"Последний раз ({last['Time']}): {reason.split(' — ')[0]}.{extra}", det, cmd, "Почему перезагрузился")


def updates() -> Result:
    cmd = "Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 6 HotFixID,Description,InstalledOn"
    rows = ps_json(cmd.replace("Select-Object -First 6 HotFixID,Description,InstalledOn",
                               "Select-Object -First 6 HotFixID,Description,@{n='On';e={if($_.InstalledOn){$_.InstalledOn.ToString('dd.MM.yyyy')}}}"))
    if not rows:
        return Result("Не вижу установленных обновлений.", command=cmd, title="Обновления Windows")
    det = "\n".join(f"• **{r['HotFixID']}** — {r.get('Description')}, {r.get('On') or '?'}" for r in rows)
    return Result(f"Последнее обновление Windows — {rows[0]['HotFixID']} от {rows[0].get('On') or '?'}.", det, cmd,
                  "Обновления Windows")


def service_status(name: str) -> Result:
    cmd = f"Get-Service -Name '{name}','*{name}*' -ErrorAction SilentlyContinue"
    rows = ps_json(cmd + " | Select-Object -First 5 Name,DisplayName,@{n='St';e={[string]$_.Status}},@{n='Start';e={[string]$_.StartType}}")
    if not rows:
        return Result(f"Службу «{name}» не нашла.", command=cmd, title="Служба")
    det = "\n".join(f"• **{r['DisplayName']}** ({r['Name']}) — {r['St']}, запуск {r['Start']}" for r in rows)
    r = rows[0]
    st = {"Running": "работает", "Stopped": "остановлена"}.get(r["St"], r["St"])
    return Result(f"{r['DisplayName']} — {st}.", det, cmd, "Служба")


SNAPINS = {
    r"диспетчер\w* устройств": "devmgmt.msc", r"управлени\w* диск\w*": "diskmgmt.msc", r"служб\w*": "services.msc",
    r"просмотр\w* событий|журнал\w* событий": "eventvwr.msc", r"управлени\w* компьютер\w*": "compmgmt.msc",
    r"(?:локальн\w* )?пользовател\w* и групп\w*": "lusrmgr.msc", r"редактор\w* (?:групповой |локальной )?политик\w*|gpedit": "gpedit.msc",
    r"планировщик\w*(?: задач)?": "taskschd.msc", r"монитор\w* ресурсов": "resmon", r"сведени\w* о системе|msinfo": "msinfo32",
    r"сетев\w* подключени\w*|сетевые адаптеры": "ncpa.cpl", r"программы и компоненты|удаление программ": "appwiz.cpl",
    r"свойства системы|переменн\w* среды": "sysdm.cpl", r"брандмауэр\w*|файрвол\w*": "wf.msc",
    r"монитор\w* производительности": "perfmon", r"диспетчер\w* задач": "taskmgr",
}


def snapin(text: str) -> str | None:
    for rx, cmd in SNAPINS.items():
        if re.fullmatch(rf"(?:мне\s+)?(?:{rx})", text.strip(" .!?"), re.I):
            return cmd
    return None


# ====================================================================== безопасность
def defender() -> Result:
    cmd = "Get-MpComputerStatus"
    try:
        r = ps_json(cmd + " | Select-Object AntivirusEnabled,RealTimeProtectionEnabled,"
                          "@{n='Sig';e={$_.AntivirusSignatureLastUpdated.ToString('dd.MM.yyyy')}},QuickScanAge,FullScanAge")[0]
    except Exception:  # noqa: BLE001
        return Result("Защитник Windows не отвечает — возможно, стоит другой антивирус.", command=cmd, title="Защитник")
    on = bool(r.get("AntivirusEnabled")) and bool(r.get("RealTimeProtectionEnabled"))
    say = ("Защитник включён, защита в реальном времени работает." if on else "Внимание: защита в реальном времени выключена!") + \
        f" Базы от {r.get('Sig')}."
    det = (f"{'✅' if r.get('AntivirusEnabled') else '❌'} Антивирус\n\n{'✅' if r.get('RealTimeProtectionEnabled') else '❌'} "
           f"Защита в реальном времени\n\n• Базы: {r.get('Sig')}\n\n• Быстрая проверка: {r.get('QuickScanAge')} дн. назад")
    return Result(say, det, cmd, "Защитник Windows")


def firewall() -> Result:
    cmd = "Get-NetFirewallProfile | Select-Object Name,Enabled"
    rows = ps_json(cmd)
    off = [r["Name"] for r in rows if not r.get("Enabled")]
    names = {"Domain": "доменный", "Private": "частный", "Public": "общественный"}
    say = "Брандмауэр включён для всех сетей." if not off else \
        "Брандмауэр выключен: " + ", ".join(names.get(n, n) for n in off) + " профиль."
    det = "\n".join(f"{'✅' if r.get('Enabled') else '❌'} {names.get(r['Name'], r['Name'])} ({r['Name']})" for r in rows)
    return Result(say, det, cmd, "Брандмауэр")


def who_logged_in() -> Result:
    out = run_cmd(["query", "user"])
    users = [ln.split()[0].lstrip(">") for ln in out.splitlines()[1:] if ln.strip()]
    say = ("Сейчас вошли: " + ", ".join(users) + ".") if users else "Не смогла узнать (query user недоступен)."
    return Result(say, f"```\n{out.strip()}\n```" if users else "", "query user   (или: quser)", "Кто залогинен")


def failed_logons() -> Result:
    """Неудачные входы (4625) за неделю — журнал «Безопасность» читается только с правами администратора."""
    script = ("$e=Get-WinEvent -FilterHashtable @{LogName='Security';Id=4625;StartTime=(Get-Date).AddDays(-7)} -MaxEvents 50 "
              "-ErrorAction SilentlyContinue; if(-not $e){'NONE'} else { $e | ForEach-Object { $x=[xml]$_.ToXml(); "
              "$d=@{}; $x.Event.EventData.Data | ForEach-Object { $d[$_.Name]=$_.'#text' }; "
              "\"$($_.TimeCreated.ToString('dd.MM HH:mm'))|$($d.TargetUserName)|$($d.IpAddress)|$($d.LogonType)\" } }")
    out = run_elevated(script)
    if not out or out.strip() == "NONE":
        return Result("За неделю неудачных входов нет.", command="Get-WinEvent -FilterHashtable @{LogName='Security';Id=4625}",
                      title="Неудачные входы")
    rows = [ln.split("|") for ln in out.splitlines() if ln.count("|") == 3]
    det = "\n".join(f"• {t} — пользователь **{u or '?'}**, адрес {ip or '—'}, тип входа {lt}" for t, u, ip, lt in rows)
    return Result(f"За неделю {len(rows)} неудачных входов. Если это не ты ошибался паролем — посмотри список.", det,
                  "Get-WinEvent -FilterHashtable @{LogName='Security';Id=4625;StartTime=(Get-Date).AddDays(-7)}",
                  "Неудачные входы")


# ====================================================================== действия, которые меняют систему (после «Да»)
def clean_temp(days: int = 1) -> Result:
    """Временные файлы старше суток из %TEMP% (свои, без прав администратора). Занятые — пропускаем."""
    import os
    from pathlib import Path
    root = Path(os.environ.get("TEMP") or os.environ.get("TMP") or "/tmp")
    cutoff = time.time() - days * 86400
    freed = count = 0
    for p in sorted(root.rglob("*"), key=lambda x: -len(x.parts)):
        try:
            st = p.lstat()
            if st.st_mtime > cutoff:
                continue
            if p.is_file() or p.is_symlink():
                p.unlink()
                freed += st.st_size
                count += 1
            elif p.is_dir() and not any(p.iterdir()):
                p.rmdir()
        except OSError:
            continue
    from .convert import human_size
    return Result(f"Удалила {count} временных файлов, освободила {human_size(freed)}.", command=
                  "Get-ChildItem $env:TEMP -Recurse | Where-Object LastWriteTime -lt (Get-Date).AddDays(-1) | Remove-Item -Recurse -Force",
                  title="Временные файлы")


def flush_dns() -> Result:
    out = run_cmd(["ipconfig", "/flushdns"])
    run_cmd(["ipconfig", "/renew"], timeout=60)
    return Result("Сбросила кэш DNS и обновила адрес. Проверь, открываются ли сайты.", f"```\n{out.strip()[:400]}\n```",
                  "ipconfig /flushdns; ipconfig /renew", "Сброс сети")


def reset_network_full() -> Result:
    run_elevated("netsh winsock reset; netsh int ip reset; ipconfig /flushdns")
    return Result("Полный сброс сети сделан. Нужно перезагрузить компьютер, чтобы он сработал.",
                  command="netsh winsock reset; netsh int ip reset   (от администратора)", title="Сброс сети")


def clear_print_queue() -> Result:
    run_elevated("Stop-Service Spooler -Force; Remove-Item \"$env:SystemRoot\\System32\\spool\\PRINTERS\\*\" -Force "
                 "-ErrorAction SilentlyContinue; Start-Service Spooler")
    return Result("Очередь печати очищена, служба печати перезапущена. Попробуй напечатать снова.",
                  command="Stop-Service Spooler; Remove-Item C:\\Windows\\System32\\spool\\PRINTERS\\*; Start-Service Spooler",
                  title="Очередь печати")


def restart_service(name: str) -> Result:
    if not re.fullmatch(r"[\w .-]{2,60}", name):
        raise AdminError("странное имя службы")
    run_elevated(f"Restart-Service -Name '{name}' -Force")
    return service_status(name)


DANGER = {"clean_temp": clean_temp, "flush_dns": flush_dns, "reset_network": reset_network_full,
          "print_queue": clear_print_queue}
