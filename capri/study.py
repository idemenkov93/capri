"""Волна 4 (окончание): языковые помощники, слово дня, утренняя сводка, подсказка на уроке, тренажёр и карточки.

Всё отвечает текстом в окне (popup) — немецкий и иврит русским голосом читать бессмысленно; голосом — коротко.
"""
from __future__ import annotations

import datetime as dt
import json
import random
import re

from . import actions as A

LANGS = {"немецк": ("de", "немецкий"), "deutsch": ("de", "немецкий"), "английск": ("en", "английский"),
         "англ": ("en", "английский"), "иврит": ("he", "иврит"), "русск": ("ru", "русский"), "украинск": ("uk", "украинский")}

RE_SAY = re.compile(r"^как\s+(?:будет|сказать|написать|спросить|ответить)?\s*(?:по|на)[\s-]+(?P<lang>немецк\w*|английск\w*|"
                    r"англ\w*|иврит\w*|русск\w*|украинск\w*|deutsch)\s*[:,—-]?\s*(?P<what>.+)$", re.I)
RE_SAY2 = re.compile(r"^как\s+(?:будет\s+)?(?P<what>.+?)\s+(?:по|на)[\s-]+(?P<lang>немецк\w*|английск\w*|иврит\w*|deutsch)\w*\s*\??$", re.I)
RE_FIX = re.compile(r"^(?:исправь|проверь|поправь)\s+(?:мою\s+)?(?:фразу|предложение|текст|немецкий|ошибки)\s*[:,—-]?\s*(?P<what>.+)$",
                    re.I | re.S)
RE_WORD = re.compile(r"^(?:слово\s+дня|новое\s+слово|дай\s+(?:мне\s+)?слово(?:\s+дня)?)$", re.I)
RE_MORNING = re.compile(r"^(?:утренняя\s+сводка|сводка\s+на\s+(?:сегодня|день)|что\s+у\s+меня\s+сегодня\s+по\s+учёбе|"
                        r"план\s+на\s+сегодня|доброе\s+утро(?:\s+капри)?)$", re.I)
RE_MORNING_AUTO = re.compile(r"^(?P<on>включи|выключи)\s+утренн\w+\s+сводк\w+$", re.I)
RE_COURSE_END = re.compile(r"^(?:курс|учёба|учеба|weiterbildung|вайтербильдунг)\s+(?:заканчивается|кончается|до)\s+(?P<d>.+)$", re.I)
RE_HINT = re.compile(r"^(?:подскажи\s+(?:ответ|что\s+ответить)|что\s+(?:мне\s+)?ответить|какой\s+был\s+вопрос|"
                     r"что\s+(?:меня\s+)?спросили|о\s+ч[её]м\s+(?:меня\s+)?спросили)\??$", re.I)
RE_ANSWER = re.compile(r"^(?:помоги\s+ответить|как\s+ответить|ответ\s+учителю|быстрый\s+ответ|хочу\s+сказать)"
                       r"\s*[:,—-]?\s*(?P<what>.+)$", re.I | re.S)
RE_TRAIN = re.compile(r"^(?:тренируй|тренировка|потренируй|потренируемся|начни\s+тренировку)\s*(?P<what>.*)$", re.I)
RE_CARDS = re.compile(r"^(?:спроси|проверь|погоняй)\s+меня(?:\s+(?P<n>\d+|пять|три|десять)\s+вопрос\w*)?\s*(?:по|на\s+тему|про)\s+"
                      r"(?P<topic>.+)$", re.I)
RE_STOP_TRAIN = re.compile(r"^(?:стоп|хватит|закончи|заверши)\s*(?:тренировк\w*|карточк\w*|вопрос\w*)?$", re.I)
RE_WEAK = re.compile(r"^(?:повтори|потренируй)\s+(?:мои\s+)?слабые\s+темы$|^какие\s+у\s+меня\s+слабые\s+темы$", re.I)

WORDS = [  # слово дня: немецкий IT-термин, перевод, пример (B1)
    ("die Störung", "сбой, неполадка", "Wir haben eine Störung im Netzwerk."),
    ("das Ticket", "заявка (тикет)", "Bitte erstellen Sie ein Ticket für das Problem."),
    ("die Berechtigung", "право доступа", "Der Benutzer hat keine Berechtigung für den Ordner."),
    ("das Kennwort", "пароль", "Das Kennwort muss alle 90 Tage geändert werden."),
    ("die Freigabe", "общий ресурс / общий доступ", "Die Freigabe auf dem Server ist nicht erreichbar."),
    ("die Gruppenrichtlinie", "групповая политика (GPO)", "Die Gruppenrichtlinie wird beim Anmelden angewendet."),
    ("der Benutzer", "пользователь", "Ich lege einen neuen Benutzer im Active Directory an."),
    ("anlegen", "создавать (учётку, папку)", "Kannst du bitte ein Konto für den neuen Kollegen anlegen?"),
    ("die Anmeldung", "вход в систему", "Die Anmeldung funktioniert nach dem Update nicht mehr."),
    ("sperren / entsperren", "заблокировать / разблокировать", "Das Konto wurde nach drei Versuchen gesperrt."),
    ("zurücksetzen", "сбросить", "Ich setze Ihr Passwort jetzt zurück."),
    ("die Datensicherung", "резервная копия, бэкап", "Die Datensicherung läuft jede Nacht um 2 Uhr."),
    ("wiederherstellen", "восстановить", "Wir können die Datei aus der Sicherung wiederherstellen."),
    ("der Drucker", "принтер", "Der Drucker im zweiten Stock druckt nicht."),
    ("die Warteschlange", "очередь (печати)", "Die Druckwarteschlange hängt."),
    ("das Laufwerk", "диск (сетевой диск)", "Das Netzlaufwerk H: ist nicht verbunden."),
    ("verbinden / trennen", "подключить / отключить", "Bitte trennen Sie das VPN und verbinden Sie es neu."),
    ("die Fehlermeldung", "сообщение об ошибке", "Können Sie mir die Fehlermeldung vorlesen?"),
    ("neu starten", "перезагрузить", "Haben Sie den Rechner schon neu gestartet?"),
    ("der Rechner", "компьютер", "Der Rechner fährt nicht hoch."),
    ("hochfahren / herunterfahren", "включить / выключить (ПК)", "Bitte fahren Sie den Rechner herunter."),
    ("die Aktualisierung", "обновление", "Die Aktualisierung wird nach dem Neustart installiert."),
    ("das Netzwerk", "сеть", "Der Laptop findet das WLAN-Netzwerk nicht."),
    ("die IP-Adresse", "IP-адрес", "Der Client bekommt keine IP-Adresse vom DHCP-Server."),
    ("die Namensauflösung", "разрешение имён (DNS)", "Die Namensauflösung funktioniert nicht, der Ping auf die IP schon."),
    ("die Schnittstelle", "интерфейс, разъём", "Die Netzwerkschnittstelle ist deaktiviert."),
    ("einrichten", "настроить", "Ich richte Ihnen Outlook auf dem neuen Laptop ein."),
    ("die Einstellung", "настройка", "Ändern Sie bitte diese Einstellung nicht."),
    ("der Zugriff", "доступ", "Ich habe keinen Zugriff auf das Postfach."),
    ("das Postfach", "почтовый ящик", "Das Postfach ist voll."),
    ("die Weiterleitung", "переадресация", "Bitte richten Sie eine Weiterleitung ein."),
    ("weiterleiten", "переслать, передать дальше", "Ich leite Ihr Ticket an den 2nd Level weiter."),
    ("die Rückmeldung", "обратная связь, ответ", "Ich gebe Ihnen bis morgen eine Rückmeldung."),
    ("dringend", "срочно", "Das Problem ist dringend, die ganze Abteilung kann nicht arbeiten."),
    ("die Abteilung", "отдел", "Welche Abteilung ist betroffen?"),
    ("betroffen", "затронутый", "Wie viele Benutzer sind betroffen?"),
    ("die Ursache", "причина", "Die Ursache war ein abgelaufenes Zertifikat."),
    ("beheben", "устранить", "Wir haben den Fehler behoben."),
    ("die Lösung", "решение", "Ich habe eine vorübergehende Lösung gefunden."),
    ("vorübergehend", "временно", "Der Server ist vorübergehend nicht erreichbar."),
    ("erreichbar", "доступен (по сети)", "Ist der Server per Ping erreichbar?"),
    ("die Fernwartung", "удалённая поддержка", "Darf ich per Fernwartung auf Ihren Rechner zugreifen?"),
    ("der Arbeitsplatz", "рабочее место", "Ich komme gleich an Ihren Arbeitsplatz."),
    ("die Bestandsaufnahme", "инвентаризация", "Wir machen eine Bestandsaufnahme aller Geräte."),
    ("das Gerät", "устройство", "Das Gerät ist noch in der Garantie."),
    ("austauschen", "заменить", "Wir tauschen die Festplatte aus."),
    ("die Festplatte", "жёсткий диск", "Die Festplatte ist fast voll."),
    ("der Speicherplatz", "место на диске", "Es gibt nicht genug Speicherplatz."),
    ("die Verschlüsselung", "шифрование", "Die Laptops nutzen BitLocker-Verschlüsselung."),
    ("das Zertifikat", "сертификат", "Das Zertifikat läuft nächste Woche ab."),
    ("ablaufen", "истекать (сроку)", "Ihr Passwort läuft in drei Tagen ab."),
    ("die Sicherheitslücke", "уязвимость", "Das Update schließt eine Sicherheitslücke."),
    ("die Zwei-Faktor-Authentifizierung", "двухфакторная аутентификация", "Bitte richten Sie die Zwei-Faktor-Authentifizierung ein."),
    ("die Dokumentation", "документация", "Bitte tragen Sie die Lösung in die Dokumentation ein."),
    ("eskalieren", "эскалировать (передать выше)", "Wenn ich es nicht lösen kann, eskaliere ich das Ticket."),
    ("der Ansprechpartner", "контактное лицо", "Wer ist Ihr Ansprechpartner in der IT?"),
    ("die Bereitschaft", "дежурство", "Am Wochenende habe ich Bereitschaft."),
    ("die Schulung", "обучение, тренинг", "Morgen haben wir eine Schulung zu Microsoft 365."),
    ("die Umgebung", "среда (окружение)", "Wir testen das zuerst in der Testumgebung."),
    ("bereitstellen", "развернуть, предоставить", "Die neue Software wird über Intune bereitgestellt."),
]


def fast_ask(agent, prompt: str) -> str:
    """Быстрый ответ (подсказка на уроке, язык, тренажёр): сначала облачные по HTTP (Groq, Gemini, OpenRouter) —
    1–3 секунды, а не Claude через программу (10–20 с). Никого нет — обычный порядок (ask_text)."""
    for p in ("groq", "gemini", "openrouter"):
        try:
            if not agent.ready(p):
                continue
            if p == "gemini":
                return agent.gemini_ask(prompt, {**agent._gcfg(), "timeout": 20}, images=None, system="")
            return agent.cloud_ask(p, prompt, {**agent._ccfg(p), "timeout": 20}, images=None, system="")
        except Exception:  # noqa: BLE001 — лимит / нет сети: следующий
            continue
    return agent.ask_text(prompt)


def word_of_day(day: dt.date | None = None) -> tuple[str, str, str]:
    day = day or dt.date.today()
    return WORDS[day.toordinal() % len(WORDS)]


def lang_code(word: str) -> tuple[str, str]:
    w = word.lower()
    for k, v in LANGS.items():
        if w.startswith(k):
            return v
    return ("de", "немецкий")


# ====================================================================== подсказки (LLM)
SAY_PROMPT = """Как сказать {lang}: «{what}».
Дай 1–2 варианта: обычный и вежливый (если отличаются). {extra}
Коротко объясни по-русски разницу, если она есть. Уровень — B1, без сложных конструкций. Без вступлений.
Формат:
**<вариант>**{tr}
<1 строка пояснения>"""

FIX_PROMPT = """Ученик (немецкий B1) написал: «{what}»
Исправь ошибки. Ответ строго в формате, по-русски, коротко:
**Правильно:** <исправленная фраза на том же языке>
**Ошибки:** <по пунктам: было → стало, почему — одной простой фразой>
**Ещё проще/естественнее:** <вариант, как сказал бы носитель, уровень B1>
Если ошибок нет — так и скажи и дай только последний пункт."""

HINT_PROMPT = """Идёт урок (курс IT-Support / Systemadministration, Германия, язык урока — {lang}). Ученик — Игорь (немецкий B1).
Последние фразы урока (кто говорил: имя/«Спикер N», «Ты» — это Игорь):
{lines}

Найди последний вопрос, который задали Игорю или всей группе. Ответ строго в формате:
**Вопрос:** <по-русски, коротко>
**Ответ ({lang}, B1):** <1–3 простых предложения, которые Игорь может сказать сам>
**Почему так:** <по-русски, 1–2 предложения — суть, чтобы понять, а не зачитать>
Если вопроса нет — напиши: «Вопроса не услышала».
"""


# ====================================================================== тренажёр
TRAIN_KINDS = {
    "interview": ("собеседование (Fachfragen IT-Support 1st/2nd Level)", "de"),
    "intro": ("самопрезентация (Selbstvorstellung) на собеседовании", "de"),
    "client": ("ролевая игра: недовольный клиент звонит в Service Desk", "de"),
    "cards": ("карточки", "ru"),
}

QUESTIONS_PROMPT = """Составь {n} вопросов для тренировки: {what}. Уровень — Junior IT-Support / Systemadministrator.
Язык вопросов — {lang}{b1}. Вопросы разные, от простого к сложному, без вариантов ответа.
Ответ — только JSON-массив строк, без markdown."""

EVAL_PROMPT = """Тренировка: {what}. Вопрос: «{q}»
Ответ ученика (немецкий B1, может быть с ошибками распознавания речи): «{a}»
Оцени по-русски, коротко, строго в формате JSON без markdown:
{{"score": 1-5, "topic": "<тема вопроса 1–3 слова>", "good": "<что верно>", "missing": "<чего не хватило по сути>",
"german": "<главные ошибки немецкого: было → стало, или пусто>", "model": "<пример хорошего ответа на языке вопроса, уровень B1, 2–4 предложения>"}}"""

CLIENT_PROMPT = """Ролевая игра на немецком (уровень B1). Ты — {persona}, звонишь в IT Service Desk. Проблема: {problem}.
Ты раздражён, но вежлив по-немецки; отвечай коротко (1–3 предложения), как живой человек. Не решай проблему сам.
Разговор:
{history}
Сотрудник (ученик) сказал: «{a}»
Ответ строго JSON без markdown: {{"client": "<твоя следующая реплика по-немецки>", "tip": "<1 короткий совет ученику по-русски: что сказать лучше / какую фразу использовать>", "solved": true/false}}"""

PROBLEMS = [("Frau Becker aus der Buchhaltung", "Outlook startet nicht, sie muss dringend Rechnungen verschicken"),
            ("Herr Wagner, Vertriebsleiter", "sein Laptop kommt nicht ins VPN, er hat in 20 Minuten ein Kundenmeeting"),
            ("Frau Schulz aus dem Personalbüro", "der Drucker druckt nichts, die Druckaufträge hängen"),
            ("Herr Krüger aus dem Lager", "sein Konto ist gesperrt, er hat das Passwort dreimal falsch eingegeben"),
            ("Frau Yilmaz aus dem Marketing", "sie hat keinen Zugriff mehr auf den Team-Ordner auf dem Netzlaufwerk")]

FALLBACK_DE = ["Was ist der Unterschied zwischen 1st und 2nd Level Support?",
               "Ein Benutzer kann sich nicht anmelden. Wie gehen Sie vor?",
               "Was ist DNS und wofür braucht man es?", "Was ist DHCP?",
               "Wie setzen Sie im Active Directory ein Passwort zurück?",
               "Ein Drucker druckt nicht. Was prüfen Sie zuerst?",
               "Was ist eine Gruppenrichtlinie?", "Wie gehen Sie mit einem verärgerten Kunden um?",
               "Was bedeutet das Prinzip der minimalen Rechte?", "Warum sind Backups wichtig und welche Arten gibt es?"]


def parse_json(raw: str):
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    m = re.search(r"[\[{].*[\]}]", s, re.S)
    if not m:
        raise ValueError("нет JSON")
    return json.loads(m.group(0))


class Trainer:
    """Одна тренировка: вопросы → ответы → разбор. Слабые темы копятся в prefs["weak_topics"]."""

    def __init__(self, ask, kind: str, topic: str = "", n: int = 5, prefs=None):
        self.ask, self.kind, self.topic, self.n, self.prefs = ask, kind, topic, n, prefs
        self.questions: list[str] = []
        self.i = 0
        self.scores: list[int] = []
        self.weak: list[str] = []
        self.history: list[str] = []
        self.persona, self.problem = random.choice(PROBLEMS)

    @property
    def lang(self) -> str:
        return "ru" if self.kind == "cards" else "de"

    def what(self) -> str:
        base = TRAIN_KINDS[self.kind][0]
        return f"{base} по теме «{self.topic}»" if self.topic else base

    def start(self) -> str:
        if self.kind == "client":
            first = f"Guten Tag, hier ist {self.persona}. Ich habe ein Problem: {self.problem}. Können Sie mir helfen?"
            self.history.append(f"Клиент: {first}")
            return ("🎭 Ролевая игра: ты — Service Desk, тебе звонят. Отвечай по-немецки, «стоп тренировка» — закончить.\n\n"
                    f"**Клиент:** {first}")
        if self.kind == "intro":
            self.questions = ["Stellen Sie sich bitte kurz vor: Wer sind Sie und warum möchten Sie bei uns arbeiten?"]
        else:
            try:
                arr = parse_json(self.ask(QUESTIONS_PROMPT.format(
                    n=self.n, what=self.what(), lang="русский" if self.lang == "ru" else "немецкий",
                    b1=", простые формулировки уровня B1" if self.lang == "de" else "")))
                self.questions = [str(q).strip() for q in arr if str(q).strip()][: self.n]
            except Exception:  # noqa: BLE001 — помощник недоступен: свои вопросы
                self.questions = random.sample(FALLBACK_DE, min(self.n, len(FALLBACK_DE))) if self.lang == "de" else []
            if not self.questions:
                raise RuntimeError("не получилось придумать вопросы — проверь, что подключён умный помощник")
        intro = {"interview": "🎯 Собеседование: отвечай по-немецки, голосом или текстом. «стоп тренировка» — закончить.",
                 "intro": "🎯 Самопрезентация: расскажи о себе по-немецки (1–2 минуты), разберу.",
                 "cards": f"🃏 Карточки: {len(self.questions)} вопросов. Отвечай по-русски."}[self.kind]
        return f"{intro}\n\n**Вопрос 1/{len(self.questions)}:** {self.questions[0]}"

    def answer(self, text: str) -> tuple[str, bool]:
        """→ (разбор + следующий вопрос, закончили ли)."""
        if self.kind == "client":
            return self._client(text)
        q = self.questions[self.i]
        try:
            ev = parse_json(self.ask(EVAL_PROMPT.format(what=self.what(), q=q, a=text)))
        except Exception:  # noqa: BLE001
            ev = {}
        score = int(ev.get("score") or 0) if str(ev.get("score", "")).isdigit() else 0
        if score:
            self.scores.append(score)
        if score and score <= 3 and ev.get("topic"):
            self.weak.append(str(ev["topic"]))
        parts = [f"**Оценка:** {'★' * score}{'☆' * (5 - score)}" if score else "**Разбор:**"]
        for key, label in (("good", "✅ Хорошо"), ("missing", "➕ Не хватило"), ("german", "🇩🇪 Немецкий"),
                           ("model", "💬 Пример ответа")):
            if ev.get(key):
                parts.append(f"**{label}:** {ev[key]}")
        self.i += 1
        if self.i >= len(self.questions):
            return "\n\n".join(parts) + "\n\n" + self.finish(), True
        return "\n\n".join(parts) + f"\n\n**Вопрос {self.i + 1}/{len(self.questions)}:** {self.questions[self.i]}", False

    def _client(self, text: str) -> tuple[str, bool]:
        try:
            r = parse_json(self.ask(CLIENT_PROMPT.format(persona=self.persona, problem=self.problem,
                                                         history="\n".join(self.history[-8:]), a=text)))
        except Exception:  # noqa: BLE001
            return "Помощник не ответил — попробуй ещё раз или «стоп тренировка».", False
        self.history += [f"Сотрудник: {text}", f"Клиент: {r.get('client', '')}"]
        done = bool(r.get("solved")) or len(self.history) > 16
        out = f"**Клиент:** {r.get('client', '')}" + (f"\n\n💡 {r['tip']}" if r.get("tip") else "")
        return (out + "\n\n🎉 Разговор закончен. Хорошая работа!" if done else out), done

    def finish(self) -> str:
        avg = sum(self.scores) / len(self.scores) if self.scores else 0
        if self.prefs is not None and self.weak:
            w = dict(self.prefs.get("weak_topics") or {})
            for t in self.weak:
                w[t] = w.get(t, 0) + 1
            self.prefs.set("weak_topics", w)
        s = f"🏁 Готово: средняя оценка {avg:.1f} из 5." if self.scores else "🏁 Готово."
        if self.weak:
            s += " Повторить: " + ", ".join(dict.fromkeys(self.weak)) + ". Потом скажи «повтори слабые темы»."
        return s


# ====================================================================== маршрутизация (из agent._rules)
def route(agent, text: str):
    from .agent import Reply
    t = text.strip()
    if m := (RE_SAY.match(t) or RE_SAY2.match(t)):
        code, name = lang_code(m.group("lang"))
        what = m.group("what").strip(" ?«»\"")
        extra = "Добавь транслитерацию русскими буквами и латиницей." if code == "he" else ""
        tr = "\n<транслитерация>" if code == "he" else ""
        how = {"de": "по-немецки", "en": "по-английски", "he": "на иврите", "ru": "по-русски", "uk": "по-украински"}[code]
        return _popup(agent, Reply, f"Как сказать: {what[:40]}", SAY_PROMPT.format(lang=how, what=what, extra=extra, tr=tr),
                      copy_first=True)
    if m := RE_FIX.match(t):
        return _popup(agent, Reply, "Исправление", FIX_PROMPT.format(what=m.group("what").strip()), copy_first=True)
    if RE_WORD.match(t):
        de, ru, ex = word_of_day()
        return Reply(f"Слово дня: {de} — {ru}.", [A.Action("popup", "Слово дня", {
            "title": "🇩🇪 Слово дня", "text": f"**{de}** — {ru}\n\n_{ex}_", "command": ""}, needs_confirm=False)], "rules")
    prefs = getattr(agent, "prefs", None)
    if m := RE_COURSE_END.match(t):
        from .when import parse_day
        d = _parse_date(m.group("d")) or parse_day(m.group("d"), dt.date.today())
        if not d or prefs is None:
            return Reply("Не поняла дату. Скажи так: «курс заканчивается 29.01.2027».", route="error")
        prefs.set("course_end", d.isoformat())
        return Reply(f"Запомнила: курс до {d:%d.%m.%Y}. Осталось {(d - dt.date.today()).days} дней.", route="rules")
    if m := RE_MORNING_AUTO.match(t):
        on = m.group("on").lower().startswith("вкл")
        if prefs is not None:
            prefs.set("morning_auto", on)
        return Reply("Буду показывать сводку утром, когда сядешь за компьютер." if on else "Утреннюю сводку больше не показываю.",
                     route="rules")
    if RE_MORNING.match(t):
        return morning(agent, Reply)
    if m := RE_ANSWER.match(t):
        return answer_reply(agent, Reply, m.group("what"))
    if RE_HINT.match(t):
        return lesson_hint(agent, Reply)
    if RE_WEAK.match(t):
        weak = dict((prefs.get("weak_topics") if prefs is not None else None) or {})
        if not weak:
            return Reply("Слабых тем пока нет — сначала потренируйся: «тренируй собеседование».", route="rules")
        top = [k for k, _ in sorted(weak.items(), key=lambda kv: -kv[1])[:3]]
        return start_training(agent, Reply, "interview", ", ".join(top), 5)
    if m := RE_CARDS.match(t):
        n = {"пять": 5, "три": 3, "десять": 10}.get((m.group("n") or "").lower()) or int(m.group("n") or 5)
        return start_training(agent, Reply, "cards", m.group("topic").strip(" ?"), min(n, 15))
    if m := RE_TRAIN.match(t):
        w = (m.group("what") or "").lower()
        if not w or re.search(r"собесед|interview|vorstellungsgespräch|fachfrag", w):
            kind = "interview"
        elif re.search(r"самопрезент|о себе|selbstvorstell|представ", w):
            kind = "intro"
        elif re.search(r"клиент|kunde|сервис|service|ролев|звон", w):
            kind = "client"
        else:
            return None
        topic = (re.search(r"(?:по|на\s+тему|про)\s+(.+)$", w) or [None, ""])[1]
        return start_training(agent, Reply, kind, topic.strip(), 5)
    return None


def start_training(agent, Reply, kind: str, topic: str, n: int):
    tr = Trainer(lambda prompt: fast_ask(agent, prompt), kind, topic, n, prefs=getattr(agent, "prefs", None))
    try:
        text = tr.start()
    except Exception as e:  # noqa: BLE001
        return Reply(f"Не получилось начать: {str(e)[:150]}", route="error")
    agent.trainer = tr
    agent.ask("trainer", ttl=1800)
    return Reply(text, route="rules")


def answer_training(agent, Reply, text: str):
    """Следующая фраза во время тренировки — это ответ (кроме «стоп тренировка»)."""
    tr = getattr(agent, "trainer", None)
    if tr is None:
        return None
    if RE_STOP_TRAIN.match(text.strip(" .!?")):
        agent.trainer = None
        return Reply(tr.finish() if tr.kind != "client" else "Закончили ролевую игру.", route="rules")
    out, done = tr.answer(text)
    if done:
        agent.trainer = None
    else:
        agent.ask("trainer", ttl=1800)
    return Reply(out, route="rules")


ANSWER_PROMPT = """Ученик Игорь (немецкий B1) на уроке хочет сказать учителю по-{lang}: «{what}».
{context}Сформулируй, как он может это сказать сам: коротко, просто, вежливо (Sie, если учителю), уровень B1.
Ответ строго JSON без markdown: {{"say": "<фраза по-{lang}>", "easy": "<ещё проще, если можно, иначе пусто>",
"ru": "<как это звучит по-русски, коротко>"}}"""


def quick_answer(agent, what: str) -> dict:
    """«Что хочу сказать» по-русски → фраза для урока. Контекст — последние фразы субтитров (если идут)."""
    live = getattr(agent, "live", None)
    lines = []
    if live is not None:
        lines = list(live.session.lines) if live.session is not None else list(getattr(live, "_last_lines", None) or [])
    ctx = ""
    if lines:
        ctx = "Последние фразы урока:\n" + "\n".join(
            f"{(getattr(x, 'speaker', '') or ('Ты' if x.source == 'mic' else 'Они'))}: {x.text}" for x in lines[-6:]) + "\n"
    code = live.prefs().get("language", "de") if live is not None else "de"
    lang = {"de": "немецки", "en": "английски"}.get(code, "немецки")
    raw = fast_ask(agent, ANSWER_PROMPT.format(lang=lang, what=what.strip(), context=ctx))
    try:
        d = parse_json(raw)
        if isinstance(d, dict) and d.get("say"):
            return {"say": str(d["say"]).strip(), "easy": str(d.get("easy") or "").strip(), "ru": str(d.get("ru") or "").strip()}
    except Exception:  # noqa: BLE001
        pass
    return {"say": raw.strip().strip('"'), "easy": "", "ru": ""}


def answer_reply(agent, Reply, what: str):
    try:
        d = quick_answer(agent, what)
    except Exception as e:  # noqa: BLE001
        return Reply(f"Не получилось: {str(e)[:150]}", route="error")
    text = f"**{d['say']}**" + (f"\n\nПроще: **{d['easy']}**" if d["easy"] else "") + (f"\n\n_{d['ru']}_" if d["ru"] else "")
    return Reply(d["say"], [A.Action("popup", "Ответ", {"title": "💬 Скажи так", "text": text, "command": "", "copy": d["say"]},
                                     needs_confirm=False),
                            A.Action("live_answer", "", {"say": d["say"], "ru": d["ru"]}, needs_confirm=False)], "rules")


def lesson_hint(agent, Reply):
    live = getattr(agent, "live", None)
    lines = []
    if live is not None:
        lines = list(live.session.lines) if live.session is not None else list(getattr(live, "_last_lines", None) or [])
    if not lines:
        return Reply("Подсказываю по субтитрам урока — включи их: «включи субтитры».", route="error")
    recent = lines[-14:]
    txt = "\n".join(f"{(getattr(x, 'speaker', '') or ('Ты' if x.source == 'mic' else 'Они'))}: {x.text}" for x in recent)
    lang = {"de": "немецкий", "en": "английский"}.get(live.prefs().get("language", "de"), "немецкий") if live else "немецкий"
    return _popup(agent, Reply, "💡 Подсказка", HINT_PROMPT.format(lang=lang, lines=txt), copy_first=False,
                  say="Подсказка — в окне.")


def morning(agent, Reply, today: dt.date | None = None):
    """Погода, календарь, прошлый урок и что задали, напоминания, дни до конца курса, слово дня."""
    today = today or dt.date.today()
    prefs = getattr(agent, "prefs", None)
    weather = agenda = homework = lesson = ""
    try:
        from . import weather as W
        weather = W.forecast("погода сегодня", agent.cfg.get("city", "München"), "ru")
    except Exception:  # noqa: BLE001
        pass
    try:
        r = agent._google("что у меня сегодня в календаре")
        if r is not None and r.route != "error" and not r.actions:
            agenda = r.text
    except Exception:  # noqa: BLE001
        pass
    try:
        from .live import notes as N
        last = N.latest()
        if last is not None and (last / "конспект.md").exists():
            md = (last / "конспект.md").read_text(encoding="utf-8")
            lesson = f"{last.name[:10]} — {N.title_of(md)}"
            homework = homework_of(md)
    except Exception:  # noqa: BLE001
        pass
    rem = []
    if prefs is not None:
        for r in prefs.get("reminders") or []:
            try:
                when = dt.datetime.fromtimestamp(float(r.get("at", 0)))
                if when.date() == today:
                    rem.append(f"{when:%H:%M} {r.get('text', '')}")
            except (TypeError, ValueError):
                continue
    text = morning_text(today, weather, agenda, homework, lesson, prefs.get("course_end") if prefs is not None else None, rem)
    say = "Доброе утро! Сводка на сегодня — в окне."
    if homework:
        say = "Доброе утро! Не забудь, что задали на прошлом уроке. Всё — в окне."
    return Reply(say, [A.Action("popup", "Сводка", {"title": "☀ Утро", "text": text, "command": ""}, needs_confirm=False)],
                 "rules")


def _parse_date(s: str) -> dt.date | None:
    m = re.search(r"(\d{1,2})[./](\d{1,2})[./](\d{2,4})", s)
    if m:
        y = int(m.group(3))
        try:
            return dt.date(y + 2000 if y < 100 else y, int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    return None


def _popup(agent, Reply, title: str, prompt: str, copy_first: bool = False, say: str = ""):
    try:
        ans = fast_ask(agent, prompt).strip()
    except Exception as e:  # noqa: BLE001
        return Reply(f"Не получилось: {str(e)[:150]}", route="error")
    right = re.search(r"\*\*Правильно:\*\*\s*(.+)", ans)
    first = right or re.search(r"\*\*([^*\n]+)\*\*", ans)
    copy = (first.group(1).strip() if first else (ans.splitlines() or [""])[0]).strip().strip("*")
    say = say or (copy if len(copy) < 140 else "Готово, ответ в окне.")
    return Reply(say, [A.Action("popup", title, {"title": title, "text": ans, "command": "", "copy": copy if copy_first else ""},
                                needs_confirm=False)], "rules")


# ====================================================================== утренняя сводка
def morning_text(today: dt.date, weather: str = "", agenda: str = "", homework: str = "", lesson: str = "",
                 course_end: str | None = None, reminders: list[str] | None = None) -> str:
    days = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
    out = [f"## ☀ Доброе утро! Сегодня {days[today.weekday()]}, {today:%d.%m.%Y}"]
    if weather:
        out.append(f"**Погода:** {weather}")
    if agenda:
        out.append(f"**Календарь:** {agenda}")
    if reminders:
        out.append("**Напоминания:** " + "; ".join(reminders))
    if lesson:
        out.append(f"**Прошлый урок:** {lesson}")
    if homework:
        out.append(f"**Что задали:** {homework}")
    if course_end:
        try:
            left = (dt.date.fromisoformat(course_end) - today).days
            if left >= 0:
                out.append(f"**До конца Weiterbildung:** {left} дн. 💪")
        except ValueError:
            pass
    de, ru, ex = word_of_day(today)
    out.append(f"**Слово дня:** {de} — {ru}\n_{ex}_")
    return "\n\n".join(out)


def homework_of(md: str) -> str:
    """Из конспекта — раздел «Что задали» (одной строкой)."""
    m = re.search(r"^##\s*Что задали\s*\n(.*?)(?=^##\s|\Z)", md, re.M | re.S)
    if not m:
        return ""
    items = [ln.strip(" -•*") for ln in m.group(1).splitlines() if ln.strip(" -•*")]
    text = "; ".join(items)
    return "" if re.search(r"ничего не задали", text, re.I) else text[:300]
