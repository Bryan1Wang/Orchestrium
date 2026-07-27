from pathlib import Path

import mido
from meta_track import crop_meta_track
from track_crop import crop_note_track


def slice_master_score_for_orchestra(
    intro_bars=135, num_bars=18, exclude_track_index=1
):
    PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
    master_midi_path = PROJECT_ROOT / "data" / "raw" / "SCORE.mid"
    output_path = PROJECT_ROOT / "data" / "raw" / "ORCHESTRA.mid"

    mid = mido.MidiFile(str(master_midi_path))
    sliced_mid = mido.MidiFile(ticks_per_beat=mid.ticks_per_beat)

    # Calculate ticks based on bars (assuming 4/4 time layout)
    ticks_per_bar = 4 * mid.ticks_per_beat
    start_tick = intro_bars * ticks_per_bar
    max_ticks = start_tick + (num_bars * ticks_per_bar)

    # Keep Track 0 for global tempo/time signature mappings, cropped to the
    # same bar window as every other track (otherwise the sliced file
    # inherits every tempo/time-signature event in the whole source score).
    sliced_mid.tracks.append(crop_meta_track(mid.tracks[0], start_tick, max_ticks))

    print(f"✂️ Slicing orchestra bars {intro_bars} to {intro_bars + num_bars}...")

    # Loop through ALL tracks in the orchestra master file
    for idx, track in enumerate(mid.tracks):
        if idx == 0:  # Skip Track 0 since we already appended it
            continue

        if (
            idx == exclude_track_index
        ):  # Skip the solo/piano track -- it's not part of the orchestra
            continue

        solo_track = crop_note_track(track, start_tick, max_ticks)

        if len(solo_track) > 0:
            sliced_mid.tracks.append(solo_track)

    sliced_mid.save(str(output_path))
    print(f"✅ Success! Saved clean separate file to: {output_path.name}")


if __name__ == "__main__":
    # Adjust your bar numbers here
    slice_master_score_for_orchestra(intro_bars=0, num_bars=154, exclude_track_index=12)
