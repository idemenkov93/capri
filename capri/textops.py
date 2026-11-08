"""Текст и экран: «что в буфере», перевод, «объясни / ответь на письмо», QR-коды, текст с экрана.

Здесь — только «мозговая» часть без окон: подсказки для помощника и разбор его ответов.
Окно с результатом — ui/popup.py, горячие клавиши — hotkeys.py, склейка — app.py.
"""
from __future__ import annotations

import ipaddress
import json
import re
from urllib.parse import urlparse

LANGS = {"ru": "русский", "de": "немецкий", "en": "английский", "he": "иврит", "uk": "украинский", "pl": "польский",
         "tr": "турецкий", "ar": "арабский", "fr": "французский", "es": "испанский", "it": "итальянский"}
SHORTENERS = ("bit.ly", "tinyurl.com", "t.co", "goo.gl", "is.gd", "cutt.ly", "ow.ly", "rebrand.ly", "shorturl.at",
              "rb.gy", "tiny.cc", "qrco.de", "s.id")


# ------------------------------------------------------------------ подсказки
def explain_prompt(text: str = "", image: bool = False) -> str:
    what = "на картинке (скриншот, его прислали тебе вместе с вопросом)" if image else f"в тексте:\n<<<\n{text[:6000]}\n>>>"
    return (
        "Пользователь скопировал что-то и спрашивает «что это?». Посмотри, что " + what + "\n"
        "Определи тип: letter (письмо, сообщение, уведомление от ведомства/компании/человека), error (ошибка, "
        "сообщение программы, лог), text (другой текст), other (картинка без текста и т.п.).\n"
        "Ответь ТОЛЬКО JSON без пояснений:\n"
        '{"kind": "letter|error|text|other", "lang": "код языка оригинала (de/en/ru/he/…)", '
        '"title": "3–6 слов по-русски: что это", '
        '"answer": "по-русски, коротко и понятно. Для письма: кто пишет, чего хотят, что нужно сделать, до какого '
        "числа. Для ошибки: что значит и как исправить (команды — блоком ```). Для текста: о чём он, 2–4 "
        'предложения", "deadline": "YYYY-MM-DD или null", "text": "весь текст с картинки дословно (для картинки), '
        'иначе пустая строка"}')


def translate_prompt(text: str, target: str = "", source: str = "", auto: bool = False) -> str:
    """target — на какой язык; source — с какого ('' — пусть определит сам); auto — язык выбран не человеком,
    а по умолчанию: если текст уже на нём, переводим на немецкий (с русского) или на русский (с другого)."""
    to = LANGS.get(target, "русский")
    frm = f"с {LANGS[source][:-2]}ого " if source in LANGS and LANGS[source].endswith("ий") else (
        "с иврита " if source == "he" else "")
    rule = f"Переведи {frm}на {to} язык."
    if auto:
        other = "немецкий" if target == "ru" else "русский"
        rule += f" Если текст уже на языке «{to}» — переведи его на {other}."
    return (f"{rule} Сохрани смысл и тон, имена и термины не переводи без нужды.\n"
            'Ответь ТОЛЬКО JSON без markdown: {"from": "код языка оригинала (de/en/ru/he/uk/…)", '
            '"to": "код языка перевода", "text": "перевод"}\n'
            f"<<<\n{text[:8000]}\n>>>")


def parse_translation(raw: str, target: str = "") -> tuple[str, str, str]:
    """→ (с какого, на какой, перевод). Модель ответила просто текстом — тоже понимаем."""
    try:
        d = parse_json(raw)
        if isinstance(d, dict) and d.get("text"):
            return str(d.get("from") or "")[:5], str(d.get("to") or target)[:5], str(d["text"]).strip()
    except Exception:  # noqa: BLE001
        pass
    return "", target, raw.strip()


def reply_prompt(letter: str, instruction: str, lang: str = "de") -> str:
    to = LANGS.get(lang, "немецкий")
    return (
        f"Вот письмо, на которое нужно ответить:\n<<<\n{letter[:6000]}\n>>>\n"
        f"Что хочет ответить пользователь (своими словами): «{instruction or 'вежливо подтвердить получение'}».\n"
        f"Напиши ответ на языке письма ({to}), вежливо и просто, уровень B1, от первого лица. Подпись — пользователь "
        "(без выдуманных фамилий, оставь «[Имя]»). Если по смыслу нужно что-то приложить (документ, справку, "
        "резюме…) — перечисли это в attach.\n"
        'Ответь ТОЛЬКО JSON: {"subject": "тема (Re: … если уместно)", "body": "текст письма", '
        '"attach": ["что приложить", …]}')


def ocr_prompt(translate: bool = False) -> str:
    if translate:
        return ("На скриншоте экрана есть текст. Перепиши его и переведи на русский (если он уже русский — на немецкий). "
                "Ответь ТОЛЬКО переводом, сохраняя абзацы.")
    return "Перепиши весь текст со скриншота дословно, сохраняя абзацы и списки. Ответь ТОЛЬКО этим текстом."


# ------------------------------------------------------------------ разбор ответов
def parse_json(answer: str) -> dict | None:
    """JSON из ответа модели: бывает в ```json …```, с текстом вокруг — достаём первый {...}."""
    if not answer:
        return None
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", answer.strip(), flags=re.S)
    for cand in (s, *(m.group(0) for m in re.finditer(r"\{.*\}", s, re.S))):
        try:
            d = json.loads(cand)
            if isinstance(d, dict):
                return d
        except ValueError:
            continue
    return None


def explained(answer: str) -> dict:
    """Ответ на explain_prompt → {kind, lang, title, answer, deadline, text}; не JSON — просто текст."""
    d = parse_json(answer) or {}
    kind = d.get("kind") if d.get("kind") in ("letter", "error", "text", "other") else "text"
    dl = d.get("deadline")
    if not (isinstance(dl, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", dl)):
        dl = None
    return {"kind": kind, "lang": str(d.get("lang") or "")[:5].lower(), "title": str(d.get("title") or "").strip(),
            "answer": str(d.get("answer") or (answer if not d else "")).strip(), "deadline": dl,
            "text": str(d.get("text") or "").strip()}


def reply_parsed(answer: str) -> dict:
    d = parse_json(answer) or {}
    attach = d.get("attach") or []
    if isinstance(attach, str):
        attach = [attach]
    return {"subject": str(d.get("subject") or "").strip(), "body": str(d.get("body") or (answer if not d else "")).strip(),
            "attach": [str(a).strip() for a in attach if str(a).strip()]}


# ------------------------------------------------------------------ ссылки и QR
URL_RX = re.compile(r"https?://[^\s<>\"']+|www\.[^\s<>\"']+", re.I)


def link_risk(url: str) -> str:
    """Почему ссылке стоит не доверять ('' — ничего подозрительного)."""
    u = url if "://" in url else "http://" + url
    p = urlparse(u)
    host = (p.hostname or "").lower()
    why = []
    if p.scheme == "http":
        why.append("без шифрования (http)")
    if host in SHORTENERS:
        why.append("сокращённая ссылка — настоящий адрес скрыт")
    try:
        ipaddress.ip_address(host)
        why.append("вместо имени сайта — IP-адрес")
    except ValueError:
        pass
    if host.startswith("xn--") or ".xn--" in host:
        why.append("имя сайта с похожими буквами (punycode)")
    if "@" in (p.netloc or ""):
        why.append("в адресе есть «@» — так маскируют настоящий сайт")
    return ", ".join(why)


def host_of(url: str) -> str:
    u = url if "://" in url else "http://" + url
    return (urlparse(u).hostname or url).removeprefix("www.")


def qr_decode(path: str) -> list[str]:
    """Все QR/штрихкоды на картинке (офлайн, zxing-cpp). Пусто — нет кодов или библиотеки."""
    try:
        import zxingcpp
        from PIL import Image
    except ImportError:
        return []
    try:
        with Image.open(path) as im:
            return [r.text for r in zxingcpp.read_barcodes(im.convert("RGB")) if r.text]
    except Exception:  # noqa: BLE001
        return []


def qr_make(text: str, path: str) -> str:
    """QR-код из ссылки/текста → PNG."""
    import zxingcpp
    from PIL import Image
    import numpy as np
    if hasattr(zxingcpp, "create_barcode"):                       # zxing-cpp ≥ 2.3
        img = zxingcpp.write_barcode_to_image(zxingcpp.create_barcode(text, zxingcpp.BarcodeFormat.QRCode), scale=10)
    else:
        img = zxingcpp.write_barcode(zxingcpp.BarcodeFormat.QRCode, text, width=480, height=480)
    Image.fromarray(np.asarray(img)).save(path)
    return path
