"""Субтитры в Капри: старт/стоп, окно, сигнал на имя, конспект после урока. Живёт в главном потоке (Qt)."""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from . import notes as N
from .session import NAME_VARIANTS, LiveSession, Settings

LANGS = {"de": "немецкий", "en": "английский", "ru": "русский", "he": "иврит", "auto": "любой"}


def chime_path() -> Path:
    """Тихий двойной «дзынь» для сигнала (делаем сами, без файлов из интернета)."""
    import wave

    import numpy as np

    from ..config import expand
    p = expand("data/chime.wav")
    if p.exists():
        return p
    sr = 22050
    t = np.arange(int(sr * 0.18)) / sr
    env = np.exp(-t * 14)
    tone = lambda f: np.sin(2 * np.pi * f * t) * env  # noqa: E731
    pcm = np.concatenate([tone(880), np.zeros(int(sr * 0.05)), tone(1320)]) * 0.35
    p.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((pcm * 32767).astype("<i2").tobytes())
    return p


class LiveController(QObject):
    sig_line = Signal(object)
    sig_tr = Signal(int, str)
    sig_partial = Signal(str, str)
    sig_status = Signal(str)
    sig_trigger = Signal(object)

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.session: LiveSession | None = None
        self.bar = None
        self.started: dt.datetime | None = None
        self.folder: Path | None = None
        self._model = None
        self._model_name = ""
        self._jobs = []
        self.sig_line.connect(self._on_line)
        self.sig_tr.connect(lambda i, t: self.bar and self.bar.set_translation(i, t))
        self.sig_partial.connect(lambda s, t: self.bar and self.bar.set_partial(s, t))
        self.sig_status.connect(lambda t: self.bar and self.bar.set_status(t))
        self.sig_trigger.connect(self._on_trigger)

    # ------------------------------------------------------------------ настройки (data/state.json → live)
    def prefs(self) -> dict:
        d = {"language": "de", "target": "ru", "mic": True, "translate_with": "auto", "names": [], "triggers": [],
             "signals": True, "geometry": None, "model": "auto"}
        return {**d, **(self.app.prefs.get("live") or {})}

    def set_pref(self, **kw):
        self.app.prefs.set("live", {**self.prefs(), **kw})

    def names(self) -> list[str]:
        p = self.prefs()
        base = list(NAME_VARIANTS)
        user = str(self.app.cfg.get("user_name") or "").strip()
        if user and user not in base:
            base.append(user)
        return base + [n for n in p["names"] if n not in base]

    @property
    def running(self) -> bool:
        return self.session is not None

    # ------------------------------------------------------------------ модель Whisper
    def _model_getter(self):
        """Видеокарта NVIDIA — отдельная точная модель large-v3-turbo (~1,6 ГБ, скачается один раз);
        процессор — та же модель, что для голоса (чтобы не тормозить)."""
        from ..voice.stt import Transcriber, gpu_available
        from ..config import expand
        v = self.app.cfg.get("voice", {})
        root = str(expand(v.get("models_dir", "data/models")) / "whisper")
        want = self.prefs()["model"]
        gpu = gpu_available()
        name = want if want != "auto" else ("large-v3-turbo" if gpu else v.get("whisper_model", "small"))
        voice = getattr(self.app, "voice", None)
        if voice is not None and getattr(voice.stt, "model_size", "") == name:
            return voice.stt._load()
        if self._model is None or self._model_name != name:
            self.sig_status.emit("Первый запуск: скачиваю модель распознавания (~1,6 ГБ), дальше — сразу…"
                                 if name == "large-v3-turbo" else "Загружаю распознавание…")
            t = Transcriber(name, v.get("languages"), root, preferred="de", device="auto" if gpu else "cpu")
            self._model, self._model_name = t._load(), name
        return self._model

    # ------------------------------------------------------------------ старт / стоп
    def start(self, language: str | None = None):
        if language:
            self.set_pref(language=language)
        if self.running:
            if language:
                self.session.s.language = language
            self.bar.show()
            self.bar.raise_()
            return f"Субтитры уже идут — язык: {LANGS.get(self.prefs()['language'])}."
        try:
            import soundcard  # noqa: F401
        except Exception:  # noqa: BLE001
            return "Для субтитров нужен модуль soundcard — обнови Капри («обнови капри»)."
        from ..voice.stt import gpu_available
        from .capture import Capture
        from .translate import Translator
        from ..ui.subtitles import SubtitleBar
        p = self.prefs()
        s = Settings(language=p["language"], target=p["target"], mic=p["mic"], names=self.names(),
                     triggers=list(p["triggers"]), signals=p["signals"])
        tr = Translator(self.app.agent, s.target, p["translate_with"])
        if self.bar is None:
            self.bar = SubtitleBar()
            self.bar.stop_clicked.connect(lambda: self.stop(ask_notes=True))
            self.bar.notes_clicked.connect(lambda: self.stop(ask_notes=False, make=True))
            self.bar.pause_toggled.connect(lambda on: self.session and self.session.set_paused(on))
            self.bar.geometry_saved.connect(lambda g: self.set_pref(geometry=g))
        self.bar.clear()
        if p.get("geometry"):
            self.bar.place(p["geometry"])
        self.session = LiveSession(
            self._model_getter, s, on_line=self.sig_line.emit, on_translation=self.sig_tr.emit,
            on_partial=self.sig_partial.emit, on_trigger=self.sig_trigger.emit, on_status=self.sig_status.emit,
            translator=tr, gpu=gpu_available(), capture_factory=Capture)
        self.started = dt.datetime.now()
        self.folder = None
        self.session.start()
        if tr.order()[0] != "local" and gpu_available():
            # переводит облако — освобождаем видеопамять от локальной модели под точное распознавание
            import threading
            threading.Thread(target=lambda: getattr(self.app.agent.llm, "unload", lambda: None)(), daemon=True).start()
        self.bar.show()
        who = {"groq": "Groq", "gemini": "Gemini", "openrouter": "OpenRouter"}.get(tr.order()[0], "локальная модель")
        return (f"Включила субтитры: {LANGS.get(s.language)} → русский, переводит {who}. "
                f"Скажу, если назовут твоё имя. Выключить — «стоп субтитры».")

    def stop(self, ask_notes: bool = True, make: bool = False) -> str:
        if not self.running:
            return "Субтитры и так выключены."
        sess, self.session = self.session, None
        sess.stop()
        if self.bar:
            self.bar.hide()
        lines = sess.lines
        if len(lines) < 3:
            return "Выключила субтитры."
        self.folder = N.save_session(lines, self.started or dt.datetime.now())
        self._last_lines = lines
        if make:
            self.make_notes()
        elif ask_notes:
            pop = self.app.text.popup
            pop.show_text("Урок записан (текстом)", f"{len(lines)} фраз. Сделать конспект по-русски: главное, термины "
                          "DE→RU, что задали, что повторить?", [("📝 Сделать конспект", self.make_notes),
                                                               ("📂 Транскрипт", lambda: self._open(self.folder / "транскрипт.md"))],
                          copy=False, markdown=False)
        return f"Выключила субтитры. Записала {len(lines)} фраз — транскрипт в «Документы\\Capri\\Уроки»."

    # ------------------------------------------------------------------ события
    def _on_line(self, line):
        if self.bar:
            self.bar.add_line(line.id, line.source, line.text, line.lang, line.trigger)

    def _on_trigger(self, line):
        self.play_chime()
        if not getattr(self.app, "in_game", False):
            self.app.tray.showMessage("Capri 🔔", f"«{line.trigger}»: {line.text[:120]}")

    def play_chime(self):
        if sys.platform != "win32":
            return
        try:
            import winsound
            winsound.PlaySound(str(chime_path()), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        except Exception:  # noqa: BLE001
            pass

    def on_app_status(self, status: str):
        """Капри говорит сама — её голос из колонок не надо субтитровать."""
        if self.session is not None:
            talking = status in ("SPEAKING", "TALKING")
            for c in self.session.captures:
                if c.source == "system":
                    c.paused = talking or self.session.paused

    # ------------------------------------------------------------------ конспект
    def make_notes(self, folder: Path | None = None):
        from ..convertui import Job
        folder = folder or self.folder
        if folder is None:
            return
        tr_file = folder / "транскрипт.md"
        lines = getattr(self, "_last_lines", None)
        text = N.transcript_plain(lines) if lines else tr_file.read_text(encoding="utf-8")
        lang = LANGS.get(self.prefs()["language"], "немецкий")
        pop = self.app.text.popup
        pop.show_busy("📝 Конспект урока", "Читаю урок… (длинный урок — пару минут)")
        job = Job(lambda: N.make_notes(self.app.agent.ask_text, text, lang,
                                       progress=lambda s: job.progress.emit(0.0, s)))
        job.progress.connect(lambda f, s: pop.status.setText(s))

        def done(md, err, secs):
            if err:
                pop.show_text("Конспект не получился", err + "\n\nТранскрипт сохранён — попробуй ещё раз: «сделай конспект».",
                              copy=False, markdown=False)
                return
            new = N.write_notes(folder, md)
            self.folder = new
            pdf = None
            try:
                pdf = N.md_to_pdf(md, new / "конспект.pdf")
            except Exception:  # noqa: BLE001
                pass
            acts = [("📂 Папка урока", lambda: self._open(new))]
            if pdf:
                acts.append(("📄 PDF", lambda: self._open(pdf)))
            pop.show_text(f"📝 {N.title_of(md)}", md, acts, copy_text=md)
            self.app.chat.add_system(f"📝 Конспект готов: {N.title_of(md)} — папка «{new.name}».")
        job.done.connect(done)
        self._jobs = [j for j in self._jobs if j.isRunning()] + [job]
        job.start()

    def _open(self, path: Path):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    # ------------------------------------------------------------------ команды из agent (действие «live»)
    def handle(self, p: dict) -> str:
        do = p.get("do")
        if do == "start":
            return self.start(p.get("lang"))
        if do == "stop":
            return self.stop(ask_notes=True)
        if do == "notes":
            if self.running:
                return self.stop(ask_notes=False, make=True)
            last = N.latest()
            if last is None:
                return "Записанных уроков пока нет. Включи субтитры на уроке: «включи субтитры»."
            self.folder, self._last_lines = last, None
            self.make_notes(last)
            return "Делаю конспект последнего урока."
        if do in ("add_word", "del_word"):
            words = [w for w in self.prefs()["triggers"] if w.lower() != p["word"].lower()]
            if do == "add_word":
                words.append(p["word"])
            self.set_pref(triggers=words)
            if self.session:
                self.session.set_words(self.names(), words)
            return (f"Буду сигналить на «{p['word']}»." if do == "add_word" else f"Больше не слушаю «{p['word']}».")
        if do == "list_words":
            t = self.prefs()["triggers"]
            return ("Сигналю на твоё имя (" + ", ".join(self.names()[:4]) + ")" +
                    (" и слова: " + ", ".join(t) if t else "") + ". Добавить: «сигнал на слово Prüfung».")
        if do == "signals":
            self.set_pref(signals=bool(p["on"]))
            if self.session:
                self.session.s.signals = bool(p["on"])
            return "Сигналы включены." if p["on"] else "Сигналы выключила — субтитры работают тихо."
        if do == "mic":
            self.set_pref(mic=bool(p["on"]))
            return ("Буду переводить и тебя (микрофон)." if p["on"] else "Твой микрофон больше не перевожу.") + \
                (" Заработает со следующего включения субтитров." if self.running else "")
        if do == "last":
            last = N.latest()
            if last is None:
                return "Записанных уроков пока нет."
            f = last / "конспект.md"
            if f.exists():
                md = f.read_text(encoding="utf-8")
                self.app.text.popup.show_text(f"📚 {N.title_of(md)}", md, [("📂 Папка", lambda: self._open(last))], copy_text=md)
                return f"Прошлый урок: {N.title_of(md)}. Конспект — в окне."
            self.folder, self._last_lines = last, None
            self.make_notes(last)
            return "Конспекта у прошлого урока ещё нет — делаю."
        if do == "search":
            hits = N.search(p["term"])
            if not hits:
                return f"В записанных уроках «{p['term']}» не нашла."
            text = "\n\n".join(f"**{f.name[:10]} — {title}**\n" + "\n".join(f"• {h[:160]}" for h in lines)
                               for f, title, lines in hits)
            first = hits[0][0]
            self.app.text.popup.show_text(f"🔎 «{p['term']}» в уроках", text, [("📂 Урок", lambda: self._open(first))])
            return f"«{p['term']}» было на {len(hits)} уроках, последний — {hits[0][0].name[:10]}: {hits[0][1]}."
        return ""
