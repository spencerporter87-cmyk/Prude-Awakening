# PyInstaller recipe for PrudeAwakening.exe  (run: pyinstaller packaging/prude.spec)
import os
import re

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
SRC = os.path.join(ROOT, "src")
version = re.search(r'__version__ = "([^"]+)"',
                    open(os.path.join(SRC, "prude", "__init__.py"), encoding="utf-8").read()).group(1)

datas = [(os.path.join(SRC, "prude", "data"), os.path.join("prude", "data")),
         (os.path.join(ROOT, "vendor", "ffmpeg"), "ffmpeg")]
datas += collect_data_files("faster_whisper")
datas += collect_data_files("nudenet")
datas += collect_data_files("rapidocr_onnxruntime")
binaries = collect_dynamic_libs("ctranslate2") + collect_dynamic_libs("onnxruntime")
hidden = collect_submodules("faster_whisper") + collect_submodules("prude") + [
    "tokenizers", "nudenet", "cv2", "rapidocr_onnxruntime", "pyclipper", "shapely", "yaml", "ctranslate2", "onnxruntime", "requests", "numpy"]

a = Analysis(
    [os.path.join(ROOT, "run_app.py")],
    pathex=[SRC],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "matplotlib", "torch", "torchvision", "PyQt5", "PyQt6", "IPython",
              "jupyter", "pandas", "scipy", "PySide6.QtWebEngineCore", "PySide6.Qt3DCore"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="PrudeAwakening",
    console=False,
    icon=os.path.join(SRC, "prude", "data", "icon.ico"),
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="PrudeAwakening", upx=False)
