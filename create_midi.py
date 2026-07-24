import mido
import numpy as np
import pretty_midi
from scipy.interpolate import PchipInterpolator
from scipy.stats import theilslopes

REFERENCE_TEMPO = (
    500_000  # fixed 120 BPM reference tempo used for tick<->second conversion
)


def _detect_tempo_jumps(times, warped, max_tempo_jump_pct, jump_floor_seconds):
    """
    Detector 1 -- instantaneous tempo-jump cap.

    For each interior anchor, compares the speed of the segment entering it
    to the speed of the segment leaving it. A real performance doesn't
    suddenly change tempo by more than `max_tempo_jump_pct` between adjacent
    notes, so a jump bigger than that is treated as a bad DTW match rather
    than expression. This only ever looks at the two immediate neighboring
    segments (never a window), so it's immune to a run of consecutive bad
    anchors that happen to agree with each other -- it fires on the jump
    into/out of such a run even if the interior points look locally smooth.

    The relative check alone isn't enough: real anchor spacing in dense/fast
    passages can be well under half a second, and at that density a few tens
    of milliseconds of ordinary DTW/quantization jitter -- harmless noise --
    is already a `max_tempo_jump_pct`-sized relative change, so the cap would
    fire constantly on noise rather than real anomalies. `jump_floor_seconds`
    guards against that: the anchor's expected position is estimated by
    linearly interpolating between its two neighbors, and the anchor is only
    flagged if its actual deviation from that expectation *also* exceeds this
    absolute floor. A jump has to be both relatively big and absolutely
    meaningful to count.
    """
    rm = len(times)
    flag = np.zeros(rm, dtype=bool)
    if rm < 3:
        return flag

    speed_in = (times[1:-1] - times[:-2]) / (warped[1:-1] - warped[:-2])
    speed_out = (times[2:] - times[1:-1]) / (warped[2:] - warped[1:-1])
    log_ratio = np.abs(np.log(speed_out / speed_in))
    bound = np.log(1.0 + max_tempo_jump_pct)

    expected = warped[:-2] + (times[1:-1] - times[:-2]) / (times[2:] - times[:-2]) * (
        warped[2:] - warped[:-2]
    )
    abs_dev = np.abs(warped[1:-1] - expected)

    flag[1:-1] = (log_ratio > bound) & (abs_dev > jump_floor_seconds)
    return flag


def _detect_erratic_blocks(
    times,
    warped,
    exclusion_radius,
    side_search_radius,
    side_min_points,
    floor_seconds,
    z_thresh,
    min_run,
):
    """
    Detector 2 -- sustained-block position residual, referenced from
    outside an exclusion zone.

    A run of consecutive bad DTW anchors can be internally self-consistent
    (each agrees with its immediate neighbors) while still being
    collectively wrong -- invisible to a speed/derivative check like
    _detect_tempo_jumps, and invisible to any reference window that
    includes the bad run itself, since the bad points would dominate their
    own local statistics. Instead, for each anchor, every other anchor
    within `exclusion_radius` (score-time) is excluded outright, and a
    reference line is fit with `theilslopes` (robust regression) on points
    pooled from `side_search_radius` beyond that exclusion boundary, on each
    side *independently* (left side and right side get their own separate
    fit, not one fit pooling both). As long as the exclusion radius
    comfortably covers half the worst-case bad-span length, each side's
    reference is guaranteed to come from outside the suspect span --
    fitting the two sides independently rather than pooling them matters
    for anchors sitting close to one edge of a bad span: the near side's
    pool can still catch a tail of that span (the exclusion boundary clears
    the test point itself, not necessarily the whole span), which would
    quietly bias a single pooled fit's intercept toward the bad span and
    mask the very residual it's supposed to catch. An anchor is flagged as
    a *candidate* if either side's independent fit -- on its own -- calls
    it anomalous, so a genuinely clean far side still catches the anomaly
    even when the near side is compromised.

    The flagging threshold is adaptive rather than a fixed constant: the
    dispersion of a side's own points around its own fitted line measures
    how much natural rubato this specific passage has, so a rigid/metronomic
    passage gets a tight threshold and a freely-rubato passage gets a loose
    one. `floor_seconds` keeps a near-perfectly-rigid passage from being
    hair-trigger-sensitive to ordinary DTW/quantization jitter.

    A flagged anchor is only actually returned as an outlier if it belongs
    to a contiguous run (in score-time order) of at least `min_run`
    flagged anchors -- an isolated flagged anchor is treated as normal
    rubato that happened to sit near the edge of the adaptive threshold;
    _detect_tempo_jumps is the dedicated single-anchor check.
    """
    rm = len(times)
    candidate = np.zeros(rm, dtype=bool)

    def side_reference(side_t, side_w):
        if len(side_t) < side_min_points:
            return None
        slope, intercept, _, _ = theilslopes(side_w, side_t)
        fitted = slope * side_t + intercept
        local_scale = 1.4826 * np.median(np.abs(side_w - fitted)) + 1e-9
        threshold = max(floor_seconds, z_thresh * local_scale)
        return slope, intercept, threshold

    for i in range(rm):
        t = times[i]
        left_lo = np.searchsorted(times, t - exclusion_radius - side_search_radius, side="left")
        left_hi = np.searchsorted(times, t - exclusion_radius, side="left")
        right_lo = np.searchsorted(times, t + exclusion_radius, side="right")
        right_hi = np.searchsorted(times, t + exclusion_radius + side_search_radius, side="right")

        for lo, hi in ((left_lo, left_hi), (right_lo, right_hi)):
            ref = side_reference(times[lo:hi], warped[lo:hi])
            if ref is None:
                continue
            slope, intercept, threshold = ref
            fitted_i = slope * t + intercept
            if abs(warped[i] - fitted_i) > threshold:
                candidate[i] = True
                break

    flag = np.zeros(rm, dtype=bool)
    i = 0
    while i < rm:
        if not candidate[i]:
            i += 1
            continue
        j = i
        while j < rm and candidate[j]:
            j += 1
        if j - i >= min_run:
            flag[i:j] = True
        i = j

    return flag


def _restore_large_gaps(times, jump_flag, keep, max_gap_seconds):
    """
    Safety valve, run right after the two detectors and before
    chunk-thinning: no matter how they're tuned, never leave a score-time
    gap this large between surviving anchors unaddressed.

    For each surviving gap wider than `max_gap_seconds`, anchors dropped only
    by Detector 2 (i.e. `jump_flag` is False for them -- the softer, more
    speculative filter) are restored. Detector-1-flagged anchors are never
    restored here, even if the gap stays too wide afterward: Detector 1 is a
    hard physical-plausibility cap (e.g. it catches DTW confusing a repeated
    note across a long rest for its own earlier occurrence -- score jumping
    30s while player time barely moves), so an anchor it flags is high-
    confidence wrong, and restoring it to shrink a gap would re-introduce
    exactly the artifact the detector exists to remove (a note held/warped
    across the whole rest instead of the rest being left for the curve to
    bridge smoothly). A gap that's still large after the Detector-2-only
    restore is left as is.

    Self-limiting for genuine structural gaps: if there were no raw anchors
    between the two surviving endpoints to begin with (e.g. a real
    skipped/rest passage), there's nothing to restore, so this correctly
    does nothing there instead of fabricating anchors.
    """
    keep = keep.copy()
    survivor_idx = np.where(keep)[0]
    if len(survivor_idx) <= 1:
        return keep

    gaps = np.diff(times[survivor_idx])
    for k in np.where(gaps > max_gap_seconds)[0]:
        a, b = survivor_idx[k], survivor_idx[k + 1]
        between = np.arange(a + 1, b)
        if len(between) == 0:
            continue
        keep[between[~jump_flag[between]]] = True

    return keep


def make_midi(
    midi_path,
    anchors,
    output_path,
    max_tempo_jump_pct=0.25,
    jump_floor_seconds=0.07,
    exclusion_radius_seconds=4.0,
    side_search_radius_seconds=10.0,
    side_min_points=6,
    position_outlier_floor_seconds=0.8,
    position_outlier_z=7.0,
    contiguous_run_min_points=5,
    max_gap_seconds=10.0,
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

    Outlier rejection (see _detect_tempo_jumps / _detect_erratic_blocks
    docstrings for the full reasoning) runs two independent detectors and
    drops the union of what they flag:

    - _detect_tempo_jumps (`max_tempo_jump_pct`, `jump_floor_seconds`): a
      hard, non-adaptive cap on instantaneous tempo change between adjacent
      notes -- no real performance jumps tempo by more than this between one
      note and the next, so a bigger jump means a bad DTW match, not
      expression. `jump_floor_seconds` is an absolute-magnitude floor
      alongside the relative cap, needed because dense/fast passages have
      tight anchor spacing where ordinary jitter alone is already a big
      relative change.

    - _detect_erratic_blocks (`exclusion_radius_seconds`,
      `side_search_radius_seconds`, `side_min_points`,
      `position_outlier_floor_seconds`, `position_outlier_z`,
      `contiguous_run_min_points`): catches a contiguous run of bad anchors
      that's internally self-consistent (so invisible to a speed-based
      check) by comparing each anchor's position against a reference fit
      built only from anchors outside an exclusion zone around it, with an
      adaptive threshold scaled to that passage's own natural rubato, and
      only acts on contiguous runs of flagged anchors rather than isolated
      ones.

    An earlier single rolling-median/z-score speed filter used to live here
    instead; it's been replaced because its reference window included the
    anchor being tested, and a run of consecutive bad anchors that agreed
    with each other would contaminate their own local median and slip
    through undetected.

    max_gap_seconds:
        Safety valve, applied right after the two detectors and before
        chunk-thinning. Real expressive tempo can have enough curvature that
        even a well-tuned Detector 2 window will occasionally over-flag a
        legitimate passage (this happened in practice -- an earlier tuning
        pass wiped out 86% of a real anchor set, including a 79-second gap
        through a normal, well-matched section). If any gap between
        surviving anchors in score-time exceeds `max_gap_seconds`, anchors
        dropped only by Detector 2 within that gap are restored (the softer,
        more speculative filter). Detector-1-flagged anchors are never
        restored by this valve, even if the gap remains large afterward --
        Detector 1 catches high-confidence, physically-implausible matches
        (e.g. DTW confusing a repeated note across a long rest for its own
        earlier occurrence), and restoring one to shrink a gap would
        reintroduce exactly that artifact (a note held/warped across the
        whole rest) instead of leaving the rest for the curve to bridge
        smoothly. This is also self-limiting for genuine structural gaps
        with no Detector-2-only anchors to restore at all (e.g. a real
        skipped/rest passage): the valve correctly does nothing there
        instead of fabricating anchors.

    The curve is allowed to drift smoothly past dropped anchors instead of
    forcing a tempo spike to hit them exactly. The first and last surviving
    anchors always pin the curve.

    chunk_seconds:
        Applied after outlier rejection, not before -- both detectors above
        need a dense set of points for their local statistics, so thinning
        first would starve them of context. The surviving anchors are then
        walked greedily in time order: keep a point, then keep the
        *farthest* surviving point still within `chunk_seconds` of it (or
        just the next surviving point if none are within range), dropping
        everything in between, and repeat from there. This caps every
        `chunk_seconds` span of score-time to at most 2 surviving anchors --
        picking the farthest rather than an arbitrary/nearby one maximizes
        their spacing, which is what actually prevents the curve from
        overfitting to noise ("skidding") in a densely-anchored span. Chunk
        boundaries follow the kept points rather than a fixed grid, so a
        dense cluster never gets awkwardly split across a grid line. The
        first and last surviving anchors are always kept by construction
        (the walk starts at the first and only stops once it reaches the
        last).

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
                jump_flag = _detect_tempo_jumps(
                    rep_times, rep_warped, max_tempo_jump_pct, jump_floor_seconds
                )
                block_flag = _detect_erratic_blocks(
                    rep_times,
                    rep_warped,
                    exclusion_radius_seconds,
                    side_search_radius_seconds,
                    side_min_points,
                    position_outlier_floor_seconds,
                    position_outlier_z,
                    contiguous_run_min_points,
                )

                rep_keep = ~(jump_flag | block_flag)
                rep_keep[0] = True
                rep_keep[-1] = True

                rep_keep = _restore_large_gaps(
                    rep_times, jump_flag, rep_keep, max_gap_seconds
                )

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
