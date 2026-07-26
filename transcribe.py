from pathlib import Path

import pretty_midi
from basic_pitch import ICASSP_2022_MODEL_PATH
from basic_pitch.inference import predict_and_save


def generated_midi_path(audio_path=None, output_dir=None):
    """Path where predict_and_save writes the transcribed MIDI for audio_path.

    basic_pitch.inference.predict_and_save derives its output filename from
    the audio file's own stem, internally -- this mirrors that same formula
    so callers can locate the transcript without re-running transcribe().
    """
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    if audio_path is None:
        audio_path = PROJECT_ROOT / "data" / "raw" / "REC.mp3"
    audio_path = Path(audio_path)
    if output_dir is None:
        output_dir = PROJECT_ROOT / "data" / "output"
    return Path(output_dir) / f"{audio_path.stem}_basic_pitch.mid"


def transcribe(audio_path=None, output_dir=None) -> list[tuple[float, set[int]]]:
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    if output_dir is None:
        output_dir = PROJECT_ROOT / "data" / "output"
    OUTPUT_DIR = Path(output_dir)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if audio_path is None:
        audio_path = PROJECT_ROOT / "data" / "raw" / "REC.mp3"
    audio_path = Path(audio_path)
    midi_path = generated_midi_path(audio_path, OUTPUT_DIR)
    midi_path.unlink(missing_ok=True)

    print(f"Analyzing {audio_path.name}... (This might take 10-30 seconds)")

    predict_and_save(
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
