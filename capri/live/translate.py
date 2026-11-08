"""Перевод субтитров пачками: несколько свежих фраз за один запрос (так быстрее и укладываемся в бесплатные лимиты),
плюс пара предыдущих фраз как контекст — чтобы «er» и «das» переводились правильно.

Кто переводит (auto): Groq → Gemini → OpenRouter → локальная модель. Claude не берём — он через программу, медленно.
В облако уходит только текст фраз, звук остаётся на компьютере.
"""
from __future__ import annotations

import json
import re

LANG_NAME = {"ru": "русский", "de": "немецкий", "en": "английский", "he": "иврит", "uk": "украинский"}

PROMPT = """Ты синхронный переводчик субтитров урока / разговора. Переведи фразы на {target} язык.
Пиши просто и понятно, как живую речь. Термины IT (Active Directory, DNS, Server, PowerShell…) оставляй как есть.
Если фраза оборвана — переведи как есть, не додумывай. Ничего не объясняй.
{context}Фразы:
{lines}
Ответ — только JSON-массив строк той же длины ({n}), без markdown."""


def build_prompt(lines: list[str], context: list[str], target: str = "ru") -> str:
    ctx = ("Что говорили перед этим (для смысла, не переводи):\n" + "\n".join(f"— {c}" for c in context[-3:]) + "\n\n"
           if context else "")
    return PROMPT.format(target=LANG_NAME.get(target, target), context=ctx,
                         lines="\n".join(f"{i}. {t}" for i, t in enumerate(lines, 1)), n=len(lines))


def parse(raw: str, n: int) -> list[str]:
    """JSON-массив → список; если модель ответила нумерованным списком — тоже понимаем."""
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    m = re.search(r"\[.*\]", s, re.S)
    if m:
        try:
            arr = json.loads(m.group(0))
            if isinstance(arr, list) and arr:
                out = [str(x).strip() for x in arr][:n]
                return out + [""] * (n - len(out))
        except ValueError:
            pass
    items = [re.sub(r"^\s*\d+[.)]\s*", "", ln).strip() for ln in s.splitlines() if re.match(r"^\s*\d+[.)]\s+", ln)]
    if len(items) >= n:
        return items[:n]
    if n == 1 and s and not s.startswith("["):
        return [s.strip()]
    raise ValueError("непонятный ответ переводчика")


class Translator:
    def __init__(self, agent, target: str = "ru", prefer: str = "auto"):
        self.agent, self.target, self.prefer = agent, target, prefer
        self.last_used = ""
        self._broken: dict[str, float] = {}          # кто не ответил — не трогаем минуту

    def order(self) -> list[str]:
        a = self.agent
        cloud = [p for p in ("groq", "gemini", "openrouter") if a.ready(p)]
        if self.prefer == "local":
            return ["local"]
        if self.prefer in cloud:
            return [self.prefer] + [p for p in cloud if p != self.prefer] + ["local"]
        return cloud + ["local"]

    def translate(self, lines: list[str], context: list[str]) -> list[str]:
        import time
        prompt = build_prompt(lines, context, self.target)
        errors = []
        for p in self.order():
            if self._broken.get(p, 0) > time.monotonic():
                continue
            try:
                raw = self._ask(p, prompt)
                out = parse(raw, len(lines))
                self.last_used = p
                return out
            except Exception as e:  # noqa: BLE001 — лимит, нет сети: следующий
                errors.append(f"{p}: {str(e)[:80]}")
                if p != "local":
                    self._broken[p] = time.monotonic() + 60
        raise RuntimeError("; ".join(errors) or "переводчик недоступен")

    def _ask(self, p: str, prompt: str) -> str:
        a = self.agent
        if p == "gemini":
            return a.gemini_ask(prompt, {**a._gcfg(), "model": a._gcfg().get("fast_model") or "gemini-flash-lite-latest",
                                         "max_tokens": 1024, "timeout": 20}, images=None, system="")
        if p in ("groq", "openrouter"):
            return a.cloud_ask(p, prompt, {**a._ccfg(p), "max_tokens": 1024, "timeout": 20}, images=None, system="")
        from ..llm.ollama_client import strip_thinking
        msg = a._text_llm().chat([{"role": "user", "content": prompt + " /no_think"}], tools=None)
        return strip_thinking(msg.get("content", ""))
