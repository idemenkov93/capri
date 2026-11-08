"""Сессия субтитров: звук → фразы → Whisper → строка → перевод; плюс сигнал на имя и слова.

Всё в фоновых потоках; наружу — через колбэки (controller.py превращает их в Qt-сигналы).
Звук нигде не сохраняется — только текст.
"""
from __future__ import annotations

import queue
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .segmenter import Chunk, Segmenter

# Whisper на тишине и шуме «слышит» титры с YouTube — такие строки выбрасываем
HALLUCINATIONS = re.compile(
    r"untertitel|amara\.org|vielen dank (?:für|fürs) zuschauen|bis zum nächsten mal|danke fürs zuschauen|"
    r"copyright|www\.|subtitles by|thanks for watching|thank you for watching|продолжение следует|"
    r"субтитры (?:сделал|создавал)|редактор субтитров|спасибо за просмотр|подписывайтесь|♪",
    re.I)

IT_PROMPT = ("Unterricht IT-Support und Systemadministration: Active Directory, PowerShell, DNS, DHCP, Netzwerk, "
             "Server, Client, Windows, Linux, Benutzer, Gruppenrichtlinie, Ticket.")

NAME_VARIANTS = ["Igor", "Ihor", "Ігор", "Игорь", "Ихор", "Игор"]


@dataclass
class Line:
    id: int
    source: str              # system | mic
    text: str
    lang: str
    start: float
    end: float
    translation: str = ""
    trigger: str = ""        # какое слово сработало


@dataclass
class Settings:
    language: str = "de"            # язык урока: de | en | ru | he | auto
    target: str = "ru"
    mic: bool = True                # и мои фразы («Ты»)
    translate: bool = True
    names: list[str] = field(default_factory=lambda: list(NAME_VARIANTS))
    triggers: list[str] = field(default_factory=list)
    signals: bool = True
    prompt: str = IT_PROMPT
    partial: bool = True            # «живая» строка, пока фраза ещё говорится (только на видеокарте)


def trigger_regex(words: list[str]) -> re.Pattern | None:
    """Слова-сигналы: целым словом, без регистра; «Prüfung» ловит и «Prüfungen»."""
    words = [w.strip() for w in words if w and w.strip()]
    if not words:
        return None
    parts = [re.escape(w) + (r"\w*" if len(w) >= 5 else "") for w in sorted(words, key=len, reverse=True)]
    return re.compile(r"(?<!\w)(" + "|".join(parts) + r")(?!\w)", re.I)


def is_echo(chunk: Chunk, system_spans: list[tuple[float, float]]) -> bool:
    """Фраза с микрофона, когда в это же время говорили в колонках — скорее эхо колонок (без наушников)."""
    dur = max(1e-3, chunk.end - chunk.start)
    overlap = sum(max(0.0, min(chunk.end, b) - max(chunk.start, a)) for a, b in system_spans)
    return overlap / dur > 0.5


def clean(text: str) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    if not t or HALLUCINATIONS.search(t) or re.fullmatch(r"[\W\d_]+", t):
        return ""
    return t


class LiveSession:
    def __init__(self, model_getter: Callable[[], object], settings: Settings, *,
                 on_line: Callable[[Line], None], on_translation: Callable[[int, str], None],
                 on_partial: Callable[[str, str], None] = lambda s, t: None,
                 on_trigger: Callable[[Line], None] = lambda line: None,
                 on_status: Callable[[str], None] = lambda s: None,
                 translator=None, gpu: bool = False, capture_factory=None):
        self.model_getter, self.s = model_getter, settings
        self.on_line, self.on_translation, self.on_partial = on_line, on_translation, on_partial
        self.on_trigger, self.on_status = on_trigger, on_status
        self.translator, self.gpu = translator, gpu
        self.capture_factory = capture_factory
        self.lines: list[Line] = []
        self.audio_q: queue.Queue = queue.Queue(maxsize=2000)
        self.chunk_q: queue.Queue = queue.Queue()
        self.tr_q: queue.Queue = queue.Queue()
        self.stop_flag = threading.Event()
        self.paused = False
        self.seg = {"system": Segmenter("system"), "mic": Segmenter("mic", end_silence=0.7)}
        self.system_spans: list[tuple[float, float]] = []
        self.t0 = time.monotonic()
        self.captures = []
        self._threads: list[threading.Thread] = []
        self._id = 0
        self._rx = trigger_regex(self.s.names + self.s.triggers)
        self._last_partial = 0.0
        self.errors: list[str] = []

    # ------------------------------------------------------------------ старт / стоп
    def start(self):
        if self.capture_factory is not None:
            sources = ["system"] + (["mic"] if self.s.mic else [])
            for src in sources:
                c = self.capture_factory(src, self.push, self._capture_error)
                c.start()
                self.captures.append(c)
        for fn, name in ((self._segment_loop, "live-seg"), (self._asr_loop, "live-asr"), (self._tr_loop, "live-tr")):
            t = threading.Thread(target=fn, name=name, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self):
        for c in self.captures:
            c.stop()
        for src, seg in self.seg.items():
            for ch in seg.flush():
                self.chunk_q.put(ch)
        self.stop_flag.set()
        self.chunk_q.put(None)
        self.tr_q.put(None)

    def set_paused(self, on: bool):
        self.paused = on
        for c in self.captures:
            c.paused = on

    def set_words(self, names: list[str], triggers: list[str]):
        self.s.names, self.s.triggers = names, triggers
        self._rx = trigger_regex(names + triggers)

    def _capture_error(self, msg: str):
        self.errors.append(msg)
        self.on_status(f"⚠ звук: {msg}")

    # ------------------------------------------------------------------ звук → фразы
    def push(self, source: str, frames: np.ndarray):
        """Сюда кладут звук 16 кГц моно (из capture.py или тестов)."""
        if not self.paused:
            try:
                self.audio_q.put_nowait((source, frames))
            except queue.Full:                 # Whisper не успевает — лучше потерять звук, чем отстать навсегда
                pass

    def _segment_loop(self):
        while not self.stop_flag.is_set():
            try:
                source, frames = self.audio_q.get(timeout=0.2)
            except queue.Empty:
                continue
            seg = self.seg[source]
            for ch in seg.feed(frames):
                if source == "system":
                    self.system_spans = [s for s in self.system_spans if s[1] > ch.end - 30] + [(ch.start, ch.end)]
                self.chunk_q.put(ch)
            if self.s.partial and self.gpu and time.monotonic() - self._last_partial > 1.5 and self.chunk_q.empty():
                if (audio := seg.partial()) is not None:
                    self._last_partial = time.monotonic()
                    self.chunk_q.put(("partial", source, audio))

    # ------------------------------------------------------------------ фразы → текст
    def _asr_loop(self):
        model = None
        while True:
            item = self.chunk_q.get()
            if item is None:
                break
            if model is None:
                self.on_status("Загружаю распознавание…")
                try:
                    model = self.model_getter()
                except Exception as e:  # noqa: BLE001
                    self.on_status(f"⚠ Whisper не загрузился: {e}")
                    return
                self.on_status("")
            if isinstance(item, tuple):                       # «живая» строка
                _, source, audio = item
                if self.chunk_q.qsize() == 0:
                    text = self._transcribe(model, audio, fast=True)[0]
                    if text:
                        self.on_partial(source, text)
                continue
            ch: Chunk = item
            if ch.source == "mic":
                sys_seg = self.seg["system"]
                spans = self.system_spans + ([(sys_seg._start, sys_seg._t)] if sys_seg.speaking else [])
                if is_echo(ch, spans):
                    continue
            text, lang = self._transcribe(model, ch.audio)
            self.on_partial(ch.source, "")
            if not text:
                continue
            self._id += 1
            line = Line(self._id, ch.source, text, lang, ch.start, ch.end)
            if self._rx and ch.source == "system" and (m := self._rx.search(text)):
                line.trigger = m.group(1)
            self.lines.append(line)
            self.on_line(line)
            if line.trigger and self.s.signals:
                self.on_trigger(line)
            if self.s.translate and self.translator is not None and lang != self.s.target:
                self.tr_q.put(line)

    def _transcribe(self, model, audio: np.ndarray, fast: bool = False) -> tuple[str, str]:
        lang = None if self.s.language == "auto" else self.s.language
        prev = " ".join(x.text for x in self.lines[-2:] if x.source == "system")[-200:]
        try:
            segs, info = model.transcribe(
                audio.astype(np.float32), language=lang, beam_size=1 if fast else (5 if self.gpu else 2),
                vad_filter=True, vad_parameters={"min_silence_duration_ms": 300},
                condition_on_previous_text=False, temperature=0.0, no_speech_threshold=0.6,
                initial_prompt=(self.s.prompt + " " + prev).strip() or None)
            parts = [s.text for s in segs if getattr(s, "no_speech_prob", 0) < 0.7 and getattr(s, "avg_logprob", 0) > -1.2]
        except Exception as e:  # noqa: BLE001
            self.on_status(f"⚠ распознавание: {str(e)[:100]}")
            return "", ""
        return clean(" ".join(parts)), (lang or getattr(info, "language", "") or "")

    # ------------------------------------------------------------------ перевод
    def _tr_loop(self):
        while True:
            line = self.tr_q.get()
            if line is None:
                break
            batch = [line]
            t_end = time.monotonic() + 0.4                     # подождать соседние фразы — переведём пачкой
            while len(batch) < 4 and time.monotonic() < t_end:
                try:
                    nxt = self.tr_q.get(timeout=max(0.0, t_end - time.monotonic()))
                except queue.Empty:
                    break
                if nxt is None:
                    self.tr_q.put(None)
                    break
                batch.append(nxt)
            context = [x.text for x in self.lines if x.id < batch[0].id][-3:]
            try:
                out = self.translator.translate([b.text for b in batch], context)
            except Exception as e:  # noqa: BLE001
                self.on_status(f"⚠ перевод: {str(e)[:120]}")
                continue
            for b, tr in zip(batch, out):
                b.translation = tr
                self.on_translation(b.id, tr)

    # ------------------------------------------------------------------ итог
    def elapsed(self) -> float:
        return time.monotonic() - self.t0
