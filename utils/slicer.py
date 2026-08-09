import sys
from pathlib import Path

import mido

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from meta_track import crop_meta_track
from track_crop import crop_note_track

import app_paths


def slice_master_score(voice_track_index=1, intro_bars=0, num_bars=10):
    PROJECT_ROOT = app_paths.PROJECT_ROOT
    master_midi_path = PROJECT_ROOT / "data" / "raw" / "SCORE.mid"
    output_path = PROJECT_ROOT / "data" / "raw" / "SOLO.mid"

    mid = mido.MidiFile(str(master_midi_path))

    # --- SCANNER: Print all track names so you can find the solo part ---
    print("--- Available Tracks in MIDI ---")
    for i, track in enumerate(mid.tracks):
        print(f"Track {i}: {track.name if track.name else '[No Name]'}")
    print("--------------------------------\n")

    # Calculate ticks based on bars (assuming 4/4 time)
    ticks_per_bar = 4 * mid.ticks_per_beat
    start_tick = intro_bars * ticks_per_bar
    max_ticks = start_tick + (num_bars * ticks_per_bar)

    sliced_mid = mido.MidiFile(ticks_per_beat=mid.ticks_per_beat)

    # Keep Track 0 for global tempo/time signature mappings, cropped to the
    # same bar window as the voice track (otherwise the sliced file inherits
    # every tempo/time-signature event in the whole source score, and mido
    # reports/treats it as running the full original duration).
    sliced_mid.tracks.append(crop_meta_track(mid.tracks[0], start_tick, max_ticks))

    raw_voice_track = mid.tracks[voice_track_index]
    solo_track = crop_note_track(raw_voice_track, start_tick, max_ticks)

    sliced_mid.tracks.append(solo_track)
    sliced_mid.save(str(output_path))
    print(
        f"Saved bars {intro_bars + 1} to {intro_bars + num_bars + 1} from Track {voice_track_index} to solo.mid!"
    )


if __name__ == "__main__":
    # Example: Skip a 12-bar orchestra intro, then grab 10 bars of Track 2
    slice_master_score(voice_track_index=12, intro_bars=0, num_bars=154)
