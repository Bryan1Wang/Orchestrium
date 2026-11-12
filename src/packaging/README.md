# Packaging Orchestrium

Builds a standalone Windows app (PyInstaller, one-folder) and an installer
(Inno Setup). Everything is built in a separate folder with its own venv, so
your dev `env\` isn't touched.

## Build

```
src\packaging\build.bat                 # app + installer (VPO bundled as a default-checked component)
src\packaging\build.bat -SmokeTest      # first runs the whole pipeline headlessly in a frozen build
src\packaging\build.bat -NoVpo          # installer without VPO (~1 GB smaller)
src\packaging\build.bat -NoInstaller    # app folder only
src\packaging\build.bat -Version 1.2.0 -Clean
```

Output goes in the project's `build\` folder. Git ignores everything in it
except `.gitkeep`:

| Path | What |
|---|---|
| `dist\Orchestrium\Orchestrium.exe` | the built app; run it from here to test |
| `installer\Orchestrium-Setup-<version>.exe` | the installer |

Use `-SmokeTest` after changing dependencies. A broken bundle often launches
fine and only fails once the pipeline runs.

## One-time setup

- **Inno Setup 6** (for the installer): `winget install --id JRSoftware.InnoSetup -e`.
  Without it, the script still builds the app folder and skips the installer.
- **FFmpeg is downloaded automatically** into `build\vendor\ffmpeg\` on the first
  build. It's the FFmpeg 8.1 **LGPL** shared build from
  https://github.com/BtbN/FFmpeg-Builds/releases, about 77 MB, and it's kept out of
  git because it's large. The script checks the DLL's build flags before using it.
  It must have `--enable-libmp3lame` (pydub exports MP3) and must not have
  `--enable-gpl`. To change version, pass `-FfmpegUrl` or edit the default in
  `build.ps1`, delete `build\vendor\ffmpeg\`, and update
  `licenses\ffmpeg\SOURCE.txt`.
- **License texts are in git**, in `src\packaging\licenses\`:
  - `GPL-3.0.txt` and `LGPL-3.0.txt`, from gnu.org. PySide6's wheels don't include
    the LGPL text, so we supply it.
  - `sfizz\LICENSE.txt`
  - `ffmpeg\LICENSE.txt` and `ffmpeg\SOURCE.txt`, which says where FFmpeg's source is.
- `sfizz_render` itself comes from the project's `bin\`.

## How it works / gotchas

- `app_paths.py` detects the frozen build. `PROJECT_ROOT` becomes the exe's folder,
  so `bin\`, `assets\` and `VPO\` sit beside `Orchestrium.exe`. The default output
  folder moves to `Documents\Orchestrium\data\output`. `bin\` is added to `PATH`
  for ffmpeg. Child processes get `CREATE_NO_WINDOW` so no consoles flash.
- **MSVC runtime:** `Orchestrium.spec` replaces every bundled `msvcp140*.dll` /
  `vcruntime140*.dll` with the system's newer copy. The ones from Python 3.11.2
  and PySide6 are too old for onnxruntime. With them, the app dies at startup on
  `NameError: _default_model_type` in `basic_pitch/__init__.py`.
- `tensorflow` (and the other Basic Pitch backends) are excluded. Only ONNX is shipped.
- `pretty_midi`'s `TimGM6mb.sf2` (GPL-2) is deliberately not bundled. It's unused.

## Licenses

`collect_licenses.py` runs after every build and writes these into the app folder:

- `CREDITS.txt` is the human-readable credits, in four sections:
  1. the VPO samples and each source collection's creator and license
  2. the main components: who made each one, its license, and what Orchestrium uses it for
  3. every other bundled library
  4. runtime and build tools
- `licenses\` holds the full license texts, one folder per component.

The installer adds a Start-menu shortcut to `CREDITS.txt`. It also copies
`VPO_ATTRIBUTION.txt` into the installed `VPO\` folder as `CREDITS.txt`, so
attribution travels with the samples. The library list is generated from what
PyInstaller actually bundled, so new dependencies are picked up automatically.
The build **fails** if any component ends up without a license text. To give a
new major dependency a "who made it / what it does" entry, add it to `MAIN` in
`collect_licenses.py`.

All bundled components are compatible with Orchestrium's GPL-3.0. The main
obligations to keep meeting:

- **GPL-3.0 (Orchestrium):** keep the source public (the GitHub link is in the notices).
- **LGPL-3.0 (Qt/PySide6, FFmpeg):** ship their license texts and keep them as
  replaceable DLLs. The onedir build does this; don't switch to `--onefile`.
- **VPO:** free to redistribute *free of charge* with credit, but some samples
  are CC BY-NC / Sampling Plus (non-commercial). **Never sell an installer that
  bundles VPO.** Build with `-NoVpo` for any commercial distribution.
