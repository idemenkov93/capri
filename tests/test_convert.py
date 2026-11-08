"""Волна 3: конвертер — картинки, PDF, архивы, имена, меню, видео и звук через ffmpeg, круговое меню."""
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from capri import convert as C
from capri import convert_menu as M
from capri import media

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def img(path: Path, size=(800, 600), color=(200, 30, 60), mode="RGB") -> Path:
    Image.new(mode, size, color).save(path)
    return path


# ------------------------------------------------------------------ имена
def test_out_path_never_overwrites(tmp_path):
    src = img(tmp_path / "фото.png")
    a = C.out_path(src, "сжато", ".jpg")
    a.write_bytes(b"x")
    assert a.name == "фото (сжато).jpg" and C.out_path(src, "сжато", ".jpg").name == "фото (сжато) 2.jpg"


def test_natural_order():
    names = ["часть 10.mp4", "часть 2.mp4", "часть 1.mp4", "VTS_01_10.VOB", "VTS_01_2.VOB"]
    got = [p.name for p in sorted(map(Path, names), key=C.natural_key)]
    assert got[:2] == ["VTS_01_2.VOB", "VTS_01_10.VOB"] and got[2:] == ["часть 1.mp4", "часть 2.mp4", "часть 10.mp4"]


def test_kind():
    assert [C.kind(n) for n in ("a.JPG", "b.heic", "c.pdf", "d.docx", "e.VOB", "f.m4a", "g.7z", "h.xyz")] == \
        ["image", "image", "pdf", "doc", "video", "audio", "archive", "other"]


# ------------------------------------------------------------------ картинки
def test_image_format_and_resize(tmp_path):
    src = img(tmp_path / "a.png", (4000, 3000))
    [jpg] = C.image_format([src], ".jpg")
    assert jpg.suffix == ".jpg" and Image.open(jpg).format == "JPEG"
    [small] = C.image_resize([src], "1920")
    assert max(Image.open(small).size) == 1920 and "1920px" in small.name
    [half] = C.image_resize([src], "50%")
    assert Image.open(half).size == (2000, 1500)
    [ico] = C.image_format([src], ".ico")
    assert Image.open(ico).format == "ICO"


def test_image_compress_hits_target(tmp_path):
    import numpy as np
    noise = (np.random.default_rng(1).random((1500, 2000, 3)) * 255).astype("uint8")
    src = tmp_path / "шум.png"
    Image.fromarray(noise).save(src)
    [out] = C.image_compress([src], 200 * 1024)
    assert out.stat().st_size <= 200 * 1024 and out.suffix == ".jpg"


def test_image_with_alpha_compresses_to_webp(tmp_path):
    src = img(tmp_path / "logo.png", (600, 600), (0, 0, 0, 0), "RGBA")
    [out] = C.image_compress([src], 50 * 1024)
    assert out.suffix == ".webp"


def test_rotate_and_trim(tmp_path):
    src = img(tmp_path / "w.png", (300, 100))
    [r] = C.image_rotate([src], 90)
    assert Image.open(r).size == (100, 300)
    canvas = Image.new("RGB", (400, 300), "white")
    canvas.paste(Image.new("RGB", (100, 50), "black"), (150, 120))
    canvas.save(tmp_path / "скрин.png")
    [t] = C.image_trim([tmp_path / "скрин.png"])
    assert Image.open(t).size == (100, 50)


def test_images_to_one_pdf_in_order(tmp_path):
    files = [img(tmp_path / f"стр {i}.jpg") for i in (10, 2, 1)]
    [pdf] = C.images_to_pdf(files)
    from pypdf import PdfReader
    assert len(PdfReader(str(pdf)).pages) == 3 and pdf.name.startswith("стр 1")


def test_remove_background_with_fake_model(tmp_path):
    import numpy as np

    class Fake:
        def get_inputs(self):
            return [type("I", (), {"name": "x"})()]

        def run(self, _, feed):
            m = np.zeros((1, 1, 320, 320), np.float32)
            m[..., 80:240, 80:240] = 1
            return [m]
    [out] = C.remove_background([img(tmp_path / "кот.jpg", (640, 640))], tmp_path, session=Fake())
    im = Image.open(out)
    assert out.suffix == ".png" and im.mode == "RGBA"
    assert im.getpixel((5, 5))[3] < 10 and im.getpixel((320, 320))[3] > 245


# ------------------------------------------------------------------ PDF
def make_pdf(path: Path, pages: int) -> Path:
    ims = [Image.new("RGB", (600, 800), (i * 40 % 255, 100, 150)) for i in range(pages)]
    ims[0].save(path, "PDF", save_all=True, append_images=ims[1:])
    return path


def test_pdf_merge_split_rotate_images(tmp_path):
    from pypdf import PdfReader
    a, b = make_pdf(tmp_path / "a 2.pdf", 2), make_pdf(tmp_path / "a 10.pdf", 3)
    [merged] = C.pdf_merge([b, a])
    assert len(PdfReader(str(merged)).pages) == 5 and merged.name.startswith("a 2")
    parts = C.pdf_split([merged])
    assert len(parts) == 5 and parts[0].parent.name == "a 2 (+1) (страницы)"
    [rot] = C.pdf_rotate([a])
    assert PdfReader(str(rot)).pages[0].get("/Rotate") == 90
    pics = C.pdf_to_images([a], dpi=50)
    assert len(pics) == 2 and Image.open(pics[0]).size[0] > 100


def test_pdf_text_scan_says_so(tmp_path):
    with pytest.raises(C.ConvertError, match="скан"):
        C.pdf_text_file([make_pdf(tmp_path / "скан.pdf", 1)])


def test_pdf_compress_shrinks_big_images(tmp_path):
    import numpy as np
    noise = (np.random.default_rng(2).random((2400, 1800, 3)) * 255).astype("uint8")
    src = tmp_path / "большой.pdf"
    Image.fromarray(noise).save(src, "PDF", resolution=300)
    [out] = C.pdf_compress([src])
    assert out.stat().st_size < src.stat().st_size * 0.7


# ------------------------------------------------------------------ архивы, хэш, имена
def test_zip_unzip_sha(tmp_path):
    a = tmp_path / "a.txt"
    a.write_text("привет")
    b = tmp_path / "b.txt"
    b.write_text("мир")
    [z] = C.to_zip([a, b])
    assert z.name == f"{tmp_path.name}.zip" and sorted(zipfile.ZipFile(z).namelist()) == ["a.txt", "b.txt"]
    [folder] = C.unpack([z])
    assert (folder / "a.txt").read_text() == "привет"
    [(_, h)] = C.sha256([a])
    import hashlib
    assert h == hashlib.sha256("привет".encode()).hexdigest()


def test_unzip_refuses_path_traversal(tmp_path):
    z = tmp_path / "злой.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("../../evil.txt", "x")
    with pytest.raises(C.ConvertError, match="подозрительные"):
        C.unpack([z])


def test_rename_with_numbers(tmp_path):
    files = [tmp_path / n for n in ("IMG_10.jpg", "IMG_2.jpg", "IMG_1.png")]
    for f in files:
        f.write_bytes(b"x")
    out = C.rename(files, "Отпуск {n}")
    assert [p.name for p in out] == ["Отпуск 01.png", "Отпуск 02.jpg", "Отпуск 03.jpg"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Отпуск 01.png", "Отпуск 02.jpg", "Отпуск 03.jpg"]
    with pytest.raises(C.ConvertError):
        C.rename(out, "плохо/имя")


# ------------------------------------------------------------------ меню
def test_menu_depends_on_files():
    labels = lambda items: [i.label for i in items]  # noqa: E731
    assert "Убрать фон" in labels(M.menu_for([Path("a.jpg")]))
    one_video = labels(M.menu_for([Path("a.mp4")]))
    assert "Склеить" not in one_video and "Сжать" in one_video
    assert "Склеить" in labels(M.menu_for([Path("a.mp4"), Path("b.mp4")]))
    assert "Склеить" in labels(M.menu_for([Path("a.pdf"), Path("b.pdf")]))
    mixed = labels(M.menu_for([Path("a.jpg"), Path("b.mp4")]))
    assert mixed == ["SHA256", "В ZIP", "Переименовать"]
    for files in ([Path("a.jpg")], [Path("a.mp4"), Path("b.mp4")], [Path("a.pdf")]):
        assert len(M.menu_for(files)) <= 8
    assert M.find(M.menu_for([Path("a.mp4")]), "vid.compress:10").label == "до 10 МБ"
    assert M.needs_ffmpeg("vid.gif") and not M.needs_ffmpeg("av.srt") and M.needs_whisper("av.srt")


def test_run_dispatches(tmp_path):
    src = img(tmp_path / "x.png")
    [out] = M.run("img.format:.webp", [src], M.Ctx(tmp_path, tmp_path))
    assert out.suffix == ".webp"
    with pytest.raises(C.ConvertError, match="ffmpeg"):
        M.run("vid.gif", [src], M.Ctx(tmp_path, tmp_path))


# ------------------------------------------------------------------ время и команды ffmpeg
def test_parse_range():
    assert media.parse_range("с 1:20 до 2:05") == (80, 125)
    assert media.parse_range("1:20-2:05") == (80, 125)
    assert media.parse_range("до 0:30") == (0, 30)
    assert media.parse_range("первые 30 секунд") == (0, 30)
    assert media.parse_range("последние 10 сек", 100) == (90, 100)
    assert media.parse_range("с 1:00:00 до 1:00:30", 4000) == (3600, 3630)
    with pytest.raises(C.ConvertError):
        media.parse_range("с 2:00 до 1:00")


def test_bitrate_fits_target():
    vb = media.video_bitrate(10 << 20, 60, True)
    assert 1100 < vb < 1300                    # 10 МБ на минуту — около 1,2 Мбит/с видео
    args = media.cmd_compress(Path("a.mp4"), Path("b.mp4"), media.Info(60, "h264", "aac", 1920, 1080), 10 << 20)
    assert "scale=-2:720" in args and f"{vb}k" in args


def test_mp4_copy_when_already_h264():
    args = media.cmd_mp4(Path("a.mkv"), Path("a.mp4"), media.Info(10, "h264", "aac"))
    assert "copy" in args and "libx264" not in args
    args = media.cmd_mp4(Path("a.vob"), Path("a.mp4"), media.Info(10, "mpeg2video", "ac3"))
    assert "libx264" in args and "yadif" in args


def test_concat_list_escapes_quotes():
    assert media.concat_list([Path("/v/it's.mp4")]) == "file '/v/it'\\''s.mp4'\n"


# ------------------------------------------------------------------ настоящий ffmpeg
def make_video(path: Path, seconds=3, size="320x240", codec=("-c:v", "libx264"), audio=True) -> Path:
    a = ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"] if audio else []
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size={size}:rate=25",
                    *a, *codec, *(["-c:a", "aac"] if audio and path.suffix != ".vob" else ["-c:a", "mp2"] if audio else []),
                    "-shortest", str(path)], check=True)
    return path


@pytest.fixture
def runner():
    if not HAS_FFMPEG:
        pytest.skip("нет ffmpeg")
    seen = []
    r = media.Runner(shutil.which("ffmpeg"), shutil.which("ffprobe"), progress=lambda f, s: seen.append(f))
    r.seen = seen
    return r


def test_video_ops(tmp_path, runner):
    v = make_video(tmp_path / "клип.mkv", 4)
    [mp4] = runner.to_mp4([v])
    assert mp4.suffix == ".mp4" and runner.info(mp4).vcodec == "h264"
    [cut] = runner.cut([mp4], "с 0:01 до 0:03")
    assert 1.7 < runner.info(cut).duration < 2.4
    [gif] = runner.gif([mp4])
    assert gif.read_bytes()[:3] == b"GIF"
    [mp3] = runner.audio([mp4], ".mp3")
    assert runner.info(mp3).acodec == "mp3"
    [mute] = runner.mute([mp4])
    assert runner.info(mute).acodec == ""
    [norm] = runner.audio([mp3], ".mp3", normalize=True)
    assert "громкость" in norm.name


def test_compress_to_target(tmp_path, runner):
    v = make_video(tmp_path / "big.mp4", 6, "1280x720", ("-c:v", "libx264", "-crf", "5"))
    target = 200 * 1024
    [out] = runner.compress([v], target)
    assert out.stat().st_size <= target * 1.05 and runner.seen and max(runner.seen) <= 1.0


def test_concat_same_format_no_reencode(tmp_path, runner):
    parts = [make_video(tmp_path / f"часть {i}.mp4", 2) for i in (2, 1, 10)]
    [out] = runner.concat(parts, tmp_path / "w")
    assert out.name.startswith("часть 1") and 5.5 < runner.info(out).duration < 6.6


def test_concat_different_formats(tmp_path, runner):
    a = make_video(tmp_path / "a.mp4", 2, "320x240")
    b = make_video(tmp_path / "b.mkv", 2, "640x360", audio=False)
    [out] = runner.concat([a, b], tmp_path / "w")
    info = runner.info(out)
    assert info.vcodec == "h264" and info.acodec == "aac" and 3.6 < info.duration < 4.6


def test_concat_dvd_vobs(tmp_path, runner):
    parts = [make_video(tmp_path / f"VTS_01_{i}.VOB".replace(".VOB", ".vob"), 2,
                        codec=("-c:v", "mpeg2video", "-f", "vob")) for i in (1, 2)]
    [out] = runner.concat(parts, tmp_path / "w")
    assert out.suffix == ".mp4" and runner.info(out).vcodec == "h264"


def test_stop_removes_half_done_file(tmp_path, runner):
    v = make_video(tmp_path / "a.mp4", 3)
    runner.cancel.set()
    with pytest.raises(C.ConvertError, match="Остановлено"):
        runner.gif([v])
    assert not list(tmp_path.glob("*.gif"))


# ------------------------------------------------------------------ субтитры (Whisper-заглушка)
def test_srt_from_segments(tmp_path):
    class Seg:
        def __init__(self, a, b, t):
            self.start, self.end, self.text = a, b, t

    class Model:
        def transcribe(self, path, **k):
            return iter([Seg(0, 1.5, " Привет"), Seg(61.2, 63, "мир ")]), type("I", (), {"duration": 63})()
    f = tmp_path / "видео.mp4"
    f.write_bytes(b"x")
    [srt] = media.transcribe([f], Model(), srt=True)
    text = srt.read_text(encoding="utf-8-sig")
    assert "00:00:00,000 --> 00:00:01,500\nПривет" in text and "00:01:01,200 --> 00:01:03,000\nмир" in text


# ------------------------------------------------------------------ круговое меню
def test_radial_menu_navigation():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from capri.ui.radial import RadialMenu
    r = RadialMenu()
    got = []
    r.picked.connect(got.append)
    items = M.menu_for([Path("a.mp4"), Path("b.mp4")])
    r.show_at(items, "2 видео")
    r._anim.stop()
    r._zoom(1.0)
    assert r.index_at(r.center()) == -2
    assert r.index_at(r.sector_center(0)) == 0 and r.index_at(r.sector_center(3)) == 3
    r.choose(1)                                       # «Сжать» → своё кольцо
    assert r.stack[-1][0] == "Сжать" and r.isVisible()
    r.back()
    assert len(r.stack) == 1
    r.choose(0)                                       # «В MP4»
    assert got == ["vid.mp4"] and not r.isVisible()
    app.processEvents()


def test_chat_commands_open_converter():
    import copy

    from capri.agent import Agent
    from capri.applist import AppIndex
    from capri.config import DEFAULTS

    class Sys:
        def run(self, cmd):
            return {}
    a = Agent(copy.deepcopy(DEFAULTS), llm=object(), claude=lambda t, c: "C", system=Sys(), apps=AppIndex(Path("/x")))
    for phrase in ("конвертер", "открой конвертер", "сконвертируй", "сожми эти видео", "склей файлы"):
        r = a.handle(phrase)
        assert r.actions and r.actions[0].kind == "convert", phrase


def test_convert_ui_runs_job_and_shows_result(tmp_path):
    import time as _t

    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from capri.convertui import ConvertUI

    class Agent:
        def _workdir(self):
            return tmp_path

    class App:
        text = None
        cfg = {}
        agent = Agent()
        voice = None
    ui = ConvertUI(App())
    src = img(tmp_path / "фото.png", (2000, 1000))
    ui.open_for([src])
    assert ui.radial.isVisible() and ui.radial.stack[0][0] == "фото.png"
    ui.radial.close_menu()
    ui.start("img.resize:50%")
    t0 = _t.monotonic()
    while ui.jobs[-1].isRunning() or "Готово" not in ui.popup.body.toPlainText():
        app.processEvents()
        assert _t.monotonic() - t0 < 10
    assert (tmp_path / "фото (50%).png").exists() and "фото (50%).png" in ui.popup.body.toPlainText()
