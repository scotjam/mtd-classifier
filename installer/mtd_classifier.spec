# PyInstaller spec: one-dir windowed build of the MTD Classifier GUI.
# Build from the repo root:  build\venv\Scripts\pyinstaller installer\mtd_classifier.spec
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = collect_data_files("mtd_classifier")   # bridging templates (*.xlsx)
datas += collect_data_files("ofxtools")        # OFX schema data

hiddenimports = collect_submodules("mtd_classifier") + [
    "sqlalchemy.dialects.sqlite",
    "PIL.ImageTk",
]

a = Analysis(
    ["launch.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["pytest", "xlwt"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MTD Classifier",
    icon="icon.ico",
    console=False,
    upx=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    upx=False,
    name="mtd-classifier",
)
