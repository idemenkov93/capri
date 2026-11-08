"""Что показать в круговом меню для выделенных файлов и как выполнить выбранное.

Меню зависит от файлов: картинки — свои действия, видео — свои; разные вперемешку — только общее (ZIP, SHA256,
переименовать). На верхнем кольце не больше 8 секторов, остальное — в «Ещё».
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import convert as C

MB = 1 << 20


@dataclass
class Item:
    label: str
    icon: str
    code: str = ""                      # что выполнить; пусто — это группа (children)
    children: list["Item"] = field(default_factory=list)
    ask: str = ""                       # нужен ввод (кусок видео, шаблон имени) — подсказка для поля
    hint: str = ""


def _common(files: list[Path]) -> list[Item]:
    out = [Item("SHA256", "#", "any.sha", hint="Контрольная сумма — сверить скачанный файл"),
           Item("В ZIP", "🗜", "any.zip", hint="Упаковать в архив рядом")]
    if all(C.kind(f) == "archive" for f in files):
        out.insert(0, Item("Распаковать", "📦", "any.unzip"))
    if len(files) > 1:
        out.append(Item("Переименовать", "✏", "any.rename", ask="Как назвать? {n} — номер. Например: Отпуск {n}",
                        hint="Отпуск 01, Отпуск 02…"))
    return out


def menu_for(files: list[Path]) -> list[Item]:
    kinds = {C.kind(f) for f in files}
    many = len(files) > 1
    k = kinds.pop() if len(kinds) == 1 else "mixed"
    common = _common(files)
    if k == "image":
        top = [Item("Формат", "🔄", children=[Item(e[1:].upper(), "", f"img.format:{e}") for e in (".jpg", ".png", ".webp", ".ico")]),
               Item("Сжать", "🗜", children=[Item(t, "", f"img.compress:{b}") for t, b in
                                              (("до 1 МБ", MB), ("до 500 КБ", 500 * 1024), ("до 200 КБ", 200 * 1024))]),
               Item("Уменьшить", "📐", children=[Item(t, "", f"img.resize:{v}") for t, v in
                                                  (("50 %", "50%"), ("1920 px", "1920"), ("1280 px", "1280"), ("800 px", "800"))]),
               Item("Повернуть", "↻", children=[Item("вправо 90°", "↻", "img.rotate:90"), Item("влево 90°", "↺", "img.rotate:-90"),
                                                Item("180°", "⟲", "img.rotate:180")]),
               Item("Убрать фон", "🪄", "img.bg", hint="PNG с прозрачным фоном (модель ~5 МБ скачается один раз)"),
               Item("В PDF", "📄", "img.pdf", hint="Все картинки — в один PDF по порядку"),
               Item("Обрезать поля", "✂", "img.trim", hint="Убрать одноцветные поля вокруг"),
               Item("Ещё", "⋯", children=[Item("Текст с картинки", "🔤", "img.ocr"), *common])]
    elif k == "pdf":
        top = ([Item("Склеить", "📎", "pdf.merge", hint="В один PDF по порядку имён")] if many else []) + [
            Item("Разделить", "✂", "pdf.split", hint="Каждая страница — отдельный PDF"),
            Item("Сжать", "🗜", "pdf.compress", hint="Картинки внутри поменьше, текст не трогаю"),
            Item("В картинки", "🖼", "pdf.images"),
            Item("В Word", "📝", "pdf.word", hint="Нужен Microsoft Word"),
            Item("В текст", "🔤", "pdf.text"),
            Item("Повернуть", "↻", "pdf.rotate")]
        top.append(Item("Ещё", "⋯", children=common))
    elif k == "doc":
        top = [Item("В PDF", "📄", "doc.pdf", hint="Через Microsoft Office или LibreOffice"), *common]
    elif k == "video":
        top = [Item("В MP4", "🔄", "vid.mp4", hint="Откроется везде: Windows, телефон, Telegram"),
               Item("Сжать", "🗜", children=[Item("до 10 МБ", "", "vid.compress:10"), Item("до 25 МБ", "", "vid.compress:25"),
                                              Item("до 50 МБ", "", "vid.compress:50"), Item("поменьше", "", "vid.compress:0")]),
               Item("Вырезать", "✂", "av.cut", ask="Какой кусок? Например: с 1:20 до 2:05 · первые 30 секунд"),
               Item("GIF", "🎞", "vid.gif"),
               Item("Звук в MP3", "🎵", "vid.mp3"),
               Item("Без звука", "🔇", "vid.mute")]
        if many:
            top.append(Item("Склеить", "🔗", "av.concat", hint="Части по порядку: часть 1, 2, 3 · VTS_01_1.VOB…"))
        top.append(Item("Ещё", "⋯", children=[Item("Субтитры .srt", "💬", "av.srt"), Item("Текст из речи", "📝", "av.text"),
                                              *common]))
    elif k == "audio":
        top = [Item("Формат", "🔄", children=[Item(e[1:].upper(), "", f"aud.format:{e}") for e in (".mp3", ".wav", ".ogg", ".m4a")]),
               Item("Громкость", "🔊", "aud.norm", hint="Выровнять: тихое громче, без перегруза"),
               Item("Вырезать", "✂", "av.cut", ask="Какой кусок? Например: с 0:30 до 1:10"),
               Item("В текст", "📝", "av.text", hint="Расшифровка речи (локально, Whisper)")]
        if many:
            top.append(Item("Склеить", "🔗", "av.concat"))
        top += common
    else:
        top = common
    return top[:8]


def find(items: list[Item], code: str) -> Item | None:
    for it in items:
        if it.code == code:
            return it
        if (sub := find(it.children, code)) is not None:
            return sub
    return None


def needs_ffmpeg(code: str) -> bool:
    return code.startswith(("vid.", "aud.")) or code in ("av.cut", "av.concat")


def needs_whisper(code: str) -> bool:
    return code in ("av.srt", "av.text")


@dataclass
class Ctx:
    models_dir: Path
    workdir: Path
    progress: Callable[[float, str], None] = lambda f, s: None
    cancel: threading.Event = field(default_factory=threading.Event)
    ffmpeg: tuple[str, str] | None = None
    whisper: Callable[[], object] | None = None       # → WhisperModel
    arg: str = ""                                      # что ввёл пользователь (кусок, шаблон имени)


def run(code: str, files: list[Path], ctx: Ctx):
    """→ список новых файлов, или [(файл, sha256)] для any.sha."""
    op, _, val = code.partition(":")
    p = ctx.progress
    if op == "img.format":
        return C.image_format(files, val, p)
    if op == "img.compress":
        return C.image_compress(files, int(val), p)
    if op == "img.resize":
        return C.image_resize(files, val, p)
    if op == "img.rotate":
        return C.image_rotate(files, int(val), p)
    if op == "img.trim":
        return C.image_trim(files, p)
    if op == "img.bg":
        return C.remove_background(files, ctx.models_dir, p)
    if op == "img.pdf":
        return C.images_to_pdf(files, p)
    if op == "pdf.merge":
        return C.pdf_merge(files, p)
    if op == "pdf.split":
        return C.pdf_split(files, p)
    if op == "pdf.compress":
        return C.pdf_compress(files, p)
    if op == "pdf.images":
        return C.pdf_to_images(files, progress=p)
    if op == "pdf.word":
        return C.pdf_to_word(files, p)
    if op == "pdf.text":
        return C.pdf_text_file(files, p)
    if op == "pdf.rotate":
        return C.pdf_rotate(files, 90, p)
    if op == "doc.pdf":
        return C.office_to_pdf(files, p)
    if op == "any.sha":
        return C.sha256(files, p)
    if op == "any.zip":
        return C.to_zip(files, p)
    if op == "any.unzip":
        return C.unpack(files, p)
    if op == "any.rename":
        return C.rename(files, ctx.arg, p)
    if needs_whisper(op):
        from .media import transcribe
        if ctx.whisper is None:
            raise C.ConvertError("распознавание речи выключено — включи голос в настройках")
        return transcribe(files, ctx.whisper(), srt=op == "av.srt", progress=p, cancel=ctx.cancel)
    if needs_ffmpeg(op):
        from .media import Runner
        if not ctx.ffmpeg:
            raise C.ConvertError("нужен ffmpeg")
        r = Runner(*ctx.ffmpeg, progress=p, cancel=ctx.cancel)
        if op == "vid.mp4":
            return r.to_mp4(files)
        if op == "vid.compress":
            return r.compress(files, int(val) * MB if int(val) else None)
        if op == "av.cut":
            return r.cut(files, ctx.arg)
        if op == "vid.gif":
            return r.gif(files)
        if op == "vid.mp3":
            return r.audio(files, ".mp3")
        if op == "vid.mute":
            return r.mute(files)
        if op == "aud.format":
            return r.audio(files, val)
        if op == "aud.norm":
            return r.audio(files, files[0].suffix.lower() if files[0].suffix.lower() in (".mp3", ".wav", ".ogg", ".m4a", ".flac")
                           else ".mp3", normalize=True)
        if op == "av.concat":
            return r.concat(files, ctx.workdir / "concat")
    raise C.ConvertError(f"не знаю действие {code}")
