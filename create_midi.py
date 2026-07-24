import mido
import numpy as np
import pretty_midi
from scipy.interpolate import PchipInterpolator
from scipy.ndimage import median_filter

REFERENCE_TEMPO = (
    500_000  # fixed 120 BPM reference tempo used for tick<->second conversion
)


def make_midi(midi_path, anchors, output_path, outlier_window=5, outlier_threshold=3.0):
    """
    Warp a MIDI file's timing to follow a performer, fitting a smooth,
    monotonic curve directly through the real alignment anchors -- no
    intermediate tempo-map/cumulative-integration step, so a single bad
    anchor can only distort the curve locally instead of permanently
    offsetting every anchor after it.

    anchors:
        [(score_time_seconds, player_time_seconds), ...] -- e.g. straight
        from tempo_sync.align(). Duplicate score_times are deduplicated
        (last wins); anchors are sorted by score_time before fitting.

    outlier_window / outlier_threshold:
        Anchors whose local (log) speed to their neighbors deviates from a
        rolling median by more than `outlier_threshold` robust standard
        deviations (over an `outlier_window`-point neighborhood) are
        dropped. The curve is allowed to drift smoothly past dropped
        anchors instead of forcing a tempo spike to hit them exactly. The
        first and last surviving anchors always pin the curve.

        A monotonicity guard (drop anchors whose player_time goes backwards
        relative to the anchors kept before them, tolerating ties) used to
        run before this, but is currently disabled/commented out below:
        tempo_sync.align()'s DTW traceback only ever decrements indices, so
        the anchors it returns are already guaranteed non-decreasing in
        player_time by construction -- this guard was dropping 0 of 663
        real anchors on the current pipeline's data. Left in as a commented
        block in case align()'s algorithm changes, or make_midi is ever fed
        anchors from a source that doesn't guarantee this.
    """

    unique = {}
    for s, y in anchors:
        unique[s] = y
    score_times = np.array(sorted(unique.keys()), dtype=float)
    player_times = np.array([unique[s] for s in score_times], dtype=float)

    n = len(score_times)
    if n == 0:
        raise ValueError("make_midi requires at least one alignment anchor")

    if n == 1:
        good_times = score_times
        good_warped = player_times
        curve = None
        edge_speed_start = edge_speed_end = 1.0
    else:
        # 1. Disabled: drop anchors whose player_time actually goes
        #    backwards versus the anchors kept before them (a greedy
        #    forward scan). tempo_sync.align()'s DTW traceback only ever
        #    decrements indices in its backward walk, so the (score_time,
        #    player_time) pairs it returns are already guaranteed
        #    non-decreasing in player_time -- this guard drops 0/663 real
        #    anchors on the current pipeline. Kept here commented out
        #    rather than deleted, in case align()'s algorithm changes or
        #    make_midi is fed anchors from a source that doesn't guarantee
        #    monotonicity.
        #
        # mono_keep = np.zeros(n, dtype=bool)
        # last_y = -np.inf
        # for i in range(n):
        #     if player_times[i] >= last_y:
        #         mono_keep[i] = True
        #         last_y = player_times[i]
        #
        # good_times = score_times[mono_keep]
        # good_warped = player_times[mono_keep]
        good_times = score_times
        good_warped = player_times

        # 2. Flag outlier anchors (robust rolling-median z-score on
        #    log-speed, computed from each anchor's real neighbors).
        #    First and last anchors are never flagged -- they pin the
        #    overall start/end alignment. Tied anchors don't individually
        #    define a local speed, so runs of ties are collapsed to one
        #    representative anchor (the first in the run) for this
        #    computation -- an outlier verdict on the representative
        #    applies to the whole tied run.

        m = len(good_times)
        if m < 2:
            # Every anchor but one was dropped; nothing left to build a
            # curve from -- fall back to a flat pass-through pinned at
            # the one surviving anchor.
            curve = None
            edge_speed_start = edge_speed_end = 1.0
        else:
            distinct = np.empty(m, dtype=bool)
            distinct[0] = True
            distinct[1:] = good_warped[1:] != good_warped[:-1]
            rep_times = good_times[distinct]
            rep_warped = good_warped[distinct]
            group_of = np.cumsum(distinct) - 1

            rep_keep = np.ones(len(rep_times), dtype=bool)
            rm = len(rep_times)
            if rm >= 3 and rm >= outlier_window:
                local_speed = np.empty(rm)
                local_speed[0] = (rep_times[1] - rep_times[0]) / (
                    rep_warped[1] - rep_warped[0]
                )
                local_speed[-1] = (rep_times[-1] - rep_times[-2]) / (
                    rep_warped[-1] - rep_warped[-2]
                )
                local_speed[1:-1] = (rep_times[2:] - rep_times[:-2]) / (
                    rep_warped[2:] - rep_warped[:-2]
                )

                log_speed = np.log(local_speed)
                local_median = median_filter(
                    log_speed, size=outlier_window, mode="nearest"
                )
                residual = log_speed - local_median
                robust_std = np.median(np.abs(residual)) * 1.4826 + 1e-9
                z = np.abs(residual) / robust_std

                rep_keep = z <= outlier_threshold
                rep_keep[0] = True
                rep_keep[-1] = True

            keep = rep_keep[group_of]
            good_times = good_times[keep]
            good_warped = good_warped[keep]

            # 3. Smooth, monotonic curve through the retained anchors

            curve = PchipInterpolator(good_times, good_warped)

            distinct = np.empty(len(good_times), dtype=bool)
            distinct[0] = True
            distinct[1:] = good_warped[1:] != good_warped[:-1]
            rep_times = good_times[distinct]
            rep_warped = good_warped[distinct]
            if len(rep_times) < 2:
                edge_speed_start = edge_speed_end = 1.0
            else:
                edge_speed_start = (rep_times[1] - rep_times[0]) / (
                    rep_warped[1] - rep_warped[0]
                )
                edge_speed_end = (rep_times[-1] - rep_times[-2]) / (
                    rep_warped[-1] - rep_warped[-2]
                )

    def warp_time(score_time):
        if score_time <= good_times[0]:  # Just in case of any errors
            return good_warped[0] + (score_time - good_times[0]) / edge_speed_start

        if score_time >= good_times[-1]:
            return good_warped[-1] + (score_time - good_times[-1]) / edge_speed_end

        assert curve is not None

        return float(curve(score_time))

    # 4. Apply warp to MIDI
    #
    # Ticks are converted to/from *output* time using a single fixed
    # REFERENCE_TEMPO, not the source file's own tempo curve: pm.tick_to_time
    # is only used to recover each message's original score-time (which is
    # what the anchors/warp_time are keyed on). The source file's set_tempo
    # messages are themselves just events on this same timeline, and
    # repositioning them (keeping their old value, moving only their tick)
    # does not reconstruct a tempo curve that reproduces the intended warp --
    # standard MIDI tempo is piecewise-constant between messages, so a
    # repositioned-but-unrevalued curve drifts from the intended warp between
    # points, compounding across the file. Emitting one fixed tempo instead
    # sidesteps that entirely: every tick is placed directly from its exact
    # warped time.

    mid = mido.MidiFile(midi_path)
    pm = pretty_midi.PrettyMIDI(midi_path)

    new_mid = mido.MidiFile()
    new_mid.ticks_per_beat = mid.ticks_per_beat

    def sec_to_tick(sec):
        return max(0, mido.second2tick(sec, mid.ticks_per_beat, REFERENCE_TEMPO))

    tempo_written = False

    for track in mid.tracks:
        new_track = mido.MidiTrack()
        abs_tick = 0
        previous_tick = 0

        for msg in track:
            abs_tick += msg.time

            if msg.type == "set_tempo":
                if not tempo_written:
                    new_track.append(
                        mido.MetaMessage("set_tempo", tempo=REFERENCE_TEMPO, time=0)
                    )
                    previous_tick = 0
                    tempo_written = True
                continue

            abs_score = pm.tick_to_time(abs_tick)
            warped_time = warp_time(abs_score)
            tick = sec_to_tick(warped_time)

            delta = tick - previous_tick
            previous_tick = tick

            new_track.append(msg.copy(time=delta))

        new_mid.tracks.append(new_track)

    if not tempo_written:
        # Source file had no set_tempo message at all -- still need one so
        # the output's tick<->time meaning is well-defined.
        new_mid.tracks[0].insert(
            0, mido.MetaMessage("set_tempo", tempo=REFERENCE_TEMPO, time=0)
        )

    new_mid.save(output_path)
