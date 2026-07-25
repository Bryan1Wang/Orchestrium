from pathlib import Path

from pydub import AudioSegment

import create_midi
import score_reader
import tempo_sync
import transcribe

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def find_intro_crop_point(buffer_seconds=1.0):
    """Estimate where REC.mp3's lead-in should be cropped.

    The soloist's first score note often has no direct DTW anchor (the
    lead-in before their entrance can't be matched), so the corresponding
    player_time is extrapolated from create_midi's warp curve instead of
    read off an anchor directly.
    """
    sheet_music_sequence = score_reader.extract_notes()
    player_onset_sequence = transcribe.transcribe()
    alignment_results = tempo_sync.align(player_onset_sequence, sheet_music_sequence)

    warp_time = create_midi.build_warp_curve(alignment_results)

    first_score_time = sheet_music_sequence[0][0]
    extrapolated_player_time = warp_time(first_score_time)
    crop_time = max(0.0, extrapolated_player_time - buffer_seconds)

    print(f"First score note at score time {first_score_time:.3f}s")
    print(f"Extrapolated player time: {extrapolated_player_time:.3f}s")
    print(f"Crop point (with {buffer_seconds}s buffer): {crop_time:.3f}s")

    return crop_time


def trim_rec_intro(output_path=None, buffer_seconds=1.0):
    rec_path = PROJECT_ROOT / "data" / "raw" / "REC.mp3"
    if output_path is None:
        output_path = PROJECT_ROOT / "data" / "output" / "REC_trimmed.mp3"

    crop_time = find_intro_crop_point(buffer_seconds=buffer_seconds)

    audio = AudioSegment.from_mp3(rec_path)
    trimmed = audio[crop_time * 1000 :]
    trimmed.export(output_path, format="mp3")

    print(f"Wrote trimmed audio ({trimmed.duration_seconds:.2f}s) to {output_path}")
    return output_path


def extend_rec_pauses(
    rest_diagnostics, output_path=None, buffer_seconds=0.0, rec_path=None
):
    """Splice real silence into REC.mp3 at each extended tutti rest.

    rest_diagnostics: the list create_midi.build_warp_curve(...,
    tutti_rests=..., return_diagnostics=True) returns. For every record
    with shortfall > 0, inserts (shortfall + buffer_seconds) seconds of
    silence at natural_end_player_time -- the point in the *original*
    recording where the soloist's next real note starts -- delaying
    everything from there onward, so the corresponding extended orchestra
    tutti in the warped MIDI has enough real silence to sit in. Records are
    processed in ascending natural_end_player_time order, accumulating a
    running offset so each successive splice point is located in the audio
    as already lengthened by every earlier splice. buffer_seconds defaults
    to 0.0: unlike trim_rec_intro's buffer, shortfall is already a lower
    bound (extend, never shrink), so padding further only makes the pause
    longer than necessary.
    """
    if rec_path is None:
        rec_path = PROJECT_ROOT / "data" / "raw" / "REC.mp3"
    if output_path is None:
        output_path = PROJECT_ROOT / "data" / "output" / "REC_extended.mp3"

    to_extend = sorted(
        (rec for rec in rest_diagnostics if rec.shortfall > 0),
        key=lambda rec: rec.natural_end_player_time,
    )

    audio = AudioSegment.from_mp3(rec_path)
    running_offset_ms = 0.0
    for rec in to_extend:
        insert_ms = round(rec.natural_end_player_time * 1000 + running_offset_ms)
        pad_ms = round((rec.shortfall + buffer_seconds) * 1000)
        silence = AudioSegment.silent(duration=pad_ms)
        audio = audio[:insert_ms] + silence + audio[insert_ms:]
        running_offset_ms += pad_ms
        print(
            f"Inserted {pad_ms / 1000:.3f}s of silence at {insert_ms / 1000:.3f}s "
            f"(original {rec.natural_end_player_time:.3f}s)"
        )

    audio.export(output_path, format="mp3")
    print(f"Wrote extended audio ({audio.duration_seconds:.2f}s) to {output_path}")
    return output_path


def mute_and_pad_intro(
    audio: AudioSegment, mute_time: float, intro_shift: float
) -> AudioSegment:
    """Replace the recording's own natural lead-in (up to mute_time) with
    clean silence, then add intro_shift more on top.

    Net shift is always +intro_shift regardless of mute_time's precision --
    trimming X seconds off the front and padding back X + intro_shift lands
    the real content at the same position either way. mute_time only
    controls how much of the recording's own (possibly noisy -- room tone,
    chair creaks, count-ins) lead-in gets replaced with silence versus left
    audible.
    """
    mute_time = max(0.0, mute_time)
    trimmed = audio[round(mute_time * 1000) :]
    pad_ms = round((mute_time + intro_shift) * 1000)
    return AudioSegment.silent(duration=pad_ms) + trimmed


def overlay_with_gain(
    orchestra_audio: AudioSegment,
    solo_audio: AudioSegment,
    orchestra_gain_db: float = 0.0,
    solo_gain_db: float = 0.0,
) -> AudioSegment:
    """Mix the rendered orchestra and the soloist's recording, both already
    aligned so position 0 in each corresponds to the same moment.
    """
    orchestra_audio = orchestra_audio.apply_gain(orchestra_gain_db)
    solo_audio = solo_audio.apply_gain(solo_gain_db)

    longest = max(len(orchestra_audio), len(solo_audio))
    mix = AudioSegment.silent(duration=longest)
    mix = mix.overlay(orchestra_audio)
    mix = mix.overlay(solo_audio)
    return mix


if __name__ == "__main__":
    # Temporary: for testing this one feature in isolation. Remove once
    # trimming is wired into the real pipeline.
    trim_rec_intro()
