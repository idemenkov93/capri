"""Волна 2: «что в буфере», перевод выделенного, «объясни / ответь на письмо», текст с экрана, QR, диктовка.

Всё показывается в маленьком окне у курсора (ui/popup.py) — прочитать и скопировать, без озвучки.
Думает «умный помощник» (Gemini / Claude / локальная модель — что доступно), картинки — Gemini/Claude или
локальная модель со зрением. QR-коды читаются офлайн (zxing-cpp).
"""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtCore import QUrl

from . import textops as T

FLAGS = (("ru", "🇷🇺 RU"), ("de", "🇩🇪 DE"), ("en", "🇬🇧 EN"), ("he", "🇮🇱 HE"))
TEXT_FILES = (".txt", ".md", ".log", ".csv", ".json", ".xml", ".ps1", ".py", ".yaml", ".yml", ".ini", ".cfg", ".eml")
IMAGE_FILES = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")


class BrainJob(QThread):
    done = Signal(str, str)          # ответ, ошибка

    def __init__(self, agent, task: str, images: list | None = None, fast: bool = False):
        super().__init__()
        self.agent, self.task, self.images, self.fast = agent, task, images, fast

    def run(self):
        try:
            if self.fast and not self.images:             # перевод — быстрые HTTP-мозги (1–3 с), не Claude
                from .study import fast_ask
                self.done.emit(fast_ask(self.agent, self.task), "")
                return
            self.done.emit(self.agent.ask_text(self.task, self.images), "")
        except Exception as e:  # noqa: BLE001
            self.done.emit("", str(e)[:300])


def pdf_text(path: str, limit: int = 12000) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    try:
        out = []
        for page in PdfReader(path).pages[:15]:
            out.append(page.extract_text() or "")
            if sum(map(len, out)) > limit:
                break
        return "\n".join(out).strip()[:limit]
    except Exception:  # noqa: BLE001
        return ""


class TextOps(QObject):
    def __init__(self, app):
        super().__init__()
        from .ui.popup import Popup
        self.app, self.agent = app, app.agent
        self.popup = Popup()
        self.jobs: list[BrainJob] = []
        self.last_source = ""        # текст письма/буфера — для «Перевести» и «Ответить»
        self.last_lang = ""
        self._dictating = False
        self._clip_saved = None

    # ------------------------------------------------------------------ общее
    def _workdir(self) -> Path:
        p = self.agent._workdir() / "screens"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _ask(self, task: str, images: list | None, then, fast: bool = False):
        job = BrainJob(self.agent, task, images, fast)
        self.jobs = [j for j in self.jobs if j.isRunning()] + [job]
        job.done.connect(lambda ans, err: self.popup.show_text("Не получилось", err, copy=False) if err else then(ans))
        job.start()

    # ------------------------------------------------------------------ «что в буфере»
    def explain_clipboard(self):
        md = QGuiApplication.clipboard().mimeData()
        if md.hasImage():
            img = QGuiApplication.clipboard().image()
            path = str(self._workdir() / f"clip-{int(time.time())}.png")
            img.save(path, "PNG")
            return self.explain_image(path)
        if md.hasUrls() and (files := [u.toLocalFile() for u in md.urls() if u.isLocalFile()]):
            return self.explain_file(files[0])
        text = (md.text() or "").strip()
        if not text:
            self.popup.show_text("Буфер пуст", "Скопируй текст (Ctrl+C), сделай скриншот (Win+Shift+S) или скопируй файл "
                                 "в Проводнике — и спроси ещё раз.", copy=False)
            return
        urls = T.URL_RX.findall(text)
        if len(urls) == 1 and len(text) <= len(urls[0]) + 3:
            return self.show_link(urls[0], "Ссылка в буфере")
        self.explain_text(text)

    def explain_file(self, path: str):
        p = Path(path)
        ext = p.suffix.lower()
        if ext in IMAGE_FILES:
            return self.explain_image(str(p))
        if ext == ".pdf":
            text = pdf_text(str(p))
            if not text:
                self.popup.show_text("PDF без текста", "Похоже, это скан. Открой его и скажи «объясни письмо» — "
                                     "я посмотрю на экран.", copy=False)
                return
            return self.explain_text(text)
        if ext in TEXT_FILES:
            try:
                return self.explain_text(p.read_text(encoding="utf-8", errors="replace")[:12000])
            except OSError as e:
                self.popup.show_text("Не открылся файл", str(e), copy=False)
                return
        conv = getattr(self.app, "convert", None)
        if conv is not None and p.exists():
            conv.open_for([p])                  # видео, архив, документ… — предлагаем, что с ним сделать
            return
        self.popup.show_text("Этот файл пока не читаю", f"{p.name}: открой его и скажи «объясни письмо» "
                             "или скопируй текст.", copy=False)

    def explain_image(self, path: str):
        codes = T.qr_decode(path)
        if codes:
            urls = [c for c in codes if T.URL_RX.match(c)]
            if urls:
                return self.show_link(urls[0], "QR-код")
            return self.popup.show_text("QR-код", codes[0], markdown=False)
        self.popup.show_busy("Смотрю картинку")
        self._ask(T.explain_prompt(image=True), [path], self._show_explained)

    def explain_text(self, text: str):
        self.last_source = text
        self.popup.show_busy("Читаю")
        self._ask(T.explain_prompt(text), None, self._show_explained)

    def _show_explained(self, answer: str):
        r = T.explained(answer)
        if r["text"]:
            self.last_source = r["text"]
        self.last_lang = r["lang"]
        actions = []
        foreign = r["lang"] not in ("", "ru")
        if self.last_source and (foreign or r["kind"] == "letter"):
            actions.append(("Перевести", lambda: self.translate(self.last_source)))
        if r["kind"] == "letter":
            actions.append(("✉ Ответить", self.ask_reply))
            if r["deadline"]:
                d = dt.date.fromisoformat(r["deadline"])
                actions.append((f"⏰ Напомнить {d:%d.%m}", lambda d=d: self.remind(d)))
        title = r["title"] or {"letter": "Письмо", "error": "Ошибка", "text": "Текст"}.get(r["kind"], "Что это")
        self.popup.show_text(title, r["answer"] or answer, actions)

    def remind(self, deadline: dt.date):
        """За день до срока в 10:00 (если уже поздно — через час)."""
        when = dt.datetime.combine(deadline - dt.timedelta(days=1), dt.time(10, 0))
        if when <= dt.datetime.now():
            when = dt.datetime.now() + dt.timedelta(hours=1)
        self.app.add_reminder(when.timestamp(), f"Срок по письму — {deadline:%d.%m}. Не забудь ответить!")
        self.popup.status.setText(f"Напомню {when:%d.%m в %H:%M}.")

    # ------------------------------------------------------------------ ссылки
    def show_link(self, url: str, title: str):
        risk = T.link_risk(url)
        full = url if "://" in url else "https://" + url
        self.popup.show_text(f"{title}: {T.host_of(url)}", f"Ссылка ведёт на **{T.host_of(url)}**\n\n`{url}`\n\nОткрыть?",
                             [("🌐 Открыть", lambda: (QDesktopServices.openUrl(QUrl(full)), self.popup.hide()))],
                             warn=("Осторожно: " + risk) if risk else "", copy_text=url)

    def make_qr(self, text: str) -> str:
        path = str(self._workdir() / f"qr-{int(time.time())}.png")
        T.qr_make(text, path)
        from PySide6.QtGui import QImage
        QGuiApplication.clipboard().setImage(QImage(path))
        return path

    # ------------------------------------------------------------------ перевод
    def translate(self, text: str, target: str = "", source: str = ""):
        """По умолчанию — на язык Капри (у тебя русский), язык оригинала определяет сама.
        В окне перевода — «С: … → На: …» и ⇄: выбрал — переводит заново."""
        self.last_source = text
        auto = not target
        target = target or getattr(self.app, "lang", "ru") or "ru"
        self.popup.show_busy("Перевожу")

        def show(ans):
            frm, to, out = T.parse_translation(ans, target)
            self.popup.show_translation(out, source or frm, to or target, T.LANGS,
                                        lambda s, t: self.translate(self.last_source, t, "" if s == "auto" else s))
        self._ask(T.translate_prompt(text, target, source, auto), None, show, fast=True)

    def translate_clipboard(self, target: str = ""):
        """«переведи буфер»: текст — сразу, скриншот — переписать и перевести, файл — прочитать и перевести."""
        cb = QGuiApplication.clipboard()
        md = cb.mimeData()
        if md.hasImage():
            path = str(self._workdir() / f"clip-{int(time.time())}.png")
            cb.image().save(path, "PNG")
            self.popup.show_busy("Перевожу картинку")
            self._ask(T.ocr_prompt(True), [path], lambda ans: self.popup.show_text("Перевод", ans.strip(), markdown=False))
            return
        if md.hasUrls() and (files := [u.toLocalFile() for u in md.urls() if u.isLocalFile()]):
            p = Path(files[0])
            text = pdf_text(str(p)) if p.suffix.lower() == ".pdf" else (
                p.read_text(encoding="utf-8", errors="replace")[:12000] if p.suffix.lower() in TEXT_FILES else "")
            if text:
                return self.translate(text, target)
        text = (cb.text() or "").strip()
        if not text:
            self.popup.show_text("Буфер пуст", "Скопируй текст (Ctrl+C) и скажи ещё раз.", copy=False)
            return
        self.translate(text, target)

    def translate_selection(self):
        """Горячая клавиша: копируем выделенное (Ctrl+C), переводим, буфер возвращаем как было."""
        from . import control
        cb = QGuiApplication.clipboard()
        self._clip_saved = cb.text()
        marker = f"⁣capri{time.time()}"
        cb.setText(marker)

        def grab(tries=[0]):  # noqa: B006 — счётчик попыток живёт в замыкании одного вызова
            text = cb.text()
            if text == marker and tries[0] < 6:
                tries[0] += 1
                QTimer.singleShot(60, grab)
                return
            if self._clip_saved is not None:
                cb.setText(self._clip_saved)
            if not text or text == marker:
                self.popup.show_text("Перевод", "Выдели текст мышкой и нажми сочетание ещё раз.", copy=False)
                return
            self.translate(text.strip())

        def press(wait=[0]):  # noqa: B006
            try:
                if control.keys_held() and wait[0] < 30:      # ещё держишь Ctrl+Shift — ждём (до 1,5 с)
                    wait[0] += 1
                    QTimer.singleShot(50, press)
                    return
                control.press_keys(0x11, 0x43)          # Ctrl+C
            except Exception:  # noqa: BLE001
                pass
            QTimer.singleShot(120, grab)
        QTimer.singleShot(60, press)

    # ------------------------------------------------------------------ письма
    def explain_letter_on_screen(self):
        from . import screen
        try:
            paths, _title = screen.capture(self.agent._workdir())
        except Exception as e:  # noqa: BLE001
            self.popup.show_text("Не получился снимок", str(e), copy=False)
            return
        self.popup.show_busy("Читаю письмо на экране")
        self._ask(T.explain_prompt(image=True), [str(self.agent._workdir() / paths[0])], self._show_explained)

    def ask_reply(self):
        self.popup.ask("Что ответить? Своими словами — я напишу письмо.", "например: согласен на четверг, 10:00",
                       self.reply)

    def reply(self, instruction: str, letter: str = ""):
        letter = letter or self.last_source or QGuiApplication.clipboard().text()
        if not letter.strip():
            self.popup.show_text("Ответ на письмо", "Не вижу письма: скопируй его текст или сначала скажи "
                                 "«объясни письмо».", copy=False)
            return
        self.popup.show_busy("Пишу ответ")
        self._ask(T.reply_prompt(letter, instruction, self.last_lang or "de"), None,
                  lambda ans: self.popup.show_reply(**T.reply_parsed(ans)))

    # ------------------------------------------------------------------ экран
    def screen_text(self, translate: bool = False):
        from . import screen
        try:
            paths, _title = screen.capture(self.agent._workdir())
        except Exception as e:  # noqa: BLE001
            self.popup.show_text("Не получился снимок", str(e), copy=False)
            return
        self.popup.show_busy("Перевожу экран" if translate else "Читаю текст с экрана")
        img = [str(self.agent._workdir() / paths[0])]

        def show(ans):
            text = ans.strip()
            if not translate:
                QGuiApplication.clipboard().setText(text)
            self.last_source = text
            self.popup.show_text("Перевод с экрана" if translate else "Текст с экрана — уже в буфере", text,
                                 [] if translate else [("Перевести", lambda: self.translate(self.last_source))],
                                 markdown=False)
        self._ask(T.ocr_prompt(translate), img, show)

    def ocr_image(self, path: str):
        """Текст с картинки (из конвертера) — в окно и сразу в буфер."""
        self.popup.show_busy("Читаю текст с картинки")

        def show(ans):
            text = ans.strip()
            QGuiApplication.clipboard().setText(text)
            self.last_source = text
            self.popup.show_text("Текст с картинки — уже в буфере", text,
                                 [("Перевести", lambda: self.translate(self.last_source))], markdown=False)
        self._ask(T.ocr_prompt(False), [path], show)

    # ------------------------------------------------------------------ диктовка
    def dictate(self, start: bool):
        v = self.app.voice
        if not v:
            return
        if start and not v.recording:
            self._dictating = True
            v.toggle_record()
        elif not start and v.recording and self._dictating:
            v.toggle_record()                              # распознаём → app.on_transcribed → paste()

    def dictate_cancel(self):
        if self._dictating and self.app.voice:
            self._dictating = False
            self.app.voice.cancel_record()

    def paste(self, text: str):
        """Напечатать надиктованное туда, где курсор: через буфер (+Ctrl+V), буфер потом вернуть."""
        from . import control
        self._dictating = False
        if not text.strip():
            return
        cb = QGuiApplication.clipboard()
        saved = cb.text()
        cb.setText(text.strip() + " ")

        def go(wait=[0]):  # noqa: B006
            try:
                if control.keys_held() and wait[0] < 40:      # Ctrl+Win ещё зажаты — дождаться
                    wait[0] += 1
                    QTimer.singleShot(50, go)
                    return
                control.press_keys(0x11, 0x56)          # Ctrl+V
            except Exception:  # noqa: BLE001
                pass
            QTimer.singleShot(600, lambda: cb.setText(saved))
        QTimer.singleShot(80, go)
