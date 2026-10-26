"""Минимальный клиент локальной модели через Ollama (/api/chat с инструментами)."""
from __future__ import annotations

import json
from typing import Callable

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


def strip_thinking(text: str) -> str:
    """Убрать рассуждения модели: всё до последнего </think> и сам тег."""
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1]
    return text.replace("<think>", "").strip()


class OllamaError(RuntimeError):
    pass


class OllamaStopped(OllamaError):
    def __init__(self):
        super().__init__("Остановила. Спрашивай по-другому 🙂")


class OllamaClient:
    def __init__(self, url: str, model: str, timeout: float = 120, think: bool | None = None,
                 options: dict | None = None):
        self.url = url.rstrip("/")
        # num_predict — максимум токенов в ответе (защита от зацикливания),
        # repeat_penalty — штраф за повторы
        self.options = {"num_predict": 700, "repeat_penalty": 1.15, **(options or {})}
        self.model = model
        self.timeout = timeout
        self.think = think

    def chat(self, messages: list[dict], tools: list[dict] | None = TOOLS,
             on_token: Callable[[str, str], None] | None = None,
             should_stop: Callable[[], bool] | None = None) -> dict:
        """Потоковый запрос к модели.

        on_token(kind, text) вызывается по мере генерации: kind = "content" или "thinking".
        Таймаут считается между кусочками ответа, а не на весь ответ, поэтому даже медленный
        процессор не упирается в лимит, пока модель продолжает печатать.
        Возвращает {'content': str, 'tool_calls': [...]}.
        """
        # keep_alive — держать модель в памяти 30 минут, чтобы не грузить её заново
        payload = {"model": self.model, "messages": messages, "stream": True, "keep_alive": "30m",
                   "options": self.options}
        if tools:
            payload["tools"] = tools
        if self.think is not None:
            payload["think"] = self.think

        content, tool_calls = [], []
        answer_started = False
        timeout = httpx.Timeout(connect=10, read=self.timeout, write=30, pool=10)
        try:
            with httpx.stream("POST", f"{self.url}/api/chat", json=payload, timeout=timeout) as r:
                if r.status_code >= 400:
                    r.read()
                    raise OllamaError(f"Ollama вернула ошибку {r.status_code}: {r.text[:200]}")
                for line in r.iter_lines():
                    if should_stop and should_stop():
                        raise OllamaStopped()
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    if err := chunk.get("error"):
                        raise OllamaError(f"Ollama: {err}")
                    msg = chunk.get("message") or {}
                    if piece := msg.get("thinking"):
                        on_token and on_token("thinking", piece)
                    if piece := msg.get("content"):
                        content.append(piece)
                        # Некоторые модели (qwen3 «thinking») игнорируют think=false и пишут
                        # рассуждения прямо в ответ, закрывая их тегом </think>.
                        joined = "".join(content)
                        if "<think>" in joined and "</think>" not in joined:
                            on_token and on_token("thinking", piece)
                        elif "</think>" in piece or ("</think>" in joined and not answer_started):
                            answer_started = True
                            on_token and on_token("reset", strip_thinking(joined))
                        elif "</think>" in joined or "<think>" not in joined:
                            on_token and on_token("content", piece)
                    tool_calls.extend(msg.get("tool_calls") or [])
                    if chunk.get("done"):
                        break
        except httpx.ConnectError as e:
            raise OllamaError("Ollama не отвечает. Запусти её (ollama serve) и проверь адрес в config.yaml.") from e
        except httpx.TimeoutException as e:
            raise OllamaError(
                f"Модель молчала больше {self.timeout:g} сек. Проверь в терминале «ollama ps»: "
                "если в колонке PROCESSOR стоит CPU — возьми модель полегче (например qwen3:1.7b)."
            ) from e
        return {"content": strip_thinking("".join(content)), "tool_calls": tool_calls}

    def warmup(self) -> None:
        """Заранее загрузить модель в видеопамять (первый запуск может занять до минуты)."""
        try:
            httpx.post(f"{self.url}/api/generate",
                       # те же options (num_ctx, num_gpu), что и в чате — иначе Ollama перезагрузит модель на первом сообщении
                       json={"model": self.model, "prompt": "", "keep_alive": "30m", "options": self.options},
                       timeout=max(self.timeout, 300))
        except httpx.HTTPError:
            pass  # если Ollama не запущена — ошибку покажем при первом сообщении
