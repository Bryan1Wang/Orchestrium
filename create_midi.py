import mido
import numpy as np
import pretty_midi
from scipy.interpolate import PchipInterpolator

REFERENCE_TEMPO = (
    500_000  # fixed 120 BPM reference tempo used for tick<->second conversion
)


def make_midi(
    midi_path,
    anchors,
    output_path,
    outlier_window_seconds=3.0,
    outlier_threshold=0.75,
    chunk_seconds=1,
):
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
        Anchors that share the same player_time (e.g. several different
        score_times all DTW-matched to one performed instant) are then
        also collapsed to one representative each (the first in the run),
        permanently -- not just for the outlier/chunk stats below, but for
        the curve fit itself. Feeding the raw ties straight to PCHIP would
        pin several different score_times to one identical player_time,
        forcing a flat plateau in the curve that PCHIP then has to
        compensate for with a steeper segment right around it to still
        reach the next real anchor -- a self-inflicted version of the same
        local overfitting ("skidding") the outlier/chunk logic below
        exists to prevent.

    outlier_window_seconds / outlier_threshold:
        Anchors whose local (log) speed to their neighbors deviates from a
        rolling median by more than `outlier_threshold` robust standard
        deviations are dropped. The rolling median for a given anchor is
        computed over every other anchor within `outlier_window_seconds/2`
        of it in score-time (a real time span, not a fixed anchor count --
        anchor density varies hugely across a piece, e.g. dense passages
        vs. long rests, so a point-count window would cover wildly
        different amounts of real time depending on where it lands),
        falling back to the immediate +-1 neighbors if that span is too
        sparse to contain at least 3 points. The curve is allowed to drift
        smoothly past dropped anchors instead of forcing a tempo spike to
        hit them exactly. The first and last surviving anchors always pin
        the curve.

    chunk_seconds:
        Applied after outlier rejection, not before -- the rolling-median
        z-score above needs a dense set of points to produce a meaningful
        local median/std, so thinning first would starve it of context.
        The surviving anchors are then walked greedily in time order: keep
        a point, then keep the *farthest* surviving point still within
        `chunk_seconds` of it (or just the next surviving point if none
        are within range), dropping everything in between, and repeat from
        there. This caps every `chunk_seconds` span of score-time to at
        most 2 surviving anchors -- picking the farthest rather than an
        arbitrary/nearby one maximizes their spacing, which is what
        actually prevents the curve from overfitting to noise ("skidding")
        in a densely-anchored span. Chunk boundaries follow the kept
        points rather than a fixed grid, so a dense cluster never gets
        awkwardly split across a grid line. The first and last surviving
        anchors are always kept by construction (the walk starts at the
        first and only stops once it reaches the last).

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

        # 2. Collapse anchors that share the same player_time (ties) into
        #    one representative each (the first in the run) -- permanently,
        #    not just for the local-speed stats below. player_time out of
        #    tempo_sync.align() is already guaranteed non-decreasing (see
        #    the disabled monotonicity guard above), so this collapse makes
        #    the array strictly increasing in player_time, and every step
        #    after this (outlier rejection, chunk-thinning, and the final
        #    PCHIP fit) only ever removes points from it -- which can never
        #    reintroduce a tie. Collapsing once up front, and feeding that
        #    same tie-free array all the way through to the curve fit,
        #    means the PCHIP curve never gets multiple different
        #    score_times pinned to one identical player_time -- that would
        #    force a flat plateau in the curve, which PCHIP then has to
        #    compensate for with a steeper segment right around it to still
        #    reach the next real anchor (the same kind of local overfit
        #    "skidding" the outlier/chunk logic below exists to prevent).

        m = len(good_times)
        if m < 2:
            cand_times = good_times
            cand_warped = good_warped
        else:
            distinct = np.empty(m, dtype=bool)
            distinct[0] = True
            distinct[1:] = good_warped[1:] != good_warped[:-1]
            cand_times = good_times[distinct]
            cand_warped = good_warped[distinct]

        if len(cand_times) < 2:
            # Every anchor but one was dropped, or every anchor tied to a
            # single player_time; nothing left to build a curve from --
            # fall back to a flat pass-through pinned at the one surviving
            # anchor.
            good_times = cand_times
            good_warped = cand_warped
            curve = None
            edge_speed_start = edge_speed_end = 1.0
        else:
            rep_times = cand_times
            rep_warped = cand_warped

            rep_keep = np.ones(len(rep_times), dtype=bool)
            rm = len(rep_times)
            if rm >= 3:
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

                # Time-windowed local median (not a fixed anchor count):
                # anchor spacing is wildly non-uniform (dense passages vs.
                # long rests), so a point-count window covers a different
                # amount of real time depending on local density. Each
                # anchor's neighborhood is instead every other anchor within
                # outlier_window_seconds/2 of it in score-time, falling back
                # to its immediate +-1 neighbors if that span is too sparse
                # (e.g. right at the edge of a long rest) to have >=3 points.
                radius = outlier_window_seconds / 2.0
                local_median = np.empty(rm)
                for i in range(rm):
                    lo = np.searchsorted(rep_times, rep_times[i] - radius, side="left")
                    hi = np.searchsorted(rep_times, rep_times[i] + radius, side="right")
                    if hi - lo < 3:
                        lo = max(0, i - 1)
                        hi = min(rm, i + 2)
                    local_median[i] = np.median(log_speed[lo:hi])

                residual = log_speed - local_median
                robust_std = np.median(np.abs(residual)) * 1.4826 + 1e-9
                z = np.abs(residual) / robust_std

                rep_keep = z <= outlier_threshold
                rep_keep[0] = True
                rep_keep[-1] = True

            # 2b. Greedily thin the surviving anchors so no chunk_seconds
            #     span of score-time keeps more than 2 of them: walk the
            #     survivors in time order, and from each kept point jump to
            #     the farthest surviving point still within chunk_seconds
            #     (or the very next surviving point if none are in range),
            #     dropping everything strictly in between. Maximizing the
            #     spacing between the 2 kept points per chunk (rather than
            #     keeping an arbitrary/nearby one) is what actually stops
            #     the curve from overfitting to noise in a densely-anchored
            #     span. The first and last survivors are always kept since
            #     the walk starts at the first and only stops at the last.
            survivors = np.where(rep_keep)[0]
            if len(survivors) > 2:
                survivor_times = rep_times[survivors]
                keep_survivor = np.zeros(len(survivors), dtype=bool)
                keep_survivor[0] = True
                pos = 0
                while pos < len(survivors) - 1:
                    limit = survivor_times[pos] + chunk_seconds
                    far = np.searchsorted(survivor_times, limit, side="right") - 1
                    if far <= pos:
                        far = pos + 1
                    keep_survivor[far] = True
                    pos = far

                rep_keep = np.zeros(len(rep_times), dtype=bool)
                rep_keep[survivors[keep_survivor]] = True

            good_times = rep_times[rep_keep]
            good_warped = rep_warped[rep_keep]

            # 3. Smooth, monotonic curve through the retained anchors. This
            #    set is already tie-free by construction (ties were
            #    collapsed once, up front, in step 2), so no second
            #    distinct-mask pass is needed here -- outlier rejection and
            #    chunk-thinning only ever remove points from a strictly
            #    increasing player_time sequence, which can't reintroduce a
            #    tie.

            curve = PchipInterpolator(good_times, good_warped)

            edge_speed_start = (good_times[1] - good_times[0]) / (
                good_warped[1] - good_warped[0]
            )
            edge_speed_end = (good_times[-1] - good_times[-2]) / (
                good_warped[-1] - good_warped[-2]
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
