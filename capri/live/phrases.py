"""Готовые фразы для урока (немецкий B1): кнопка 🗣 на субтитрах — фраза крупно сразу, без ожидания модели.
Свои фразы — Документы\\Capri\\Уроки\\Фразы.txt, по строке «Deutsch — Русский» (заголовки групп: «# Группа»)."""
from __future__ import annotations

import re
from pathlib import Path

from . import notes as N

PHRASES: list[tuple[str, list[tuple[str, str]]]] = [
    ("Не понял", [
        ("Können Sie das bitte wiederholen?", "Можете повторить, пожалуйста?"),
        ("Können Sie bitte etwas langsamer sprechen?", "Можете говорить чуть медленнее?"),
        ("Das habe ich nicht ganz verstanden.", "Я не совсем понял."),
        ("Was bedeutet das genau?", "Что это значит точно?"),
        ("Können Sie ein Beispiel geben?", "Можете привести пример?"),
    ]),
    ("Вопрос", [
        ("Ich habe eine Frage.", "У меня вопрос."),
        ("Darf ich kurz etwas fragen?", "Можно коротко спросить?"),
        ("Wie schreibt man das?", "Как это пишется?"),
        ("Bis wann sollen wir das machen?", "До какого срока это сделать?"),
        ("Wo finde ich die Unterlagen?", "Где найти материалы?"),
    ]),
    ("Ответ", [
        ("Einen Moment, bitte. Ich überlege kurz.", "Секунду, я подумаю."),
        ("Ich bin mir nicht sicher, aber ich denke …", "Я не уверен, но думаю…"),
        ("Ja, das habe ich verstanden.", "Да, я понял."),
        ("Ich bin fertig.", "Я закончил."),
        ("Ich bin noch nicht fertig, ich brauche noch ein paar Minuten.", "Я ещё не закончил, нужно пару минут."),
    ]),
    ("Техника", [
        ("Entschuldigung, mein Mikrofon hat nicht funktioniert.", "Извините, у меня не работал микрофон."),
        ("Ich höre Sie nicht gut.", "Я плохо вас слышу."),
        ("Ich sehe Ihren Bildschirm nicht.", "Я не вижу ваш экран."),
        ("Meine Verbindung ist schlecht, ich komme gleich wieder.", "Плохая связь, сейчас вернусь."),
        ("Ich bin wieder da.", "Я снова здесь."),
    ]),
]

LINE = re.compile(r"^\s*(?P<de>[^—–|]+?)\s*(?:—|–|\|| - )\s*(?P<ru>.+?)\s*$")


def path() -> Path:
    return N.lessons_dir() / "Фразы.txt"


def own(p: Path | None = None) -> list[tuple[str, list[tuple[str, str]]]]:
    p = p or path()
    if not p.exists():
        return []
    groups: list[tuple[str, list[tuple[str, str]]]] = []
    cur: list[tuple[str, str]] = []
    title = "Мои фразы"
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if ln.strip().startswith("#"):
            if cur:
                groups.append((title, cur))
            title, cur = ln.strip("# ").strip() or "Мои фразы", []
        elif m := LINE.match(ln):
            cur.append((m.group("de"), m.group("ru")))
    if cur:
        groups.append((title, cur))
    return groups


def all_groups(p: Path | None = None) -> list[tuple[str, list[tuple[str, str]]]]:
    try:
        mine = own(p)
    except OSError:
        mine = []
    return mine + PHRASES


def markdown(p: Path | None = None) -> str:
    out = []
    for title, items in all_groups(p):
        out.append(f"**{title}**")
        out += [f"- {de} — _{ru}_" for de, ru in items]
        out.append("")
    out.append("_Свои фразы — в файле Документы\\Capri\\Уроки\\Фразы.txt: строка «Deutsch — Русский»._")
    return "\n".join(out)
