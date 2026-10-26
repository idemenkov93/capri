"""Минимальный клиент локальной модели через Ollama (/api/chat с инструментами)."""
from __future__ import annotations

import httpx

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "open_app",
            "description": "Открыть программу из списка разрешённых",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "Название программы"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_url",
            "description": "Открыть сайт в браузере",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Поискать информацию в интернете (откроет поиск в браузере)",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_timer",
            "description": "Поставить таймер-напоминание",
            "parameters": {
                "type": "object",
                "properties": {
                    "minutes": {"type": "number"},
                    "label": {"type": "string"},
                },
                "required": ["minutes"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_note",
            "description": "Записать заметку",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_claude",
            "description": "Передать сложную задачу (код, отладка, подробный разбор) более сильному ИИ — Claude",
            "parameters": {
                "type": "object",
                "properties": {"task": {"type": "string"}},
                "required": ["task"],
            },
        },
    },
]


class OllamaError(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, url: str, model: str, timeout: float = 120):
        self.url = url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def chat(self, messages: list[dict], tools: list[dict] | None = TOOLS) -> dict:
        """Возвращает message из ответа: {'content': str, 'tool_calls': [...]}."""
        payload = {"model": self.model, "messages": messages, "stream": False}
        if tools:
            payload["tools"] = tools
        try:
            r = httpx.post(f"{self.url}/api/chat", json=payload, timeout=self.timeout)
            r.raise_for_status()
        except httpx.ConnectError as e:
            raise OllamaError("Ollama не отвечает. Запусти её (ollama serve) и проверь адрес в config.yaml.") from e
        except httpx.HTTPStatusError as e:
            raise OllamaError(f"Ollama вернула ошибку {e.response.status_code}: {e.response.text[:200]}") from e
        return r.json().get("message", {})
