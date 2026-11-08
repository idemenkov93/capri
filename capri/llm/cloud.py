"""Бесплатные облачные «мозги» с одинаковым (OpenAI-совместимым) API: Groq и OpenRouter.

Правила — как у Gemini: ключ вставляешь в чат («ключ groq gsk_…»), он уходит в Диспетчер учётных данных Windows
(keyring), в логах и в чате — только маска. Капри спрашивает их, когда выбран этот «мозг» в чате, или по очереди,
если Claude / Gemini не ответили.

Только бесплатно:
  Groq        — бесплатный план без карты, очень быстрый (Llama, Qwen, GPT-OSS). Карту не добавляй — и платить нечем.
  OpenRouter  — только модели с пометкой «:free» (DeepSeek, Qwen, Llama… — список у них меняется). Платные модели
                Капри не выбирает никогда. Без пополнения — около 50 запросов в день.

Не добавлены: Grok (xAI) и официальный API DeepSeek — платные, Cerebras просит карту, Mistral — не нужен.
"""
from __future__ import annotations

import base64
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import httpx


@dataclass(frozen=True)
class Provider:
    code: str
    title: str
    base: str
    key_page: str
    prefix: str                       # с чего начинается ключ
    env: str
    prefer: tuple[str, ...]           # каких моделей ищем (по началу имени), по порядку
    vision: tuple[str, ...] = ()      # модели, которые видят картинки
    free_only: bool = False           # OpenRouter: только «…:free»
    words: str = ""                   # как его называют голосом / в чате (regex)
    how: str = ""                     # инструкция «как подключить»
    note: str = ""                    # подсказка на кнопке
    extra_headers: dict = field(default_factory=dict)


PROVIDERS: dict[str, Provider] = {
    "groq": Provider(
        "groq", "Groq", "https://api.groq.com/openai/v1", "https://console.groq.com/keys", "gsk_", "GROQ_API_KEY",
        # Llama 3.x, Qwen3-32B, Llama 4 Groq сняла летом 2026 — остались GPT-OSS и новые Qwen
        ("openai/gpt-oss-120b", "qwen/qwen3.8", "qwen/qwen3.6", "openai/gpt-oss-20b", "moonshotai/kimi-k2"),
        vision=("meta-llama/llama-4-scout", "qwen/qwen3.8", "qwen/qwen3.6"),
        words=r"groq|грок|грог|гроку|грока",
        how="Открой console.groq.com/keys, войди (Google или почта) и нажми «Create API Key» — бесплатно и без карты. "
            "Скопируй ключ (начинается с gsk_) и вставь его в чат Капри: «ключ groq gsk_…».",
        note="Groq — бесплатно, очень быстро"),
    "openrouter": Provider(
        "openrouter", "OpenRouter", "https://openrouter.ai/api/v1", "https://openrouter.ai/keys", "sk-or-",
        "OPENROUTER_API_KEY",
        ("deepseek/", "qwen/qwen3", "moonshotai/kimi", "z-ai/glm", "openai/gpt-oss-120b", "meta-llama/llama-3.3-70b",
         "google/gemma"),
        vision=("google/gemma", "qwen/qwen2.5-vl", "meta-llama/llama-4", "mistralai/mistral-small"),
        free_only=True,
        words=r"open\s*router|опен\s*роутер|опенроутер|deep\s*seek|дип\s*сик|дипсик|дипсику|дипсика",
        how="Открой openrouter.ai/keys, войди и нажми «Create Key» — бесплатно, карту не добавляй. Поле «Credit limit» "
            "оставь пустым: оно не обязательное, платить ничего не нужно. Скопируй ключ "
            "(начинается с sk-or-) и вставь в чат Капри: «ключ openrouter sk-or-…». Капри берёт только бесплатные "
            "модели (DeepSeek, Qwen, Llama — какие сейчас бесплатны). Лимит без пополнения — около 50 запросов в день.",
        note="OpenRouter — бесплатные модели (DeepSeek и др.)",
        extra_headers={"HTTP-Referer": "https://github.com/idemenkov93/capri", "X-Title": "Capri"}),
}
ORDER = ("groq", "openrouter")          # в этом порядке спрашиваем, если Claude / Gemini не ответили

# «ключ groq gsk_…», «ключ для дипсика sk-or-…», или просто вставленный ключ с узнаваемым началом
RE_KEY = re.compile(
    r"(?:(?:ключ|key)\s+(?:для\s+)?(?P<who>" + "|".join(p.words for p in PROVIDERS.values()) +
    r")\w*\s*[:=—-]?\s*)?\b(?P<key>gsk_[A-Za-z0-9]{20,}|sk-or-[A-Za-z0-9-]{20,})\b", re.I)


def parse_key(text: str) -> tuple[Provider, str] | None:
    """Ключ в сообщении → (чей, ключ): gsk_… — Groq, sk-or-… — OpenRouter."""
    m = RE_KEY.search(text)
    if not m:
        return None
    key = m.group("key")
    p = provider_by_key(key) or (provider_for(m.group("who")) if m.group("who") else None)
    if p is None or (p.prefix and not key.startswith(p.prefix)):
        return None
    return p, key


class CloudError(RuntimeError):
    pass


def provider_for(word: str) -> Provider | None:
    """«дипсик» → OpenRouter, «грок» → Groq."""
    for p in PROVIDERS.values():
        if re.fullmatch(rf"(?:{p.words})\w*", word.strip(), re.I):
            return p
    return None


def provider_by_key(key: str) -> Provider | None:
    for p in PROVIDERS.values():
        if p.prefix and key.startswith(p.prefix):
            return p
    return None


def mask(key: str) -> str:
    """gsk_abcd…wxyz — так ключ можно показать в чате и в логе."""
    return f"{key[:6]}…{key[-4:]}" if len(key) > 12 else "…"


# ------------------------------------------------------------------ ключ (Диспетчер учётных данных Windows)
def get_key(code: str, cfg: dict | None = None) -> str | None:
    p = PROVIDERS[code]
    if cfg and cfg.get("api_key"):
        return cfg["api_key"]
    if os.environ.get(p.env):
        return os.environ[p.env]
    try:
        import keyring
        return keyring.get_password(f"capri-{code}", "api_key")
    except Exception:  # noqa: BLE001
        return None


def set_key(code: str, key: str) -> None:
    import keyring
    keyring.set_password(f"capri-{code}", "api_key", key.strip())


# ------------------------------------------------------------------ модели
def _headers(p: Provider, key: str) -> dict:
    return {"Authorization": f"Bearer {key}", **p.extra_headers}


def is_free(m: dict) -> bool:
    """OpenRouter: бесплатная — только «…:free» (или цена 0 за запрос и за ответ)."""
    mid = m.get("id", "")
    if mid.endswith(":free") or mid == "openrouter/free":
        return True
    price = m.get("pricing") or {}
    try:
        return float(price.get("prompt", 1)) == 0 and float(price.get("completion", 1)) == 0 \
            and float(price.get("request", 0) or 0) == 0
    except (TypeError, ValueError):
        return False


def list_models(code: str, key: str, timeout: float = 15) -> list[dict]:
    p = PROVIDERS[code]
    try:
        r = httpx.get(f"{p.base}/models", headers=_headers(p, key), timeout=timeout)
    except httpx.HTTPError as e:
        raise CloudError(f"{p.title} не отвечает: {e}") from e
    if r.status_code in (401, 403):
        raise CloudError(f"{p.title} не принял ключ — скопируй его ещё раз на {p.key_page}")
    if r.status_code >= 400:
        raise CloudError(f"{p.title}: ошибка {r.status_code}")
    data = r.json()
    models = data.get("data", data if isinstance(data, list) else [])
    return [m for m in models if isinstance(m, dict) and m.get("id")]


def pick_model(code: str, models: list[dict], vision: bool = False) -> str:
    """Самая подходящая модель из списка аккаунта. OpenRouter — только бесплатные."""
    p = PROVIDERS[code]
    if p.free_only:
        models = [m for m in models if is_free(m)]
    ids = [m["id"] for m in models]
    for want in (p.vision if vision else p.prefer):
        hits = [i for i in ids if i.startswith(want) and not re.search(r"guard|whisper|tts|embed|ocr|moderation", i)]
        if hits:
            return sorted(hits, key=lambda i: (-("r1" in i or "v3" in i or "v4" in i), len(i)))[0]
    if vision:
        raise CloudError(f"у {p.title} сейчас нет бесплатной модели, которая видит картинки")
    if p.free_only:
        if "openrouter/free" in ids or not ids:
            return "openrouter/free"
        return ids[0]
    if not ids:
        raise CloudError(f"у {p.title} нет доступных моделей")
    return ids[0]


_picked: dict[tuple[str, bool], str] = {}      # выбранная модель — на всё время работы Капри


def model_for(code: str, key: str, cfg: dict, vision: bool = False) -> str:
    if not vision and cfg.get("model"):
        return cfg["model"]
    if (code, vision) not in _picked:
        try:
            _picked[(code, vision)] = pick_model(code, list_models(code, key), vision)
        except CloudError:
            if vision:
                raise
            p = PROVIDERS[code]
            _picked[(code, vision)] = "openrouter/free" if p.free_only else p.prefer[0]
    return _picked[(code, vision)]


def check_key(code: str, key: str) -> str:
    """Проверить ключ настоящим коротким запросом → модель, которую будем использовать."""
    model = pick_model(code, list_models(code, key))
    try:
        ask(code, "Ответь одним словом: ок", {"api_key": key, "model": model, "max_tokens": 32, "timeout": 30})
    except CloudError as e:
        if "пустой ответ" in str(e):
            return model                      # ключ принят, модель «подумала» и не уложилась в 32 токена
        raise
    return model


# ------------------------------------------------------------------ запрос
def _error_text(r: httpx.Response) -> str:
    try:
        e = r.json().get("error", {})
        return (e.get("message") if isinstance(e, dict) else str(e) or "")[:200]
    except ValueError:
        return r.text[:200]


def ask(code: str, prompt: str, cfg: dict, images: list[Path] | None = None, system: str = "") -> str:
    p = PROVIDERS[code]
    key = get_key(code, cfg)
    if not key:
        raise CloudError(f"Нет ключа {p.title}. Скажи «как подключить {p.code}».")
    model = model_for(code, key, cfg, vision=bool(images))
    if p.free_only and not is_free({"id": model}):
        raise CloudError(f"модель {model} у {p.title} платная — Капри берёт только бесплатные (…:free)")
    content: list | str = prompt
    if images:
        content = [{"type": "text", "text": prompt}] + [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," +
                                                 base64.b64encode(Path(i).read_bytes()).decode()}} for i in images]
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": content}]
    body = {"model": model, "messages": messages, "max_tokens": cfg.get("max_tokens", 2048)}
    if code == "groq" and model.startswith("openai/gpt-oss"):
        body["reasoning_effort"] = cfg.get("reasoning_effort", "low")     # думает меньше — отвечает быстрее
    try:
        r = httpx.post(f"{p.base}/chat/completions", json=body, headers=_headers(p, key), timeout=cfg.get("timeout", 60))
    except httpx.HTTPError as e:
        raise CloudError(f"{p.title} не отвечает: {e}") from e
    if r.status_code == 429:
        raise CloudError(f"лимит {p.title} пока исчерпан (бесплатный). Попробуй позже.")
    if r.status_code == 402:
        raise CloudError(f"{p.title} просит пополнить счёт — платить не будем; бесплатный лимит на сегодня кончился.")
    if r.status_code in (401, 403):
        raise CloudError(f"{p.title} не принял ключ: {_error_text(r)}")
    if r.status_code == 404 or (r.status_code == 400 and "model" in r.text.lower()):
        _picked.pop((code, bool(images)), None)          # модель убрали — в следующий раз выберем заново
        raise CloudError(f"модель {model} у {p.title} недоступна — в следующий раз возьму другую.")
    if r.status_code >= 400:
        raise CloudError(f"{p.title}: ошибка {r.status_code}: {_error_text(r)}")
    try:
        text = r.json()["choices"][0]["message"].get("content") or ""
    except (KeyError, IndexError, ValueError):
        raise CloudError(f"{p.title}: пустой ответ") from None
    from .ollama_client import strip_thinking
    text = strip_thinking(text).strip()
    if not text:
        raise CloudError(f"{p.title}: пустой ответ")
    return text
