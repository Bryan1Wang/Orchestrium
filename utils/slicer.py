from pathlib import Path
import mido

from meta_track import crop_meta_track


def slice_master_score(voice_track_index=1, intro_bars=0, num_bars=10):
    PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
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
    solo_track = mido.MidiTrack()

    time_accumulated = 0
    for msg in raw_voice_track:
        time_accumulated += msg.time

        # Skip everything until we hit our desired starting bar
        if time_accumulated < start_tick:
            continue

        # Stop processing once we've grabbed our 10 bars
        if time_accumulated > max_ticks:
            break

        # Shift the delta-times of the notes back so the sliced file
        # starts cleanly at its own time 0 instead of sitting in silence
        if len(solo_track) == 0:
            # First note in our slice gets a delta time relative to the start of our slice
            msg_copy = msg.copy(time=int(time_accumulated - start_tick))
        else:
            msg_copy = msg.copy()

        solo_track.append(msg_copy)

    sliced_mid.tracks.append(solo_track)
    sliced_mid.save(str(output_path))
    print(
        f"Saved bars {intro_bars + 1} to {intro_bars + num_bars + 1} from Track {voice_track_index} to solo.mid!"
    )


if __name__ == "__main__":
    # Example: Skip a 12-bar orchestra intro, then grab 10 bars of Track 2
    slice_master_score(voice_track_index=12, intro_bars=0, num_bars=154)
