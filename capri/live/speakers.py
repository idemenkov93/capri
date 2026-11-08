"""Кто говорит: голосовые «отпечатки» (speaker embeddings) и группировка по голосу — как в Granola, только с именами.

Модель WeSpeaker ResNet34 (~26 МБ, ONNX — onnxruntime уже есть для голоса Piper), скачивается при первом включении.
Каждая фраза из колонок (≥ 1 с) → вектор 256 чисел; похож на известный голос — тот же человек, нет — «Спикер N».
Имена: «спикер 2 — это Herr Müller» (или правый клик в записи урока). Голоса с именами запоминаются в
data/voices.json — на следующем уроке учитель узнаётся сам. Звук не хранится, только эти числа.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

MODEL_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"
             "wespeaker_en_voxceleb_resnet34_LM.onnx")
MODEL_FILE = "wespeaker_resnet34.onnx"
SR = 16000
SAME = 0.55              # косинусное сходство: выше — тот же человек (для этой модели ~0.5–0.6)
KNOWN = 0.60             # узнать человека из прошлых уроков — чуть строже
MIN_SEC = 1.0            # короче — голос не разобрать, берём последнего говорившего


def model_path(models_dir: Path) -> Path:
    return models_dir / "speakers" / MODEL_FILE


def download(models_dir: Path, progress=lambda s: None) -> Path:
    p = model_path(models_dir)
    if p.exists() and p.stat().st_size > 20_000_000:
        return p
    import httpx
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".part")
    progress("Скачиваю модель голосов (~26 МБ)…")
    with httpx.stream("GET", MODEL_URL, follow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        with tmp.open("wb") as fh:
            for chunk in r.iter_bytes(1 << 16):
                fh.write(chunk)
    if tmp.stat().st_size < 20_000_000:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("модель голосов скачалась не целиком")
    tmp.replace(p)
    return p


def fbank(audio: np.ndarray) -> np.ndarray:
    """80 мел-полос, окно 25 мс, шаг 10 мс (как Kaldi/WeSpeaker), минус среднее."""
    import kaldi_native_fbank as knf
    opts = knf.FbankOptions()
    opts.frame_opts.dither = 0
    opts.frame_opts.samp_freq = SR
    opts.frame_opts.snip_edges = True
    opts.mel_opts.num_bins = 80
    f = knf.OnlineFbank(opts)
    f.accept_waveform(SR, (audio.astype(np.float32) * 32768).tolist())
    f.input_finished()
    feats = np.array([f.get_frame(i) for i in range(f.num_frames_ready)], dtype=np.float32)
    return feats - feats.mean(axis=0, keepdims=True) if len(feats) else feats


class Embedder:
    def __init__(self, path: Path, session=None):
        if session is None:
            import onnxruntime as ort
            o = ort.SessionOptions()
            o.intra_op_num_threads = 2                  # процессор — уроку и Teams
            session = ort.InferenceSession(str(path), o, providers=["CPUExecutionProvider"])
        self.s = session
        self.inp = self.s.get_inputs()[0].name
        self.lock = threading.Lock()

    def __call__(self, audio: np.ndarray) -> np.ndarray | None:
        audio = trim_silence(audio)
        if len(audio) < MIN_SEC * SR:
            return None
        feats = fbank(audio[: 12 * SR])
        with self.lock:
            e = self.s.run(None, {self.inp: feats[None]})[0][0]
        n = np.linalg.norm(e)
        return (e / n).astype(np.float32) if n else None


def trim_silence(audio: np.ndarray, win: int = 480) -> np.ndarray:
    """Оставить только громкие кусочки — паузы портят «отпечаток» голоса."""
    n = len(audio) // win
    if n == 0:
        return audio
    frames = audio[: n * win].reshape(n, win)
    rms = np.sqrt((frames ** 2).mean(axis=1))
    keep = rms > max(0.005, float(np.percentile(rms, 30)) * 1.5)
    return frames[keep].reshape(-1) if keep.any() else audio


@dataclass
class Speaker:
    id: int
    name: str
    centroid: np.ndarray
    count: int = 1
    known: bool = False          # узнан по прошлым урокам / назван пользователем

    def add(self, e: np.ndarray):
        self.count += 1
        k = min(self.count, 20)                       # новые фразы весят больше старых, но не дёргают центр
        c = self.centroid * (k - 1) / k + e / k
        self.centroid = c / (np.linalg.norm(c) or 1)


@dataclass
class Speakers:
    """Группировка по голосу внутри одного урока + память голосов с именами между уроками."""
    voices_file: Path | None = None
    same: float = SAME
    known_thr: float = KNOWN
    speakers: list[Speaker] = field(default_factory=list)
    memory: dict[str, list[float]] = field(default_factory=dict)
    last: Speaker | None = None
    alias: dict[int, int] = field(default_factory=dict)      # слитые: «спикер 3 — это тоже Herr Müller»

    def __post_init__(self):
        if self.voices_file and self.voices_file.exists():
            try:
                self.memory = json.loads(self.voices_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.memory = {}

    def assign(self, e: np.ndarray | None) -> Speaker | None:
        """→ кто это. e=None (короткая фраза) — тот же, кто говорил последним."""
        if e is None:
            return self.last
        best, sim = None, -1.0
        for s in self.speakers:
            v = float(np.dot(s.centroid, e))
            if v > sim:
                best, sim = s, v
        if best is not None and sim >= self.same:
            best.add(e)
            self.last = best
            return best
        name, known = self._recall(e)
        s = Speaker(len(self.speakers) + 1, name or f"Спикер {len(self.speakers) + 1}", e.copy(), known=bool(name))
        self.speakers.append(s)
        self.last = s
        return s

    def named(self, name: str) -> int:
        """Имя уже известно (субтитры Teams) → номер этого говорящего в уроке (без отпечатка голоса)."""
        for s in self.speakers:
            if s.name == name:
                self.last = s
                return s.id
        dim = len(self.speakers[0].centroid) if self.speakers else 256
        s = Speaker(len(self.speakers) + 1, name, np.zeros(dim, np.float32), count=0)
        self.speakers.append(s)
        self.last = s
        return s.id

    def _recall(self, e: np.ndarray) -> tuple[str, bool]:
        best, sim = "", -1.0
        for name, vec in self.memory.items():
            v = float(np.dot(np.asarray(vec, np.float32), e))
            if v > sim:
                best, sim = name, v
        taken = {s.name for s in self.speakers}
        return (best, True) if sim >= self.known_thr and best not in taken else ("", False)

    def rename(self, ref: str, name: str) -> Speaker | None:
        """ref: «2», «спикер 2», «Спикер 2» или текущее имя. Голос с именем запоминаем на следующие уроки."""
        import re
        s = None
        m = re.search(r"\d+", ref)
        if m:
            s = next((x for x in self.speakers if x.id == int(m.group())), None)
        if s is None:
            s = next((x for x in self.speakers if x.name.lower() == ref.strip().lower()), None)
        if s is None:
            return None
        name = name.strip()
        other = next((x for x in self.speakers if x is not s and x.name.lower() == name.lower()), None)
        if other is not None:                      # такое имя уже есть — это один человек: сливаем
            c = other.centroid * other.count + s.centroid * s.count
            other.centroid = c / (np.linalg.norm(c) or 1)
            other.count += s.count
            self.speakers.remove(s)
            self.alias[s.id] = other.id
            self.alias.update({k: other.id for k, v in self.alias.items() if v == s.id})
            if self.last is s:
                self.last = other
            s = other
        s.name, s.known = name, True
        self.memory = {k: v for k, v in self.memory.items() if k != s.name}
        self.memory[s.name] = [round(float(x), 5) for x in s.centroid]
        self.save()
        return s

    def resolve(self, sid: int) -> Speaker | None:
        sid = self.alias.get(sid, sid)
        return next((x for x in self.speakers if x.id == sid), None)

    def forget(self, name: str) -> bool:
        if name in self.memory:
            del self.memory[name]
            self.save()
            return True
        return False

    def save(self):
        if self.voices_file:
            try:
                self.voices_file.parent.mkdir(parents=True, exist_ok=True)
                self.voices_file.write_text(json.dumps(self.memory, ensure_ascii=False), encoding="utf-8")
            except OSError:
                pass

    def remember_session(self):
        """Конец урока: уже названным голосам обновить «отпечаток» (голос в разных уроках чуть разный)."""
        for s in self.speakers:
            if s.known and s.count >= 3:
                old = np.asarray(self.memory.get(s.name, s.centroid), np.float32)
                c = old * 0.5 + s.centroid * 0.5
                self.memory[s.name] = [round(float(x), 5) for x in c / (np.linalg.norm(c) or 1)]
        self.save()
