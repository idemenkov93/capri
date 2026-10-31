"""Grok (xAI) — голос для характера «Киберпсих»: быстрый и без лишней цензуры на мат.

Ключ: console.x.ai → API Keys (начинается с «xai-»). API платный: на счёт кладёшь кредиты заранее,
быстрая модель стоит копейки (короткая реплика — доли цента). Ключ хранится в Диспетчере учётных данных Windows.
Формат запросов как у OpenAI (/v1/chat/completions), поэтому инструменты (открыть программу, таймер…)
работают так же, как у локальной модели.
"""
from __future__ import annotations

import json
import re
from typing import Callable

import httpx

from .ollama_client import TOOLS

API = "https://api.x.ai/v1"
KEY_PAGE = "https://console.x.ai"
SERVICE = "capri-grok"
DEFAULT_MODEL = "grok-4-1-fast-non-reasoning"
RE_KEY = re.compile(r"xai-[A-Za-z0-9]{20,}")


class GrokError(RuntimeError):
    pass


class GrokStopped(GrokError):
    def __init__(self):
        super().__init__("остановлено")


# ------------------------------------------------------------------ ключ
def get_key(cfg: dict | None = None) -> str | None:
    import os
    if cfg and cfg.get("api_key"):
        return cfg["api_key"]
    if os.environ.get("XAI_API_KEY"):
        return os.environ["XAI_API_KEY"]
    try:
        import keyring
        return keyring.get_password(SERVICE, "api_key")
    except Exception:  # noqa: BLE001
        return None


def set_key(key: str) -> None:
    import keyring
    keyring.set_password(SERVICE, "api_key", key.strip())


def _error(r: httpx.Response) -> GrokError:
    try:
        detail = r.json().get("error") or r.text
        if isinstance(detail, dict):
            detail = detail.get("message") or str(detail)
    except ValueError:
        detail = r.text
    detail = str(detail)[:200]
    if r.status_code in (400, 401, 403) and re.search(r"key|auth|permission", detail, re.I):
        return GrokError("ключ Grok не подошёл — скопируй его ещё раз на console.x.ai")
    if r.status_code == 402 or re.search(r"credit|billing|balance|spending", detail, re.I):
        return GrokError("на счету xAI нет денег — пополни кредиты на console.x.ai")
    if r.status_code == 429:
        return GrokError("Grok просит подождать — слишком много запросов")
    return GrokError(f"Grok вернул ошибку {r.status_code}: {detail}")


# ------------------------------------------------------------------ модели
def pick_model(models: list[str]) -> str:
    """Самая новая быстрая модель без рассуждений: она отвечает за секунду и стоит копейки."""
    def ver(m: str) -> tuple:
        return tuple(int(x) for x in re.findall(r"\d+", m)[:3]) or (0,)
    for want in (r"fast-non-reasoning$", r"fast", r"mini", r"^grok"):
        cands = [m for m in models if re.search(want, m) and "image" not in m and "vision" not in m
                 and "imagine" not in m and "code" not in m]
        if cands:
            return max(cands, key=ver)
    return DEFAULT_MODEL


def list_models(key: str, timeout: float = 15) -> list[str]:
    r = httpx.get(f"{API}/models", headers={"Authorization": f"Bearer {key}"}, timeout=timeout)
    if r.status_code >= 400:
        raise _error(r)
    return [m.get("id", "") for m in r.json().get("data", []) if m.get("id")]


def check_key(key: str) -> str:
    """Проверить ключ настоящим коротким запросом и вернуть модель."""
    try:
        model = pick_model(list_models(key))
    except GrokError:
        raise
    except httpx.HTTPError:
        model = DEFAULT_MODEL
    GrokClient(key, model).chat([{"role": "user", "content": "Ответь одним словом: на связи?"}], tools=None)
    return model


# ------------------------------------------------------------------ клиент (как OllamaClient.chat)
class GrokClient:
    def __init__(self, key: str, model: str = DEFAULT_MODEL, timeout: float = 60, max_tokens: int = 700):
        self.key, self.model, self.timeout, self.max_tokens = key, model or DEFAULT_MODEL, timeout, max_tokens

    def chat(self, messages: list[dict], tools: list[dict] | None = TOOLS,
             on_token: Callable[[str, str], None] | None = None,
             should_stop: Callable[[], bool] | None = None) -> dict:
        """Потоковый ответ. Возвращает {'content': str, 'tool_calls': [{'function': {'name', 'arguments'}}]}."""
        msgs = [{**m, "content": m.get("content", "").replace("/no_think", "")} if m.get("role") == "system" else m
                for m in messages]
        payload = {"model": self.model, "messages": msgs, "stream": True, "max_tokens": self.max_tokens,
                   "temperature": 0.9}
        if tools:
            payload["tools"] = tools
        content: list[str] = []
        calls: dict[int, dict] = {}
        timeout = httpx.Timeout(connect=10, read=self.timeout, write=30, pool=10)
        try:
            with httpx.stream("POST", f"{API}/chat/completions", json=payload, timeout=timeout,
                              headers={"Authorization": f"Bearer {self.key}"}) as r:
                if r.status_code >= 400:
                    r.read()
                    raise _error(r)
                for line in r.iter_lines():
                    if should_stop and should_stop():
                        raise GrokStopped()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except ValueError:
                        continue
                    for ch in chunk.get("choices") or []:
                        delta = ch.get("delta") or {}
                        if piece := delta.get("content"):
                            content.append(piece)
                            on_token and on_token("content", piece)
                        for tc in delta.get("tool_calls") or []:
                            slot = calls.setdefault(tc.get("index", len(calls)), {"name": "", "arguments": ""})
                            fn = tc.get("function") or {}
                            slot["name"] += fn.get("name") or ""
                            args = fn.get("arguments")
                            if isinstance(args, dict):
                                slot["arguments"] = args
                            elif args:
                                slot["arguments"] += args
        except httpx.ConnectError as e:
            raise GrokError("нет связи с Grok — проверь интернет") from e
        except httpx.TimeoutException as e:
            raise GrokError(f"Grok молчал больше {self.timeout:g} сек") from e
        tool_calls = [{"function": c} for _i, c in sorted(calls.items()) if c["name"]]
        return {"content": "".join(content).strip(), "tool_calls": tool_calls}
