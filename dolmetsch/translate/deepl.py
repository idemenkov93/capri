"""DeepL: онлайн-перевод для «важного» (собеседования). Бесплатный тариф ограничен по символам в месяц.

Ключ хранится в хранилище Windows (keyring) и никогда не пишется в журнал.
Документация: https://developers.deepl.com/docs/getting-started/auth
"""
from __future__ import annotations

import json
import socket
import ssl
import urllib.error
import urllib.request

from .base import TranslateError, Translator

FREE_URL, PRO_URL = "https://api-free.deepl.com", "https://api.deepl.com"


def _ssl_context():
    """Сертификаты Windows (работает за антивирусом и корпоративным прокси), если есть truststore."""
    try:
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:  # noqa: BLE001
        return ssl.create_default_context()


class DeepLTranslator(Translator):
    name = "DeepL"

    def __init__(self, get_key, timeout: float = 3.0, opener=None):
        self.get_key = get_key                 # функция: ключ берётся в момент запроса
        self.timeout = timeout
        self.opener = opener or self._open     # в тестах подменяется

    def ready(self) -> bool:
        return bool(self.get_key())

    @staticmethod
    def base_url(key: str) -> str:
        return FREE_URL if key.strip().endswith(":fx") else PRO_URL

    def _open(self, request, timeout):
        return urllib.request.urlopen(request, timeout=timeout, context=_ssl_context())

    def _call(self, path: str, body: dict | None):
        key = (self.get_key() or "").strip()
        if not key:
            raise TranslateError("auth", "нет ключа DeepL")
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            self.base_url(key) + path, data=data, method="POST" if body is not None else "GET",
            headers={"Authorization": f"DeepL-Auth-Key {key}", "Content-Type": "application/json",
                     "User-Agent": "Dolmetsch"})
        try:
            with self.opener(request, self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 456:
                raise TranslateError("quota", "квота DeepL на этот месяц закончилась") from None
            if e.code in (401, 403):
                raise TranslateError("auth", "DeepL не принял ключ") from None
            raise TranslateError("network", f"DeepL: ошибка {e.code}") from None
        except (socket.timeout, TimeoutError):
            raise TranslateError("timeout", "DeepL не ответил вовремя") from None
        except (urllib.error.URLError, OSError, ValueError) as e:
            if "timed out" in str(e).lower():
                raise TranslateError("timeout", "DeepL не ответил вовремя") from None
            raise TranslateError("network", "нет связи с DeepL") from None

    def translate(self, texts: list[str], context: str = "") -> list[str]:
        body = {"text": texts, "source_lang": "DE", "target_lang": "RU"}
        if context:
            body["context"] = context[-500:]   # предыдущая фраза: помогает с местоимениями и терминами
        data = self._call("/v2/translate", body)
        out = [t.get("text", "") for t in data.get("translations", [])]
        if len(out) != len(texts):
            raise TranslateError("network", "DeepL вернул неполный ответ")
        return out

    def usage(self) -> tuple[int, int]:
        """(использовано символов, лимит) за текущий месяц."""
        data = self._call("/v2/usage", None)
        return int(data.get("character_count", 0)), int(data.get("character_limit", 0))
