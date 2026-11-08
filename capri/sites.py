"""Проверка сайтов и серверов: открывается ли, за сколько, когда кончается SSL-сертификат.
«Следи за сайтом …» — проверяю каждые 5 минут и сама говорю, когда упал и когда поднялся."""
from __future__ import annotations

import datetime as dt
import re
import socket
import ssl
import time
from dataclasses import dataclass
from urllib.parse import urlparse

RE_HOST = re.compile(r"^(?:https?://)?([a-z0-9.-]+\.[a-z]{2,}|localhost|\d{1,3}(?:\.\d{1,3}){3})(?::(\d+))?(/\S*)?$", re.I)


@dataclass
class Check:
    url: str
    ok: bool
    status: int = 0
    ms: float = 0.0
    ssl_days: int | None = None
    error: str = ""

    def line(self) -> str:
        if not self.ok:
            return f"❌ {host_of(self.url)} — не работает ({self.error or self.status})"
        ssl_txt = "" if self.ssl_days is None else f", сертификат ещё {self.ssl_days} дн." + (" ⚠" if self.ssl_days < 14 else "")
        return f"✅ {host_of(self.url)} — {self.status}, {self.ms:.0f} мс{ssl_txt}"


def normalize(text: str) -> str | None:
    t = text.strip().strip(".,!?«»\"'").lower().replace(" точка ", ".")
    m = RE_HOST.match(t)
    if not m:
        return None
    return t if t.startswith("http") else f"https://{t}"


def host_of(url: str) -> str:
    return urlparse(url).hostname or url


def ssl_days_left(host: str, port: int = 443, timeout: float = 6) -> int | None:
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=timeout) as s, ctx.wrap_socket(s, server_hostname=host) as tls:
            cert = tls.getpeercert()
    except (OSError, ssl.SSLError):
        return None
    end = dt.datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z")
    return (end - dt.datetime.utcnow()).days


def check(url: str, timeout: float = 10) -> Check:
    import httpx
    t0 = time.monotonic()
    try:
        r = httpx.get(url, timeout=timeout, follow_redirects=True, headers={"User-Agent": "Capri-site-check"})
        ms = (time.monotonic() - t0) * 1000
        ok = r.status_code < 500
        c = Check(url, ok, r.status_code, ms, error="" if ok else f"ошибка сервера {r.status_code}")
    except httpx.ConnectError as e:
        c = Check(url, False, error="не соединяется" if "Name" not in str(e) else "нет такого адреса (DNS)")
    except httpx.TimeoutException:
        c = Check(url, False, error=f"не ответил за {timeout:.0f} с")
    except Exception as e:  # noqa: BLE001
        c = Check(url, False, error=type(e).__name__)
    if c.ok and url.startswith("https"):
        c.ssl_days = ssl_days_left(host_of(url))
    return c


def say(c: Check) -> str:
    h = host_of(c.url)
    if not c.ok:
        return f"{h} не работает: {c.error or c.status}."
    s = f"{h} работает, ответил за {c.ms:.0f} мс"
    if c.ssl_days is not None:
        s += f", сертификат ещё {c.ssl_days} дней" + (" — скоро кончится!" if c.ssl_days < 14 else "")
    return s + "."
