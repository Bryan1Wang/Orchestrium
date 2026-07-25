from pathlib import Path

import pretty_midi

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def sounding_intervals_from_midi(midi_path, merge_gap_seconds=0.0):
    """Merged (start, end) sounding intervals from a MIDI file's real notes.

    Unlike score_reader.extract_notes()/transcribe.transcribe() (which only
    keep onset times), this reads each note's actual start AND end, so a
    long sustained note isn't mistaken for silence. Overlapping or
    touching (gap <= merge_gap_seconds) intervals are merged. Returns a
    sorted, non-overlapping list[(float, float)].
    """
    midi_data = pretty_midi.PrettyMIDI(str(midi_path))

    intervals = sorted(
        (note.start, note.end)
        for instrument in midi_data.instruments
        for note in instrument.notes
    )
    if not intervals:
        return []

    merged = [intervals[0]]
    for start, end in intervals[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end + merge_gap_seconds:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))

    return merged


def find_score_rests(midi_path=None, min_rest_seconds=3.0):
    """Rests (gaps >= min_rest_seconds) between the score's sounding notes.

    Default midi_path = PROJECT_ROOT/data/raw/solo.mid (matches
    score_reader.extract_notes()'s hardcode). Returns list[(gap_start,
    gap_end)] in score-time seconds.
    """
    if midi_path is None:
        midi_path = PROJECT_ROOT / "data" / "raw" / "solo.mid"

    intervals = sounding_intervals_from_midi(midi_path)

    rests = []
    for (_, prev_end), (next_start, _) in zip(intervals, intervals[1:]):
        gap = next_start - prev_end
        if gap >= min_rest_seconds:
            rests.append((prev_end, next_start))

    return rests


def verify_recording_pause(
    rest_start_score,
    rest_end_score,
    warp_time,
    rec_midi_path=None,
    min_pause_seconds=2.0,
    search_margin_seconds=1.5,
):
    """Confirm a real pause exists in the recording near a score rest.

    Maps the rest's score-time boundaries to an expected player-time window
    via `warp_time` (pass the plain, tutti_rests=None curve -- this never
    rebuilds or re-runs alignment itself), widens that window by
    search_margin_seconds on each side, and looks for a gap between the
    recording's own merged sounding intervals (see
    sounding_intervals_from_midi, applied to rec_midi_path -- default
    PROJECT_ROOT/data/output/REC_basic_pitch.mid, transcribe.py's own
    output) that is >= min_pause_seconds and overlaps that window. The
    window's own edges count as sounding-interval boundaries too, so a gap
    that runs off either edge of the search window is still found.

    Returns the found (gap_start, gap_end) in player-time, or None.
    """
    if rec_midi_path is None:
        rec_midi_path = PROJECT_ROOT / "data" / "output" / "REC_basic_pitch.mid"

    expected_start = warp_time(rest_start_score)
    expected_end = warp_time(rest_end_score)
    window_start = expected_start - search_margin_seconds
    window_end = expected_end + search_margin_seconds

    intervals = sounding_intervals_from_midi(rec_midi_path)
    nearby = [
        (max(start, window_start), min(end, window_end))
        for start, end in intervals
        if end > window_start and start < window_end
    ]

    boundaries = [window_start] + [t for pair in nearby for t in pair] + [window_end]

    best = None
    for i in range(0, len(boundaries) - 1, 2):
        gap_start, gap_end = boundaries[i], boundaries[i + 1]
        gap = gap_end - gap_start
        if gap >= min_pause_seconds and (best is None or gap > best[1] - best[0]):
            best = (gap_start, gap_end)

    return best


def get_verified_tutti_rests(
    warp_time,
    min_score_rest_seconds=3.0,
    min_recording_pause_seconds=2.0,
    search_margin_seconds=1.5,
    solo_midi_path=None,
    rec_midi_path=None,
):
    """Find score rests and keep only the ones confirmed in the recording.

    `warp_time` should be the plain (tutti_rests=None) warp curve, e.g.
    create_midi.build_warp_curve(alignment_results). Returns the verified
    list[(rest_start_score, rest_end_score)], ready to pass straight to
    create_midi.build_warp_curve(alignment_results, tutti_rests=...).
    """
    score_rests = find_score_rests(solo_midi_path, min_score_rest_seconds)

    verified = []
    for rest_start_score, rest_end_score in score_rests:
        match = verify_recording_pause(
            rest_start_score,
            rest_end_score,
            warp_time,
            rec_midi_path=rec_midi_path,
            min_pause_seconds=min_recording_pause_seconds,
            search_margin_seconds=search_margin_seconds,
        )
        if match is None:
            print(
                f"Skipping score rest {rest_start_score:.2f}-{rest_end_score:.2f}s: "
                f"no >= {min_recording_pause_seconds}s pause found in the recording "
                f"near expected player time {warp_time(rest_start_score):.2f}-"
                f"{warp_time(rest_end_score):.2f}s"
            )
            continue
        verified.append((rest_start_score, rest_end_score))

    return verified


if __name__ == "__main__":
    # Temporary: for testing this one feature in isolation. Remove once
    # rest-detection is wired into the real pipeline.
    import numpy as np

    import audio_utils
    import create_midi
    import score_reader
    import tempo_sync
    import transcribe

    sheet_music_sequence = score_reader.extract_notes()
    player_onset_sequence = transcribe.transcribe()
    alignment_results = tempo_sync.align(player_onset_sequence, sheet_music_sequence)

    base_warp = create_midi.build_warp_curve(alignment_results)

    tutti_rests = get_verified_tutti_rests(warp_time=base_warp)
    print(f"Found {len(tutti_rests)} verified tutti rests:")
    for s, e in tutti_rests:
        print(
            f"  score {s:.2f}s -> {e:.2f}s (span {e - s:.2f}s), "
            f"expected player {base_warp(s):.2f}s -> {base_warp(e):.2f}s"
        )

    if tutti_rests:
        warp_with_rests, diagnostics, intro_shift = create_midi.build_warp_curve(
            alignment_results, tutti_rests=tutti_rests, return_diagnostics=True
        )
        for rec in diagnostics:
            print(
                f"  rest {rec.rest_start_score:.2f}-{rec.rest_end_score:.2f}: "
                f"tutti_speed={rec.tutti_speed:.3f} natural={rec.natural_duration:.2f}s "
                f"computed={rec.computed_duration:.2f}s shortfall={rec.shortfall:.2f}s"
            )

        last_score_time = sheet_music_sequence[-1][0]
        for t in np.linspace(0, last_score_time, 20):
            print(
                f"t={t:7.2f}s  base={base_warp(t):7.2f}s  with_rests={warp_with_rests(t):7.2f}s  "
                f"delta={warp_with_rests(t) - base_warp(t):+6.2f}s"
            )

        extended_path = audio_utils.extend_rec_pauses(diagnostics)
        print(f"Wrote {extended_path}")

        # warp_midi_file (not make_midi) so this reuses warp_with_rests
        # directly instead of triggering a third _clean_anchors/curve-fit
        # pass through build_warp_curve.
        score_path = PROJECT_ROOT / "data" / "raw" / "score_cropped.mid"
        midi_output_path = PROJECT_ROOT / "data" / "output" / "output_with_rests.mid"
        create_midi.warp_midi_file(score_path, warp_with_rests, midi_output_path)
        print(f"Wrote {midi_output_path}")
    else:
        print("No verified tutti rests -- nothing to extend.")
