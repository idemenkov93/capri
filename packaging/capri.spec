# -*- mode: python ; coding: utf-8 -*-
# Сборка Capri.exe:  pyinstaller --noconfirm --clean packaging/capri.spec --distpath dist --workpath build
#
# CAPRI_PERSONAL=1 — личная сборка: внутрь кладутся твои анимации из assets/custom (только для себя, не публиковать).
# Без неё (GitHub Actions) — публичная сборка без чужих картинок: внешность добавляется скинами.
import os

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
PERSONAL = os.environ.get("CAPRI_PERSONAL") == "1"


def safe(fn, *a, **k):
    try:
        return fn(*a, **k)
    except Exception:  # пакета нет (например, Google не установлен) — просто пропускаем
        return []


datas = [(os.path.join(ROOT, "config.example.yaml"), "."), (os.path.join(ROOT, "docs"), "docs")]
custom = os.path.join(ROOT, "assets", "custom")
if PERSONAL and os.path.isdir(custom):
    datas.append((custom, os.path.join("assets", "custom")))
binaries = []
hidden = ["webbrowser", "keyring.backends.Windows"]

for pkg in ("faster_whisper", "vosk", "piper", "uiautomation", "googleapiclient", "tzdata", "certifi",
            "_sounddevice_data", "edge_tts", "recurring_ical_events"):
    datas += safe(collect_data_files, pkg)
for pkg in ("ctranslate2", "vosk", "piper", "onnxruntime", "uiautomation", "_sounddevice_data"):
    binaries += safe(collect_dynamic_libs, pkg)
for pkg in ("capri", "dxcam", "winrt", "pycaw", "comtypes", "screen_brightness_control", "send2trash", "win32ctypes", "keyring",
            "uiautomation", "google_auth_oauthlib", "icalendar"):
    hidden += safe(collect_submodules, pkg)

a = Analysis(
    [os.path.join(SPECPATH, "capri_entry.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "torch", "tensorflow", "hf_xet", "scipy", "pandas",
              "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore", "PySide6.QtQuick3D"],
    noarchive=False,
)


# --- поменьше установщик: выкидываем то, что Капри не использует -------------------------------------------
GOOGLE_DOCS = ("gmail.v1.json", "calendar.v3.json", "tasks.v1.json")   # описания API: почта, календарь, задачи
ESPEAK_LANGS = ("ru_dict", "en_dict", "de_dict", "he_dict")            # словари офлайн-голоса: только наши языки


def _drop(name: str) -> bool:
    n = name.replace("\\", "/").lower()
    if "googleapiclient/discovery_cache/documents/" in n:
        return n.rsplit("/", 1)[-1] not in GOOGLE_DOCS                    # было 100+ МБ описаний всех API Google
    if "/espeak-ng-data/" in n and n.endswith("_dict"):
        return n.rsplit("/", 1)[-1] not in ESPEAK_LANGS
    if n.endswith(("nakdimon.onnx", "/tashkeel/model.onnx")):            # огласовка иврита/арабского для piper
        return not os.environ.get("CAPRI_KEEP_HEBREW")
    base = n.rsplit("/", 1)[-1]
    return base in ("opengl32sw.dll",) or base.startswith(("qt6pdf", "qt6quick", "qt6qml", "qt6virtualkeyboard",
                                                           "qt6designer", "qt6webengine", "qt6charts", "qt63d"))


a.binaries = [x for x in a.binaries if not _drop(x[0])]
a.datas = [x for x in a.datas if not _drop(x[0])]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Capri",
    console=False,                          # без чёрного окна консоли
    icon=os.path.join(SPECPATH, "capri.ico"),
    upx=False,                              # UPX сжатие антивирусы не любят
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Capri")
