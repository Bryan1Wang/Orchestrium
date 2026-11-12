# PyInstaller spec for Orchestrium. Don't run this directly -- use build.ps1,
# which sets up the build venv, copies bin/ + assets/ + licenses next to the
# exe, and optionally runs a smoke test and builds the installer.
#
# Env vars (set by build.ps1):
#   ORCH_ENTRY     entry script, relative to src/ (default: orchestrium.py)
#   ORCH_NAME      exe/folder name (default: Orchestrium)
#   ORCH_CONSOLE   "1" = console build, shows tracebacks (default: windowed)
#   ORCH_MANIFEST  path to write the list of bundled source files (for licenses)
import json
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

SRC = Path(SPECPATH).parent          # src/
PROJECT = SRC.parent                 # outer project folder (assets/, bin/, VPO/)

entry = os.environ.get("ORCH_ENTRY", "orchestrium.py")
app_name = os.environ.get("ORCH_NAME", "Orchestrium")
console = os.environ.get("ORCH_CONSOLE") == "1"

datas = collect_data_files("basic_pitch")  # Basic Pitch model files (tiny)
# Deliberately NOT collecting pretty_midi's data: it's only TimGM6mb.sf2, a
# GPL-2 soundfont used by pretty_midi's fluidsynth playback, which we never call.

binaries = collect_dynamic_libs("onnxruntime")

# basic-pitch's other optional backends -- we only ship ONNX.
excludes = [
    "tensorflow", "tensorflow_core", "tensorflow_intel", "keras", "tflite_runtime",
    "coremltools", "torch", "IPython", "notebook", "matplotlib",
]

a = Analysis(
    [str(SRC / entry)],
    # slicer_gui imports meta_track/track_crop as bare modules (runtime sys.path hack)
    pathex=[str(SRC), str(SRC / "utils")],
    binaries=binaries,
    datas=datas,
    hiddenimports=["onnxruntime", "utils.slicer_gui", "meta_track", "track_crop"],
    excludes=excludes,
    noarchive=False,
)

# --- MSVC runtime fix --------------------------------------------------------
# PyInstaller copies the MSVC runtime from the Python install (3.11.2 ships
# 14.27, from 2020), and PySide6/shiboken6 ship their own 14.29 copies, which
# PyInstaller's PySide6 runtime hook puts on the DLL search path before any app
# code runs. onnxruntime >= 1.2x needs 14.4x+: with the old one its native
# module fails with "DLL initialization routine failed", basic_pitch finds no
# backend, and the app dies at startup on `_default_model_type`. Replace every
# copy with the system's (newer, backward compatible; Microsoft permits
# app-local redistribution of these DLLs).
_SYS32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
_VCRT = {"msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
         "vcruntime140.dll", "vcruntime140_1.dll", "concrt140.dll"}
_fixed, _have_root = [], set()
for dest, src, kind in a.binaries:
    name = os.path.basename(dest).lower()
    if name in _VCRT and (_SYS32 / name).exists():
        src = str(_SYS32 / name)
        if os.path.dirname(dest) == "":
            _have_root.add(name)
    _fixed.append((dest, src, kind))
for name in sorted(_VCRT - _have_root):
    if (_SYS32 / name).exists():
        _fixed.append((name, str(_SYS32 / name), "BINARY"))
a.binaries = _fixed

# --- Manifest of bundled files, used by collect_licenses.py -------------------
manifest = os.environ.get("ORCH_MANIFEST")
if manifest:
    files = sorted({str(Path(src).resolve()) for _, src, _ in (a.pure + a.binaries + a.datas) if src})
    Path(manifest).write_text(json.dumps(files, indent=0), encoding="utf-8")

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=app_name,
    console=console,
    icon=str(PROJECT / "assets" / "icon.ico"),
)
coll = COLLECT(exe, a.binaries, a.datas, name=app_name)
