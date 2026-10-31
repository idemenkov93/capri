"""Танцы под музыку: следим, играет ли музыка (YouTube Music, Spotify…), и слушаем громкость вывода.

Поток раз в ~1.5 с спрашивает Windows, что играет (те же «мультимедиа-элементы», что в плашке громкости),
и 20 раз в секунду читает пиковый уровень звука колонок. Наружу — level (0..1, насколько «качает»)
и kick (сильная доля — подпрыгнуть ровно в такт).
"""
from __future__ import annotations

import asyncio
import re
import time

from PySide6.QtCore import QThread, Signal

MUSIC_APPS = ("music", "spotify", "yandex", "vlc", "winamp", "foobar", "itunes", "deezer", "tidal", "soundcloud",
              "aimp", "groove", "zune", "cinhimbnkk")  # «cinhimbnkk…» — начало id сайта-приложения YouTube Music в Chrome


MUSIC_NAME = re.compile(r"music|musik|spotify|deezer|tidal|soundcloud|yandex|vlc|winamp|foobar|aimp|itunes|groove", re.I)


def music_app_ids(apps) -> set[str]:
    """ID музыкальных приложений из меню «Пуск»: у сайта-приложения YouTube Music он свой на каждом ПК
    (Chrome._crx_…), поэтому берём его из списка программ, а не угадываем."""
    return {a["id"].lower() for a in (getattr(apps, "apps", None) or []) if MUSIC_NAME.search(a.get("name", ""))}


def is_music(app_id: str, album: str, artist: str, any_media: bool = False, known_ids: set[str] | None = None) -> bool:
    """Музыка ли это (а не ролик на YouTube или созвон): по приложению или по наличию альбома."""
    if any_media:
        return True
    a = (app_id or "").lower()
    if known_ids and a in known_ids:
        return True
    return any(m in a for m in MUSIC_APPS) or bool((album or "").strip())


class Beat:
    """Простой детектор «ударов»: громкость резко выше средней → kick."""

    def __init__(self):
        self.avg = 0.0
        self.env = 0.0
        self.last_kick = 0.0

    def push(self, peak: float, now: float) -> tuple[float, bool]:
        self.env = max(peak, self.env * 0.85)              # быстрая атака, плавный спад
        self.avg = self.avg * 0.97 + peak * 0.03
        kick = peak > max(0.08, self.avg * 1.35) and now - self.last_kick > 0.28
        if kick:
            self.last_kick = now
        level = min(1.0, self.env * 1.6)
        return level, kick


class MusicWatcher(QThread):
    level = Signal(float)      # 0 — не танцуем
    kick = Signal()
    playing = Signal(str)      # что играет (для журнала), "" — перестало
    talking = Signal(bool)     # играет НЕ музыка (видео, подкаст, стрим) — там много речи, «Капри» может послышаться

    def __init__(self, any_media: bool = False, apps=None):
        super().__init__()
        self.any_media = any_media
        self.apps = apps      # AppIndex — чтобы узнать YouTube Music и др. по их ID
        self.slow = False     # режим ожидания: реже спрашивать Windows, что играет
        self._stop = False

    def stop(self):
        self._stop = True
        self.wait(6000)          # опрос плеера может занять пару секунд — поток должен закончиться до удаления

    @staticmethod
    async def _now_playing():
        from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as M
        mgr = await M.request_async()
        for s in mgr.get_sessions():
            if s.get_playback_info().playback_status == 4:      # Playing
                p = await s.try_get_media_properties_async()
                return s.source_app_user_model_id or "", (p.album_title or "") if p else "", \
                    (p.artist or "") if p else "", (p.title or "") if p else ""
        return None

    def _meter(self):
        from ctypes import POINTER, cast

        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioMeterInformation
        dev = AudioUtilities.GetSpeakers()
        raw = getattr(dev, "_dev", dev)
        return cast(raw.Activate(IAudioMeterInformation._iid_, CLSCTX_ALL, None), POINTER(IAudioMeterInformation))

    def run(self):
        from .log import log
        try:
            import comtypes
            comtypes.CoInitialize()
            meter = self._meter()
        except Exception as e:  # noqa: BLE001
            log.warning("танцы выключены: нет доступа к звуку (%s)", e)
            return
        beat = Beat()
        music, last_check, last_title, talk = False, 0.0, "", False
        while not self._stop:
            now = time.monotonic()
            if now - last_check > (1.5 if music or not self.slow else 5.0):   # в режиме ожидания — реже
                last_check = now
                try:
                    np_ = asyncio.run(self._now_playing())
                except Exception:  # noqa: BLE001
                    np_ = None
                music = bool(np_) and is_music(np_[0], np_[1], np_[2], self.any_media, music_app_ids(self.apps))
                real_music = bool(np_) and is_music(np_[0], np_[1], np_[2], False, music_app_ids(self.apps))
                new_talk = bool(np_) and not real_music
                if new_talk != talk:
                    talk = new_talk
                    if talk:
                        log.info("играет не музыка: %s — %s (%s)", np_[2], np_[3], np_[0])
                    self.talking.emit(talk)
                title = f"{np_[2]} — {np_[3]}" if np_ and music else ""
                if title != last_title:
                    last_title = title
                    self.playing.emit(title)
                    if title:
                        log.info("музыка: %s (%s)", title, np_[0])
            if music:
                try:
                    lvl, k = beat.push(meter.GetPeakValue(), now)
                except Exception:  # noqa: BLE001
                    lvl, k = 0.0, False
                self.level.emit(lvl)
                if k:
                    self.kick.emit()
            else:
                self.level.emit(0.0)
            time.sleep(0.05 if music else 0.5)   # без музыки громкость колонок не нужна — спим дольше
