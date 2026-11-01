# Run with: python -m PyInstaller --noconfirm TATClean.spec
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

root = Path(SPECPATH)
notices = collect_data_files("tatclean", includes=["ui/assets/*.svg"])
for package in ("PyQt6", "PyQt6-Qt6", "PyQt6-sip", "psutil", "pyinstaller"):
    notices.extend(copy_metadata(package))
python_license = Path(sys.base_prefix) / "LICENSE.txt"
if python_license.is_file():
    notices.append((str(python_license), "third_party/Python"))
analysis = Analysis(
    [str(root / "run.py")],
    pathex=[str(root / "src")],
    binaries=[],
    datas=notices,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "PyQt5", "PySide2", "PySide6", "pytest", "pytestqt"],
    noarchive=False,
)
archive = PYZ(analysis.pure)
executable = EXE(
    archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="TATClean",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=str(root / "assets" / "app.ico"),
    version=str(root / "assets" / "version_info.txt"),
    manifest=str(root / "assets" / "app.manifest"),
    uac_admin=False,
    contents_directory="_internal",
)
distribution = COLLECT(
    executable, analysis.binaries, analysis.datas,
    strip=False, upx=False, name="TATClean",
)
