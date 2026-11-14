# -*- mode: python ; coding: utf-8 -*-
# Сборка:  pyinstaller --noconfirm --clean packaging/dolmetsch.spec --distpath dist --workpath build
# Модель перевода в сборку не входит: её кладут рядом, в dist/Dolmetsch/models/opus-mt-de-ru.
import os

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))


def safe(fn, *a, **k):
    try:
        return fn(*a, **k)
    except Exception:
        return []


datas, binaries, hidden = [], [], ["keyring.backends.Windows"]
for pkg in ("uiautomation", "certifi"):
    datas += safe(collect_data_files, pkg)
for pkg in ("ctranslate2", "uiautomation", "sentencepiece"):
    binaries += safe(collect_dynamic_libs, pkg)
for pkg in ("dolmetsch", "comtypes", "win32ctypes", "keyring", "uiautomation", "truststore"):
    hidden += safe(collect_submodules, pkg)

a = Analysis(
    [os.path.join(SPECPATH, "entry.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "torch", "transformers", "tensorflow", "scipy", "pandas", "numpy.tests"],
    noarchive=False,
)


def _drop(name: str) -> bool:
    """Части Qt, которые программе не нужны (уменьшают архив)."""
    base = name.replace("\\", "/").lower().rsplit("/", 1)[-1]
    return base in ("opengl32sw.dll",) or base.startswith(("qt6pdf", "qt6quick", "qt6qml", "qt6virtualkeyboard",
                                                           "qt6designer", "qt6webengine", "qt6charts", "qt63d"))


a.binaries = [x for x in a.binaries if not _drop(x[0])]
a.datas = [x for x in a.datas if not _drop(x[0])]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Dolmetsch", console=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Dolmetsch")
