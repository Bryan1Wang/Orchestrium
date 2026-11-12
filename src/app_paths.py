import os
import subprocess
import sys
from pathlib import Path

# True inside the PyInstaller build (see packaging/), False when run from source.
FROZEN = getattr(sys, "frozen", False)


def _project_root() -> Path:
    # Frozen (PyInstaller onedir): <root>/Orchestrium.exe, with bin/, VPO/,
    # assets/ beside it -- the bundled code itself lives in <root>/_internal/,
    # so __file__ no longer points at <root>/src/.
    if FROZEN:
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


PROJECT_ROOT = _project_root()


def _default_output_dir() -> Path:
    # An installed app's own folder (e.g. Program Files) usually isn't writable
    # by an unelevated process, so a frozen build writes under Documents.
    if FROZEN:
        return Path.home() / "Documents" / "Orchestrium" / "data" / "output"
    return PROJECT_ROOT / "data" / "output"


DEFAULT_OUTPUT_DIR = _default_output_dir()


if FROZEN:
    # pydub finds ffmpeg/ffprobe via PATH; users won't have them installed, so
    # the build ships them in bin/.
    os.environ["PATH"] = str(PROJECT_ROOT / "bin") + os.pathsep + os.environ.get("PATH", "")

if FROZEN and sys.platform == "win32":
    # The build is a windowed (no-console) app, so every console child process
    # (ffmpeg/ffprobe via pydub, sfizz_render) would flash its own black console
    # window. pydub doesn't expose creationflags, so default it for every Popen.
    # Patching __init__ (not rebinding subprocess.Popen) also covers modules that
    # already did `from subprocess import Popen`, regardless of import order.
    _original_popen_init = subprocess.Popen.__init__

    def _popen_init_no_window(self, *args, **kwargs):
        if "creationflags" not in kwargs:
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        _original_popen_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = _popen_init_no_window
