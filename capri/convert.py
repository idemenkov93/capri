"""Волна 3: конвертер файлов. Выделил файлы → Ctrl+Shift+Q → круговое меню (ui/radial.py) → результат рядом.

Здесь — сами операции, без окон: каждая берёт список файлов и возвращает список новых файлов.
Оригиналы никогда не трогаем; новое имя — «фото (сжато).jpg», если такое есть — «фото (сжато) 2.jpg».

Чем делаем (всё бесплатно и локально):
  картинки — Pillow (+ pillow-heif для HEIC с iPhone); фон убирает маленькая модель u2netp (~5 МБ, скачается один раз);
  PDF — pypdf и pypdfium2; Word/Excel/PowerPoint → PDF — через установленный Office (или LibreOffice);
  видео и звук — ffmpeg (media.py: найдёт или предложит скачать один раз); субтитры и текст из звука — Whisper Капри.
"""
from __future__ import annotations

import hashlib
import io
import re
import shutil
import zipfile
from pathlib import Path
from typing import Callable

IMAGE = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff", ".heic", ".heif", ".ico")
PDF = (".pdf",)
DOC = (".doc", ".docx", ".odt", ".rtf", ".xls", ".xlsx", ".ods", ".ppt", ".pptx", ".odp")
VIDEO = (".mp4", ".mkv", ".mov", ".avi", ".webm", ".wmv", ".flv", ".m4v", ".vob", ".ts", ".mts", ".m2ts", ".3gp",
         ".mpg", ".mpeg")
AUDIO = (".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac", ".opus", ".wma")
ARCHIVE = (".zip", ".7z", ".rar", ".tar", ".gz", ".tgz")

Progress = Callable[[float, str], None]          # (0…1, что делаю) — для строки в окне


class ConvertError(RuntimeError):
    pass


def kind(path: str | Path) -> str:
    ext = Path(path).suffix.lower()
    for name, exts in (("image", IMAGE), ("pdf", PDF), ("doc", DOC), ("video", VIDEO), ("audio", AUDIO),
                       ("archive", ARCHIVE)):
        if ext in exts:
            return name
    return "dir" if Path(path).is_dir() else "other"


def natural_key(p: Path):
    """«часть 2» раньше «часть 10», VTS_01_2.VOB раньше VTS_01_10.VOB."""
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", p.name)]


def out_path(src: Path, tag: str = "", ext: str | None = None, folder: Path | None = None) -> Path:
    """Рядом с оригиналом, не затирая: «фото (сжато).jpg», «фото (сжато) 2.jpg»…"""
    folder = folder or src.parent
    stem = src.stem + (f" ({tag})" if tag else "")
    ext = ext if ext is not None else src.suffix
    p = folder / f"{stem}{ext}"
    n = 2
    while p.exists():
        p = folder / f"{stem} {n}{ext}"
        n += 1
    return p


def human_size(n: float) -> str:
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024 or unit == "ГБ":
            return f"{n:.0f} {unit}" if unit in ("Б", "КБ") else f"{n:.1f} {unit}".replace(".", ",")
        n /= 1024
    return ""


# ====================================================================== картинки
def _open_image(path: Path):
    from PIL import Image, ImageOps
    if path.suffix.lower() in (".heic", ".heif"):
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except ImportError as e:
            raise ConvertError("для HEIC нужен pillow-heif — обнови Капри") from e
    img = Image.open(path)
    try:
        img = ImageOps.exif_transpose(img)        # фото с телефона — сразу правильной стороной вверх
    except Exception:  # noqa: BLE001
        pass
    return img


def _has_alpha(img) -> bool:
    return img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)


def _save(img, dst: Path, quality: int = 90):
    ext = dst.suffix.lower()
    if ext in (".jpg", ".jpeg"):
        if _has_alpha(img):
            from PIL import Image
            bg = Image.new("RGB", img.size, "white")
            bg.paste(img.convert("RGBA"), mask=img.convert("RGBA").split()[-1])
            img = bg
        img.convert("RGB").save(dst, "JPEG", quality=quality, optimize=True, progressive=True)
    elif ext == ".webp":
        img.save(dst, "WEBP", quality=quality, method=5)
    elif ext == ".png":
        img.save(dst, "PNG", optimize=True)
    elif ext == ".ico":
        img.convert("RGBA").save(dst, "ICO", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)
                                                    if s <= max(img.size)] or [(16, 16)])
    elif ext == ".bmp":
        img.convert("RGB").save(dst, "BMP")
    elif ext in (".tif", ".tiff"):
        img.save(dst, "TIFF", compression="tiff_lzw")
    elif ext == ".gif":
        img.save(dst, "GIF")
    else:
        raise ConvertError(f"не умею сохранять {ext}")
    return dst


def image_format(files: list[Path], ext: str, progress: Progress | None = None) -> list[Path]:
    out = []
    for i, f in enumerate(files):
        _tick(progress, i, len(files), f)
        out.append(_save(_open_image(f), out_path(f, "", ext) if f.suffix.lower() != ext else out_path(f, "копия", ext)))
    return out


def image_resize(files: list[Path], how: str, progress: Progress | None = None) -> list[Path]:
    """how: «50%» или «1920» (длинная сторона в пикселях; меньше — не увеличиваем)."""
    from PIL import Image
    out = []
    for i, f in enumerate(files):
        _tick(progress, i, len(files), f)
        img = _open_image(f)
        w, h = img.size
        k = int(how[:-1]) / 100 if how.endswith("%") else min(1.0, int(how) / max(w, h))
        tag = how if how.endswith("%") else f"{how}px"
        img = img.resize((max(1, round(w * k)), max(1, round(h * k))), Image.LANCZOS) if k < 1 else img
        ext = f.suffix.lower() if f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif") else ".jpg"
        out.append(_save(img, out_path(f, tag, ext)))
    return out


def image_compress(files: list[Path], max_bytes: int, progress: Progress | None = None) -> list[Path]:
    """Сжать до N байт: подбираем качество, если мало — ещё и уменьшаем. С прозрачностью — WEBP, иначе JPG."""
    from PIL import Image
    out = []
    for i, f in enumerate(files):
        _tick(progress, i, len(files), f)
        img = _open_image(f)
        ext = ".webp" if _has_alpha(img) else (".webp" if f.suffix.lower() == ".webp" else ".jpg")
        fmt = "WEBP" if ext == ".webp" else "JPEG"
        if fmt == "JPEG":
            img = img.convert("RGB")
        data = None
        for _ in range(8):
            lo, hi, best = 20, 95, None
            while lo <= hi:
                q = (lo + hi) // 2
                buf = io.BytesIO()
                img.save(buf, fmt, quality=q, optimize=True)
                if buf.tell() <= max_bytes:
                    best, lo = buf.getvalue(), q + 1
                else:
                    hi = q - 1
            if best is not None:
                data = best
                break
            img = img.resize((max(1, int(img.width * 0.8)), max(1, int(img.height * 0.8))), Image.LANCZOS)
        if data is None:
            raise ConvertError(f"{f.name}: не получается сжать до {human_size(max_bytes)}")
        dst = out_path(f, "сжато", ext)
        dst.write_bytes(data)
        out.append(dst)
    return out


def image_rotate(files: list[Path], degrees: int, progress: Progress | None = None) -> list[Path]:
    out = []
    for i, f in enumerate(files):
        _tick(progress, i, len(files), f)
        img = _open_image(f).rotate(-degrees, expand=True)        # + — по часовой
        ext = f.suffix.lower() if f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif") else ".png"
        out.append(_save(img, out_path(f, "повёрнуто", ext)))
    return out


def image_trim(files: list[Path], progress: Progress | None = None) -> list[Path]:
    """Обрезать одноцветные поля вокруг (скриншоты, сканы)."""
    from PIL import Image, ImageChops
    out = []
    for i, f in enumerate(files):
        _tick(progress, i, len(files), f)
        img = _open_image(f)
        rgb = img.convert("RGB")
        bg = Image.new("RGB", rgb.size, rgb.getpixel((0, 0)))
        diff = ImageChops.add(ImageChops.difference(rgb, bg), ImageChops.difference(rgb, bg), 2.0, -20)
        box = diff.getbbox()
        if not box or box == (0, 0, *img.size):
            raise ConvertError(f"{f.name}: полей для обрезки не нашла")
        ext = f.suffix.lower() if f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp", ".bmp") else ".png"
        out.append(_save(img.crop(box), out_path(f, "обрезано", ext)))
    return out


def images_to_pdf(files: list[Path], progress: Progress | None = None) -> list[Path]:
    files = sorted(files, key=natural_key)
    pages = []
    for i, f in enumerate(files):
        _tick(progress, i, len(files), f)
        img = _open_image(f)
        pages.append(img.convert("RGB"))
    dst = out_path(files[0], "PDF" if len(files) == 1 else f"+{len(files) - 1}", ".pdf")
    pages[0].save(dst, "PDF", save_all=True, append_images=pages[1:], resolution=150)
    return [dst]


# ---- фон: u2netp (ONNX, ~4,6 МБ), onnxruntime уже есть (им говорит голос Piper)
U2NETP_URL = "https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2netp.onnx"


def _u2net(models_dir: Path) -> Path:
    p = models_dir / "u2netp.onnx"
    if not p.exists():
        import httpx
        models_dir.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".part")
        with httpx.stream("GET", U2NETP_URL, follow_redirects=True, timeout=60) as r:
            r.raise_for_status()
            with tmp.open("wb") as fh:
                for chunk in r.iter_bytes():
                    fh.write(chunk)
        if tmp.stat().st_size < 1_000_000:
            tmp.unlink(missing_ok=True)
            raise ConvertError("модель для фона скачалась не целиком — попробуй ещё раз")
        tmp.replace(p)
    return p


def remove_background(files: list[Path], models_dir: Path, progress: Progress | None = None,
                      session=None) -> list[Path]:
    import numpy as np
    from PIL import Image
    if session is None:
        import onnxruntime as ort
        session = ort.InferenceSession(str(_u2net(models_dir)), providers=["CPUExecutionProvider"])
    name = session.get_inputs()[0].name
    out = []
    for i, f in enumerate(files):
        _tick(progress, i, len(files), f)
        img = _open_image(f).convert("RGB")
        x = np.asarray(img.resize((320, 320), Image.LANCZOS), dtype=np.float32) / 255.0
        x = (x - (0.485, 0.456, 0.406)) / (0.229, 0.224, 0.225)
        x = x.transpose(2, 0, 1)[None].astype(np.float32)
        mask = session.run(None, {name: x})[0][0, 0]
        mask = (mask - mask.min()) / max(1e-6, float(mask.max() - mask.min()))
        m = Image.fromarray((mask * 255).astype("uint8")).resize(img.size, Image.LANCZOS)
        rgba = img.convert("RGBA")
        rgba.putalpha(m)
        out.append(_save(rgba, out_path(f, "без фона", ".png")))
    return out


# ====================================================================== PDF
def pdf_merge(files: list[Path], progress: Progress | None = None) -> list[Path]:
    from pypdf import PdfWriter
    files = sorted(files, key=natural_key)
    w = PdfWriter()
    for i, f in enumerate(files):
        _tick(progress, i, len(files), f)
        w.append(str(f))
    dst = out_path(files[0], f"+{len(files) - 1}", ".pdf")
    with dst.open("wb") as fh:
        w.write(fh)
    return [dst]


def pdf_split(files: list[Path], progress: Progress | None = None) -> list[Path]:
    from pypdf import PdfReader, PdfWriter
    out = []
    for f in files:
        r = PdfReader(str(f))
        folder = out_path(f, "страницы", "")
        folder.mkdir()
        n = len(r.pages)
        for i, page in enumerate(r.pages, 1):
            _tick(progress, i - 1, n, f)
            w = PdfWriter()
            w.add_page(page)
            dst = folder / f"{f.stem} — стр {i:0{len(str(n))}d}.pdf"
            with dst.open("wb") as fh:
                w.write(fh)
            out.append(dst)
    return out


def pdf_compress(files: list[Path], progress: Progress | None = None) -> list[Path]:
    """Картинки внутри — JPEG качества 60 и не больше 1600 px; потоки — сжать. Тексту ничего не будет."""
    from pypdf import PdfWriter
    out = []
    for f in files:
        w = PdfWriter(clone_from=str(f))
        n = len(w.pages)
        for i, page in enumerate(w.pages):
            _tick(progress, i, n, f)
            for im in page.images:
                try:
                    pil = im.image
                    if max(pil.size) > 1600:
                        pil.thumbnail((1600, 1600))
                    if pil.mode not in ("RGB", "L"):
                        pil = pil.convert("RGB")
                    im.replace(pil, quality=60)
                except Exception:  # noqa: BLE001 — необычные картинки (маски, CMYK) оставляем как есть
                    pass
            page.compress_content_streams()
        w.compress_identical_objects()
        dst = out_path(f, "сжато", ".pdf")
        with dst.open("wb") as fh:
            w.write(fh)
        if dst.stat().st_size >= f.stat().st_size:
            dst.unlink()
            raise ConvertError(f"{f.name}: меньше не получается — PDF уже сжат")
        out.append(dst)
    return out


def pdf_to_images(files: list[Path], dpi: int = 150, progress: Progress | None = None) -> list[Path]:
    try:
        import pypdfium2 as pdfium
    except ImportError as e:
        raise ConvertError("для этого нужен pypdfium2 — обнови Капри") from e
    out = []
    for f in files:
        doc = pdfium.PdfDocument(str(f))
        folder = out_path(f, "картинки", "")
        folder.mkdir()
        n = len(doc)
        for i in range(n):
            _tick(progress, i, n, f)
            img = doc[i].render(scale=dpi / 72).to_pil()
            dst = folder / f"{f.stem} — стр {i + 1:0{len(str(n))}d}.jpg"
            img.convert("RGB").save(dst, "JPEG", quality=90)
            out.append(dst)
        doc.close()
    return out


def pdf_text_file(files: list[Path], progress: Progress | None = None) -> list[Path]:
    from pypdf import PdfReader
    out = []
    for f in files:
        r = PdfReader(str(f))
        parts = []
        for i, page in enumerate(r.pages):
            _tick(progress, i, len(r.pages), f)
            parts.append(page.extract_text() or "")
        text = "\n\n".join(parts).strip()
        if not text:
            raise ConvertError(f"{f.name}: текста нет — похоже, это скан (картинки). Открой и скажи «скопируй текст с экрана».")
        dst = out_path(f, "", ".txt")
        dst.write_text(text, encoding="utf-8-sig")
        out.append(dst)
    return out


def pdf_rotate(files: list[Path], degrees: int = 90, progress: Progress | None = None) -> list[Path]:
    from pypdf import PdfWriter
    out = []
    for f in files:
        w = PdfWriter(clone_from=str(f))
        for i, page in enumerate(w.pages):
            _tick(progress, i, len(w.pages), f)
            page.rotate(degrees)
        dst = out_path(f, "повёрнуто", ".pdf")
        with dst.open("wb") as fh:
            w.write(fh)
        out.append(dst)
    return out


# ====================================================================== Office ↔ PDF
WORD, EXCEL, POWERPOINT = (".doc", ".docx", ".odt", ".rtf"), (".xls", ".xlsx", ".ods"), (".ppt", ".pptx", ".odp")


def _soffice() -> str | None:
    import os
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", "")):
        p = Path(base or "-") / "LibreOffice" / "program" / "soffice.exe"
        if p.is_file():
            return str(p)
    return shutil.which("soffice") or shutil.which("libreoffice")


def _com(app: str):
    import comtypes
    import comtypes.client
    comtypes.CoInitialize()                 # мы в рабочем потоке
    try:
        o = comtypes.client.CreateObject(app, dynamic=True)
    except Exception:  # noqa: BLE001
        return None
    try:
        o.DisplayAlerts = 0
    except Exception:  # noqa: BLE001
        pass
    return o


def office_to_pdf(files: list[Path], progress: Progress | None = None) -> list[Path]:
    """Через установленный Microsoft Office (как «Сохранить как PDF»), иначе LibreOffice."""
    import subprocess
    out = []
    apps: dict[str, object] = {}
    try:
        for i, f in enumerate(files):
            _tick(progress, i, len(files), f)
            dst = out_path(f, "", ".pdf")
            ext = f.suffix.lower()
            prog = "Word.Application" if ext in WORD else "Excel.Application" if ext in EXCEL else "PowerPoint.Application"
            app = apps.get(prog) if prog in apps else apps.setdefault(prog, _com(prog))
            if app is not None:
                if ext in WORD:
                    d = app.Documents.Open(str(f), False, True, False)      # ConfirmConversions, ReadOnly, AddToRecent
                    d.ExportAsFixedFormat(str(dst), 17)
                    d.Close(False)
                elif ext in EXCEL:
                    wb = app.Workbooks.Open(str(f), 0, True)
                    wb.ExportAsFixedFormat(0, str(dst))
                    wb.Close(False)
                else:
                    pr = app.Presentations.Open(str(f), True, False, False)  # ReadOnly, Untitled, WithWindow
                    pr.SaveAs(str(dst), 32)
                    pr.Close()
            elif so := _soffice():
                tmp = dst.parent / f".capri-pdf-{i}"
                tmp.mkdir(exist_ok=True)
                subprocess.run([so, "--headless", "--convert-to", "pdf", "--outdir", str(tmp), str(f)],
                               capture_output=True, timeout=300, creationflags=flags())
                made = next(tmp.glob("*.pdf"), None)
                if made is None:
                    shutil.rmtree(tmp, ignore_errors=True)
                    raise ConvertError(f"{f.name}: LibreOffice не смог сделать PDF")
                made.replace(dst)
                shutil.rmtree(tmp, ignore_errors=True)
            else:
                raise ConvertError("нужен Microsoft Office или бесплатный LibreOffice (libreoffice.org) — его нет")
            out.append(dst)
    finally:
        for app in apps.values():
            try:
                app.Quit()
            except Exception:  # noqa: BLE001
                pass
    return out


def pdf_to_word(files: list[Path], progress: Progress | None = None) -> list[Path]:
    """Word сам умеет открывать PDF и сохранять в .docx (вёрстка — как получится, текст — точно)."""
    word = _com("Word.Application")
    if word is None:
        raise ConvertError("для PDF → Word нужен установленный Microsoft Word")
    out = []
    try:
        for i, f in enumerate(files):
            _tick(progress, i, len(files), f)
            dst = out_path(f, "", ".docx")
            d = word.Documents.Open(str(f), False, False, False)
            d.SaveAs2(str(dst), 16)
            d.Close(False)
            out.append(dst)
    finally:
        try:
            word.Quit()
        except Exception:  # noqa: BLE001
            pass
    return out


# ====================================================================== всё подряд
def sha256(files: list[Path], progress: Progress | None = None) -> list[tuple[Path, str]]:
    out = []
    for i, f in enumerate(files):
        _tick(progress, i, len(files), f)
        h = hashlib.sha256()
        with f.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        out.append((f, h.hexdigest()))
    return out


def to_zip(files: list[Path], progress: Progress | None = None) -> list[Path]:
    files = sorted(files, key=natural_key)
    # один файл — «фото.zip», несколько — по имени папки: «Отпуск.zip»
    base = files[0] if len(files) == 1 else files[0].parent / (files[0].parent.name or "Архив")
    dst = out_path(Path(str(base) + ".zip") if base.is_dir() or len(files) > 1 else base, "", ".zip")
    total = len(files)
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for i, f in enumerate(files):
            _tick(progress, i, total, f)
            if f.is_dir():
                for sub in sorted(f.rglob("*")):
                    z.write(sub, sub.relative_to(f.parent))
            else:
                z.write(f, f.name)
    return [dst]


def unpack(files: list[Path], progress: Progress | None = None) -> list[Path]:
    """ZIP — сами; 7z / rar / tar — tar.exe из Windows (в Windows 11 умеет и их)."""
    import subprocess
    out = []
    for i, f in enumerate(files):
        _tick(progress, i, len(files), f)
        folder = out_path(f, "", "")
        if f.suffix.lower() == ".zip":
            with zipfile.ZipFile(f) as z:
                for name in z.namelist():
                    target = (folder / name).resolve()
                    if not str(target).startswith(str(folder.resolve())):
                        raise ConvertError(f"{f.name}: в архиве подозрительные пути — распаковывать не буду")
                z.extractall(folder)
        else:
            tar = shutil.which("tar")
            if not tar:
                raise ConvertError(f"{f.suffix} распаковать нечем — нужен tar.exe (есть в Windows 10/11)")
            folder.mkdir()
            r = subprocess.run([tar, "-xf", str(f), "-C", str(folder)], capture_output=True, text=True,
                               creationflags=flags())
            if r.returncode:
                shutil.rmtree(folder, ignore_errors=True)
                raise ConvertError(f"{f.name}: не распаковалось ({(r.stderr or '').strip()[:120]})")
        out.append(folder)
    return out


def rename_plan(files: list[Path], pattern: str) -> list[tuple[Path, Path]]:
    """«Отпуск {n}» → Отпуск 01.jpg, Отпуск 02.jpg… ({n} нет — номер добавим в конец). Расширения не трогаем."""
    files = sorted(files, key=natural_key)
    pattern = pattern.strip() or "Файл {n}"
    if "{n}" not in pattern:
        pattern += " {n}"
    if re.search(r'[<>:"/\\|?*]', pattern.replace("{n}", "")):
        raise ConvertError('в имени нельзя: < > : " / \\ | ? *')
    width = max(2, len(str(len(files))))
    plan, taken = [], {f.name.lower() for f in files}
    for i, f in enumerate(files, 1):
        dst = f.with_name(pattern.replace("{n}", f"{i:0{width}d}") + f.suffix)
        if dst.exists() and dst.name.lower() not in taken:
            raise ConvertError(f"{dst.name} уже есть в папке")
        plan.append((f, dst))
    return plan


def rename(files: list[Path], pattern: str, progress: Progress | None = None) -> list[Path]:
    plan = rename_plan(files, pattern)
    tmp = []
    for i, (src, dst) in enumerate(plan):                 # через временные имена — можно «1↔2» без конфликтов
        t = src.with_name(f".capri-rename-{i}{src.suffix}")
        src.rename(t)
        tmp.append((t, dst))
    for t, dst in tmp:
        t.rename(dst)
    return [d for _, d in plan]


def flags(low: bool = False) -> int:
    """Без чёрного окна; low — пониже приоритет (ПК не тормозит, пока жмём видео). Не Windows — 0."""
    import sys
    if sys.platform != "win32":
        return 0
    return 0x08000000 | (0x00004000 if low else 0)


def _tick(progress: Progress | None, i: int, n: int, f: Path):
    if progress:
        progress(i / max(1, n), f.name)
