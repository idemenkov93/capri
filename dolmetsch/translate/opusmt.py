"""Офлайн-переводчик: модель Opus-MT (Helsinki-NLP/opus-mt-tc-big-de-zle) через CTranslate2.

Модель переводит с немецкого на русский, украинский и белорусский; нужный язык задаётся меткой
«>>rus<<» в начале каждого предложения. Ничего не отправляет в интернет.
Папка модели: model.bin, shared_vocabulary.json, source.spm, target.spm.
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

from .base import TranslateError, Translator, chunks

MODEL_DIR = "opus-mt-de-ru"
LANG_TOKEN = ">>rus<<"


def model_dirs() -> list[Path]:
    """Где искать модель: рядом с программой, в %APPDATA%\\Dolmetsch, в папке проекта."""
    here = Path(getattr(sys, "_MEIPASS", "") or Path(__file__).resolve().parents[2])
    exe = Path(sys.executable).resolve().parent
    appdata = Path(os.environ.get("APPDATA", Path.home())) / "Dolmetsch"
    return [exe / "models" / MODEL_DIR, here / "models" / MODEL_DIR, appdata / "models" / MODEL_DIR]


def find_model() -> Path | None:
    for path in model_dirs():
        if (path / "model.bin").exists() and (path / "source.spm").exists():
            return path
    return None


class OpusMTTranslator(Translator):
    name = "офлайн"

    def __init__(self, model_path: Path | None = None, device: str = "auto", threads: int = 0, beam: int = 0):
        self.model_path = model_path
        self.device, self.threads, self.beam = device, threads, beam
        self.used_device = ""               # на чём реально работает: cpu / cuda
        self._lock = threading.Lock()
        self._tr = self._src = self._tgt = None
        self.load_error = ""

    def ready(self) -> bool:
        return self._tr is not None or (self.model_path or find_model()) is not None

    def load(self) -> None:
        """Загрузить модель (один раз, при старте). Видеокарта не подошла — тихо переходим на процессор."""
        with self._lock:
            if self._tr is not None:
                return
            path = self.model_path or find_model()
            if path is None:
                raise TranslateError("model", "офлайн-модель не найдена")
            try:
                import ctranslate2
                import sentencepiece as spm
            except ImportError as e:
                raise TranslateError("model", f"нет библиотеки: {e}") from None
            threads = self.threads or max(1, (os.cpu_count() or 4) // 2)   # половина потоков: Teams тоже нужен процессор
            attempts = []
            if self.device in ("auto", "cuda"):
                try:
                    if ctranslate2.get_cuda_device_count() > 0:
                        # GTX 10xx плохо считает float16 — берём то, что карта умеет
                        types = ctranslate2.get_supported_compute_types("cuda")
                        kind = "int8_float16" if "int8_float16" in types and "float16" in types else \
                            ("int8" if "int8" in types else "float32")
                        attempts.append(("cuda", kind))
                except Exception:  # noqa: BLE001
                    pass
            attempts.append(("cpu", "int8"))
            last = ""
            for device, kind in attempts:
                try:
                    self._tr = ctranslate2.Translator(str(path), device=device, compute_type=kind,
                                                      intra_threads=threads, inter_threads=1)
                    self.used_device = device
                    break
                except Exception as e:  # noqa: BLE001
                    last = str(e)[:200]
            if self._tr is None:
                self.load_error = last
                raise TranslateError("model", f"модель не загрузилась: {last}")
            self._src = spm.SentencePieceProcessor(model_file=str(path / "source.spm"))
            self._tgt = spm.SentencePieceProcessor(model_file=str(path / "target.spm"))

    def translate(self, texts: list[str], context: str = "") -> list[str]:
        self.load()
        # длинные тексты режем на куски, переводим одной пачкой и склеиваем обратно
        pieces, owner = [], []
        for i, text in enumerate(texts):
            for part in chunks(text) or [""]:
                pieces.append(part)
                owner.append(i)
        batch = [[LANG_TOKEN] + self._src.encode(p, out_type=str) + ["</s>"] for p in pieces]
        beam = self.beam or (4 if self.used_device == "cuda" else 2)
        try:
            results = self._tr.translate_batch(batch, beam_size=beam, max_decoding_length=256,
                                               repetition_penalty=1.1, no_repeat_ngram_size=4)
        except Exception as e:  # noqa: BLE001
            raise TranslateError("model", f"сбой офлайн-перевода: {str(e)[:150]}") from None
        out = [""] * len(texts)
        for i, res in zip(owner, results):
            tokens = [t for t in res.hypotheses[0] if t not in ("</s>", "<pad>")]
            out[i] = (out[i] + " " + self._tgt.decode(tokens)).strip()
        return out
