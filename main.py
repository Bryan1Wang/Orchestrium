import logging
import os
import sys

import create_midi
import find_tempo
import score_reader
import tempo_sync
import transcribe

# Silence TensorFlow messages
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"

# Silence the root/basic-pitch warnings
logging.getLogger().setLevel(logging.ERROR)


def midi_to_note_name(midi_num: int) -> str:
    """Converts a MIDI note number to a human-readable string (e.g., 60 -> C4)"""
    notes = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    octave = (midi_num // 12) - 1
    note_name = notes[midi_num % 12]
    return f"{note_name}{octave}"


def format_note_set(note_set: set[int]) -> str:
    """Converts a set of MIDI numbers into a sorted, readable string bracket"""
    if not note_set:
        return "{}"
    readable_notes = [midi_to_note_name(n) for n in sorted(note_set)]
    return f"{{{', '.join(readable_notes)}}}"


def main():
    print("--- Starting Music Alignment ---")

    # 1. Parse the mathematically perfect sheet music score
    print("Parsing sheet music MIDI score...")
    sheet_music_sequence = score_reader.extract_notes()
    print(f"Loaded {len(sheet_music_sequence)} chord/note events from the score.\n")

    # 2. Transcribe the raw audio performance using Basic Pitch
    print("Transcribing live performance audio...")
    player_onset_sequence = transcribe.transcribe()
    print(f"Extracted {len(player_onset_sequence)} performance events from audio.\n")

    # 3. Align the performance to the score using your DTW algorithm
    print("Aligning performance to sheet music...")
    alignment_results = tempo_sync.align(player_onset_sequence, sheet_music_sequence)

    print("--- Alignment Complete! ---")
    print("\n--- Detailed Alignment Breakdown ---")

    # Track counts for your summary
    matches_count = 0

    for match_idx, (orch_time, player_time) in enumerate(alignment_results):
        # 1. Find the matching chord in the sheet music by matching the timestamp
        sheet_notes = set()
        for time_stamp, notes in sheet_music_sequence:
            if abs(time_stamp - orch_time) < 0.001:  # Small delta for float precision
                sheet_notes = notes
                break

        # 2. Find the matching chord in the player data by matching the timestamp
        player_notes = set()
        for time_stamp, notes in player_onset_sequence:
            if abs(time_stamp - player_time) < 0.001:
                player_notes = notes
                break

        # 3. Format the sets into readable note names (e.g., {C4, E4, G4})
        readable_sheet = format_note_set(sheet_notes)
        readable_player = format_note_set(player_notes)

        matches_count += 1

        # Print everything nicely aligned with seconds and note clusters
        print(
            f"Match #{match_idx:03d} | Score: {orch_time:6.2f}s {readable_sheet:<18} -> SYNCED TO -> Player: {player_time:6.2f}s {readable_player}"
        )

    print(f"\nTotal Successful Matches Synced: {matches_count}")
    # 4. Generate the tempo map
    print("\nGenerating Tempo Map...")
    tempo_map = find_tempo.map_tempo(alignment_results)
    print(f"Generated {len(tempo_map)} tempo mapping windows.")
    counter = 0
    prev = 0
    prev_speed = 1
    for idx, (window_time, speed) in enumerate(tempo_map):
        counter += (window_time - prev) / prev_speed
        prev = window_time
        prev_speed = speed
        print(
            f"Tempo Marker #{idx:03d} at {window_time:6.2f}s (Score Time) -> Speed Multiplier: {speed:.2f}x"
        )

    print("=" * 40 + "\n")
    print(counter)

    create_midi.make_midi(
        r"C:\Users\Bryan\Downloads\Orchestrium2\orchestra-warp-backend\data\raw\score_cropped.mid",
        alignment_results,
        r"C:\Users\Bryan\Downloads\Orchestrium2\orchestra-warp-backend\data\output\output.mid",
    )


if __name__ == "__main__":
    # Open log file and redirect stdout BEFORE running main
    log_file = open("debug_output.txt", "w", encoding="utf-8")
    sys.stdout = log_file

    try:
        main()
    finally:
        # This guarantees your file closes properly even if the code crashes
        sys.stdout = sys.__stdout__
        log_file.close()

    # main()
