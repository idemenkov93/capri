"""Субтитры в Капри: старт/стоп, окно, сигнал на имя, конспект после урока. Живёт в главном потоке (Qt)."""
from __future__ import annotations

import datetime as dt
import os
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
        self.speakers = None                  # кто говорит — в текущем (или последнем) уроке
        self._embedder = None
        self._jobs = []
        self.sig_line.connect(self._on_line)
        self.sig_tr.connect(lambda i, t: self.bar and self.bar.set_translation(i, t))
        self.sig_partial.connect(lambda s, t: self.bar and self.bar.set_partial(s, t))
        self.sig_status.connect(lambda t: self.bar and self.bar.set_status(t))
        self.sig_trigger.connect(self._on_trigger)

    # ------------------------------------------------------------------ настройки (data/state.json → live)
    def prefs(self) -> dict:
        d = {"language": "de", "target": "ru", "mic": True, "translate_with": "auto", "names": [], "triggers": [],
             "signals": True, "geometry": None, "model": "auto",
             "source": "auto", "teams_name": ""}
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
        cores = os.cpu_count() or 4
        if want == "auto" and not gpu and name in ("small", "medium") and cores <= 8:
            name = "base"            # ноутбук без видеокарты: small не успевает за речью — base в 2–3 раза быстрее
        voice = getattr(self.app, "voice", None)
        if gpu and voice is not None and getattr(voice.stt, "model_size", "") == name:
            return voice.stt._load()
        if self._model is None or self._model_name != name:
            self.sig_status.emit("Первый запуск: скачиваю модель распознавания (~1,6 ГБ), дальше — сразу…"
                                 if name == "large-v3-turbo" else "Загружаю распознавание…")
            t = Transcriber(name, v.get("languages"), root, preferred="de", device="auto" if gpu else "cpu")
            if not gpu:
                t.threads = max(2, cores // 2)      # половину процессора — видео, браузеру и Teams, иначе всё тормозит
            self._model, self._model_name = t._load(), name
            from ..log import log
            log.info(f"live: распознавание {name} на {'видеокарте' if gpu else f'процессоре, потоков {t.threads}'}")
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
        from . import glossary
        tr.glossary = glossary.load
        if self.bar is None:
            self.bar = SubtitleBar()
            self.bar.stop_clicked.connect(lambda: self.stop(ask_notes=True))
            self.bar.notes_clicked.connect(lambda: self.stop(ask_notes=False, make=True))
            self.bar.pause_toggled.connect(lambda on: self.session and self.session.set_paused(on))
            self.bar.geometry_saved.connect(lambda g: self.set_pref(geometry=g))
            self.bar.rename_requested.connect(self.ask_name)
            self.bar.hint_clicked.connect(lambda: self.app.on_message("подскажи ответ"))
            self.bar.answer_clicked.connect(self.ask_answer)
            self.bar.word_clicked.connect(self.word_lookup)
        self.bar.clear()
        if p.get("geometry"):
            self.bar.place(p["geometry"])
        from ..config import expand
        from .speakers import Speakers
        self.speakers = Speakers(voices_file=expand("data/voices.json")) if p.get("speakers", True) else None
        self.session = LiveSession(
            self._model_getter, s, on_line=self.sig_line.emit, on_translation=self.sig_tr.emit,
            on_partial=self.sig_partial.emit, on_trigger=self.sig_trigger.emit, on_status=self.sig_status.emit,
            translator=tr, gpu=gpu_available(), capture_factory=Capture, speakers=self.speakers,
            embedder_getter=self._embedder_getter)
        self.session.me = {n for n in [p.get("teams_name") or "", str(self.app.cfg.get("user_name") or "")] if n}
        self.started = dt.datetime.now()
        self.folder = None
        self.session.start()
        self._start_teams(p)
        if tr.order()[0] != "local":
            # переводит облако — выгружаем локальную модель: видеопамять под распознавание, а на ноутбуке — память и процессор
            import threading
            threading.Thread(target=lambda: getattr(self.app.agent.llm, "unload", lambda: None)(), daemon=True).start()
        self.bar.show()
        from ..ui.anim import fade_in
        fade_in(self.bar, slide=14)
        who = {"groq": "Groq", "gemini": "Gemini", "openrouter": "OpenRouter"}.get(tr.order()[0], "локальная модель")
        return (f"Включила субтитры: {LANGS.get(s.language)} → русский, переводит {who}. "
                f"Скажу, если назовут твоё имя. Выключить — «стоп субтитры».")

    def stop(self, ask_notes: bool = True, make: bool = False) -> str:
        if not self.running:
            return "Субтитры и так выключены."
        sess, self.session = self.session, None
        if self.teams is not None:
            self.teams.stop()
            self.teams = None
        sess.stop()
        if self.bar:
            self.bar.hide()
        lines = sess.lines
        if self.speakers is not None:
            self._refresh_names(lines)
            self.speakers.remember_session()
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

    # ------------------------------------------------------------------ субтитры Teams
    teams = None

    def _start_teams(self, p: dict):
        """source: auto — субтитры Teams, если они включены в звонке, иначе звук; audio — только звук."""
        import sys
        if p.get("source", "auto") == "audio" or sys.platform != "win32":
            return
        from ..config import expand
        from .teams import TeamsCaptions, clean_name
        sess = self.session

        def on_event(ev):
            if self.session is not sess:
                return
            name = clean_name(ev.speaker)
            if ev.kind == "final":
                sess.external_line(name, ev.text)
            else:
                sess.external_partial("Ты" if sess.is_me(name) else name, ev.text)

        def on_state(active: bool):
            if self.session is not sess:
                return
            sess.external = active
            from ..log import log
            log.info(f"teams captions: {'найдены' if active else 'пропали'}")
            self.sig_status.emit("Читаю субтитры Teams: имена и текст — из Teams, перевожу сразу." if active else
                                 "Субтитры Teams пропали — слушаю звук сама.")
        self.teams = TeamsCaptions(on_event, on_state, dump_path=expand("data/teams_uia.txt"))
        self.teams.start()

    # ------------------------------------------------------------------ кто говорит
    def _embedder_getter(self):
        if self._embedder is None:
            from ..config import expand
            from .speakers import Embedder, download
            v = self.app.cfg.get("voice", {})
            self._embedder = Embedder(download(expand(v.get("models_dir", "data/models")), self.sig_status.emit))
            self.sig_status.emit("")
        return self._embedder

    def _refresh_names(self, lines):
        for ln in lines:
            if ln.spk and self.speakers is not None and (sp := self.speakers.resolve(ln.spk)) is not None:
                ln.spk, ln.speaker = sp.id, sp.name

    def rename(self, ref: str, name: str) -> str:
        if self.speakers is None or not self.speakers.speakers:
            return "Пока не знаю ни одного голоса — включи субтитры на уроке."
        sp = self.speakers.rename(ref, name)
        if sp is None:
            have = ", ".join(s.name for s in self.speakers.speakers)
            return f"Не нашла «{ref}». Сейчас говорят: {have}."
        names = {sid: sp.name for sid in [sp.id] + [k for k, v in self.speakers.alias.items() if v == sp.id]}
        if self.bar:
            self.bar.rename_speakers(names)
        lines = self.session.lines if self.session else getattr(self, "_last_lines", None)
        if lines:
            self._refresh_names(lines)
            if self.session is None and self.folder is not None and (self.folder / "транскрипт.md").exists():
                (self.folder / "транскрипт.md").write_text(N.transcript_md(lines, self.started or dt.datetime.now()),
                                                         encoding="utf-8")
        return f"Запомнила голос: {sp.name}. В следующий раз узнаю сама."

    def word_lookup(self, word: str, sentence: str):
        """Двойной клик по слову в записи урока: перевод в этом смысле + «➕ В словарь»."""
        from ..convertui import Job
        from ..study import fast_ask, parse_json
        from . import glossary
        pop = self.app.text.popup
        try:
            known = [e for e in glossary.find(word, limit=3)
                     if word.lower() in [v.lower() for v in glossary.variants(e["de"])]]
        except Exception:  # noqa: BLE001
            known = []
        if known:                                   # есть в словаре курса — мгновенно, без интернета
            e = known[0]
            pop.show_text(f"🔤 {e['de']}", glossary.line(e) + "\n\n_из словаря курса_", copy_text=f"{e['de']} — {e['ru']}")
            return
        pop.show_busy(f"🔤 {word}", "Смотрю слово…")
        prompt = (f"Немецкое слово «{word}» в предложении «{sentence[:300]}». Ответ строго JSON без markdown: "
                  '{"de": "<начальная форма с артиклем для существительных>", "ru": "<перевод в этом смысле, 1–3 слова>", '
                  '"note": "<очень коротко: что это / пример, по-русски>"}')
        job = Job(lambda: fast_ask(self.app.agent, prompt))

        def done(raw, err, secs):
            if err:
                return pop.show_text("Не получилось", err, copy=False)
            try:
                d = parse_json(raw)
            except Exception:  # noqa: BLE001
                d = {"de": word, "ru": raw.strip()[:80], "note": ""}
            de, ru, note = str(d.get("de") or word), str(d.get("ru") or ""), str(d.get("note") or "")
            pop.show_text(f"🔤 {de}", f"**{de}** — {ru}" + (f"\n\n_{note}_" if note else ""),
                          [("➕ В словарь", lambda: (glossary.add(de, ru, note), pop.status.setText("Добавила в словарь курса.")))],
                          copy_text=f"{de} — {ru}")
        job.done.connect(done)
        self._jobs = [j for j in self._jobs if j.isRunning()] + [job]
        job.start()

    def ask_answer(self):
        """💬 / Win+Alt+A: «что хочешь сказать?» по-русски → фраза для урока крупно на полосе субтитров."""
        pop = self.app.text.popup
        pop.show_text("💬 Хочу ответить", "Напиши по-русски, что хочешь сказать (или держи Ctrl+Win и скажи) — "
                      "покажу, как сказать по-немецки. Enter — готово.", copy=False, markdown=False)
        pop.ask("Что сказать?", "например: я не понял, повторите пожалуйста медленнее",
                lambda text: text and (pop.hide(), self.app.on_message(f"помоги ответить: {text}")))

    def show_answer(self, say: str, ru: str = ""):
        if self.bar is not None and self.running:
            self.bar.show_answer(say, ru)

    def ask_name(self, sid: int):
        from PySide6.QtWidgets import QInputDialog
        sp = self.speakers.resolve(sid) if self.speakers else None
        if sp is None:
            return
        name, ok = QInputDialog.getText(None, "Кто это говорит?", f"Имя для «{sp.name}» (голос запомню):", text=
                                        "" if sp.name.startswith("Спикер") else sp.name)
        if ok and name.strip():
            self.app.chat.add_system(self.rename(str(sp.id), name.strip()))

    def who_speaks(self) -> str:
        if self.speakers is None or self.speakers.last is None:
            return "Пока никого не слышала."
        others = [s for s in self.speakers.speakers if s is not self.speakers.last]
        tail = (" Ещё: " + ", ".join(f"{s.name} ({s.count})" for s in others[:5]) + ".") if others else ""
        return f"Сейчас говорит {self.speakers.last.name}.{tail}"

    # ------------------------------------------------------------------ события
    def _on_line(self, line):
        if self.bar:
            self.bar.add_line(line.id, line.source, line.text, line.lang, line.trigger, line.spk, line.speaker)

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
        from . import glossary
        gl = glossary.for_prompt(text)
        job = Job(lambda: N.make_notes(self.app.agent.ask_text, text, lang,
                                       progress=lambda s: job.progress.emit(0.0, s), glossary=gl))
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
            try:
                fresh = glossary.new_terms(new / "конспект.md")
            except Exception:  # noqa: BLE001
                fresh = []
            if fresh:
                acts.insert(0, (f"📖 Новые термины ({len(fresh)})", lambda: self.app.chat.add_system(self.review_terms(new))))
            pop.show_text(f"📝 {N.title_of(md)}", md, acts, copy_text=md)
            self.app.chat.add_system(f"📝 Конспект готов: {N.title_of(md)} — папка «{new.name}»." +
                                     (f" Новых терминов: {len(fresh)} — нажми «📖 Новые термины», "
                                      "отметь, что оставить в бестиарии." if fresh else ""))
        job.done.connect(done)
        self._jobs = [j for j in self._jobs if j.isRunning()] + [job]
        job.start()

    @staticmethod
    def _clipboard_file(exts: tuple) -> str:
        """Файл, скопированный в Проводнике (Ctrl+C), — путь; иначе ''."""
        from PySide6.QtGui import QGuiApplication
        md = QGuiApplication.clipboard().mimeData()
        if md is not None and md.hasUrls():
            for u in md.urls():
                f = u.toLocalFile()
                if f and f.lower().endswith(exts):
                    return f
        return ""

    def review_terms(self, folder: Path | None = None) -> str:
        """Новые термины урока → галочки → бестиарий (data\\glossary\\Из уроков.csv)."""
        from . import glossary
        if folder is None:
            folder = next((p for p in reversed(N.lessons()) if (p / "конспект.md").exists()), None)
        if folder is None:
            return "Конспектов пока нет — сначала «сделай конспект»."
        f = folder / "конспект.md"
        fresh = glossary.new_terms(f)
        if not fresh:
            return f"В уроке «{folder.name}» новых терминов нет — всё уже в бестиарии."
        from ..ui.terms import TermsDialog
        dlg = TermsDialog(fresh, N.title_of(f.read_text(encoding="utf-8")))
        if not dlg.exec():
            return "Ок, термины разберём позже: «новые термины урока»."
        n = glossary.accept(dlg.accepted_rows)
        glossary.skip(dlg.skipped)
        msg = f"📖 В бестиарий добавила {n}" + (f", пропустила {len(dlg.skipped)}" if dlg.skipped else "") + \
            f". Всего терминов: {glossary.count()}."
        return msg

    def export_glossary(self) -> str:
        from . import glossary
        dest = N.lessons_dir().parent / f"Бестиарий {dt.date.today():%Y-%m-%d}.csv"
        n = glossary.export(dest)
        self.app.text.popup.show_text("📖 Бестиарий выгружен", f"**{n}** терминов → `{dest}`\n\n"
                                      "Колонки: Deutsch;Русский;Заметка;Источник;Тема — как у твоего бестиария. "
                                      "На новом ноутбуке: «импортируй бестиарий» и выбери этот файл. "
                                      "Можно открыть в Excel или закинуть в проект Claude.",
                                      [("📂 Открыть папку", lambda: self._open(dest.parent))], copy=False)
        return f"Выгрузила бестиарий: {n} терминов — Документы\\Capri\\{dest.name}."

    def prepare(self) -> str:
        """«Подготовь к уроку»: прошлые уроки, домашка, термины, вероятные вопросы, фразы. Уроки — личное: локально."""
        from ..convertui import Job
        last = [p for p in reversed(N.lessons()) if (p / "конспект.md").exists()][:2]
        if not last:
            return "Конспектов пока нет — после первого урока скажи «сделай конспект», и я буду готовить тебя к следующему."
        pop = self.app.text.popup
        pop.show_busy("📋 Подготовка к уроку", "Читаю прошлые уроки…")
        text = "\n\n".join(f"=== Урок {p.name[:10]} ===\n" + (p / "конспект.md").read_text(encoding="utf-8")[:5000]
                            for p in last)
        from . import glossary
        prompt = PREP_PROMPT.format(notes=text, glossary=glossary.for_prompt(text, 25) or "—")
        agent = self.app.agent

        def ask():
            from .. import mentor
            text, who = mentor.ask_brain(agent, prompt)       # с видеокартой — локально, без неё — облако (быстро)
            return text + f"\n\n_{who}_"
        job = Job(ask)
        job.done.connect(lambda res, err, secs: pop.show_text(
            "📋 Подготовка к уроку", err or res,
            [("🗣 Фразы для урока", self.show_phrases), ("📖 Прошлый урок", lambda: self.handle({"do": "last"}))],
            copy_text=None if err else res))
        self._jobs = [j for j in self._jobs if j.isRunning()] + [job]
        job.start()
        return "Готовлю тебя к уроку — по прошлым конспектам."

    def phrases_menu(self):
        """Горячая клавиша 🗣: меню фраз у мыши → фраза крупно на субтитрах (субтитры выключены — в окне)."""
        from PySide6.QtGui import QCursor
        from PySide6.QtWidgets import QMenu
        from . import phrases
        m = QMenu()
        for title, items in phrases.all_groups():
            sub = m.addMenu(title)
            for de, ru in items:
                sub.addAction(f"{de}  —  {ru}", lambda de=de, ru=ru: self._say_phrase(de, ru))
        m.exec(QCursor.pos())

    def _say_phrase(self, de: str, ru: str):
        if self.bar is not None and self.running:
            self.bar.show_answer(de, ru)
        else:
            self.app.text.popup.show_text("🗣 Скажи так", f"## {de}\n\n_{ru}_", copy_text=de)

    def show_phrases(self) -> str:
        from . import phrases
        self.app.text.popup.show_text("🗣 Фразы для урока", phrases.markdown(), copy=False)
        return "Фразы — в окне. На уроке: кнопка 🗣 на субтитрах — фраза крупно одним кликом."

    def _open(self, path: Path):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    # ------------------------------------------------------------------ команды из agent (действие «live»)
    def handle(self, p: dict) -> str:
        try:
            return self._handle(p)
        except Exception as e:  # noqa: BLE001 — команда не должна молча «зависать»
            from ..log import log
            log.exception(f"live {p.get('do')}: {e}")
            return f"Не получилось ({p.get('do')}): {str(e)[:160]}"

    def _handle(self, p: dict) -> str:
        do = p.get("do")
        if do == "start":
            return self.start(p.get("lang"))
        if do == "rename":
            return self.rename(p["ref"], p["name"])
        if do == "who":
            return self.who_speaks()
        if do == "forget_voice":
            from ..config import expand
            from .speakers import Speakers
            sp = self.speakers or Speakers(voices_file=expand("data/voices.json"))
            return f"Забыла голос «{p['name']}»." if sp.forget(p["name"]) else f"Голоса «{p['name']}» я не знаю."
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
        if do == "asr_model":
            self.set_pref(model=p["value"])
            self._model = None
            what = {"base": "быстрее (base) — для ноутбука", "small": "точнее (small) — нужен хороший процессор",
                    "large-v3-turbo": "самое точное (large-v3-turbo) — для видеокарты", "auto": "сама выбираю по компьютеру"}
            return f"Распознавание субтитров: {what.get(p['value'], p['value'])}." + \
                (" Сработает со следующего включения субтитров." if self.running else "")
        if do == "review_terms":
            return self.review_terms()
        if do == "export_glossary":
            return self.export_glossary()
        if do == "prepare":
            return self.prepare()
        if do == "phrases":
            return self.show_phrases()
        if do == "teams_name":
            self.set_pref(teams_name=p["name"])
            if self.session is not None:
                self.session.me.add(p["name"])
            return f"Запомнила: в Teams ты — «{p['name']}». Твои фразы покажу как «Ты»."
        if do == "source":
            self.set_pref(source=p["value"])
            return ("Субтитры беру только из звука (Whisper)." if p["value"] == "audio" else
                    "Если в Teams включены Live-Untertitel — беру имена и текст оттуда, иначе слушаю звук.") + \
                (" Сработает со следующего включения субтитров." if self.running else "")
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
        if do in ("search", "explain"):
            from . import importer
            term = p["term"]
            from . import glossary
            hits = N.search(term)
            mats = importer.search_materials(term)
            try:
                words = glossary.find(term, limit=5)
            except Exception:  # noqa: BLE001 — словарь не прочитался: ищем без него
                words = []
            if not hits and not mats and not words:
                return f"В уроках, материалах и словаре «{term}» не нашла."
            text = ("**📖 Словарь курса:**\n" + "\n".join(f"• {glossary.line(e)}" for e in words) + "\n\n"
                    if words else "")
            if not hits and not mats:
                if do == "explain":
                    self.explain(term, text)
                    return f"Объясняю «{term}»."
                self.app.text.popup.show_text(f"📖 {term}", text.strip(), [("🧠 Объясни", lambda: self.explain(term, text))])
                return f"{words[0]['de']} — {words[0]['ru']}."
            text += "\n\n".join(f"**{f.name[:10]} — {title}**\n" + "\n".join(f"• {h[:160]}" for h in lines)
                               for f, title, lines in hits)
            if mats:
                text += ("\n\n" if text else "") + "**📁 Материалы:**\n\n" + "\n\n".join(
                    f"**{title}**\n" + "\n".join(f"• {h[:160]}" for h in lines) for f, title, lines in mats)
            if do == "explain":
                self.explain(term, text)
                return f"Объясняю «{term}» по твоим урокам."
            first = (hits or mats)[0][0]
            self.app.text.popup.show_text(f"🔎 «{term}» в уроках", text, [
                ("🧠 Объясни", lambda: self.explain(term, text)),
                ("📂 Открыть", lambda: self._open(first))])
            if hits:
                return f"«{term}» было на {len(hits)} уроках, последний — {hits[0][0].name[:10]}: {hits[0][1]}."
            return f"«{term}» есть в материалах: {mats[0][1]}."
        if do == "import_glossary":
            from . import glossary
            f = p.get("path") or self._clipboard_file((".csv", ".md", ".txt"))
            if not f:
                from PySide6.QtCore import Qt
                from PySide6.QtWidgets import QFileDialog
                dlg = QFileDialog(None, "Выбери бестиарий / словарь курса (CSV)", str(Path.home() / "Downloads"),
                                  "Словарь (*.csv *.md *.txt);;Все файлы (*)")
                dlg.setFileMode(QFileDialog.ExistingFile)
                dlg.setWindowFlag(Qt.WindowStaysOnTopHint, True)    # не прятаться за чатом и окнами
                dlg.raise_()
                dlg.activateWindow()
                if not dlg.exec() or not dlg.selectedFiles():
                    return "Ок, без словаря. Можно и так: скопируй CSV в Проводнике (Ctrl+C) и скажи «импортируй бестиарий»."
                f = dlg.selectedFiles()[0]
            try:
                n = glossary.import_file(Path(f))
            except Exception as e:  # noqa: BLE001
                return f"Не получилось импортировать словарь: {e}"
            return (f"Добавила словарь «{Path(f).stem}»: {n} терминов (всего в словаре {glossary.count()}). "
                    "Субтитры переводят термины по нему, а «что такое …» отвечаю сразу.")
        if do == "import_granola":
            return self._run_import("Granola", lambda progress: importer_granola())
        if do == "import_folder":
            from PySide6.QtWidgets import QFileDialog
            d = p.get("path") or QFileDialog.getExistingDirectory(None, "Папка с материалами курса (PDF, Word, PowerPoint…)")
            if not d:
                return ""
            folders = [f for f in self.app.prefs.get("import_folders") or [] if f != d] + [d]
            self.app.prefs.set("import_folders", folders)
            return self._run_import(Path(d).name, lambda progress: _import_folders([d], progress))
        if do == "import_refresh":
            folders = self.app.prefs.get("import_folders") or []
            if not folders:
                return "Папок с материалами пока нет — скажи «импортируй материалы»."
            return self._run_import("материалы", lambda progress: _import_folders(folders, progress))
        return ""

    def _run_import(self, what: str, fn) -> str:
        from ..convertui import Job
        pop = self.app.text.popup
        pop.show_busy(f"📥 Импорт: {what}", "Читаю…")
        job = Job(lambda: fn(lambda f, s: job.progress.emit(f, s)))
        job.progress.connect(lambda f, s: pop.status.setText(f"{int(f * 100)} % — {s}"))
        job.done.connect(lambda res, err, secs: pop.show_text("📥 Импорт", err if err else res, copy=False, markdown=False))
        self._jobs = [j for j in self._jobs if j.isRunning()] + [job]
        job.start()
        return f"Импортирую: {what}."

    def explain(self, term: str, found: str):
        from ..convertui import Job
        pop = self.app.text.popup
        pop.show_busy(f"🧠 {term}", "Читаю твои уроки…")
        prompt = (f"Объясни тему «{term}» по-русски, простыми словами, для ученика курса IT-Support (немецкий B1). "
                  "Опирайся на фрагменты из его уроков и материалов ниже; немецкие термины оставляй с переводом; "
                  "в конце — на каком уроке это было и 2 вопроса для самопроверки.\n\n" + found[:12000])
        def ask():
            from .. import mentor
            text, who = mentor.ask_brain(self.app.agent, prompt)
            return text + f"\n\n_{who}_"
        job = Job(ask)
        job.done.connect(lambda res, err, secs: pop.show_text(f"🧠 {term}", err or res, copy_text=None if err else res))
        self._jobs = [j for j in self._jobs if j.isRunning()] + [job]
        job.start()


PREP_PROMPT = """Ученик курса IT-Support / Systemadministration в Германии (немецкий B1) идёт на урок.
Вот конспекты прошлых уроков:

{notes}

Словарь курса (переводы терминов — именно так):
{glossary}

Подготовь его к сегодняшнему уроку. По-русски, коротко, строго в формате Markdown, без вступлений:

## Что было
<2–3 предложения: о чём были прошлые уроки>

## Домашка
- <что задали и к какому сроку; если ничего — «Ничего не задавали»>

## 5 терминов на сегодня
| Deutsch | Русский |
|---|---|
| <термин с артиклем> | <перевод> |

## Что могут спросить
1. **<вопрос учителя по-немецки, B1>** — _<перевод>_
   Ответ: **<простой ответ по-немецки B1, 1–2 предложения>** — _<перевод>_
(3 вопроса)

## Фразы на урок
- **<фраза по-немецки по теме урока>** — _<перевод>_
(3 фразы)

Только то, что следует из конспектов. Не выдумывай новые темы."""


def importer_granola() -> str:
    from . import importer
    try:
        added, total = importer.import_granola()
    except FileNotFoundError as e:
        return (f"{e}. Если Granola у тебя только в браузере или на другом компьютере — экспортируй заметки в файлы "
                "(Markdown / PDF) и скажи «импортируй материалы».")
    return f"Granola: добавила {added} встреч (всего в Granola {total}). Теперь ищу и по ним: «что мы проходили по …»."


def _import_folders(folders: list[str], progress) -> str:
    from . import importer
    out = []
    for d in folders:
        if not Path(d).is_dir():
            out.append(f"• {d}: папки нет")
            continue
        done, skipped, errors = importer.import_folder(Path(d), progress=progress)
        line = f"• {Path(d).name}: новых/обновлённых {done}, без изменений {skipped}"
        if errors:
            line += f", не прочитала {len(errors)} ({'; '.join(errors[:3])})"
        out.append(line)
    return "Материалы:\n" + "\n".join(out) + "\n\nИщу и по ним: «что мы проходили по …»."
