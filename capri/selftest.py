"""Самопроверка Капри: прогоняет все части по-настоящему и пишет отчёт в data/selftest.txt.

Запуск:  .venv\\Scripts\\python -m capri.selftest
Ничего не меняет в системе: громкость, яркость и музыку только читает, скриншоты не делает.
"""
from __future__ import annotations

import importlib
import os
import platform
import sys
import time
import traceback
from pathlib import Path

from .config import expand, load_config

OK, WARN, FAIL = "OK  ", "WARN", "FAIL"
results: list[tuple[str, str, str]] = []
PHRASE = "Привет, Капри. Какая сегодня погода в Мюнхене?"


def _limited(fn, seconds, *a, **k):
    """Выполнить с ограничением по времени: зависший шаг не должен вешать всю проверку."""
    import concurrent.futures as cf
    ex = cf.ThreadPoolExecutor(max_workers=1)
    fut = ex.submit(fn, *a, **k)
    try:
        return fut.result(timeout=seconds)
    except cf.TimeoutError:
        return FAIL, f"нет ответа за {seconds} с"
    finally:
        ex.shutdown(wait=False, cancel_futures=True)


def check(name: str, limit: int = 240):
    def deco(fn):
        def run(*a, **k):
            t0 = time.perf_counter()
            try:
                status, detail = _limited(fn, limit, *a, **k)
            except Exception as e:  # noqa: BLE001
                status, detail = FAIL, f"{type(e).__name__}: {e}"
                tb = traceback.format_exc().strip().splitlines()[-3:]
                detail += " | " + " / ".join(x.strip() for x in tb)
            detail = f"{detail} ({time.perf_counter() - t0:.1f} s)"
            results.append((status, name, detail))
            print(f"[{status}] {name}: {detail}", flush=True)
            return status
        return run
    return deco


@check("Python и система")
def t_python():
    return OK, f"Python {platform.python_version()}, {platform.platform()}, {os.cpu_count()} потоков"


@check("Пакеты")
def t_packages():
    need = ["PySide6", "httpx", "yaml", "numpy", "faster_whisper", "sounddevice", "edge_tts", "av", "vosk",
            "psutil", "PIL", "pycaw", "comtypes", "screen_brightness_control", "winrt.windows.media.control"]
    missing = []
    for m in need:
        try:
            importlib.import_module(m)
        except Exception as e:  # noqa: BLE001
            missing.append(f"{m} ({type(e).__name__})")
    return (FAIL, "нет: " + ", ".join(missing)) if missing else (OK, f"все {len(need)} на месте")


@check("Ollama: сервер и модель")
def t_ollama_model(cfg):
    import httpx
    o = cfg["ollama"]
    tags = httpx.get(f"{o['url']}/api/tags", timeout=10).json().get("models", [])
    names = [m["name"] for m in tags]
    if o["model"] not in names and f"{o['model']}:latest" not in names:
        return FAIL, f"модели {o['model']} нет, есть: {', '.join(names) or 'ничего'}"
    return OK, f"{o['model']} скачана (всего моделей: {len(names)})"


@check("Ollama: ответ и скорость")
def t_ollama_chat(cfg):
    import httpx
    o = cfg["ollama"]
    opts = {"num_predict": 60, **(o.get("options") or {})}
    r = httpx.post(f"{o['url']}/api/chat", timeout=300, json={
        "model": o["model"], "stream": False, "think": False, "keep_alive": "30m", "options": opts,
        "messages": [{"role": "user", "content": "Ответь одним коротким предложением: как дела? /no_think"}]}).json()
    text = (r.get("message") or {}).get("content", "").strip()
    n, dur = r.get("eval_count", 0), (r.get("eval_duration") or 1) / 1e9
    load = (r.get("load_duration") or 0) / 1e9
    speed = n / dur if dur else 0
    status = OK if speed >= 15 else WARN
    return status, f"{speed:.0f} токенов/с, загрузка модели {load:.1f} с, ответ: «{text[:80]}»"


@check("Ollama: видеокарта")
def t_ollama_gpu(cfg):
    import httpx
    o = cfg["ollama"]
    for m in httpx.get(f"{o['url']}/api/ps", timeout=10).json().get("models", []):
        if m["name"].startswith(o["model"].split(":")[0]):
            size, vram = m.get("size", 0), m.get("size_vram", 0)
            pct = 100 * vram / size if size else 0
            status = OK if pct >= 99 else WARN if pct > 0 else FAIL
            return status, f"{pct:.0f}% на GPU ({vram / 2**30:.1f} из {size / 2**30:.1f} ГБ), context {m.get('context_length', '?')}"
    return WARN, "модель не загружена в память (ollama ps пуст)"


@check("Агент: мгновенные команды и маршрут к модели")
def t_agent(cfg):
    from .agent import Agent

    class FakeSystem:
        def run(self, cmd):
            return {"volume": 40}
    agent = Agent(cfg, system=FakeSystem(), claude=lambda t, c: "")
    r1 = agent.handle("громкость 40")
    r2 = agent.handle("Расскажи в одном предложении, что такое DNS.")
    ok = r1.route == "rules" and r2.route in ("local", "claude") and len(r2.text) > 10
    return (OK if ok else FAIL), f"команда → {r1.route} «{r1.text}»; вопрос → {r2.route} «{r2.text[:90]}»"


@check("Claude Code")
def t_claude(cfg):
    from .llm import claude_code
    exe = claude_code.find_claude(cfg["claude_code"].get("command", "claude"))
    if not exe:
        return FAIL, "claude не найден (ни в PATH, ни в %USERPROFILE%\\.local\\bin)"
    ans = claude_code.ask("Ответь ровно одним словом: работает", {**cfg["claude_code"], "timeout": 180})
    return OK, f"{exe} → «{ans.strip()[:60]}»"


@check("Озвучка Edge (+ фильтры)", limit=60)
def t_tts(cfg, store):
    import edge_tts

    from .state import State
    from .voice.fx import apply
    from .voice.tts import decode_mp3
    tune = State(expand("data/state.json")).get("voice_tune") or {}
    voice = (tune.get("voices") or cfg["voice"]["edge_voices"]).get("ru", "ru-RU-SvetlanaNeural")
    comm = edge_tts.Communicate(PHRASE, voice, connect_timeout=10, receive_timeout=30)
    mp3 = b"".join(c["data"] for c in comm.stream_sync() if c.get("type") == "audio")
    audio, sr = decode_mp3(mp3)
    store["mp3"] = mp3
    fx = apply(audio, sr, tune.get("fx"))
    return OK, f"голос {voice}: {len(audio) / sr:.1f} с звука; фильтры {tune.get('fx') or 'выключены'} → {len(fx) / sr:.1f} с"


def _pcm16k(mp3: bytes):
    import io

    import av
    import numpy as np
    with av.open(io.BytesIO(mp3)) as c:
        rs = av.AudioResampler(format="s16", layout="mono", rate=16000)
        chunks = [o.to_ndarray().reshape(-1) for f in c.decode(audio=0) for o in rs.resample(f)]
    return np.concatenate(chunks)


@check("Распознавание речи (Whisper)")
def t_stt(cfg, store):
    if "mp3" not in store:
        return WARN, "пропущено — нет звука из теста озвучки"
    from .voice.stt import Transcriber
    v = cfg["voice"]
    pcm = _pcm16k(store["mp3"])
    store["pcm"] = pcm
    stt = Transcriber(v["whisper_model"], v["languages"], str(expand(v["models_dir"]) / "whisper"),
                      preferred=v.get("preferred_language", "ru"))
    t0 = time.perf_counter()
    stt.warmup()
    load = time.perf_counter() - t0
    t0 = time.perf_counter()
    text, lang = stt.transcribe(pcm.astype("float32") / 32768)
    took = time.perf_counter() - t0
    good = "капри" in text.lower() and "погод" in text.lower()
    return (OK if good else WARN), f"модель {v['whisper_model']} загрузка {load:.1f} с, распознала за {took:.1f} с [{lang}]: «{text}»"


@check("«Привет, Капри» (vosk)")
def t_wake(cfg, store):
    import json

    from vosk import KaldiRecognizer, Model, SetLogLevel

    from .voice.wake import is_wake, model_path
    path = model_path(expand(cfg["voice"]["models_dir"]))
    if not (path / "am").is_dir():
        return FAIL, f"нет модели {path}"
    if "pcm" not in store:
        return WARN, "пропущено — нет звука"
    SetLogLevel(-1)
    rec = KaldiRecognizer(Model(str(path)), 16000)
    pcm = store["pcm"].tobytes()
    heard = []
    for i in range(0, len(pcm), 8000):
        if rec.AcceptWaveform(pcm[i:i + 8000]):
            heard.append(json.loads(rec.Result()).get("text", ""))
    heard.append(json.loads(rec.FinalResult()).get("text", ""))
    text = " ".join(h for h in heard if h)
    return (OK if is_wake(text) else WARN), f"услышала «{text}» → {'фраза поймана' if is_wake(text) else 'фраза НЕ поймана'}"


@check("Микрофоны")
def t_mics():
    from .state import State
    from .system import input_devices
    devs = input_devices()
    cur = State(expand("data/state.json")).get("input_device")
    names = "; ".join(f"{i}: {n}" + (" ← выбран" if i == cur else "") for i, n in devs)
    if cur is not None and cur not in [i for i, _ in devs]:
        return WARN, f"в меню выбран микрофон №{cur}, но такого нет. Есть: {names}"
    return (OK if devs else FAIL), names or "микрофоны не найдены"


@check("Микрофон: уровень сигнала (1 с)", limit=15)
def t_mic_level():
    import numpy as np
    import sounddevice as sd

    from .state import State
    dev = State(expand("data/state.json")).get("input_device")
    rec = sd.rec(16000, samplerate=16000, channels=1, dtype="float32", device=dev)
    sd.wait()
    lvl = float(np.sqrt(np.mean(rec ** 2)))
    return (OK if lvl > 0.0005 else WARN), f"уровень {lvl:.4f} ({'тишина/выключен' if lvl <= 0.0005 else 'звук идёт'})"


@check("Системные команды (только чтение)")
def t_system():
    from .commands import Command
    from .system import SystemControl
    s = SystemControl(expand("data"))
    vol = s.run(Command("volume_get"))
    np_ = s.run(Command("now_playing"))
    try:
        import screen_brightness_control as sbc
        br = f"{sbc.get_brightness()}"
    except Exception as e:  # noqa: BLE001
        br = f"недоступна ({type(e).__name__}) — для обычного монитора без DDC/CI это нормально"
    bad = [x for x in (vol, np_) if x.get("error")]
    return (WARN if bad else OK), f"громкость {vol}, играет {np_ or 'ничего'}, яркость {br}"


@check("Диагностика ПК (без блокнота)")
def t_pc():
    from .system import analyze_pc
    r = analyze_pc()
    return OK, f"CPU {r['cpu']:.0f}%, RAM {r['ram_percent']:.0f}%, проблемы: {'; '.join(r['issues']) or 'нет'}"


@check("Анимации")
def t_sprites(cfg):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])

    from .app import CapriApp
    from .ui.sprites import SpriteSet
    av = cfg["avatar"]
    out = []
    for where in ("desktop", "hud"):
        src = CapriApp._avatar_source(av, where)
        out.append(f"{where}: {SpriteSet(src).kinds() if src else 'нет'}")
    return OK, "; ".join(out)


def main() -> int:
    cfg = load_config()
    print("Самопроверка Капри… (1–3 минуты)\n", flush=True)
    store: dict = {}
    t_python()
    t_packages()
    if t_ollama_model(cfg) != FAIL:
        t_ollama_chat(cfg)
        t_ollama_gpu(cfg)
        t_agent(cfg)
    t_claude(cfg)
    t_tts(cfg, store)
    t_stt(cfg, store)
    t_wake(cfg, store)
    t_mics()
    t_mic_level()
    t_system()
    t_pc()
    t_sprites(cfg)

    report = Path(expand("data/selftest.txt"))
    report.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"Capri selftest — {time.strftime('%Y-%m-%d %H:%M:%S')}", ""]
    lines += [f"[{s}] {n}: {d}" for s, n, d in results]
    fails = sum(s == FAIL for s, _, _ in results)
    warns = sum(s == WARN for s, _, _ in results)
    lines += ["", f"Итого: {len(results) - fails - warns} OK, {warns} WARN, {fails} FAIL"]
    report.write_text("\n".join(lines), encoding="utf-8")
    print("\n" + lines[-1])
    print(f"Отчёт: {report}")
    os._exit(1 if fails else 0)  # не ждать зависшие фоновые проверки


if __name__ == "__main__":
    sys.exit(main())
