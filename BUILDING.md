# Building Orchestrium (quick reference)

## Build the exe and installer

1. Close Orchestrium if it's running from `build\dist\`.
2. Double-click `src\packaging\build.bat`, or run it from a terminal:

   ```
   src\packaging\build.bat
   ```

3. Wait about 5–8 minutes. The output goes to:
   - App: `build\dist\Orchestrium\Orchestrium.exe`
   - Installer: `build\installer\Orchestrium-Setup-<date>.exe`

## Useful options

| Command | When to use it |
|---|---|
| `build.bat -SmokeTest` | After changing dependencies or `requirements.txt`. It runs the whole pipeline in a test build first. |
| `build.bat -Version 1.2.0` | For a release version number (otherwise the date is used). |
| `build.bat -NoVpo` | For a small installer without the VPO samples. |
| `build.bat -NoInstaller` | To build just the app folder, which is faster. |
| `build.bat -Clean` | When a build behaves strangely. It clears PyInstaller's cache. |

## Before releasing

- [ ] Run `build.bat -SmokeTest` and check that it says `PIPELINE OK`.
- [ ] Install the new installer and run one song through it.
- [ ] Keep the installer **free**. VPO's license forbids selling it.

## Setting up on a new PC (one time)

- Install Python 3.11 and Inno Setup (`winget install --id JRSoftware.InnoSetup -e`).
- `VPO\` and `bin\sfizz_render_1.2.2.exe` must be present.
- Everything else is automatic. The first build creates `build\venv` and downloads
  FFmpeg into `build\vendor\`, which takes about 10 minutes plus the download.

## If something breaks

- The build stops with a red `BUILD FAILED: ...` message saying what's missing.
- The build stops on `MISSING LICENSE TEXTS`: a new dependency has no license file.
  See `src\packaging\collect_licenses.py`.
- The app crashes on startup with `_default_model_type`: it's the old MSVC runtime DLLs.
  The fix is in `src\packaging\Orchestrium.spec`. Run with `-Clean`.
- Details on all of this: `src\packaging\README.md`.
