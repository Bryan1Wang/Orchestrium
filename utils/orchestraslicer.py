from pathlib import Path
import mido

from meta_track import crop_meta_track

def slice_master_score_for_orchestra(intro_bars=135, num_bars=18, exclude_track_index=12):
    PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
    master_midi_path = PROJECT_ROOT / "data" / "raw" / "SCORE.mid"
    output_path = PROJECT_ROOT / "data" / "raw" / "score_cropped.mid"

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

        if idx == exclude_track_index:  # Skip the solo/piano track -- it's not part of the orchestra
            continue

        solo_track = mido.MidiTrack()
        time_accumulated = 0
        active_notes = {}  # Tracks notes currently held down per channel to prevent drone loops

        for msg in track:
            time_accumulated += msg.time
            
            # Skip everything until we hit our desired starting bar
            if time_accumulated < start_tick:
                continue
                
            # Stop processing once we've grabbed our required bars
            if time_accumulated > max_ticks:
                # --- FIXED: FORCE CLOSE ANY STUCK NOTES AT THE BAR SPLIT ---
                # This explicitly stops the random endless oboe/drone note
                for channel, notes_down in active_notes.items():
                    for note in list(notes_down):
                        solo_track.append(mido.Message('note_off', note=note, velocity=0, channel=channel, time=0))
                break
            
            # Maintain note states to see what is currently playing
            if msg.type == 'note_on' and msg.velocity > 0:
                if msg.channel not in active_notes:
                    active_notes[msg.channel] = set()
                active_notes[msg.channel].add(msg.note)
            elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                if msg.channel in active_notes and msg.note in active_notes[msg.channel]:
                    active_notes[msg.channel].remove(msg.note)

            # Shift delta times back so the slice starts cleanly at its own time 0
            if len(solo_track) == 0:
                msg_copy = msg.copy(time=int(time_accumulated - start_tick))
            else:
                msg_copy = msg.copy()
                
            solo_track.append(msg_copy)
            
        if len(solo_track) > 0:
            solo_track.append(mido.MetaMessage('end_of_track', time=0))
            sliced_mid.tracks.append(solo_track)
        
    sliced_mid.save(str(output_path))
    print(f"✅ Success! Saved clean separate file to: {output_path.name}")

if __name__ == "__main__":
    # Adjust your bar numbers here
    slice_master_score_for_orchestra(intro_bars=0, num_bars=154)
