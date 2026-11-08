"""Видео и звук для конвертера: ffmpeg (бесплатный). Сам находим его на компьютере, а если нет — по «Да»
скачиваем один раз (~90 МБ) в папку Капри. Субтитры и «звук в текст» — локальный Whisper Капри, ffmpeg не нужен.

Команды собираются отдельными функциями (их проверяют тесты), запускаются с прогрессом и кнопкой «Стоп».
Видео кодируем H.264 (-preset veryfast) — открывается везде: Windows, телефоны, Telegram, Discord.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .convert import ConvertError, flags, natural_key, out_path

Progress = Callable[[float, str], None]

FFMPEG_ZIPS = ("https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
               "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip")
MP4_VIDEO = ("h264", "hevc", "av1")
MP4_AUDIO = ("aac", "mp3", "")


# ====================================================================== где ffmpeg
def tools_dir() -> Path:
    from .config import expand
    return expand("tools/ffmpeg")


def find_ffmpeg(cfg: dict | None = None) -> tuple[str, str] | None:
    """(ffmpeg, ffprobe) или None."""
    cands: list[Path] = []
    if cfg and (p := (cfg.get("tools") or {}).get("ffmpeg")):
        cands.append(Path(p))
    exe = ".exe" if sys.platform == "win32" else ""
    if w := shutil.which("ffmpeg"):
        cands.append(Path(w))
    cands.append(tools_dir() / f"ffmpeg{exe}")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        cands.append(Path(local) / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe")
        cands += sorted((Path(local) / "Microsoft" / "WinGet" / "Packages").glob("Gyan.FFmpeg*/*/bin/ffmpeg.exe"))
    for c in cands:
        probe = c.with_name(f"ffprobe{exe}")
        if c.is_file() and (probe.is_file() or shutil.which("ffprobe")):
            return str(c), str(probe if probe.is_file() else shutil.which("ffprobe"))
    return None


def install_ffmpeg(progress: Progress | None = None, cancel: threading.Event | None = None) -> tuple[str, str]:
    """Скачать сборку ffmpeg для Windows и достать из архива только ffmpeg.exe и ffprobe.exe."""
    import tempfile
    import zipfile

    import httpx
    dst = tools_dir()
    dst.mkdir(parents=True, exist_ok=True)
    last = None
    for url in FFMPEG_ZIPS:
        tmp = Path(tempfile.gettempdir()) / "capri-ffmpeg.zip"
        try:
            with httpx.stream("GET", url, follow_redirects=True, timeout=60) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length") or 0)
                got = 0
                with tmp.open("wb") as fh:
                    for chunk in r.iter_bytes(1 << 16):
                        if cancel is not None and cancel.is_set():
                            raise ConvertError("отменено")
                        fh.write(chunk)
                        got += len(chunk)
                        if progress and total:
                            progress(got / total, f"Скачиваю ffmpeg: {got >> 20} из {total >> 20} МБ")
            if tmp.stat().st_size < 30_000_000:
                raise ConvertError("архив ffmpeg скачался не целиком")
            with zipfile.ZipFile(tmp) as z:
                for name in z.namelist():
                    base = name.rsplit("/", 1)[-1].lower()
                    if base in ("ffmpeg.exe", "ffprobe.exe") and "/bin/" in name.lower():
                        (dst / base).write_bytes(z.read(name))
            found = find_ffmpeg()
            if found:
                return found
            raise ConvertError("в архиве не нашлось ffmpeg.exe")
        except ConvertError as e:
            if str(e) == "отменено":
                raise
            last = e
        except Exception as e:  # noqa: BLE001 — сайт недоступен: пробуем следующий
            last = e
        finally:
            tmp.unlink(missing_ok=True)
    raise ConvertError(f"не получилось скачать ffmpeg: {str(last)[:150]}")


# ====================================================================== что внутри файла
@dataclass
class Info:
    duration: float = 0.0
    vcodec: str = ""
    acodec: str = ""
    width: int = 0
    height: int = 0


def probe(ffprobe: str, path: Path) -> Info:
    r = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type,codec_name,width,height",
                        "-of", "json", str(path)], capture_output=True, text=True, creationflags=flags())
    if r.returncode:
        raise ConvertError(f"{path.name}: не похоже на видео или звук")
    data = json.loads(r.stdout or "{}")
    info = Info(duration=float((data.get("format") or {}).get("duration") or 0))
    for s in data.get("streams", []):
        if s.get("codec_type") == "video" and not info.vcodec and s.get("codec_name") not in ("mjpeg", "png"):
            info.vcodec, info.width, info.height = s.get("codec_name", ""), int(s.get("width") or 0), int(s.get("height") or 0)
        elif s.get("codec_type") == "audio" and not info.acodec:
            info.acodec = s.get("codec_name", "")
    return info


# ====================================================================== время «1:20», «с 0:10 до 0:45»
def parse_time(s: str) -> float:
    s = s.strip().replace(",", ".")
    if m := re.fullmatch(r"(?:(\d+):)?(\d{1,2}):(\d{1,2}(?:\.\d+)?)", s):
        return int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    if m := re.fullmatch(r"(?:(\d+)\s*(?:ч\w*|h))?\s*(?:(\d+)\s*(?:м\w*|min|m))?\s*(?:(\d+(?:\.\d+)?)\s*(?:с\w*|s|sec))?", s, re.I):
        if any(m.groups()):
            return int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + float(m.group(3) or 0)
    if re.fullmatch(r"\d+(?:\.\d+)?", s):
        return float(s)
    raise ConvertError(f"не поняла время «{s}» — пиши так: 1:20 или 0:05:30")


def parse_range(text: str, duration: float = 0.0) -> tuple[float, float]:
    """«с 1:20 до 2:05», «1:20-2:05», «до 0:30», «первые 30 секунд», «последние 10 секунд»."""
    t = text.lower().strip().replace("—", "-").replace("–", "-")
    if m := re.fullmatch(r"первые\s+(.+)", t):
        return 0.0, parse_time(m.group(1))
    if m := re.fullmatch(r"последние\s+(.+)", t):
        if not duration:
            raise ConvertError("не знаю длину файла")
        return max(0.0, duration - parse_time(m.group(1))), duration
    if m := re.fullmatch(r"(?:с|от)?\s*(\S+?)\s*(?:-|до|по)\s*(\S+)", t):
        a, b = parse_time(m.group(1)), parse_time(m.group(2))
    elif m := re.fullmatch(r"(?:до)\s*(\S+)", t):
        a, b = 0.0, parse_time(m.group(1))
    elif m := re.fullmatch(r"(?:с|от)\s*(\S+)", t):
        a, b = parse_time(m.group(1)), duration
    else:
        raise ConvertError("напиши, какой кусок: «с 1:20 до 2:05»")
    if duration:
        b = min(b, duration)
    if b <= a:
        raise ConvertError("конец должен быть позже начала")
    return a, b


# ====================================================================== команды (списки аргументов)
H264 = ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p"]
AAC = ["-c:a", "aac", "-b:a", "160k"]
FAST = ["-movflags", "+faststart"]


def cmd_mp4(src: Path, dst: Path, info: Info) -> list[str]:
    if info.vcodec == "h264" and info.acodec in MP4_AUDIO:
        return ["-i", str(src), "-map", "0:v:0", "-map", "0:a?", "-c", "copy", *FAST, str(dst)]   # только переупаковать
    deint = ["-vf", "yadif"] if src.suffix.lower() in (".vob", ".mpg", ".mpeg", ".ts", ".mts", ".m2ts") else []
    return ["-i", str(src), "-map", "0:v:0", "-map", "0:a?", *deint, *H264, "-crf", "21", *AAC, *FAST, str(dst)]


def video_bitrate(target_bytes: int, duration: float, has_audio: bool) -> int:
    """Битрейт видео (кбит/с), чтобы файл влез в target_bytes (5 % запас на контейнер)."""
    total = target_bytes * 8 / 1000 * 0.95 / max(1.0, duration)
    return int(max(80, total - (96 if has_audio else 0)))


def cmd_compress(src: Path, dst: Path, info: Info, target_bytes: int | None) -> list[str]:
    if target_bytes is None:
        return ["-i", str(src), "-map", "0:v:0", "-map", "0:a?", *H264, "-crf", "28", "-c:a", "aac", "-b:a", "128k",
                *FAST, str(dst)]
    vb = video_bitrate(target_bytes, info.duration, bool(info.acodec))
    h = info.height or 1080
    limit = 1080 if vb >= 3000 else 720 if vb >= 1200 else 480 if vb >= 500 else 360
    scale = ["-vf", f"scale=-2:{limit}"] if h > limit else []
    return ["-i", str(src), "-map", "0:v:0", "-map", "0:a?", *scale, *H264, "-b:v", f"{vb}k", "-maxrate", f"{int(vb * 1.4)}k",
            "-bufsize", f"{vb * 2}k", "-c:a", "aac", "-b:a", "96k", *FAST, str(dst)]


def cmd_cut(src: Path, dst: Path, a: float, b: float, video: bool) -> list[str]:
    codec = [*H264, "-crf", "20", *AAC, *FAST] if video else _audio_codec(dst.suffix)
    return ["-ss", f"{a:.3f}", "-i", str(src), "-t", f"{b - a:.3f}", "-map", "0:v:0?" if video else "0:a:0",
            *(["-map", "0:a?"] if video else []), *codec, str(dst)]


def cmd_gif(src: Path, dst: Path, seconds: float | None = None) -> list[str]:
    lim = ["-t", f"{seconds:.2f}"] if seconds else []
    return ["-i", str(src), *lim, "-vf", "fps=12,scale='min(480,iw)':-2:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];"
            "[b][p]paletteuse=dither=bayer", "-loop", "0", str(dst)]


def _audio_codec(ext: str) -> list[str]:
    return {".mp3": ["-c:a", "libmp3lame", "-q:a", "2"], ".wav": ["-c:a", "pcm_s16le"],
            ".ogg": ["-c:a", "libvorbis", "-q:a", "5"], ".m4a": ["-c:a", "aac", "-b:a", "192k"],
            ".flac": ["-c:a", "flac"]}.get(ext.lower(), ["-c:a", "libmp3lame", "-q:a", "2"])


def cmd_audio(src: Path, dst: Path, normalize: bool = False) -> list[str]:
    af = ["-af", "loudnorm=I=-16:TP=-1.5:LRA=11"] if normalize else []
    return ["-i", str(src), "-vn", "-map", "0:a:0", *af, *_audio_codec(dst.suffix), str(dst)]


def cmd_mute(src: Path, dst: Path) -> list[str]:
    return ["-i", str(src), "-map", "0:v:0", "-c", "copy", "-an", *FAST, str(dst)]


def concat_list(files: list[Path]) -> str:
    """Файл-список для concat: пути в одинарных кавычках (кавычка внутри — '\\'')."""
    return "".join("file '" + str(f).replace("\\", "/").replace("'", "'\\''") + "'\n" for f in files)


# ====================================================================== запуск
class Runner:
    """ffmpeg с прогрессом и «Стоп». cancel — threading.Event: выставили — процесс убиваем, недоделанный файл удаляем."""

    def __init__(self, ffmpeg: str, ffprobe: str, progress: Progress | None = None,
                 cancel: threading.Event | None = None):
        self.ffmpeg, self.ffprobe = ffmpeg, ffprobe
        self.progress = progress or (lambda f, s: None)
        self.cancel = cancel or threading.Event()

    def info(self, p: Path) -> Info:
        return probe(self.ffprobe, p)

    def run(self, args: list[str], dst: Path, duration: float, what: str, part: tuple[int, int] = (0, 1)):
        cmd = [self.ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-progress", "pipe:1", "-nostats", *args]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                errors="replace", creationflags=flags(low=True))
        err: list[str] = []
        t = threading.Thread(target=lambda: err.extend(proc.stderr.readlines()), daemon=True)
        t.start()
        k, n = part
        for line in proc.stdout:
            if self.cancel.is_set():
                proc.kill()
                break
            if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                try:
                    sec = int(line.split("=", 1)[1]) / 1_000_000
                except ValueError:
                    continue
                if duration:
                    self.progress((k + min(1.0, sec / duration)) / n, what)
        proc.wait()
        t.join(2)
        if self.cancel.is_set():
            dst.unlink(missing_ok=True)
            raise ConvertError("Остановлено.")
        if proc.returncode:
            dst.unlink(missing_ok=True)
            msg = " ".join(x.strip() for x in err[-3:])[:200]
            raise ConvertError(f"ffmpeg не справился: {msg or 'код ' + str(proc.returncode)}")
        return dst

    # ---------------------------------------------------------------- операции
    def to_mp4(self, files: list[Path]) -> list[Path]:
        out = []
        for i, f in enumerate(files):
            info = self.info(f)
            dst = out_path(f, "" if f.suffix.lower() != ".mp4" else "H.264", ".mp4")
            out.append(self.run(cmd_mp4(f, dst, info), dst, info.duration, f"В MP4: {f.name}", (i, len(files))))
        return out

    def compress(self, files: list[Path], target_bytes: int | None) -> list[Path]:
        out = []
        for i, f in enumerate(files):
            info = self.info(f)
            if target_bytes and f.stat().st_size <= target_bytes:
                raise ConvertError(f"{f.name} и так меньше {target_bytes >> 20} МБ")
            tag = f"{target_bytes >> 20} МБ" if target_bytes else "сжато"
            dst = out_path(f, tag, ".mp4")
            self.run(cmd_compress(f, dst, info, target_bytes), dst, info.duration, f"Сжимаю {f.name}", (i, len(files)))
            if target_bytes and dst.stat().st_size > target_bytes * 1.02:          # не влезли — ещё раз потише
                vb = int(video_bitrate(target_bytes, info.duration, bool(info.acodec)) * 0.8)
                args = cmd_compress(f, dst, info, target_bytes)
                args[args.index("-b:v") + 1] = f"{vb}k"
                self.run(args, dst, info.duration, f"Сжимаю ещё раз {f.name}", (i, len(files)))
            out.append(dst)
        return out

    def cut(self, files: list[Path], text: str) -> list[Path]:
        out = []
        for f in files:
            info = self.info(f)
            a, b = parse_range(text, info.duration)
            video = bool(info.vcodec)
            dst = out_path(f, f"{_ts(a)}–{_ts(b)}".replace(":", "."), ".mp4" if video else f.suffix)
            out.append(self.run(cmd_cut(f, dst, a, b, video), dst, b - a, f"Вырезаю из {f.name}"))
        return out

    def gif(self, files: list[Path]) -> list[Path]:
        out = []
        for f in files:
            info = self.info(f)
            sec = 20.0 if info.duration > 20 else None          # длинный GIF весит сотни МБ — берём первые 20 с
            dst = out_path(f, "", ".gif")
            out.append(self.run(cmd_gif(f, dst, sec), dst, sec or info.duration, f"Делаю GIF из {f.name}"))
        return out

    def audio(self, files: list[Path], ext: str, normalize: bool = False) -> list[Path]:
        out = []
        for i, f in enumerate(files):
            info = self.info(f)
            if not info.acodec:
                raise ConvertError(f"{f.name}: звука нет")
            dst = out_path(f, "громкость" if normalize else ("" if f.suffix.lower() != ext else "копия"), ext)
            out.append(self.run(cmd_audio(f, dst, normalize), dst, info.duration,
                                f"{'Выравниваю громкость' if normalize else 'Звук'}: {f.name}", (i, len(files))))
        return out

    def mute(self, files: list[Path]) -> list[Path]:
        out = []
        for f in files:
            info = self.info(f)
            dst = out_path(f, "без звука", ".mp4" if info.vcodec in MP4_VIDEO else ".mkv")
            out.append(self.run(cmd_mute(f, dst), dst, info.duration, f"Убираю звук: {f.name}"))
        return out

    def concat(self, files: list[Path], workdir: Path) -> list[Path]:
        """Склеить части по порядку («часть 2» перед «часть 10»). Одинаковый формат — без перекодирования
        (секунды); DVD (VOB) — склеиваем и делаем MP4 с деинтерлейсом; разные — приводим к одному виду."""
        files = sorted(files, key=natural_key)
        if len(files) < 2:
            raise ConvertError("выдели хотя бы два файла")
        infos = [self.info(f) for f in files]
        total = sum(i.duration for i in infos)
        first = files[0]
        video = any(i.vcodec for i in infos)
        workdir.mkdir(parents=True, exist_ok=True)
        if not video:
            dst = out_path(first, "склеено", ".mp3" if first.suffix.lower() not in (".wav", ".m4a", ".ogg", ".flac") else first.suffix)
            inputs = [x for f in files for x in ("-i", str(f))]
            flt = "".join(f"[{i}:a:0]" for i in range(len(files))) + f"concat=n={len(files)}:v=0:a=1[a]"
            return [self.run([*inputs, "-filter_complex", flt, "-map", "[a]", *_audio_codec(dst.suffix), str(dst)],
                             dst, total, "Склеиваю звук")]
        if all(f.suffix.lower() in (".vob", ".mpg", ".mpeg") for f in files):
            dst = out_path(first, "склеено", ".mp4")
            src = "concat:" + "|".join(str(f) for f in files)
            return [self.run(["-i", src, "-map", "0:v:0", "-map", "0:a?", "-vf", "yadif", *H264, "-crf", "20", *AAC, *FAST,
                              str(dst)], dst, total, "Склеиваю DVD в MP4")]
        same = len({(i.vcodec, i.acodec, i.width, i.height) for i in infos}) == 1
        lst = workdir / "concat.txt"
        if same:
            ext = ".mp4" if infos[0].vcodec in MP4_VIDEO and infos[0].acodec in MP4_AUDIO else ".mkv"
            dst = out_path(first, "склеено", ext)
            lst.write_text(concat_list(files), encoding="utf-8")
            try:
                return [self.run(["-f", "concat", "-safe", "0", "-i", str(lst), "-map", "0:v:0", "-map", "0:a?", "-c", "copy",
                                  *(FAST if ext == ".mp4" else []), str(dst)], dst, total, "Склеиваю без перекодирования")]
            except ConvertError as e:
                if "Остановлено" in str(e):
                    raise
        # разные форматы: каждую часть — в одинаковый MP4, потом склеить
        w = max(i.width for i in infos) or 1280
        h = max(i.height for i in infos) or 720
        w, h = w - w % 2, h - h % 2
        parts = []
        try:
            for k, (f, info) in enumerate(zip(files, infos)):
                p = workdir / f"part{k:03d}.mp4"
                vf = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30"
                if info.acodec:
                    args = ["-i", str(f), "-map", "0:v:0", "-map", "0:a:0", "-vf", vf, *H264, "-crf", "21", *AAC,
                            "-ar", "48000", "-ac", "2", str(p)]
                else:                       # части без звука — подкладываем тишину, иначе склейка собьётся
                    args = ["-i", str(f), "-f", "lavfi", "-t", f"{info.duration:.3f}", "-i", "anullsrc=r=48000:cl=stereo",
                            "-map", "0:v:0", "-map", "1:a", "-vf", vf, *H264, "-crf", "21", *AAC, "-shortest", str(p)]
                parts.append(self.run(args, p, info.duration, f"Привожу к одному виду: {f.name}", (k, len(files) + 1)))
            dst = out_path(first, "склеено", ".mp4")
            lst.write_text(concat_list(parts), encoding="utf-8")
            return [self.run(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", *FAST, str(dst)], dst, total,
                             "Склеиваю", (len(files), len(files) + 1))]
        finally:
            for p in parts:
                p.unlink(missing_ok=True)


def _ts(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"


# ====================================================================== субтитры и текст из звука (Whisper)
def _srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms % 3600000 // 60000:02d}:{ms % 60000 // 1000:02d},{ms % 1000:03d}"


def segments_to_srt(segments) -> str:
    out = []
    for i, s in enumerate(segments, 1):
        out.append(f"{i}\n{_srt_time(s.start)} --> {_srt_time(s.end)}\n{s.text.strip()}\n")
    return "\n".join(out)


def transcribe(files: list[Path], model, srt: bool, progress: Progress | None = None,
               cancel: threading.Event | None = None, languages: tuple[str, ...] = ("ru", "de", "en", "he")) -> list[Path]:
    """model — WhisperModel Капри. srt=True — субтитры рядом с видео, иначе текст (.txt)."""
    out = []
    for f in files:
        segs, info = model.transcribe(str(f), beam_size=5, vad_filter=True)
        dur = float(getattr(info, "duration", 0) or 0)
        got = []
        for s in segs:
            if cancel is not None and cancel.is_set():
                raise ConvertError("Остановлено.")
            got.append(s)
            if progress and dur:
                progress(min(1.0, s.end / dur), f"Слушаю {f.name}")
        if not got:
            raise ConvertError(f"{f.name}: речи не слышно")
        if srt:
            dst = out_path(f, "", ".srt")
            dst.write_text(segments_to_srt(got), encoding="utf-8-sig")
        else:
            dst = out_path(f, "текст", ".txt")
            dst.write_text("\n".join(s.text.strip() for s in got), encoding="utf-8-sig")
        out.append(dst)
    return out
