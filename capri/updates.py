"""Обновления Capri.exe: последняя версия в GitHub Releases → скачать установщик → тихо поставить → перезапуститься.

Меню «⬆ Проверить обновления» или голосом «обнови капри». Установщик ставится поверх: настройки, скины, ключи
и модели лежат в %LOCALAPPDATA%\\Capri и не трогаются. После тихой установки он сам запускает новую Капри.
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

import httpx

from . import __version__

REPO = "idemenkov93/capri"
ASSET = re.compile(r"^CapriSetup-[\d.]+\.exe$")       # только публичный установщик (без -personal)
# /VERYSILENT — без окон; /CLOSEAPPLICATIONS — закрыть старую Капри, если ещё жива; запуск — секция [Run] (WizardSilent)
SILENT = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/SP-"]


def _v(text: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", text)[:3])


def latest() -> dict | None:
    """{'version', 'url' (страница релиза), 'asset' (ссылка на установщик или ''), 'size'} или None (нет сети)."""
    try:
        r = httpx.get(f"https://api.github.com/repos/{REPO}/releases/latest", timeout=10,
                      headers={"Accept": "application/vnd.github+json"})
        if r.status_code != 200:
            return None
        d = r.json()
        asset = next((a for a in d.get("assets") or [] if ASSET.match(a.get("name", ""))), None) or {}
        return {"version": d.get("tag_name", "").lstrip("v"), "url": d.get("html_url", ""),
                "asset": asset.get("browser_download_url", ""), "size": int(asset.get("size") or 0)}
    except (httpx.HTTPError, ValueError):
        return None


def newer(version: str, current: str = __version__) -> bool:
    return bool(version) and _v(version) > _v(current)


def download(info: dict, progress: Callable[[int], None] | None = None, folder: Path | None = None) -> Path:
    """Скачать установщик во временную папку. Размер сверяем с GitHub — оборванный файл не запускаем."""
    url = info.get("asset") or ""
    if not url.startswith(f"https://github.com/{REPO}/releases/download/"):
        raise RuntimeError("в релизе нет установщика")
    out = Path(folder or tempfile.gettempdir()) / url.rsplit("/", 1)[-1]
    part = out.with_suffix(".part")
    total, got, last = info.get("size") or 0, 0, -1
    with httpx.stream("GET", url, follow_redirects=True, timeout=httpx.Timeout(30, read=60)) as r:
        r.raise_for_status()
        total = total or int(r.headers.get("content-length") or 0)
        with part.open("wb") as f:
            for chunk in r.iter_bytes(1 << 16):
                f.write(chunk)
                got += len(chunk)
                pct = int(100 * got / total) if total else 0
                if progress and pct != last:
                    last = pct
                    progress(pct)
    if total and got != total:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"скачалось {got} из {total} байт — попробуй ещё раз")
    part.replace(out)
    return out


def install(setup: Path) -> None:
    """Запустить установщик тихо и отдельно от Капри (она сразу закроется, он её заменит и запустит заново)."""
    flags = 0x00000008 | 0x00000200 if sys.platform == "win32" else 0     # DETACHED_PROCESS | NEW_PROCESS_GROUP
    subprocess.Popen([str(setup), *SILENT], close_fds=True, creationflags=flags)  # noqa: S603
