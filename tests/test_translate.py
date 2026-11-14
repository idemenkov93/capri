"""Переводчики и выбор между ними. Без сети и без настоящей модели."""
import datetime as dt
import io
import json
import socket
import urllib.error

import pytest

from dolmetsch.translate.base import TranslateError, Translator, chunks
from dolmetsch.translate.deepl import DeepLTranslator
from dolmetsch.translate.opusmt import LANG_TOKEN, OpusMTTranslator
from dolmetsch.translate.router import Router


# ---------- DeepL
class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def deepl(answer=None, error=None, key="abc:fx"):
    seen = {}

    def opener(request, timeout):
        seen.update(url=request.full_url, auth=request.headers.get("Authorization"), timeout=timeout,
                    body=json.loads(request.data) if request.data else None)
        if error:
            raise error
        return Response(json.dumps(answer).encode())
    return DeepLTranslator(lambda: key, opener=opener), seen


def test_deepl_request_and_free_or_pro_address():
    tr, seen = deepl({"translations": [{"text": "Доброе утро"}]})
    assert tr.translate(["Guten Morgen"], context="Hallo.") == ["Доброе утро"]
    assert seen["url"] == "https://api-free.deepl.com/v2/translate" and seen["auth"] == "DeepL-Auth-Key abc:fx"
    assert seen["body"] == {"text": ["Guten Morgen"], "source_lang": "DE", "target_lang": "RU", "context": "Hallo."}
    assert seen["timeout"] == 3.0
    assert DeepLTranslator.base_url("pro-key") == "https://api.deepl.com"


@pytest.mark.parametrize("error,kind", [
    (urllib.error.HTTPError("u", 456, "quota", {}, None), "quota"),
    (urllib.error.HTTPError("u", 403, "forbidden", {}, None), "auth"),
    (urllib.error.HTTPError("u", 429, "slow down", {}, None), "network"),
    (urllib.error.HTTPError("u", 503, "busy", {}, None), "network"),
    (socket.timeout("timed out"), "timeout"),
    (urllib.error.URLError("no route"), "network"),
])
def test_deepl_errors_have_kinds_and_never_show_the_key(error, kind):
    tr, _ = deepl(error=error, key="SECRET-KEY:fx")
    with pytest.raises(TranslateError) as e:
        tr.translate(["Hallo"])
    assert e.value.kind == kind and "SECRET" not in str(e.value)


def test_deepl_without_key_and_usage():
    assert not DeepLTranslator(lambda: "").ready()
    tr, seen = deepl({"character_count": 1200, "character_limit": 500000})
    assert tr.usage() == (1200, 500000) and seen["url"].endswith("/v2/usage") and seen["body"] is None


# ---------- офлайн-модель (заглушки вместо CTranslate2 и sentencepiece)
def test_opus_adds_language_token_and_translates_batch():
    tr = OpusMTTranslator()
    seen = {}

    class Model:
        def translate_batch(self, batch, **kw):
            seen["batch"], seen["kw"] = batch, kw

            class R:
                def __init__(self, tokens):
                    self.hypotheses = [tokens]
            return [R(["▁ru"] + [t for t in b[1:-1]] + ["</s>"]) for b in batch]

    class SP:
        def encode(self, text, out_type=str):
            return ["▁" + w for w in text.split()]

        def decode(self, tokens):
            return " ".join(t.strip("▁") for t in tokens)
    tr._tr, tr._src, tr._tgt, tr.used_device = Model(), SP(), SP(), "cpu"
    out = tr.translate(["Guten Morgen", "Hallo"])
    assert out == ["ru Guten Morgen", "ru Hallo"]
    assert seen["batch"][0] == [LANG_TOKEN, "▁Guten", "▁Morgen", "</s>"] and seen["kw"]["beam_size"] == 2


def test_opus_without_model_reports_clearly(tmp_path, monkeypatch):
    import dolmetsch.translate.opusmt as O
    monkeypatch.setattr(O, "model_dirs", lambda: [tmp_path / "нет"])
    tr = OpusMTTranslator()
    assert not tr.ready()
    with pytest.raises(TranslateError) as e:
        tr.load()
    assert e.value.kind == "model"


def test_long_text_is_cut_into_chunks():
    assert chunks("Kurz.") == ["Kurz."] and chunks("") == []
    text = " ".join(f"Das ist Satz Nummer {i} über das Netzwerk." for i in range(30))
    parts = chunks(text, 200)
    assert all(len(p) <= 200 for p in parts) and " ".join(parts) == text
    blob = "wort " * 200
    assert all(len(p) <= 100 for p in chunks(blob, 100)) and "".join(chunks(blob, 100)).replace(" ", "") == blob.replace(" ", "")


# ---------- Router
class Fake(Translator):
    def __init__(self, name, ok=True, fail=None):
        self.name, self.ok, self.fail, self.calls = name, ok, fail, []

    def ready(self):
        return self.ok

    def translate(self, texts, context=""):
        self.calls.append(list(texts))
        if self.fail:
            raise TranslateError(self.fail)
        return [f"{self.name}:{t}" for t in texts]


def router(profile="auto", when=dt.datetime(2026, 11, 16, 10, 0), **kw):       # понедельник, 10:00
    off, on = kw.pop("off", Fake("офлайн")), kw.pop("on", Fake("DeepL"))
    clock = kw.pop("clock", [0.0])
    return Router(off, on, profile, state={}, clock=lambda: clock[0], today=lambda: when), off, on, clock


def test_profiles_lesson_is_offline_only_and_important_uses_deepl():
    r, off, on, _ = router("auto")
    assert r.active_profile() == "lesson" and r.translate(["Hallo"])[0].engine == "офлайн" and not on.calls
    r, off, on, _ = router("auto", when=dt.datetime(2026, 11, 16, 18, 30))     # вечер — «Важное»
    assert r.active_profile() == "important" and r.translate(["Hallo"])[0].text == "DeepL:Hallo"
    r, off, on, _ = router("auto", when=dt.datetime(2026, 11, 14, 10, 0))      # суббота
    assert r.active_profile() == "important"
    r, off, on, _ = router("auto", when=dt.datetime(2026, 11, 14, 10, 0), on=Fake("DeepL", ok=False))
    assert r.active_profile() == "lesson"                                       # ключа нет — офлайн
    r, off, on, _ = router("important")
    assert r.toggle() == "lesson" and r.toggle() == "important"


def test_deepl_failure_falls_back_and_is_retried_later():
    r, off, on, clock = router("important", on=Fake("DeepL", fail="timeout"))
    res = r.translate(["Hallo"])[0]
    assert res.text == "офлайн:Hallo" and r.last_error
    r.translate(["Noch mal"])
    assert len(on.calls) == 1                     # 30 секунд DeepL не трогаем
    clock[0] = 31
    r.translate(["Und wieder"])
    assert len(on.calls) == 2


def test_quota_blocks_deepl_until_next_month_and_chars_are_counted():
    r, off, on, _ = router("important")
    r.translate(["Hallo", "Welt"])
    assert r.chars_this_month() == 9
    r.online = Fake("DeepL", fail="quota")
    r.cache.clear()
    r.translate(["Neu"])
    assert r.state["blocked_month"] == "2026-11" and not r._online_allowed()


def test_cache_and_total_failure_never_raises():
    r, off, on, _ = router("lesson")
    r.translate(["Hallo"])
    r.translate(["Hallo"])
    assert len(off.calls) == 1
    r, off, on, _ = router("lesson", off=Fake("офлайн", ok=False))
    res = r.translate(["Hallo"])[0]
    assert res.text == "" and res.error
