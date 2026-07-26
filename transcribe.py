import contextlib
from pathlib import Path

import pretty_midi
from basic_pitch import ICASSP_2022_MODEL_PATH
from basic_pitch import inference as basic_pitch_inference

import app_paths


@contextlib.contextmanager
def _silenced_basic_pitch_output(log_path: Path):
    """Redirects basic_pitch's own stdout/stderr chatter into a log file instead
    of showing it -- basic_pitch's status prints and any tqdm/TensorFlow warnings
    write directly to sys.stdout/sys.stderr, so redirecting those streams (rather
    than filtering specific known lines) is the only way to silence output from
    code we don't control, without depending on basic_pitch's exact wording.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as log_file, contextlib.redirect_stdout(
        log_file
    ), contextlib.redirect_stderr(log_file):
        yield


def generated_midi_path(audio_path=None, output_dir=None):
    """Path where predict_and_save writes the transcribed MIDI for audio_path.

    basic_pitch.inference.predict_and_save derives its output filename from
    the audio file's own stem, internally -- this mirrors that same formula
    so callers can locate the transcript without re-running transcribe().
    """
    if audio_path is None:
        audio_path = app_paths.PROJECT_ROOT / "data" / "raw" / "REC.mp3"
    audio_path = Path(audio_path)
    if output_dir is None:
        output_dir = app_paths.DEFAULT_OUTPUT_DIR
    return Path(output_dir) / f"{audio_path.stem}_basic_pitch.mid"


def transcribe(audio_path=None, output_dir=None) -> list[tuple[float, set[int]]]:
    if output_dir is None:
        output_dir = app_paths.DEFAULT_OUTPUT_DIR
    OUTPUT_DIR = Path(output_dir)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if audio_path is None:
        audio_path = app_paths.PROJECT_ROOT / "data" / "raw" / "REC.mp3"
    audio_path = Path(audio_path)
    midi_path = generated_midi_path(audio_path, OUTPUT_DIR)
    midi_path.unlink(missing_ok=True)

    print(f"Analyzing {audio_path.name}... (This might take 10-30 seconds)")

    with _silenced_basic_pitch_output(OUTPUT_DIR / "basic_pitch_console.log"):
        basic_pitch_inference.predict_and_save(
            audio_path_list=[str(audio_path)],
            output_directory=str(OUTPUT_DIR),
            save_midi=True,
            sonify_midi=False,
            save_model_outputs=False,
            save_notes=False,
            model_or_model_path=ICASSP_2022_MODEL_PATH,
        )

    print("Transcription complete! Generated MIDI saved.")

    midi_data = pretty_midi.PrettyMIDI(str(midi_path))

    attacks = []
    for instrument in midi_data.instruments:
        for note in instrument.notes:
            attacks.append((note.start, note.pitch))

    if not attacks:
        return []

    attacks.sort(key=lambda x: x[0])

    notes = []
    cluster = set()

    anchor_time = attacks[0][0]
    flex = 0.025  # 25 milliseconds

    for note_time, note in attacks:
        if note_time - anchor_time > flex:
            notes.append((anchor_time, cluster))
            cluster = {note}
            anchor_time = note_time
        else:
            cluster.add(note)

    if cluster:
        notes.append((anchor_time, cluster))

    return notes
