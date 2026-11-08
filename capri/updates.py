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


UA = {"User-Agent": "Capri-updater", "Accept": "application/vnd.github+json"}
last_error = ""          # почему не получилось — показываем человеку, а не «нет интернета?»


def _why(e: Exception | None = None, status: int = 0) -> str:
    if status == 403 or status == 429:
        return "GitHub временно ограничил проверки с твоего адреса (лимит 60 в час) — попробую обходной путь"
    if status:
        return f"GitHub ответил {status}"
    s = f"{type(e).__name__}: {e}" if e else ""
    if "CERTIFICATE" in s.upper() or "SSL" in s.upper():
        return "не доверяю сертификату GitHub — обычно это антивирус или прокси, который проверяет HTTPS"
    if "ConnectTimeout" in s or "ReadTimeout" in s:
        return "GitHub не ответил за 10 секунд"
    if "getaddrinfo" in s or "Name or service" in s or "11001" in s:
        return "не нахожу github.com (DNS) — проверь интернет или VPN"
    if "ProxyError" in s or "407" in s:
        return "мешает прокси"
    return s[:160] or "неизвестная ошибка"


def latest() -> dict | None:
    """{'version', 'url', 'asset', 'size'} или None (тогда причина — в updates.last_error).

    1) API GitHub (точный размер файла); 2) если API не пустил (лимит 60 запросов в час на адрес) —
    страница github.com/…/releases/latest: она перенаправляет на последнюю версию, а ссылку на установщик
    собираем по имени (CapriSetup-<версия>.exe)."""
    global last_error
    last_error = ""
    try:
        r = httpx.get(f"https://api.github.com/repos/{REPO}/releases/latest", timeout=10, headers=UA)
        if r.status_code == 200:
            d = r.json()
            asset = next((a for a in d.get("assets") or [] if ASSET.match(a.get("name", ""))), None) or {}
            return {"version": d.get("tag_name", "").lstrip("v"), "url": d.get("html_url", ""),
                    "asset": asset.get("browser_download_url", ""), "size": int(asset.get("size") or 0)}
        last_error = _why(status=r.status_code)
    except (httpx.HTTPError, ValueError) as e:
        last_error = _why(e)
    try:                                                      # обходной путь без API
        r = httpx.get(f"https://github.com/{REPO}/releases/latest", timeout=10, follow_redirects=False,
                      headers={"User-Agent": "Capri-updater"})
        loc = r.headers.get("location", "")
        m = re.search(r"/releases/tag/v?([\d.]+)$", loc)
        if r.status_code in (301, 302) and m:
            v = m.group(1)
            asset = f"https://github.com/{REPO}/releases/download/v{v}/CapriSetup-{v}.exe"
            size = 0
            try:
                h = httpx.head(asset, timeout=10, follow_redirects=True, headers={"User-Agent": "Capri-updater"})
                size = int(h.headers.get("content-length") or 0) if h.status_code == 200 else 0
                if h.status_code == 404:
                    asset = ""
            except httpx.HTTPError:
                pass
            last_error = ""
            return {"version": v, "url": f"https://github.com/{REPO}/releases/tag/v{v}", "asset": asset, "size": size}
        last_error = last_error or _why(status=r.status_code)
    except httpx.HTTPError as e:
        last_error = _why(e) if not last_error or "лимит" in last_error else last_error
    from .log import log
    log.warning("обновления: %s", last_error)
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
