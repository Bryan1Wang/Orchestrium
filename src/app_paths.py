from pathlib import Path


def _project_root() -> Path:
    # Not currently building a standalone executable. If/when that resumes
    # (e.g. via Nuitka), __file__-based resolution won't hold -- a compiled
    # build's on-disk layout doesn't match <root>/src/<file>.py, so root
    # would need to be derived from sys.executable (or whatever the chosen
    # tool exposes) instead, similar to the commented-out branch below.
    # if getattr(sys, "frozen", False):
    #     return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


PROJECT_ROOT = _project_root()


def _default_output_dir() -> Path:
    # Same caveat as above: a compiled build would likely be installed
    # somewhere an unelevated process can't write to (e.g. Program Files),
    # so the output default would need to move to a user-writable location
    # (e.g. ~/Documents/Orchestrium/data/output) rather than PROJECT_ROOT.
    # if getattr(sys, "frozen", False):
    #     return Path.home() / "Documents" / "Orchestrium" / "data" / "output"
    return PROJECT_ROOT / "data" / "output"


DEFAULT_OUTPUT_DIR = _default_output_dir()
