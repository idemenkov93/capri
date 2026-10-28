"""Gemini (Google AI Studio) как «умный помощник» — если нет подписки Claude.

Бесплатно: ключ на https://aistudio.google.com/apikey, без карты. Лимиты маленькие (Flash — около 20 запросов
в день, Flash-Lite — несколько сотен), а запросы бесплатного уровня Google может использовать для улучшения
своих моделей (и их могут читать люди) — поэтому личное туда лучше не отправлять.
Платно: тот же ключ, но в проекте включена оплата → доступна Pro и лимиты большие.

Модели меняются часто, поэтому используем «-latest»-названия (Google сам переводит их на новую версию),
а если такого нет — выбираем самую новую подходящую из списка моделей аккаунта.
"""
from __future__ import annotations

import base64
import re
from pathlib import Path

import httpx

API = "https://generativelanguage.googleapis.com/v1beta"
KEY_PAGE = "https://aistudio.google.com/apikey"
SERVICE = "capri-gemini"
ALIASES = {"free": ("gemini-flash-lite-latest", "gemini-flash-latest"),
           "free_smart": ("gemini-flash-latest", "gemini-flash-lite-latest"),
           "paid": ("gemini-pro-latest", "gemini-flash-latest")}


class GeminiError(RuntimeError):
    pass


# ------------------------------------------------------------------ ключ (Диспетчер учётных данных Windows)
def get_key(cfg: dict | None = None) -> str | None:
    import os
    if cfg and cfg.get("api_key"):
        return cfg["api_key"]
    if os.environ.get("GEMINI_API_KEY"):
        return os.environ["GEMINI_API_KEY"]
    try:
        import keyring
        return keyring.get_password(SERVICE, "api_key")
    except Exception:  # noqa: BLE001
        return None


def set_key(key: str) -> None:
    import keyring
    keyring.set_password(SERVICE, "api_key", key.strip())


# ------------------------------------------------------------------ модели
def list_models(key: str, timeout: float = 15) -> list[str]:
    r = httpx.get(f"{API}/models", params={"pageSize": 200}, headers={"x-goog-api-key": key}, timeout=timeout)
    if r.status_code in (400, 401, 403):
        raise GeminiError("ключ не подошёл — скопируй его ещё раз на aistudio.google.com/apikey")
    r.raise_for_status()
    return [m["name"].split("/", 1)[1] for m in r.json().get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])]


def _version(name: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", name.split("-", 1)[1] if "-" in name else name)[:2]) or (0,)


def pick_model(models: list[str], tier: str) -> str:
    """tier: free | free_smart | paid → имя модели. Сначала «-latest», иначе самая новая подходящая."""
    for alias in ALIASES[tier]:
        if alias in models:
            return alias
    want = {"free": "flash-lite", "free_smart": "flash", "paid": "pro"}[tier]
    pat = re.compile(rf"^gemini-[\d.]+-{want}(-preview)?$")
    cands = [m for m in models if pat.match(m)] or [m for m in models if re.match(r"^gemini-[\d.]+-flash$", m)]
    if not cands:
        raise GeminiError("в аккаунте нет подходящей модели Gemini")
    return max(cands, key=_version)


def check_key(key: str, tier: str) -> str:
    """Проверить ключ и вернуть модель, которую будем использовать.
    Новые ключи «AQ.…» у части аккаунтов не пускают в список моделей — тогда пробуем короткий запрос к модели."""
    try:
        return pick_model(list_models(key), tier)
    except GeminiError:
        model = ALIASES[tier][0]
        r = httpx.post(f"{API}/models/{model}:generateContent", headers={"x-goog-api-key": key}, timeout=20,
                       json={"contents": [{"role": "user", "parts": [{"text": "Ответь одним словом: ок"}]}],
                             "generationConfig": {"maxOutputTokens": 8}})
        if r.status_code == 200:
            return model
        try:
            msg = r.json().get("error", {}).get("message", "")[:160]
        except ValueError:
            msg = r.text[:160]
        raise GeminiError(f"Google не принял ключ ({r.status_code}): {msg}") from None


# ------------------------------------------------------------------ запрос
def ask(prompt: str, cfg: dict, images: list[Path] | None = None, system: str = "") -> str:
    key = get_key(cfg)
    if not key:
        raise GeminiError(f"Нет ключа Gemini. Возьми бесплатный на {KEY_PAGE} и впиши его в мастере настройки.")
    parts: list[dict] = [{"text": prompt}]
    for img in images or []:
        parts.append({"inline_data": {"mime_type": "image/png", "data": base64.b64encode(Path(img).read_bytes()).decode()}})
    body: dict = {"contents": [{"role": "user", "parts": parts}],
                  "generationConfig": {"maxOutputTokens": cfg.get("max_tokens", 2048)}}
    if system:
        body["system_instruction"] = {"parts": [{"text": system}]}
    model = cfg.get("model") or ALIASES["free"][0]
    try:
        r = httpx.post(f"{API}/models/{model}:generateContent", json=body, headers={"x-goog-api-key": key},
                       timeout=cfg.get("timeout", 120))
    except httpx.HTTPError as e:
        raise GeminiError(f"Gemini не отвечает: {e}") from e
    if r.status_code == 429:
        raise GeminiError("Лимит Gemini на сегодня исчерпан (у бесплатного ключа он маленький). "
                          "Попробуй позже или включи оплату в AI Studio.")
    if r.status_code in (401, 403) or (r.status_code == 400 and "API key" in r.text):
        raise GeminiError("Gemini не принял ключ — проверь его в мастере настройки.")
    if r.status_code == 404:
        raise GeminiError(f"Модель {model} больше недоступна — запусти мастер настройки, он выберет новую.")
    if r.status_code >= 400:
        raise GeminiError(f"Gemini: ошибка {r.status_code}: {r.text[:200]}")
    data = r.json()
    try:
        cand = data["candidates"][0]
        text = "".join(p.get("text", "") for p in cand["content"]["parts"] if not p.get("thought"))
    except (KeyError, IndexError):
        reason = (data.get("promptFeedback") or {}).get("blockReason") or "пустой ответ"
        raise GeminiError(f"Gemini не ответил ({reason}).") from None
    return text.strip()
