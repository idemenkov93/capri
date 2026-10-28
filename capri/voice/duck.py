"""Пока Капри слушает — музыка и видео тише (как у телефона при звонке).

Громкая музыка из колонок попадает в микрофон: Капри не слышит, где ты договорил, слушает до упора и
распознаёт песню вместо команды. Поэтому на время записи громкость других программ (не самой Капри и не
системная) опускается до четверти, а потом возвращается — если ты её за это время сам не поменял.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor


class Ducker:
    def __init__(self, factor: float = 0.25):
        self.factor = factor
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="duck")   # всё по порядку, в одном потоке
        self._saved: list[tuple[object, float, float]] = []

    def duck(self) -> None:
        self._pool.submit(self._safe, self._duck)

    def restore(self) -> None:
        self._pool.submit(self._safe, self._restore)

    @staticmethod
    def _safe(fn):
        try:
            fn()
        except Exception:  # noqa: BLE001 — нет pycaw или звук недоступен: просто не приглушаем
            from ..log import log
            log.debug("duck: не вышло", exc_info=True)

    @staticmethod
    def _sessions():
        try:
            import comtypes
            comtypes.CoInitialize()
        except Exception:  # noqa: BLE001
            pass
        from pycaw.pycaw import AudioUtilities
        me = os.getpid()
        for s in AudioUtilities.GetAllSessions():
            if s.Process is None or s.ProcessId == me:
                continue                                   # системные звуки и сама Капри — не трогаем
            yield s

    def _duck(self) -> None:
        if self._saved:
            return
        for s in self._sessions():
            vol = s.SimpleAudioVolume
            old = float(vol.GetMasterVolume())
            if old <= 0.02:
                continue
            new = old * self.factor
            vol.SetMasterVolume(new, None)
            self._saved.append((vol, old, new))

    def _restore(self) -> None:
        saved, self._saved = self._saved, []
        for vol, old, new in saved:
            try:
                if abs(float(vol.GetMasterVolume()) - new) < 0.03:   # сам не крутил — возвращаем как было
                    vol.SetMasterVolume(old, None)
            except Exception:  # noqa: BLE001 — программа закрылась
                continue

    def close(self) -> None:
        self.restore()
        self._pool.shutdown(wait=True, cancel_futures=False)
