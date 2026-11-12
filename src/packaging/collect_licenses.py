"""Writes CREDITS.txt + licenses/ for everything that ends up in the build.

Run by build.ps1 with the *build venv's* Python, after PyInstaller:

    python collect_licenses.py <manifest.json> <app_dir> <licenses_dir> <project_dir>

<licenses_dir> is src/packaging/licenses (license texts kept in git: GPL/LGPL,
sfizz, FFmpeg).

<manifest.json> is written by Orchestrium.spec (every source file PyInstaller
bundled). A pip distribution counts as bundled only if one of its installed
files is in that list, so build-only packages (pip, tensorflow, ...) are never
credited by mistake -- and a new dependency is picked up automatically.

Output in <app_dir>:
  CREDITS.txt          human-readable credits: samples, main components (who
                       made them and what they do), every other bundled library
  LICENSE.txt          Orchestrium's own license (GPL-3.0)
  licenses/<name>/     full license texts, one folder per component

Exits non-zero if any bundled component ends up without a license text.
"""
import json
import re
import shutil
import sys
import textwrap
from importlib import metadata
from pathlib import Path

HERE = Path(__file__).resolve().parent

LICENSE_FILE = re.compile(
    r"^(licen[cs]e|copying|notice|third.?party.?notices?|authors|copyright)", re.I)
SKIP_DIRS = {"tests", "test", "testing", "docs", "examples"}

# Main components: shown first, with who made them and what Orchestrium uses them for.
# key = pip distribution name (lowercase), or a non-pip component handled below.
MAIN = {
    "basic-pitch": ("Basic Pitch", "Spotify", "Apache-2.0",
                    "transcribes your recording to notes (audio-to-MIDI)"),
    "onnxruntime": ("ONNX Runtime", "Microsoft", "MIT",
                    "runs Basic Pitch's neural network"),
    "sfizz": ("sfizz (sfizz_render)", "sfizz contributors", "BSD-2-Clause",
              "plays the orchestra parts through the VPO samples"),
    "ffmpeg": ("FFmpeg", "the FFmpeg developers", "LGPL-3.0-or-later",
               "reads and writes audio files (used via pydub)"),
    "pyside6": ("Qt 6 / PySide6", "The Qt Company", "LGPL-3.0-only",
                "the desktop user interface"),
    "python": ("Python", "Python Software Foundation", "PSF-2.0",
               "the language Orchestrium is written in"),
    "mido": ("Mido", "Ole Martin Bjørndalen and contributors", "MIT",
             "reads and writes MIDI files"),
    "pretty_midi": ("pretty_midi", "Colin Raffel and contributors", "MIT",
                    "reads note timings from MIDI files"),
    "pydub": ("pydub", "James Robert and contributors", "MIT",
              "audio editing, mixing and silence detection"),
    "numpy": ("NumPy", "NumPy developers", "BSD-3-Clause",
              "numerical computing (includes OpenBLAS, BSD-3-Clause, and the GCC Fortran "
              "runtime, GPL-3.0 with GCC Runtime Library Exception)"),
    "scipy": ("SciPy", "SciPy developers", "BSD-3-Clause",
              "tempo-curve fitting (PCHIP, Theil-Sen); includes OpenBLAS and the GCC "
              "Fortran runtime like NumPy"),
    "librosa": ("librosa", "librosa developers", "ISC", "audio analysis (used by Basic Pitch)"),
}
# Qt's license is stated once under "Qt 6 / PySide6"; these are its pip pieces.
QT_DISTS = {"pyside6", "pyside6_essentials", "pyside6_addons", "shiboken6"}

# Metadata is often vague ("BSD License", "LGPL") -- precise SPDX ids, checked by hand.
LICENSE_OVERRIDES = {
    "basic-pitch": "Apache-2.0", "llvmlite": "BSD-2-Clause", "numba": "BSD-2-Clause",
    "numpy": "BSD-3-Clause", "scipy": "BSD-3-Clause", "onnxruntime": "MIT",
    "packaging": "Apache-2.0 OR BSD-2-Clause", "setuptools": "MIT", "wheel": "MIT",
    "soundfile": "BSD-3-Clause", "pyinstaller": "GPL-2.0-or-later WITH Bootloader-exception",
    "cffi": "MIT-0",
    # Packages that bundle a native library under its own license:
    "soundfile": "BSD-3-Clause (bundles libsndfile, LGPL-2.1-or-later)",
    "soxr": "LGPL-2.1-or-later (bundles libsoxr, LGPL-2.1-or-later)",
}
URL_OVERRIDES = {"basic-pitch": "https://github.com/spotify/basic-pitch"}
# Credited in section 4 instead of the library list.
TOOL_DISTS = {"pyinstaller"}


def norm(name):
    return name.lower().replace("-", "_")


def license_expression(dist):
    name = dist.metadata["Name"].lower()
    if name in LICENSE_OVERRIDES:
        return LICENSE_OVERRIDES[name]
    md = dist.metadata
    for key in ("License-Expression", "License"):
        value = (md.get(key) or "").strip()
        if value and value.upper() != "UNKNOWN" and len(value) < 60 and "\n" not in value:
            return value
    classifiers = [c.split("::")[-1].strip() for c in (md.get_all("Classifier") or [])
                   if c.startswith("License ::")]
    return ", ".join(classifiers) or "see license files"


def homepage(dist):
    md = dist.metadata
    if md["Name"].lower() in URL_OVERRIDES:
        return URL_OVERRIDES[md["Name"].lower()]
    urls = md.get_all("Project-URL") or []
    for label in ("homepage", "home", "source", "repository", "documentation"):
        for url in urls:
            k, _, v = url.partition(",")
            if k.strip().lower() == label:
                return v.strip()
    return (md.get("Home-page") or "").strip() or (urls[0].partition(",")[2].strip() if urls else "")


def license_files(dist):
    """(source path, name to save as) for every license-ish file the distribution ships,
    in its .dist-info folder or anywhere in its package (e.g. numpy/random/LICENSE.md,
    onnxruntime/ThirdPartyNotices.txt)."""
    found = []
    for f in dist.files or []:
        parts = Path(str(f)).parts
        if not LICENSE_FILE.match(parts[-1]) or SKIP_DIRS & {p.lower() for p in parts[:-1]}:
            continue
        path = Path(dist.locate_file(f))
        if not path.is_file() or path.suffix in (".py", ".pyc", ".pyi"):
            continue
        in_dist_info = any(p.endswith(".dist-info") for p in parts)
        # dist-info/licenses/LICENSE -> LICENSE ; numpy/random/LICENSE.md -> numpy_random_LICENSE.md
        save_as = parts[-1] if in_dist_info else "_".join(parts)
        found.append((path, save_as))
    return found


def copy_into(folder, sources):
    folder.mkdir(parents=True, exist_ok=True)
    for src, save_as in sources:
        shutil.copy2(src, folder / save_as)


def main():
    manifest, app_dir, licenses_dir, project_dir = map(Path, sys.argv[1:5])
    bundled = {str(Path(p).resolve()).lower() for p in json.loads(manifest.read_text("utf-8"))}
    lic_dir = app_dir / "licenses"
    shutil.rmtree(lic_dir, ignore_errors=True)
    lic_dir.mkdir(parents=True)
    old_notices = app_dir / "THIRD_PARTY_NOTICES.txt"  # name used by earlier builds
    if old_notices.exists():
        old_notices.unlink()

    missing = []
    gpl3 = licenses_dir / "GPL-3.0.txt"
    lgpl3 = licenses_dir / "LGPL-3.0.txt"
    for f in (gpl3, lgpl3):
        if not f.is_file():
            missing.append(f"{f} (get it from https://www.gnu.org/licenses/)")

    # --- pip distributions actually in the bundle ----------------------------
    pip_rows = {}  # norm name -> (display name, version, license, url)
    for dist in sorted(metadata.distributions(), key=lambda d: d.metadata["Name"].lower()):
        if not any(str(Path(dist.locate_file(f)).resolve()).lower() in bundled
                   for f in dist.files or []):
            continue
        name = dist.metadata["Name"]
        files = license_files(dist)
        if norm(name) in QT_DISTS:
            # PySide6 6.7.x wheels ship only a stub about the *commercial* license and
            # no LGPL text at all -- supply it (LGPL-3.0 incorporates GPL-3.0).
            files = [(p, n) for p, n in files if "commercial" not in n.lower()]
            files += [(f, f.name) for f in (lgpl3, gpl3) if f.is_file()]
            folder = lic_dir / "Qt-PySide6"
        else:
            folder = lic_dir / name
            if not files:
                missing.append(name)
        copy_into(folder, files)
        pip_rows[norm(name)] = (name, dist.version, license_expression(dist), homepage(dist))

    # --- non-pip components --------------------------------------------------
    def vendor_copy(folder_name, files):
        ok = [(Path(f), Path(f).name) for f in files if Path(f).is_file()]
        missing.extend(f"{folder_name} ({f})" for f in files if not Path(f).is_file())
        copy_into(lic_dir / folder_name, ok)

    def write_note(folder_name, text):
        (lic_dir / folder_name).mkdir(parents=True, exist_ok=True)
        (lic_dir / folder_name / "NOTE.txt").write_text(textwrap.dedent(text).strip() + "\n",
                                                        encoding="utf-8")

    vendor_copy("Python", [Path(sys.base_prefix) / "LICENSE.txt"])
    vendor_copy("FFmpeg", [licenses_dir / "ffmpeg" / "LICENSE.txt",
                           licenses_dir / "ffmpeg" / "SOURCE.txt", gpl3])
    vendor_copy("sfizz", [licenses_dir / "sfizz" / "LICENSE.txt"])
    tk_terms = next((p for p in sorted((app_dir / "_internal").rglob("license.terms"))
                     if p.parent.name.lower().lstrip("_").startswith(("tcl", "tk"))), None)
    vendor_copy("Tcl-Tk", [tk_terms or app_dir / "_internal" / "_tk_data" / "license.terms"])
    write_note("Qt-PySide6", """
        Qt 6 and PySide6 are used under the GNU Lesser General Public License v3
        (LGPL-3.0.txt; it builds on GPL-3.0.txt). They are shipped as separate,
        replaceable DLL/PYD files in the _internal folder, so you may swap in your own
        compatible builds. Source code: https://code.qt.io/ and
        https://download.qt.io/official_releases/QtForPython/
        Qt itself includes third-party components (FreeType, HarfBuzz, libpng, zlib,
        PCRE2 and others) under their own permissive licenses, listed at
        https://doc.qt.io/qt-6/licenses-used-in-qt.html""")
    write_note("Microsoft-Visual-C++-Runtime", """
        msvcp140*.dll, vcruntime140*.dll and concrt140.dll (Microsoft Visual C++
        Runtime) are redistributed app-locally as permitted by the Visual Studio
        redistributable terms.""")
    vendor_copy("Virtual-Playing-Orchestra", [HERE / "VPO_ATTRIBUTION.txt"])
    vpo_license = project_dir / "VPO" / "Documentation" / "license.htm"
    if vpo_license.is_file():
        shutil.copy2(vpo_license, lic_dir / "Virtual-Playing-Orchestra" / "license.htm")

    project_license = project_dir / "LICENSE"
    if project_license.is_file():
        shutil.copy2(project_license, app_dir / "LICENSE.txt")
    else:
        missing.append("Orchestrium's own LICENSE")

    # --- CREDITS.txt -----------------------------------------------------------
    out = []
    w = out.append
    w("ORCHESTRIUM - CREDITS AND LICENSES")
    w("==================================")
    w("")
    w("Orchestrium by Bryan Wang is free software, released under the GNU General")
    w("Public License v3 (see LICENSE.txt). Source code:")
    w("    https://github.com/Bryan1Wang/Orchestrium")
    w("")
    w("Orchestrium stands on the work of many people. Thank you to everyone below.")
    w("Full license texts are in the licenses\\ folder next to this file, one")
    w("subfolder per component.")
    w("")
    w("")
    w("1. ORCHESTRA SAMPLES")
    w("--------------------")
    w("")
    w((HERE / "VPO_ATTRIBUTION.txt").read_text("utf-8").split("\n", 3)[3].strip())
    w("")
    w("(If you installed Orchestrium without the sample library, VPO is not included;")
    w("the credits above apply to the copy you download yourself.)")
    w("")
    w("")
    w("2. MAIN COMPONENTS")
    w("------------------")
    w("")
    versions = {"sfizz": "1.2.2", "ffmpeg": "8.1", "python": sys.version.split()[0]}
    for key, (title, by, lic, role) in MAIN.items():
        if norm(key) in pip_rows:
            version, url = pip_rows[norm(key)][1], pip_rows[norm(key)][3]
        elif key in versions:
            version = versions[key]
            url = {"sfizz": "https://sfz.tools/sfizz", "ffmpeg": "https://ffmpeg.org",
                   "python": "https://www.python.org"}.get(key, "")
        else:
            continue  # not in this build (e.g. a dependency was dropped)
        if key == "pyside6":
            url = "https://www.qt.io/qt-for-python"
        w(f"  {title} {version}".rstrip())
        w(f"      by {by} - {lic}")
        for line in textwrap.wrap(role, 72):
            w(f"      {line}")
        if url:
            w(f"      {url}")
        w("")

    w("")
    w("3. OTHER LIBRARIES INCLUDED IN THIS BUILD")
    w("-----------------------------------------")
    w("")
    main_keys = {norm(k) for k in MAIN}
    others = [r for k, r in pip_rows.items()
              if k not in main_keys and k not in QT_DISTS and k not in TOOL_DISTS]
    width = max(len(r[0]) for r in others) + 2
    for name, version, lic, url in others:
        w(f"  {name:<{width}}{version:<14}{lic}")
        if url:
            w(f"  {'':<{width}}{url}")
    w("")
    w("")
    w("4. RUNTIME AND BUILD TOOLS")
    w("--------------------------")
    w("")
    w("  Tcl/Tk 8.6 (loading splash screen) - Tcl/Tk license (BSD-style) - https://www.tcl.tk")
    w("  Microsoft Visual C++ Runtime - redistributed under the Visual Studio")
    w("      redistributable terms")
    w("  PyInstaller (the launcher in Orchestrium.exe and its runtime hooks) -")
    w("      GPL-2.0-or-later with the PyInstaller bootloader exception - https://pyinstaller.org")
    w("")
    (app_dir / "CREDITS.txt").write_text("\n".join(out) + "\n", encoding="utf-8")

    print(f"Credited {len(pip_rows)} pip packages + samples/tools -> {app_dir / 'CREDITS.txt'}")
    empty = [d.name for d in lic_dir.iterdir() if d.is_dir() and not any(d.iterdir())]
    missing += [f"{n} (empty licenses folder)" for n in empty]
    if missing:
        print("MISSING LICENSE TEXTS:\n  " + "\n  ".join(missing))
        sys.exit(1)


if __name__ == "__main__":
    main()
