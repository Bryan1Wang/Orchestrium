"""Headless end-to-end test of a frozen build (built + run by `build.ps1 -SmokeTest`).

Not shipped. Built windowed (no console) like the real app, so it also catches
problems that only show up without a console (no stdout/stdin handles). Mirrors
the real app's import order (pipeline/onnxruntime first, Qt never) and writes
its own log, ending in PIPELINE OK / PIPELINE FAILED.

usage: smoke_test.exe <solo.mid> <orchestra.mid> <recording> <vpo_dir> <out_dir>
"""
import sys
import traceback
from pathlib import Path

solo, orchestra, rec, vpo, out = sys.argv[1:6]
out_dir = Path(out)
out_dir.mkdir(parents=True, exist_ok=True)

with open(out_dir / "smoke_test.log", "w", encoding="utf-8", buffering=1) as log:
    sys.stdout = sys.stderr = log  # the GUI redirects print() similarly
    try:
        import app_paths
        import main as pipeline

        print("PROJECT_ROOT =", app_paths.PROJECT_ROOT)
        print("DEFAULT_OUTPUT_DIR =", app_paths.DEFAULT_OUTPUT_DIR)
        pipeline.run_pipeline(
            solo_midi_path=solo,
            orchestra_midi_path=orchestra,
            rec_audio_path=rec,
            output_dir=str(out_dir),
            output_filename="smoke_test_mix.mp3",
            vpo_dir=vpo,
        )
        if not (out_dir / "smoke_test_mix.mp3").exists():
            raise RuntimeError("pipeline returned but no mix was written")
        print("PIPELINE OK")
    except BaseException:
        traceback.print_exc()
        print("PIPELINE FAILED")
