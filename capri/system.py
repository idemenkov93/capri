"""Выполнение системных команд на Windows: музыка, громкость, яркость, клавиши, скриншот, проверка ПК.

Всё импортируется лениво: если пакета нет или это не Windows — команда вернёт понятную ошибку,
а остальная Капри продолжит работать. Части кода адаптированы из kystt29/capricorn (MIT).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from .commands import Command, key_codes

IS_WIN = sys.platform == "win32"
NO_WINDOW = 0x08000000 if IS_WIN else 0
PLAYING = 4
TICKS = 10_000_000  # время в Windows — в тиках по 100 нс
VK_MEDIA = {"toggle": 0xB3, "next": 0xB0, "previous": 0xB1}


def run_ps(cmd: str, timeout: int = 60) -> str:
    full = ("[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
            "$ErrorActionPreference='SilentlyContinue'; " + cmd)
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", full],
                       capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
    return r.stdout.decode("utf-8", "replace").strip()


def ps_json(cmd: str) -> list:
    out = run_ps(cmd + " | ConvertTo-Json -Depth 3 -Compress")
    try:
        data = json.loads(out) if out else []
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else [data]


def _com_init():
    """CoInitialize для текущего потока; возвращает модуль comtypes, если инициализировали мы."""
    try:
        import comtypes
        comtypes.CoInitialize()
        return comtypes
    except Exception:  # noqa: BLE001 — уже инициализирован в другом режиме или comtypes нет
        return None


class SystemControl:
    """Один объект на приложение; методы вызываются из рабочего потока, не из интерфейса."""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)

    def run(self, cmd: Command) -> dict:
        if not IS_WIN:
            return {"error": "это работает только в Windows"}
        try:
            fn = getattr(self, "_" + cmd.op)
        except AttributeError:
            return {"error": f"неизвестная команда {cmd.op}"}
        com = _com_init()   # громкость (pycaw) — это COM, а мы в рабочем потоке: COM нужно включить для него
        try:
            return fn(cmd) or {}
        except ImportError as e:
            return {"error": f"не установлен пакет {e.name} — перезапусти run_capri.bat"}
        except Exception as e:  # noqa: BLE001 — любую ошибку показываем пользователю, а не падаем
            return {"error": str(e) or type(e).__name__}
        finally:
            if com:
                com.CoUninitialize()

    # ------------------------------------------------------------ клавиши
    @staticmethod
    def press(codes: list[tuple[int, bool]], repeat: int = 1) -> None:
        import ctypes
        u = ctypes.windll.user32
        for _ in range(max(1, repeat)):
            for code, ext in codes:
                u.keybd_event(code, 0, 1 if ext else 0, 0)
                time.sleep(0.02)
            for code, ext in reversed(codes):
                u.keybd_event(code, 0, (1 if ext else 0) | 2, 0)  # 2 = KEYUP
                time.sleep(0.02)
            time.sleep(0.05)

    def _keys(self, cmd: Command):
        self.press(key_codes(cmd.keys or []), cmd.repeat)

    # ------------------------------------------------------------ мышь, текст, окна (control.py)
    def _type(self, cmd: Command):
        from . import control
        control.type_text(cmd.text or "")

    def _scroll(self, cmd: Command):
        from . import control
        control.scroll(int(cmd.value or -5))

    def _click(self, cmd: Command):
        from . import control
        control.click("right" if cmd.text == "right" else "left", double=cmd.text == "double")

    def _mouse_center(self, cmd: Command):
        from . import control
        control.mouse_center()

    def _window(self, cmd: Command):
        from . import control
        name = (cmd.keys or [None])[0]
        return {"title": control.window(cmd.text or "minimize", name)}

    def _fullscreen(self, cmd: Command):
        """YouTube и плееры — клавиша F, остальное — F11 (браузер/проводник)."""
        from . import control
        h = control.target_window()
        title = control.window_title(h).lower()
        self.press(key_codes(["f"] if re.search(r"youtube|netflix|twitch|vlc|кинопоиск", title) else ["f11"]))
        return {"title": title}

    def _display(self, cmd: Command):
        from . import control
        mode = cmd.text or "extend"
        if mode in ("external", "extend") and control.connected_displays() < 2 and len(control.monitors()) < 2:
            return {"error": "второй монитор не подключён — переключать нечего"}
        return {"active": control.switch_display(mode)}

    def _nightlight(self, cmd: Command):
        from . import control
        try:
            if control.night_light(bool(cmd.value)):
                return {}
        except ImportError:
            os.startfile("ms-settings:nightlight")  # noqa: S606
        return {"opened": True}

    def _settings(self, cmd: Command):
        os.startfile(f"ms-settings:{cmd.text}")  # noqa: S606 — ms-settings: открывает нужную страницу «Параметров»
        return {}

    def _type_in(self, cmd: Command):
        """Напечатать в конкретной программе; не запущена — запустить и дождаться окна."""
        from . import control
        app = (cmd.keys or ["powershell"])[0]
        h = control.find_app_window(app)
        if not h:
            subprocess.Popen({"notepad": ["notepad.exe"], "cmd": ["cmd.exe"]}.get(app, ["powershell.exe"]),
                             creationflags=0x00000010)          # CREATE_NEW_CONSOLE — своё окно
            for _ in range(30):
                time.sleep(0.3)
                if h := control.find_app_window(app):
                    break
        if not h:
            return {"error": f"не дождалась окна {app}"}
        control.focus(h)
        time.sleep(0.4)
        control.type_text(cmd.text or "")
        return {"title": control.window_title(h)}

    def _move_monitor(self, cmd: Command):
        from . import control
        return control.move_to_monitor(cmd.text, (cmd.keys or ["next"])[0])

    def _click_name(self, cmd: Command, confirmed: bool = False):
        """Найти кнопку по названию в текущем окне и нажать. Рискованное («Удалить», «Отправить», «Оплатить»…) —
        только после «Да»: тогда возвращаем confirm, а нажимает уже действие click_confirmed."""
        import uiautomation as auto

        from . import control
        with auto.UIAutomationInitializerInThread():
            ctrl, found, score = control.find_element(cmd.text or "")
            if ctrl is None or score < 0.72:
                hint = f" (похожее: «{found}»)" if found and score > 0.5 else ""
                return {"error": f"не нашла в окне кнопку «{cmd.text}»{hint}"}
            if not confirmed and control.RISKY.search(found or cmd.text or ""):
                return {"confirm": True, "found": found}
            control.click_element(ctrl)
            return {"found": found}

    # ------------------------------------------------------------ музыка (системные мультимедиа-элементы)
    apps = None   # AppIndex (ставит агент) — чтобы узнать ID приложения YouTube Music на этом ПК

    def _kind_of(self, app_id: str) -> str:
        """Какой это плеер: music (YouTube Music, Яндекс Музыка…) | spotify | browser | other."""
        a = (app_id or "").lower()
        try:
            from .music import music_app_ids
            if a in music_app_ids(self.apps):
                return "music"
        except Exception:  # noqa: BLE001
            pass
        if "spotify" in a:
            return "spotify"
        if "_crx_" in a or "music" in a:
            return "music"          # сайт-приложение (YouTube Music и т.п.)
        if any(b in a for b in ("chrome", "msedge", "firefox", "opera", "brave", "vivaldi")):
            return "browser"
        return "other"

    async def _session(self, target: str | None = None, for_play: bool = False):
        """Какой плеер слушается команды. Порядок: то, что ты назвал («в ютуб мюзик»), → то, что у тебя перед
        глазами (окно YouTube Music / вкладка браузера) → то, что играет → последнее, что играло."""
        from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as M
        mgr = await M.request_async()
        sessions = list(mgr.get_sessions())
        if not sessions:
            return None
        want = target
        if not want:
            try:
                from . import control
                want = control.media_context()
            except Exception:  # noqa: BLE001
                want = None
        if want:
            mine = [x for x in sessions if self._kind_of(x.source_app_user_model_id) == want]
            if mine:
                playing = [x for x in mine if x.get_playback_info().playback_status == PLAYING]
                return (playing or mine)[0]
            if target:
                return None             # назвал плеер, а его нет — не трогаем чужой
        playing = [x for x in sessions if x.get_playback_info().playback_status == PLAYING]
        if playing and not for_play:
            return playing[0]
        if for_play:
            paused = [x for x in sessions if x.get_playback_info().playback_status != PLAYING]
            music = [x for x in paused if self._kind_of(x.source_app_user_model_id) in ("music", "spotify")]
            return (music or paused or playing or [None])[0] or mgr.get_current_session()
        return mgr.get_current_session() or sessions[0]

    @staticmethod
    async def _describe(s) -> str:
        p = await s.try_get_media_properties_async()
        if p is None:
            return ""
        title, artist = (p.title or "").strip(), (p.artist or "").strip()
        if artist and artist.lower() not in ("неизв. исполнитель", "unknown artist"):
            return f"{artist} — {title}"
        return title

    def _media(self, action: str, target: str | None = None) -> dict:
        try:
            import winrt.windows.media.control  # noqa: F401
        except ImportError:
            self.press([(VK_MEDIA["toggle" if action in ("play", "pause") else action], True)])
            return {}

        async def go():
            s = await self._session(target, for_play=action == "play")
            if (not s or self._kind_of(s.source_app_user_model_id) == "browser") and self._music_tab(target):
                from . import control
                if control.press_player_button(action):
                    return {}
            if not s:
                return {"nothing": True, "target": target}
            if action == "pause" and s.get_playback_info().playback_status != PLAYING:
                return {}
            f = {"pause": s.try_pause_async, "play": s.try_play_async,
                 "next": s.try_skip_next_async, "previous": s.try_skip_previous_async}[action]
            await f()
            if action in ("next", "previous"):
                await asyncio.sleep(0.8)  # плееру нужно время, чтобы сменить трек
                return {"track": await self._describe(s)}
            return {}
        return asyncio.run(go())

    @staticmethod
    def _music_tab(target: str | None) -> bool:
        """YouTube Music открыт вкладкой в браузере (не приложением): у вкладок общий сеанс с YouTube,
        поэтому команду надо нажать кнопкой на самой странице."""
        if target not in (None, "music"):
            return False
        try:
            from . import control
            if target is None and control.media_context() != "music":
                return False
            h = control.find_app_window("youtube music")
            return bool(h) and "youtube music" in control.window_title(h).lower()
        except Exception:  # noqa: BLE001
            return False

    def _pause(self, cmd):
        return self._media("pause", cmd.text)

    def _play(self, cmd):
        return self._media("play", cmd.text)

    def _next(self, cmd):
        return self._media("next", cmd.text)

    def _previous(self, cmd):
        return self._media("previous", cmd.text)

    def _now_playing(self, cmd):
        async def go():
            s = await self._session(cmd.text)
            return {"track": await self._describe(s)} if s else {}
        return asyncio.run(go())

    def _seek(self, absolute: float | None, delta: float | None) -> dict:
        async def go():
            s = await self._session()
            if not s:
                return {"nothing": True}
            if not s.get_playback_info().controls.is_playback_position_enabled:
                return {"unsupported": True}
            tl = s.get_timeline_properties()
            pos, end = tl.position.total_seconds(), tl.end_time.total_seconds()
            if s.get_playback_info().playback_status == PLAYING and tl.last_updated_time.year > 1970:
                pos += max(0.0, (dt.datetime.now(dt.timezone.utc) - tl.last_updated_time).total_seconds())
            target = absolute if absolute is not None else pos + (delta or 0)
            target = max(0.0, min(target, end - 1) if end > 0 else target)
            ok = await s.try_change_playback_position_async(int(target * TICKS))
            return {} if ok else {"unsupported": True}
        return asyncio.run(go())

    def _seek_to(self, cmd):
        return self._seek(cmd.value or 0, None)

    def _seek_by(self, cmd):
        return self._seek(None, cmd.value or 0)

    # ------------------------------------------------------------ громкость
    @staticmethod
    def _endpoint():
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        dev = AudioUtilities.GetSpeakers()
        if hasattr(dev, "EndpointVolume"):
            return dev.EndpointVolume
        from ctypes import POINTER, cast

        from comtypes import CLSCTX_ALL
        return cast(dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None), POINTER(IAudioEndpointVolume))

    def _volume_state(self) -> dict:
        ep = self._endpoint()
        return {"volume": round(ep.GetMasterVolumeLevelScalar() * 100), "muted": bool(ep.GetMute())}

    def _volume_get(self, cmd):
        return self._volume_state()

    def _set_volume(self, v: float) -> dict:
        ep = self._endpoint()
        v = max(0, min(100, round(v)))
        ep.SetMasterVolumeLevelScalar(v / 100, None)
        if v > 0:
            ep.SetMute(0, None)
        return {"volume": v}

    def _volume_set(self, cmd):
        return self._set_volume(cmd.value or 0)

    def _volume_change(self, cmd):
        return self._set_volume(self._volume_state()["volume"] + (cmd.value or 0))

    def _mute(self, cmd):
        self._endpoint().SetMute(1, None)

    def _unmute(self, cmd):
        self._endpoint().SetMute(0, None)

    # ------------------------------------------------------------ яркость (ноутбук или монитор с DDC/CI)
    @staticmethod
    def _set_brightness(v: float) -> dict:
        import screen_brightness_control as sbc
        v = max(0, min(100, round(v)))
        sbc.set_brightness(v)
        return {"brightness": v}

    def _brightness_set(self, cmd):
        return self._set_brightness(cmd.value or 0)

    def _brightness_change(self, cmd):
        import screen_brightness_control as sbc
        return self._set_brightness(sbc.get_brightness()[0] + (cmd.value or 0))

    # ------------------------------------------------------------ скриншот и блокировка
    @staticmethod
    def pictures_dir() -> Path:
        import ctypes
        buf = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x27, None, 0, buf) == 0:  # CSIDL_MYPICTURES
            return Path(buf.value)
        return Path.home() / "Pictures"

    def _screenshot(self, cmd):
        from PIL import ImageGrab
        folder = self.pictures_dir() / "Capri"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"Screenshot_{dt.datetime.now():%Y-%m-%d_%H-%M-%S}.png"
        ImageGrab.grab(all_screens=True).save(path)
        return {"path": str(path)}

    def _lock(self, cmd):
        import ctypes
        ctypes.windll.user32.LockWorkStation()

    # ------------------------------------------------------------ проверка ПК
    def _pc_check(self, cmd):
        r = analyze_pc()
        path = self.data_dir / "pc_report.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(r["report"], encoding="utf-8-sig")  # BOM — чтобы блокнот точно понял UTF-8
        subprocess.Popen(["notepad.exe", str(path)], creationflags=NO_WINDOW)
        r["path"] = str(path)
        return r


def analyze_pc() -> dict:
    """Снимок здоровья системы: загрузка, память, диски, ошибки в журналах, тяжёлые процессы."""
    import psutil

    issues: list[str] = []
    lines: list[str] = []
    add = lines.append
    now = dt.datetime.now()
    add(f"ДИАГНОСТИКА ПК / PC CHECK — {now:%d.%m.%Y %H:%M}")
    add("=" * 60)

    # загрузку процесса меряем одновременно с общей: первый вызов cpu_percent «обнуляет» счётчик
    procs = list(psutil.process_iter(["name", "memory_info"]))
    for p in procs:
        try:
            p.cpu_percent(None)
        except psutil.Error:
            pass
    cpu = psutil.cpu_percent(interval=1.5)
    cores = psutil.cpu_count() or 1
    top_cpu: list[tuple[float, str]] = []
    for p in procs:
        try:
            name = p.info["name"] or "?"
            if name.lower() in ("system idle process", "idle"):
                continue
            top_cpu.append((p.cpu_percent(None) / cores, name))
        except psutil.Error:
            pass
    top_cpu.sort(reverse=True)
    mem = psutil.virtual_memory()
    add(f"Процессор / CPU: {cpu:.0f}% ({cores} потоков)")
    add(f"Память / RAM: {mem.percent:.0f}% ({mem.used / 2**30:.1f} из {mem.total / 2**30:.1f} ГБ)")
    if cpu > 85:
        issues.append(f"процессор загружен на {cpu:.0f}%")
    if mem.percent > 85:
        issues.append(f"память занята на {mem.percent:.0f}%")

    up = now - dt.datetime.fromtimestamp(psutil.boot_time())
    add(f"Без перезагрузки / Uptime: {up.days} д {up.seconds // 3600} ч")
    if up.days >= 7:
        issues.append(f"компьютер не перезагружался {up.days} дней")
    bat = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
    if bat:
        add(f"Батарея / Battery: {bat.percent:.0f}%{' (заряжается)' if bat.power_plugged else ''}")

    add("\nДИСКИ / DISKS:")
    for part in psutil.disk_partitions():
        if "cdrom" in part.opts or not part.fstype:
            continue
        try:
            u = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        free = u.free / 2**30
        add(f"  {part.mountpoint} {part.fstype}: свободно {free:.1f} ГБ из {u.total / 2**30:.0f} ГБ")
        if u.percent > 90 or free < 10:
            issues.append(f"на диске {part.mountpoint[:2]} мало места ({free:.0f} ГБ)")
    for d in ps_json("Get-PhysicalDisk | Select-Object FriendlyName,HealthStatus,OperationalStatus"):
        add(f"  Накопитель {d.get('FriendlyName')}: {d.get('HealthStatus')} / {d.get('OperationalStatus')}")
        if str(d.get("HealthStatus")) not in ("Healthy", "0"):
            issues.append(f"накопитель {d.get('FriendlyName')} сообщает о проблемах")

    add("\nУСТРОЙСТВА С ОШИБКАМИ / DEVICES WITH ERRORS:")
    bad = ps_json("Get-PnpDevice -PresentOnly | Where-Object {$_.Status -eq 'Error'} | Select-Object FriendlyName,Class")
    for d in bad:
        add(f"  {d.get('FriendlyName')} ({d.get('Class')})")
    if bad:
        issues.append(f"устройств с ошибкой драйвера: {len(bad)}")
    else:
        add("  нет / none")

    add("\nОШИБКИ В ЖУРНАЛАХ ЗА 24 Ч / EVENT LOG ERRORS (24 h):")
    groups = ps_json("Get-WinEvent -FilterHashtable @{LogName='System','Application';Level=1,2;"
                     "StartTime=(Get-Date).AddDays(-1)} | Group-Object ProviderName | "
                     "Sort-Object Count -Descending | Select-Object -First 10 Count,Name")
    total = sum(int(g.get("Count", 0)) for g in groups)
    for g in groups:
        add(f"  {g.get('Count'):>4}  {g.get('Name')}")
    if not groups:
        add("  нет / none")
    if total > 30:
        issues.append(f"в журналах {total} ошибок за сутки")
    add("  Подробно: eventvwr.msc → Журналы Windows → Система")

    crashes = run_ps("(Get-WinEvent -FilterHashtable @{LogName='System';Id=41;StartTime=(Get-Date).AddDays(-7)} | Measure-Object).Count")
    crashes_n = int(crashes) if crashes.isdigit() else 0
    add(f"\nАварийные выключения за 7 дней / unexpected shutdowns (7 d): {crashes_n}")
    if crashes_n:
        issues.append(f"аварийных выключений за неделю: {crashes_n}")

    add("\nТОП ПО ПРОЦЕССОРУ / TOP CPU:")
    for pct, name in top_cpu[:6]:
        add(f"  {pct:>5.1f}%  {name}")
    add("\nТОП ПО ПАМЯТИ / TOP MEMORY:")
    by_mem = []
    for p in procs:
        try:
            by_mem.append((p.info["memory_info"].rss, p.info["name"]))
        except Exception:  # noqa: BLE001
            pass
    for rss, name in sorted(by_mem, reverse=True)[:8]:
        add(f"  {rss / 2**20:>7.0f} МБ  {name}")

    add("\nАВТОЗАГРУЗКА / STARTUP:")
    for s in ps_json("Get-CimInstance Win32_StartupCommand | Select-Object Name,Location"):
        add(f"  {s.get('Name')}  [{s.get('Location')}]")

    add("\n" + "=" * 60)
    add("НАЙДЕННЫЕ ПРОБЛЕМЫ:" if issues else "Серьёзных проблем не найдено.")
    for i in issues:
        add(f"  • {i}")
    hog = top_cpu[0][1] if top_cpu and top_cpu[0][0] >= 15 else None
    return {"cpu": cpu, "ram_percent": mem.percent, "issues": issues, "top_cpu": hog,
            "report": "\n".join(lines)}


def input_devices() -> list[tuple[int, str]]:
    """Микрофоны: [(индекс, имя)] — без дублей, которые Windows показывает через разные драйверы."""
    try:
        import sounddevice as sd
    except (ImportError, OSError):  # OSError — нет PortAudio
        return []
    out, seen = [], set()
    try:
        apis = sd.query_hostapis()
        mme = next((i for i, a in enumerate(apis) if "MME" in a["name"]), None)
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] <= 0 or (mme is not None and d["hostapi"] != mme):
                continue
            name = re.sub(r"\s+", " ", d["name"]).strip()
            if name.lower().startswith(("microsoft sound mapper", "переназначение звуковых")) or name in seen:
                continue
            seen.add(name)
            out.append((i, name))
    except Exception:  # noqa: BLE001
        return []
    return out
