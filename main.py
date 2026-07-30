import logging
import os

# Silence TensorFlow native startup logs (must run before tensorflow is imported,
# which happens transitively via audio_utils -> transcribe -> basic_pitch)
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"

# Silence the root/basic-pitch warnings (coremltools/tflite/onnx "not installed" notices)
logging.getLogger().setLevel(logging.ERROR)

# import sys
from pathlib import Path
from typing import TypedDict

from pydub import AudioSegment

import app_paths
import audio_utils
import create_midi
import orchestra_player
import rest_utils
import score_reader
import tempo_sync
import transcribe

DEFAULT_OUTPUT_FILENAME = "OrchestriumRecording.mp3"


class _CurveFitKwargs(TypedDict):
    max_tempo_jump_pct: float
    jump_floor_seconds: float
    absolute_deviation_seconds: float
    exclusion_radius_seconds: float
    side_search_radius_seconds: float
    side_min_points: int
    position_outlier_floor_seconds: float
    position_outlier_z: float
    contiguous_run_min_points: int
    max_gap_seconds: float
    chunk_seconds: float
    tempo_window_seconds: float
    start_speed_window_seconds: float


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


def run_pipeline(
    *,
    solo_midi_path=None,
    orchestra_midi_path=None,
    rec_audio_path=None,
    output_dir=None,
    output_filename=None,
    keep_temp_files=False,
    sfizz_exe_path="sfizz_render_1.2.2.exe",
    vpo_dir=None,
    skip_track=None,
    orchestra_gain_db=-4.0,
    solo_gain_db=3.0,
    min_score_rest_seconds=3.0,
    min_recording_pause_seconds=2.0,
    max_tempo_jump_pct=0.25,
    jump_floor_seconds=0.07,
    absolute_deviation_seconds=1.0,
    exclusion_radius_seconds=4.0,
    side_search_radius_seconds=10.0,
    side_min_points=6,
    position_outlier_floor_seconds=0.8,
    position_outlier_z=7.0,
    contiguous_run_min_points=5,
    max_gap_seconds=10.0,
    chunk_seconds=1,
    tempo_window_seconds=5.0,
    start_speed_window_seconds=10.0,
):
    output_dir = Path(output_dir) if output_dir else app_paths.DEFAULT_OUTPUT_DIR
    output_filename = (output_filename or DEFAULT_OUTPUT_FILENAME).strip()
    if not output_filename.lower().endswith(".mp3"):
        output_filename += ".mp3"

    # Curve-fit knobs shared by both build_warp_curve calls below, so the
    # rest-verification lookup curve and the final warp curve never desync.
    curve_fit_kwargs: _CurveFitKwargs = {
        "max_tempo_jump_pct": max_tempo_jump_pct,
        "jump_floor_seconds": jump_floor_seconds,
        "absolute_deviation_seconds": absolute_deviation_seconds,
        "exclusion_radius_seconds": exclusion_radius_seconds,
        "side_search_radius_seconds": side_search_radius_seconds,
        "side_min_points": side_min_points,
        "position_outlier_floor_seconds": position_outlier_floor_seconds,
        "position_outlier_z": position_outlier_z,
        "contiguous_run_min_points": contiguous_run_min_points,
        "max_gap_seconds": max_gap_seconds,
        "chunk_seconds": chunk_seconds,
        "tempo_window_seconds": tempo_window_seconds,
        "start_speed_window_seconds": start_speed_window_seconds,
    }

    print("Starting Music Alignment...")

    # 1. Parse the mathematically perfect sheet music score
    print("Parsing sheet music MIDI score...")
    sheet_music_sequence = score_reader.extract_notes(solo_midi_path)
    print(f"Loaded {len(sheet_music_sequence)} chord/note events from the score.\n")

    # 2. Transcribe the raw audio performance using Basic Pitch
    print("Transcribing live performance audio...")
    player_onset_sequence = transcribe.transcribe(rec_audio_path, output_dir=output_dir)
    rec_midi_path = transcribe.generated_midi_path(rec_audio_path, output_dir=output_dir)
    print(f"Extracted {len(player_onset_sequence)} performance events from audio.\n")

    # 3. Align the performance to the score using your DTW algorithm
    print("Aligning performance to sheet music...")
    alignment_results = tempo_sync.align(player_onset_sequence, sheet_music_sequence)

    print("\nAlignment Complete!")

    # Track counts for your summary
    """
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
    """
    print(f"\nTotal Successful Matches Synced: {len(alignment_results)}")
    """
    # 4. Generate the tempo map
    print("\nGenerating Tempo Map...")
    tempo_map = find_tempo.map_tempo(alignment_results)
    print(f"\nGenerated {len(tempo_map)} tempo mapping windows.")
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
    plain_warp = create_midi.build_warp_curve(alignment_results, **curve_fit_kwargs)
    tutti_rests = rest_utils.get_verified_tutti_rests(
        warp_time=plain_warp,
        min_score_rest_seconds=min_score_rest_seconds,
        min_recording_pause_seconds=min_recording_pause_seconds,
        solo_midi_path=solo_midi_path,
        rec_midi_path=rec_midi_path,
    )
    print(f"\nFound {len(tutti_rests)} verified tutti rest(s): {tutti_rests}")

    orchestra_score_path = (
        Path(orchestra_midi_path)
        if orchestra_midi_path
        else app_paths.PROJECT_ROOT / "data" / "raw" / "ORCHESTRA.mid"
    )
    output_midi_path = output_dir / "output.mid"

    warp_time, diagnostics, intro_shift = create_midi.build_warp_curve(
        alignment_results,
        tutti_rests=tutti_rests,
        extend_intro=True,
        return_diagnostics=True,
        **curve_fit_kwargs,
    )
    print(f"\nIntro shift: {intro_shift:.3f}s")

    create_midi.warp_midi_file(
        str(orchestra_score_path), warp_time, str(output_midi_path)
    )

    # Render the warped MIDI to audio via sfizz.
    orchestra_rendered_path = output_dir / "orchestra_rendered.mp3"
    orchestra_audio = orchestra_player.render_precise_gm_orchestra(
        sfizz_path=sfizz_exe_path,
        midi_filename=str(output_midi_path),
        final_master_filename=str(orchestra_rendered_path),
        vpo_dir=vpo_dir,
        skip_track=skip_track,
    )

    # Splice real silence into the recording at each extended tutti rest.
    # RestRecord player-time fields are relative to the pre-intro-shift
    # (i.e. raw REC.mp3) timeline, so no offset adjustment is needed here.
    extended_path = audio_utils.extend_rec_pauses(
        diagnostics or [], rec_path=rec_audio_path, output_path=output_dir / "REC_extended.mp3"
    )

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
    solo_final_path = output_dir / "REC_trimmed.mp3"
    solo_audio.export(solo_final_path, format="mp3")
    #print(f"Wrote aligned solo audio to {solo_final_path}")

    if orchestra_audio is not None:
        final_mix = audio_utils.overlay_with_gain(
            orchestra_audio,
            solo_audio,
            orchestra_gain_db=orchestra_gain_db,
            solo_gain_db=solo_gain_db,
        )
        final_mix_path = output_dir / output_filename
        final_mix.export(final_mix_path, format="mp3", bitrate="192k")
        print(f"\nWrote final mix to {final_mix_path}")

        if not keep_temp_files:
            temp_paths = (
                rec_midi_path,
                output_midi_path,
                orchestra_rendered_path,
                extended_path,
                solo_final_path,
                output_dir / "basic_pitch_console.log",
            )
            for temp_path in temp_paths:
                if temp_path != final_mix_path:
                    temp_path.unlink(missing_ok=True)
            print(
                "Removed intermediate files (enable 'Keep temporary/intermediate "
                "files' to preserve them)."
            )
    else:
        print("Orchestra render produced nothing; skipping final mix.")


def main():
    run_pipeline()


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