import logging
import os

# import sys
from pathlib import Path

from pydub import AudioSegment

import audio_utils
import create_midi
import find_tempo
import orchestra_player
import rest_utils
import score_reader
import tempo_sync
import transcribe

# Silence TensorFlow messages
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"

# Silence the root/basic-pitch warnings
logging.getLogger().setLevel(logging.ERROR)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Final mix balance -- tune by ear and re-run.
ORCHESTRA_GAIN_DB = -4.0
SOLO_GAIN_DB = 3.0


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
    """
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
    """
    print(f"\nTotal Successful Matches Synced: {matches_count}")
    # 4. Generate the tempo map
    print("\nGenerating Tempo Map...")
    tempo_map = find_tempo.map_tempo(alignment_results)
    print(f"Generated {len(tempo_map)} tempo mapping windows.")
    """
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
    """
    # Verified tutti rests need a warp curve to check expected player-time
    # windows against -- build the plain (unshifted, unsplit) curve just for
    # that lookup, per rest_utils.get_verified_tutti_rests's docstring.
    plain_warp = create_midi.build_warp_curve(alignment_results)
    tutti_rests = rest_utils.get_verified_tutti_rests(warp_time=plain_warp)
    print(f"\nFound {len(tutti_rests)} verified tutti rests: {tutti_rests}")

    score_path = PROJECT_ROOT / "data" / "raw" / "score_cropped.mid"
    output_midi_path = PROJECT_ROOT / "data" / "output" / "output.mid"

    warp_time, diagnostics, intro_shift = create_midi.build_warp_curve(
        alignment_results,
        tutti_rests=tutti_rests,
        extend_intro=True,
        return_diagnostics=True,
    )
    print(f"\nIntro shift: {intro_shift:.3f}s")

    create_midi.warp_midi_file(str(score_path), warp_time, str(output_midi_path))

    # Render the warped MIDI to audio via sfizz.
    orchestra_audio = orchestra_player.render_precise_gm_orchestra(
        sfizz_path="sfizz_render_1.2.2.exe",
        midi_filename=str(output_midi_path),
        final_master_filename="data/output/orchestra_rendered.mp3",
    )

    # Splice real silence into the recording at each extended tutti rest.
    # RestRecord player-time fields are relative to the pre-intro-shift
    # (i.e. raw REC.mp3) timeline, so no offset adjustment is needed here.
    extended_path = audio_utils.extend_rec_pauses(diagnostics)

    # Mute the recording's own natural lead-in (an estimate is fine here --
    # see mute_and_pad_intro's docstring) and pad back exactly enough silence
    # so the recording's real content ends up intro_shift seconds later,
    # matching where the warped MIDI's corresponding note lands.
    first_score_time = sheet_music_sequence[0][0]
    entrance_estimate = plain_warp(first_score_time)
    mute_time = max(0.0, entrance_estimate - 1.0)
    print(f"Entrance estimate: {entrance_estimate:.3f}s, mute_time: {mute_time:.3f}s")

    solo_audio = audio_utils.mute_and_pad_intro(
        AudioSegment.from_mp3(extended_path), mute_time, intro_shift
    )
    solo_final_path = PROJECT_ROOT / "data" / "output" / "REC_final.mp3"
    solo_audio.export(solo_final_path, format="mp3")
    print(f"Wrote aligned solo audio to {solo_final_path}")

    if orchestra_audio is not None:
        final_mix = audio_utils.overlay_with_gain(
            orchestra_audio, solo_audio,
            orchestra_gain_db=ORCHESTRA_GAIN_DB,
            solo_gain_db=SOLO_GAIN_DB,
        )
        final_mix_path = PROJECT_ROOT / "data" / "output" / "full_mix.mp3"
        final_mix.export(final_mix_path, format="mp3", bitrate="192k")
        print(f"Wrote final mix to {final_mix_path}")
    else:
        print("Orchestra render produced nothing; skipping final mix.")


if __name__ == "__main__":
    """
    # Open log file and redirect stdout BEFORE running main
    with open("debug_output.txt", "w", encoding="utf-8") as log_file:
        sys.stdout = log_file

        try:
            main()
        finally:
            # This guarantees stdout is restored even if the code crashes
            sys.stdout = sys.__stdout__
    """
    main()
