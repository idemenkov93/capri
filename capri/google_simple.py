"""Google без Google Cloud — «простой режим» (подключение за 2 минуты, без проектов и OAuth):

- Календарь (чтение): «Секретный адрес в формате iCal» из настроек календаря — одна ссылка.
- Календарь (добавить): открываем в браузере уже заполненное событие Google Календаря — остаётся нажать «Сохранить».
- Почта (непрочитанные): Gmail по IMAP с «паролем приложения» (16 букв, создаётся в аккаунте Google).
- Задачи: свой список дел Капри (data/todo.md) — Google Tasks доступен только в полном режиме (docs/GOOGLE.md).

Секреты (ссылка календаря, пароль приложения) хранятся в Диспетчере учётных данных Windows (keyring), не в файлах.
"""
from __future__ import annotations

import datetime as dt
import email
import email.header
import imaplib
import re
from pathlib import Path
from urllib.parse import quote

SERVICE = "capri-google"


# ------------------------------------------------------------------ секреты
def get_secret(name: str) -> str | None:
    try:
        import keyring
        return keyring.get_password(SERVICE, name)
    except Exception:  # noqa: BLE001 — нет keyring/хранилища
        return None


def set_secret(name: str, value: str) -> None:
    import keyring
    keyring.set_password(SERVICE, name, value)


# ------------------------------------------------------------------ календарь (iCal)
def valid_ical_url(url: str) -> bool:
    return bool(re.match(r"^https://calendar\.google\.com/calendar/ical/.+\.ics$", url.strip()))


def agenda_from_ics(ics: bytes, day: dt.date, tz) -> list[tuple[str, str]]:
    """[(время или «весь день», название)] на этот день — с повторяющимися событиями."""
    import icalendar
    import recurring_ical_events
    cal = icalendar.Calendar.from_ical(ics)
    start = dt.datetime.combine(day, dt.time.min, tz)
    items = recurring_ical_events.of(cal).between(start, start + dt.timedelta(days=1))
    out = []
    for ev in items:
        s = ev.get("DTSTART").dt
        title = str(ev.get("SUMMARY", "без названия"))
        if isinstance(s, dt.datetime):
            out.append((s.astimezone(tz).strftime("%H:%M"), title))
        else:
            out.append(("весь день", title))
    return sorted(out, key=lambda x: (x[0] != "весь день", x[0]))


def fetch_agenda(url: str, day: dt.date, tz) -> list[tuple[str, str]]:
    import httpx
    r = httpx.get(url, timeout=15, follow_redirects=True)
    r.raise_for_status()
    return agenda_from_ics(r.content, day, tz)


def calendar_add_url(ev: dict) -> str:
    """Ссылка «создать событие» в Google Календаре: откроется заполненная форма, нажать «Сохранить»."""
    if ev["all_day"]:
        dates = f"{ev['start']:%Y%m%d}/{ev['end']:%Y%m%d}"
    else:
        utc = dt.timezone.utc
        dates = f"{ev['start'].astimezone(utc):%Y%m%dT%H%M%SZ}/{ev['end'].astimezone(utc):%Y%m%dT%H%M%SZ}"
    return f"https://calendar.google.com/calendar/render?action=TEMPLATE&text={quote(ev['title'])}&dates={dates}"


# ------------------------------------------------------------------ почта (IMAP)
def _decode(h: str | None) -> str:
    if not h:
        return ""
    parts = []
    for text, enc in email.header.decode_header(h):
        parts.append(text.decode(enc or "utf-8", "replace") if isinstance(text, bytes) else text)
    return "".join(parts)


def unread_imap(address: str, app_password: str, limit: int = 3, imap_cls=imaplib.IMAP4_SSL) -> str:
    """Непрочитанные во «Входящих» (только чтение, письма остаются непрочитанными)."""
    m = imap_cls("imap.gmail.com")
    try:
        m.login(address, app_password.replace(" ", ""))
        m.select("INBOX", readonly=True)
        _, data = m.search(None, "UNSEEN")
        ids = data[0].split() if data and data[0] else []
        if not ids:
            return "Новых писем нет. Тишина."
        lines = []
        for mid in reversed(ids[-limit:]):
            _, msg = m.fetch(mid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
            hdr = email.message_from_bytes(msg[0][1])
            sender = re.sub(r"\s*<.*?>", "", _decode(hdr.get("From"))).strip('" ') or "?"
            lines.append(f"от {sender}: «{_decode(hdr.get('Subject')) or '(без темы)'}»")
        more = f" Всего непрочитанных: {len(ids)}." if len(ids) > len(lines) else ""
        return "Новые письма: " + "; ".join(lines) + "." + more
    finally:
        try:
            m.logout()
        except Exception:  # noqa: BLE001
            pass


# ------------------------------------------------------------------ свой список дел
class Todo:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _lines(self) -> list[str]:
        try:
            return self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []

    def open_items(self) -> list[str]:
        return [ln[6:] for ln in self._lines() if ln.startswith("- [ ] ")]

    def add(self, title: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lines = self._lines() or ["# Список дел Капри", ""]
        lines.append(f"- [ ] {title}")
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def done(self, words: str) -> str | None:
        """Отметить выполненной задачу, в которой есть эти слова. Возвращает её название."""
        lines = self._lines()
        w = words.lower()
        for i, ln in enumerate(lines):
            if ln.startswith("- [ ] ") and w in ln.lower():
                lines[i] = "- [x] " + ln[6:]
                self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                return ln[6:]
        return None
