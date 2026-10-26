"""Окошки «простого» подключения Google: ссылка календаря и пароль приложения для почты."""
from __future__ import annotations

import webbrowser

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QLineEdit, QVBoxLayout

CAL_PAGE = "https://calendar.google.com/calendar/u/0/r/settings"
APP_PW_PAGE = "https://myaccount.google.com/apppasswords"
TWO_STEP_PAGE = "https://myaccount.google.com/signinoptions/twosv"

CAL_TEXT = (
    "<b>Календарь — одна ссылка</b><br><br>"
    "1. В браузере открылись настройки Google Календаря.<br>"
    "2. Слева в «Настройки моих календарей» выбери свой календарь (обычно с твоим именем).<br>"
    "3. Прокрути до «Интеграция календаря» → <b>«Секретный адрес в формате iCal»</b> → значок «копировать».<br>"
    "4. Вставь ссылку сюда.<br><br>"
    "<small>По ссылке Капри только читает события. Ссылка хранится в Диспетчере учётных данных Windows.</small>")
MAIL_TEXT = (
    "<b>Почта — пароль приложения (2 минуты)</b><br><br>"
    "1. В браузере открылась страница «Пароли приложений» Google.<br>"
    "2. Название — <b>Capri</b> → «Создать» → скопируй 16 букв.<br>"
    f"&nbsp;&nbsp;&nbsp;Если страница пишет, что недоступно, — сначала включи <a href='{TWO_STEP_PAGE}'>двухэтапную аутентификацию</a>.<br>"
    "3. Впиши свою почту и этот пароль.<br><br>"
    "<small>Капри только читает, от кого новые письма и тема — ничего не отправляет и не помечает прочитанным. "
    "Пароль хранится в Диспетчере учётных данных Windows; отозвать — на той же странице Google.</small>")


class ConnectDialog(QDialog):
    def __init__(self, title: str, html: str, fields: list[tuple[str, str, bool]], parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlag(Qt.WindowStaysOnTopHint)
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lab = QLabel(html)
        lab.setWordWrap(True)
        lab.setOpenExternalLinks(True)
        lay.addWidget(lab)
        self.edits: dict[str, QLineEdit] = {}
        for key, placeholder, secret in fields:
            e = QLineEdit(placeholderText=placeholder)
            if secret:
                e.setEchoMode(QLineEdit.Password)
            lay.addWidget(e)
            self.edits[key] = e
        self.error = QLabel("")
        self.error.setStyleSheet("color: #ff3355")
        lay.addWidget(self.error)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def values(self) -> dict[str, str]:
        return {k: e.text().strip() for k, e in self.edits.items()}


def ask_calendar(parent=None, check=None) -> str | None:
    """Ссылка iCal или None. check(url) → текст ошибки или None."""
    webbrowser.open(CAL_PAGE)
    d = ConnectDialog("Capri — календарь", CAL_TEXT, [("url", "https://calendar.google.com/calendar/ical/…/basic.ics", False)], parent)
    while d.exec() == QDialog.Accepted:
        url = d.values()["url"]
        err = check(url) if check else None
        if not err:
            return url
        d.error.setText(err)
    return None


def ask_mail(parent=None, check=None) -> tuple[str, str] | None:
    """(почта, пароль приложения) или None. check(addr, pw) → текст ошибки или None."""
    webbrowser.open(APP_PW_PAGE)
    d = ConnectDialog("Capri — почта", MAIL_TEXT,
                      [("addr", "твоя@gmail.com", False), ("pw", "пароль приложения (16 букв)", True)], parent)
    while d.exec() == QDialog.Accepted:
        v = d.values()
        err = check(v["addr"], v["pw"]) if check else None
        if not err:
            return v["addr"], v["pw"]
        d.error.setText(err)
    return None
