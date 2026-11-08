"""Наставник: вопросы по учёбе и работе решает ЛОКАЛЬНАЯ модель (Ollama) — личное не уходит в облако.

Как она «знает» твой курс: модель не переобучается (на домашнем ПК это недели и мощная видеокарта), а каждый раз
получает самое нужное из ТВОИХ файлов — словаря курса (бестиарий), конспектов и транскриптов уроков, импортированных
материалов (PDF, Word, Granola). Добавил файлы — она сразу «знает» больше. Так же работают корпоративные ассистенты.

Чего нет в твоих файлах — ищет в интернете. Наружу уходит только короткий поисковый запрос по теме
(без имён, почты, IP и номеров), сами файлы и ответ остаются на компьютере. «наставник без интернета» — не ищет.
Видео — ссылки YouTube по теме (поиск без ключей и регистрации).
"""
from __future__ import annotations

import html as H
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

STOP = set("""что как где когда почему зачем какой какая какие каких это эта этот эти для при про без над под или
если чем так там тут все всё мне меня мой моя мои твой есть был была были будет можно нужно надо очень просто
объясни расскажи скажи подскажи покажи помоги вопрос ответ разница между чтобы тоже ещё уже только такое такой
она оно они его её их она работает работать делать сделать нужен нужна
der die das und oder ein eine einen einem einer ist sind wie was wo wann warum mit von für auf aus bei
the and for with what how why when where which""".split())

GOAL = ("ученик Weiterbildung IT-Support / Systemadministration в Германии (1st & 2nd Level Support, Windows Server, "
        "Active Directory, сети, PowerShell, Linux). Цель — расти в Cloud & Automation / DevOps, дальше — IT-менеджер. "
        "Немецкий — B1.")

PROMPT = """Ты — наставник по IT. Ученик: {goal}
Отвечай по-русски, простыми словами, по делу. Немецкие термины пиши как в курсе, с переводом в скобках.
Опирайся в первую очередь на ЕГО материалы [M…] — это его курс, там правильные для него формулировки.
Интернет [W…] — для того, чего в материалах нет. Не выдумывай: если информации не хватает — так и скажи.
Если уместно — команды (PowerShell / Bash / cmd) в блоке кода. Ссылайся на источники: [M2], [W1].
В конце — раздел «🎯 Что подучить»: 1–3 коротких пункта, как это связано с его целью (Cloud / DevOps / IT-менеджмент).

{context}
Вопрос: {question}"""

PLAN_PROMPT = """Ты — наставник по IT. Ученик: {goal}
Вот что он уже проходил (темы уроков и материалов):
{topics}
{weak}
Составь короткий план, что подучить в ближайшие 2–4 недели, чтобы:
1) уверенно сдать курс и проходить собеседования на 1st/2nd Level Support в Германии;
2) двигаться к Cloud / DevOps (Azure, AWS, Linux, Docker, CI/CD, IaC) и дальше — к IT-менеджменту (ITIL, проекты).
Формат: 5–7 тем. Для каждой: **название (немецкий термин, если есть)** — почему важно (1 строка) — мини-практика дома
(1 строка). Последней строкой каждой темы — «Ищи: <2–4 слова для поиска видео>». По-русски, без воды."""


# ====================================================================== поиск по своим файлам
def keywords(text: str) -> list[str]:
    words = re.findall(r"[\w.+#$-]{2,}", text.lower())
    out = []
    for w in words:
        w = w.strip(".-")
        if len(w) < 2 or w in STOP or w.isdigit():
            continue
        if w not in out:
            out.append(w)
    return out[:12]


def _stem(w: str) -> str:
    return w if len(w) <= 5 else w[:max(5, len(w) - 2)]


_files_cache: dict[str, tuple[float, list[str]]] = {}


def _paragraphs(f: Path) -> list[str]:
    try:
        mt = f.stat().st_mtime
    except OSError:
        return []
    c = _files_cache.get(str(f))
    if c and c[0] == mt:
        return c[1]
    text = f.read_text(encoding="utf-8", errors="replace")
    paras, buf = [], ""
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block:
            continue
        if len(buf) + len(block) < 700:
            buf = (buf + "\n" + block).strip()
        else:
            if buf:
                paras.append(buf)
            buf = block[:1500]
    if buf:
        paras.append(buf)
    _files_cache[str(f)] = (mt, paras)
    return paras


def corpus(root: Path | None = None) -> list[tuple[str, Path]]:
    """(подпись, файл): конспекты и транскрипты уроков, материалы, свой словарь."""
    from .live import notes as N
    root = root or N.lessons_dir()
    out = []
    for p in reversed(N.lessons(root)):
        for name in ("конспект.md", "транскрипт.md"):
            if (p / name).exists():
                out.append((f"урок {p.name[:10]} ({name[:-3]})", p / name))
    mats = root / "Материалы"
    if mats.exists():
        out += [(f"материалы: {f.stem}", f) for f in sorted(mats.rglob("*.md"))]
    return out


def retrieve(question: str, root: Path | None = None, limit: int = 6) -> tuple[list[dict], int]:
    """→ ([{label, text}], сколько слов вопроса нашлось). Сначала словарь курса, потом лучшие абзацы."""
    from .live import glossary
    kws = keywords(question)
    if not kws:
        return [], 0
    hits: list[dict] = []
    found: set[str] = set()
    try:
        terms = []
        for k in kws:
            for e in glossary.find(k, limit=2, root=root):
                if e not in terms:
                    terms.append(e)
                    found.add(k)
        if terms:
            hits.append({"label": "словарь курса", "text": "\n".join(glossary.line(e) for e in terms[:8])})
    except Exception:  # noqa: BLE001 — нет словаря: ищем дальше
        pass
    rx = [(k, re.compile(rf"(?<!\w){re.escape(_stem(k))}", re.I)) for k in kws]
    scored = []
    for label, f in corpus(root):
        for para in _paragraphs(f):
            got = [k for k, r in rx if r.search(para)]
            if got:
                scored.append((len(got) + (0.3 if "конспект" in label or "материалы" in label else 0), label, para, got))
    scored.sort(key=lambda x: -x[0])
    for score, label, para, got in scored[:limit]:
        hits.append({"label": label, "text": para[:1200]})
        found.update(got)
    return hits, len(found)


# ====================================================================== интернет (без ключей)
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/130.0 Safari/537.36", "Accept-Language": "de-DE,de;q=0.9,ru;q=0.8,en;q=0.7"}


def safe_query(text: str, names: list[str] | None = None) -> str:
    """Поисковый запрос без личного: имена, почта, IP, телефоны, пути, длинные числа — убираем."""
    q = re.sub(r"\S+@\S+", " ", text)
    q = re.sub(r"\b\d{1,3}(?:\.\d{1,3}){3}(?:/\d+)?\b", " ", q)
    q = re.sub(r"[A-Za-z]:\\\S*|\\\\\S+", " ", q)
    q = re.sub(r"\+?\d[\d\s/-]{6,}\d", " ", q)
    for n in names or []:
        if n:
            q = re.sub(rf"(?i)\b{re.escape(n)}\w*", " ", q)
    q = re.sub(r"(?i)^(?:наставник|ментор|локально|по\s+учёбе|по\s+учебе|по\s+работе|вопрос)[:,\s]+", "", q.strip())
    words = [w for w in re.findall(r"[\w.+#-]+", q) if w.lower() not in STOP]
    return " ".join(words[:12])


def web_search(query: str, n: int = 4, timeout: float = 8) -> list[dict]:
    """DuckDuckGo (HTML-версия) → [{title, url, snippet}]."""
    import httpx
    if not query.strip():
        return []
    err = None
    try:
        r = httpx.post("https://html.duckduckgo.com/html/", data={"q": query, "kl": "de-de"}, headers=UA,
                       timeout=timeout, follow_redirects=True)
        r.raise_for_status()
        if out := parse_ddg(r.text, n):
            return out
    except Exception as e:  # noqa: BLE001 — DuckDuckGo капризничает: пробуем Bing
        err = e
    r = httpx.get("https://www.bing.com/search", params={"q": query, "setlang": "de"}, headers=UA,
                  timeout=timeout, follow_redirects=True)
    if r.status_code >= 400 and err:
        raise err
    return parse_bing(r.text, n)


def parse_bing(page: str, n: int = 4) -> list[dict]:
    out = []
    clean = lambda s: H.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()  # noqa: E731
    for m in re.finditer(r'<li class="b_algo"[^>]*>(.*?)</li>', page, re.S):
        block = m.group(1)
        a = re.search(r'<h2[^>]*>\s*<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not a:
            continue
        p = re.search(r"<p[^>]*>(.*?)</p>", block, re.S)
        out.append({"title": clean(a.group(2)), "url": H.unescape(a.group(1)), "snippet": clean(p.group(1) if p else "")})
        if len(out) >= n:
            break
    return out


def parse_ddg(page: str, n: int = 4) -> list[dict]:
    out = []
    for m in re.finditer(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=<a[^>]+class="result__a"|\Z)',
                         page, re.S):
        href, title, rest = m.group(1), m.group(2), m.group(3)
        url = H.unescape(href)
        if "uddg=" in url:
            url = unquote(parse_qs(urlparse(url if url.startswith("http") else "https:" + url).query).get("uddg", [""])[0])
        if not url.startswith("http") or "duckduckgo.com/y.js" in url:
            continue                                         # реклама
        sn = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', rest, re.S)
        clean = lambda s: H.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()  # noqa: E731
        out.append({"title": clean(title), "url": url, "snippet": clean(sn.group(1) if sn else "")})
        if len(out) >= n:
            break
    return out


def page_text(url: str, limit: int = 2500, timeout: float = 6) -> str:
    import httpx
    r = httpx.get(url, headers=UA, timeout=timeout, follow_redirects=True)
    if "html" not in r.headers.get("content-type", ""):
        return ""
    t = re.sub(r"(?is)<(script|style|nav|header|footer|aside|form)[^>]*>.*?</\1>", " ", r.text)
    t = H.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"\s+", " ", t).strip()[:limit]


def youtube(query: str, n: int = 3, timeout: float = 8) -> list[dict]:
    """Поиск YouTube без ключа → [{title, url}]. Не получилось — пусто (ссылку на поиск даём всегда)."""
    import httpx
    r = httpx.get("https://www.youtube.com/results", params={"search_query": query, "hl": "de"}, headers=UA,
                  timeout=timeout, follow_redirects=True)
    return parse_youtube(r.text, n)


def parse_youtube(page: str, n: int = 3) -> list[dict]:
    out, seen = [], set()
    for m in re.finditer(r'"videoRenderer":\{"videoId":"([\w-]{11})".{0,2500}?"title":\{"runs":\[\{"text":"((?:[^"\\]|\\.)*)"',
                         page, re.S):
        vid, title = m.group(1), m.group(2)
        if vid in seen:
            continue
        seen.add(vid)
        try:
            title = __import__("json").loads(f'"{title}"')
        except ValueError:
            pass
        out.append({"title": title, "url": f"https://www.youtube.com/watch?v={vid}"})
        if len(out) >= n:
            break
    return out


def youtube_search_url(query: str) -> str:
    return "https://www.youtube.com/results?search_query=" + quote_plus(query)


# ====================================================================== ответ
def local_llm(agent):
    """Самая сильная установленная локальная модель (ollama.text_model, иначе основная)."""
    return agent._text_llm()


_HW: dict = {}


def has_gpu() -> bool:
    """Есть видеокарта, где локальная модель отвечает быстро (≥ 6 ГБ). Проверяем один раз."""
    if "gpu" not in _HW:
        try:
            from . import hardware
            _HW["gpu"] = hardware.detect().vram_gb >= 6
        except Exception:  # noqa: BLE001
            _HW["gpu"] = False
    return _HW["gpu"]


def local_ok(agent) -> bool:
    """Кто думает за наставника: local — всегда локально (приватно), cloud — облако (быстро),
    auto — локально, если есть видеокарта; на ноутбуке без неё локальная модель отвечает минуту, поэтому облако."""
    prefs = getattr(agent, "prefs", None)
    mode = (prefs.get("mentor_brain", "auto") if prefs is not None else "auto") or "auto"
    if mode == "local":
        return True
    if mode == "cloud":
        return not any(agent.ready(p) for p in ("groq", "gemini", "openrouter"))
    return has_gpu() or not any(agent.ready(p) for p in ("groq", "gemini", "openrouter"))


def ask_brain(agent, prompt: str) -> tuple[str, str]:
    """→ (ответ, кто ответил). Локально — если local_ok, иначе бесплатное облако по HTTP (1–3 с)."""
    if local_ok(agent):
        try:
            return ask_local(agent, prompt), f"🔒 локальная модель {getattr(local_llm(agent), 'model', '')}"
        except Exception:  # noqa: BLE001 — Ollama выключена: облако
            pass
    from .study import fast_ask
    return fast_ask(agent, prompt).strip(), "☁ облако (Groq / Gemini)"


def ask_local(agent, prompt: str) -> str:
    from .llm.ollama_client import strip_thinking
    msg = local_llm(agent).chat([{"role": "user", "content": prompt + " /no_think"}], tools=None)
    return strip_thinking(msg.get("content", "")).strip()


def build_context(mine: list[dict], web: list[dict]) -> str:
    parts = []
    for i, h in enumerate(mine, 1):
        parts.append(f"[M{i}] ({h['label']})\n{h['text']}")
    for i, w in enumerate(web, 1):
        parts.append(f"[W{i}] {w['title']} — {w['url']}\n{w.get('text') or w['snippet']}")
    return ("Материалы:\n\n" + "\n\n".join(parts) + "\n") if parts else "Материалов по теме не нашлось.\n"


def sources_md(mine: list[dict], web: list[dict], videos: list[dict], query: str) -> str:
    out = []
    if mine:
        out.append("**Твои материалы:** " + "; ".join(f"[M{i}] {h['label']}" for i, h in enumerate(mine, 1)))
    if web:
        out.append("**Интернет:**\n" + "\n".join(f"[W{i}] [{w['title'][:80]}]({w['url']})" for i, w in enumerate(web, 1)))
    if videos:
        out.append("**▶ Видео:**\n" + "\n".join(f"• [{v['title'][:90]}]({v['url']})" for v in videos))
    if query:
        out.append(f"[▶ Ещё видео на YouTube]({youtube_search_url(query)})")
    return "\n\n".join(out)


def answer(agent, question: str, *, online: bool = True, goal: str = "", names: list[str] | None = None,
           root: Path | None = None, status=lambda s: None) -> tuple[str, dict]:
    """→ (markdown-ответ, подробности). Модель — только локальная."""
    t0 = time.monotonic()
    status("Ищу в твоих материалах…")
    mine, found = retrieve(question, root)
    kws = keywords(question)
    need_web = online and (found < max(1, min(3, len(kws) // 2 + 1)) or
                           re.search(r"(?i)актуальн|новое|сейчас|202\d|версия|ссылк|цен|стоит|вакан", question))
    query = safe_query(question, names)
    web: list[dict] = []
    if need_web and query:
        status("Не всё нашла у тебя — смотрю в интернете…")
        try:
            web = web_search(query)
            for w in web[:2]:
                try:
                    w["text"] = page_text(w["url"])
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001 — нет сети: отвечаем по своим файлам
            web = []
    videos: list[dict] = []
    if online and query:
        try:
            videos = youtube(query + " einfach erklärt", 2) + youtube(query + " простыми словами", 1)
        except Exception:  # noqa: BLE001
            videos = []
    status("Думаю…")
    prompt = PROMPT.format(goal=goal or GOAL, context=build_context(mine, web), question=question)
    text, who = ask_brain(agent, prompt)
    tail = sources_md(mine, web, videos, query if online else "")
    info = {"model": who, "mine": len(mine), "web": len(web), "secs": round(time.monotonic() - t0, 1)}
    if who.startswith("🔒"):
        foot = f"_{who} за {info['secs']} с." + (f" В интернет ушёл только запрос: «{query}»._" if web or videos
                                                  else " Без интернета._")
    else:
        foot = (f"_{who} за {info['secs']} с — на этом компьютере нет видеокарты, локально ответ шёл бы около минуты. "
                "Приватно: «наставник только локально»._")
    return text + ("\n\n---\n" + tail if tail else "") + "\n\n" + foot, info


def topics(root: Path | None = None, limit: int = 30) -> list[str]:
    from .live import notes as N
    root = root or N.lessons_dir()
    out = []
    for p in reversed(N.lessons(root)):
        f = p / "конспект.md"
        if f.exists():
            out.append(N.title_of(f.read_text(encoding="utf-8", errors="replace")))
    mats = root / "Материалы"
    if mats.exists():
        out += [f.stem for f in sorted(mats.rglob("*.md"))]
    return list(dict.fromkeys(t for t in out if t))[:limit]


def plan(agent, *, weak: list[str] | None = None, goal: str = "", online: bool = True, root: Path | None = None,
         status=lambda s: None) -> str:
    status("Смотрю, что ты уже прошёл…")
    tp = topics(root)
    prompt = PLAN_PROMPT.format(goal=goal or GOAL, topics="\n".join(f"- {t}" for t in tp) or "- (уроков пока нет)",
                                weak=("Слабые места по тренажёру: " + ", ".join(weak) + "\n") if weak else "")
    status("Составляю план…")
    text, who = ask_brain(agent, prompt)
    if online:
        status("Подбираю видео…")
        lines = []
        for ln in text.splitlines():
            lines.append(ln)
            m = re.match(r"^\s*[*_]*Ищи[*_]*:\s*(.+?)\s*$", ln)
            if m:
                q = m.group(1).strip(" .«»\"")
                try:
                    vids = youtube(q, 2)
                except Exception:  # noqa: BLE001
                    vids = []
                lines += [f"  ▶ [{v['title'][:80]}]({v['url']})" for v in vids] or \
                    [f"  ▶ [YouTube: {q}]({youtube_search_url(q)})"]
        text = "\n".join(lines)
    return text + f"\n\n_{who}._"


# ====================================================================== фразы
RE_ASK = re.compile(r"^(?:наставник|ментор|локально|спроси\s+локально|по\s+уч[её]бе|по\s+работе|вопрос\s+по\s+(?:уч[её]бе|работе)|"
                    r"приватно)\s*[:,—–-]?\s+(?P<q>.{3,})$", re.I | re.S)
RE_PLAN = re.compile(r"^(?:что\s+(?:мне\s+)?(?:подучить|подтянуть|учить\s+дальше|изучить\s+дальше)|"
                     r"(?:составь\s+)?план\s+(?:обучения|учёбы|учебы|развития)|куда\s+(?:мне\s+)?расти)\s*\??$", re.I)
RE_VIDEO = re.compile(r"^(?:найди\s+)?(?:видео|ютуб|youtube|ролик\w*)\s+(?:про|по|о|об)\s+(?P<q>.{2,80}?)\??$", re.I)
RE_OFFLINE = re.compile(r"^наставник\s+(?P<off>без\s+интернета)|^наставник\s+(?:с\s+интернетом|онлайн)$", re.I)
RE_BRAIN = re.compile(r"^наставник\s+(?:(?P<local>только\s+локально|локально|приватно)|(?P<cloud>через\s+облако|в\s+облаке|быстро)|"
                      r"(?P<auto>авто(?:матически)?))$", re.I)
RE_GOAL = re.compile(r"^(?:моя\s+цель|запомни\s+мою\s+цель)\s*[:—–-]?\s+(?P<g>.{5,300})$", re.I)
RE_INSTALL = re.compile(r"^(?:да[,\s]+)?(?:ставь|поставь|скачай|включи)\s+умную\s+модель$", re.I)
RE_MODEL = re.compile(r"^(?:поставь|скачай|установи)\s+(?:самую\s+)?(?:умную|сильную|лучшую|большую)\s+локальную\s+модель$|"
                      r"^какая\s+(?:у\s+меня\s+)?локальная\s+модель$", re.I)


def route(agent, text: str):
    from . import actions as A
    from .agent import Reply
    t = text.strip()
    prefs = getattr(agent, "prefs", None)
    if m := RE_GOAL.match(t):
        if prefs is not None:
            prefs.set("mentor_goal", m.group("g").strip())
        return Reply("Запомнила цель — наставник будет учитывать её в ответах и плане.", route="rules")
    if m := RE_BRAIN.match(t.strip(" .!")):
        mode = "local" if m.group("local") else "cloud" if m.group("cloud") else "auto"
        if prefs is not None:
            prefs.set("mentor_brain", mode)
        return Reply({"local": "Наставник думает только локально — личное не уходит в облако (без видеокарты — медленно).",
                      "cloud": "Наставник думает через бесплатное облако — ответ за пару секунд.",
                      "auto": "Наставник: с видеокартой — локально, без неё — через облако (быстро)."}[mode], route="rules")
    if m := RE_OFFLINE.match(t.strip(" .!")):
        on = not m.group("off")
        if prefs is not None:
            prefs.set("mentor_online", on)
        return Reply("Наставник ищет недостающее в интернете (уходит только запрос по теме)." if on else
                     "Наставник работает только по твоим файлам, без интернета.", route="rules")
    if RE_INSTALL.match(t.strip(" .!?")):
        return Reply("Ставлю.", [A.Action("mentor", "Локальная модель", {"do": "install"}, needs_confirm=False)], "rules")
    if RE_MODEL.match(t.strip(" .!?")):
        return Reply("Смотрю.", [A.Action("mentor", "Локальная модель", {"do": "model"}, needs_confirm=False)], "rules")
    if m := RE_ASK.match(t):
        return Reply("Думаю локально — личное не уходит в облако.", [A.Action(
            "mentor", "Наставник", {"do": "ask", "q": m.group("q").strip()}, needs_confirm=False)], "rules")
    if RE_PLAN.match(t.strip(" .!")):
        return Reply("Составляю план.", [A.Action("mentor", "План", {"do": "plan"}, needs_confirm=False)], "rules")
    if m := RE_VIDEO.match(t.strip(" .!")):
        q = m.group("q").strip()
        return Reply("Ищу видео.", [A.Action("mentor", "Видео", {"do": "video", "q": q}, needs_confirm=False)], "rules")
    return None


def run(agent, p: dict, status=lambda s: None) -> tuple[str, str]:
    """Выполнить действие «mentor» (в фоне). → (заголовок, markdown)."""
    prefs = getattr(agent, "prefs", None)
    get = (lambda k, d=None: prefs.get(k, d)) if prefs is not None else (lambda k, d=None: d)
    online = get("mentor_online", True) is not False
    goal = get("mentor_goal") or ""
    names = [str(agent.cfg.get("user_name") or "")] + list(get("mentor_private_names") or [])
    do = p.get("do")
    if do == "ask":
        text, _ = answer(agent, p["q"], online=online, goal=goal, names=names, status=status)
        return f"🎓 {p['q'][:50]}", text
    if do == "plan":
        w = dict(get("weak_topics") or {})
        weak = sorted(w, key=lambda k: -w[k])[:6]
        return "🎯 Что подучить", plan(agent, weak=weak, goal=goal, online=online, status=status)
    if do == "video":
        q = p["q"]
        vids = []
        try:
            vids = youtube(q, 3) + youtube(q + " простыми словами", 2)
        except Exception:  # noqa: BLE001
            pass
        body = "\n".join(f"• [{v['title'][:90]}]({v['url']})" for v in vids) or "Список не загрузился."
        return f"▶ {q}", body + f"\n\n[Все результаты на YouTube]({youtube_search_url(q)})"
    if do == "model":
        return "🧠 Локальная модель", model_advice(agent)
    return "Наставник", "Не поняла."


def model_advice(agent) -> str:
    from . import hardware
    hw = hardware.detect()
    cur = getattr(local_llm(agent), "model", "?")
    best = hardware.text_model(hw)
    gpu = hw.best_gpu
    lines = [f"Сейчас наставник думает моделью **{cur}**.",
             f"Видеокарта: {gpu.name} ({gpu.vram_gb:.0f} ГБ)" if gpu else "Видеокарты для моделей нет — работает процессор.",
             f"Оперативка: {hw.ram_gb:.0f} ГБ."]
    if best is None or best.model == cur:
        lines.append("Сильнее этой на твоём компьютере нормально работать не будет — это максимум. 👍")
    else:
        lines.append(f"Можно поставить **{best.model}** (~{best.download_gb:.0f} ГБ, {best.note}). Она заметно умнее для "
                     "объяснений и плана; голос остаётся на быстрой модели. Скажи «да, ставь умную модель» — "
                     "скачаю и включу для наставника.")
    return "\n\n".join(lines)
