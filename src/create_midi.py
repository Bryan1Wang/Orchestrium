from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, cast, overload

import mido
import numpy as np
import pretty_midi
from scipy.interpolate import PchipInterpolator
from scipy.stats import theilslopes

REFERENCE_TEMPO = (
    500_000  # fixed 120 BPM reference tempo used for tick<->second conversion
)

WarpTime = Callable[[float], float]


def _neighbor_interp_deviation(times, warped):
    """Absolute deviation of each interior anchor from where linearly
    interpolating its two immediate neighbors would put it.

    Used by _detect_tempo_jumps and _detect_absolute_position_jumps.
    Returns an array aligned to `times`/`warped`, with the first/last entries
    undefined (callers only ever index into the interior [1:-1] range).
    """
    return np.abs(
        warped[1:-1]
        - (
            warped[:-2]
            + (times[1:-1] - times[:-2])
            / (times[2:] - times[:-2])
            * (warped[2:] - warped[:-2])
        )
    )


def _detect_tempo_jumps(times, warped, max_tempo_jump_pct, jump_floor_seconds):
    """Flag anchors where tempo changes too sharply between adjacent segments.

    An anchor is flagged only if the jump is both a big relative change
    (> max_tempo_jump_pct) AND a big absolute deviation from the position
    expected by linearly interpolating its two neighbors (> jump_floor_seconds).
    The absolute floor matters because dense/fast passages have tight anchor
    spacing, where ordinary micro-timing jitter alone is already a large
    relative change.
    """
    rm = len(times)
    flag = np.zeros(rm, dtype=bool)
    if rm < 3:
        return flag

    speed_in = (times[1:-1] - times[:-2]) / (warped[1:-1] - warped[:-2])
    speed_out = (times[2:] - times[1:-1]) / (warped[2:] - warped[1:-1])
    log_ratio = np.abs(np.log(speed_out / speed_in))
    bound = np.log(1.0 + max_tempo_jump_pct)

    abs_dev = _neighbor_interp_deviation(times, warped)

    flag[1:-1] = (log_ratio > bound) & (abs_dev > jump_floor_seconds)
    return flag


def _detect_absolute_position_jumps(times, warped, absolute_deviation_seconds):
    """Flag anchors whose position deviates from linear interpolation of
    their immediate neighbors by more than absolute_deviation_seconds, in
    absolute terms, regardless of the relative tempo change.

    Independent of (not gated by) _detect_tempo_jumps's relative check --
    catches an anchor that lands an implausible amount away from its
    neighbor-implied position even where the surrounding tempo is loose/slow
    enough that the same absolute error doesn't clear max_tempo_jump_pct in
    relative terms (e.g. anchors spanning a long tutti rest, where a large
    score-time gap dilutes any relative-percentage comparison).
    """
    rm = len(times)
    flag = np.zeros(rm, dtype=bool)
    if rm < 3:
        return flag

    abs_dev = _neighbor_interp_deviation(times, warped)
    flag[1:-1] = abs_dev > absolute_deviation_seconds
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
    """Flag contiguous runs of anchors that drift from the surrounding trend.

    Catches a run of bad anchors that agree with each other locally but are
    collectively wrong -- something _detect_tempo_jumps can't see. For each
    anchor, a reference line is fit with theilslopes on the anchors outside
    `exclusion_radius`, on the left and right sides independently (so a bad
    span leaking into one side's pool can't bias that side's own fit and hide
    the anomaly there). An anchor is a candidate if either side's fit alone
    calls it anomalous. The threshold adapts to each side's own dispersion
    (`floor_seconds` sets a minimum, `z_thresh` scales it), so a rigid passage
    gets a tight threshold and an expressive one gets a loose one.

    A candidate is only returned as flagged if it's part of a contiguous run
    of at least `min_run` anchors -- an isolated candidate is more likely
    normal rubato than a real error.
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
        left_lo = np.searchsorted(
            times, t - exclusion_radius - side_search_radius, side="left"
        )
        left_hi = np.searchsorted(times, t - exclusion_radius, side="left")
        right_lo = np.searchsorted(times, t + exclusion_radius, side="right")
        right_hi = np.searchsorted(
            times, t + exclusion_radius + side_search_radius, side="right"
        )

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
    """Restore some dropped anchors to close any surviving gap > max_gap_seconds.

    Only restores anchors _detect_erratic_blocks dropped (jump_flag is False
    for them). Anchors _detect_tempo_jumps flagged are never restored, even if
    the gap stays large afterward -- that detector catches high-confidence
    physically-implausible matches (e.g. DTW matching a repeated note across a
    long rest to its own earlier occurrence), and restoring one just to shrink
    a gap would recreate that exact bug. A gap with nothing eligible to
    restore (e.g. a genuine rest, where no anchors ever existed in the gap)
    is correctly left alone.
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


def _dedup_anchors(anchors):
    """Dedup raw (score_time, player_time) anchors.

    Deduplicated by score_time (last wins), then collapsed so no two
    anchors share a player_time (ties would otherwise force a flat plateau
    into any curve fit through them).

    Returns (times, warped): strictly increasing float arrays, length >= 1.
    """
    unique = {}
    for s, y in anchors:
        unique[s] = y
    score_times = np.array(sorted(unique.keys()), dtype=float)
    player_times = np.array([unique[s] for s in score_times], dtype=float)

    n = len(score_times)
    if n == 0:
        raise ValueError("at least one alignment anchor required")

    if n == 1:
        return score_times, player_times

    distinct = np.empty(n, dtype=bool)
    distinct[0] = True
    distinct[1:] = player_times[1:] != player_times[:-1]
    return score_times[distinct], player_times[distinct]


def _reject_and_thin(
    times,
    warped,
    max_tempo_jump_pct,
    jump_floor_seconds,
    absolute_deviation_seconds,
    exclusion_radius_seconds,
    side_search_radius_seconds,
    side_min_points,
    position_outlier_floor_seconds,
    position_outlier_z,
    contiguous_run_min_points,
    max_gap_seconds,
    chunk_seconds,
):
    """Outlier-reject and thin already-deduped (times, warped) anchors.

    Outliers are dropped by _detect_tempo_jumps, _detect_absolute_position_jumps,
    and _detect_erratic_blocks, gaps left too large by that are partly closed
    by _restore_large_gaps, and the survivors are thinned by chunk_seconds so
    no chunk_seconds span of score-time keeps more than 2 anchors (picking
    the farthest pair maximizes their spacing, which is what keeps a curve
    fit through them from overfitting a densely-anchored span).

    Returns (good_times, good_warped, keep_mask): keep_mask is a boolean
    array aligned to the input `times`/`warped`.
    """
    n = len(times)
    if n < 2:
        return times, warped, np.ones(n, dtype=bool)

    rep_keep = np.ones(n, dtype=bool)
    if n >= 3:
        jump_flag = _detect_tempo_jumps(
            times, warped, max_tempo_jump_pct, jump_floor_seconds
        )
        abs_flag = _detect_absolute_position_jumps(
            times, warped, absolute_deviation_seconds
        )
        block_flag = _detect_erratic_blocks(
            times,
            warped,
            exclusion_radius_seconds,
            side_search_radius_seconds,
            side_min_points,
            position_outlier_floor_seconds,
            position_outlier_z,
            contiguous_run_min_points,
        )

        rep_keep = ~(jump_flag | abs_flag | block_flag)
        rep_keep[0] = True
        rep_keep[-1] = True

        rep_keep = _restore_large_gaps(
            times, jump_flag | abs_flag, rep_keep, max_gap_seconds
        )

    # Thin dense clusters: keep each survivor, then jump to the farthest
    # surviving point still within chunk_seconds (or the next one if none
    # are in range), dropping everything between.
    survivors = np.where(rep_keep)[0]
    if len(survivors) > 2:
        survivor_times = times[survivors]
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

        rep_keep = np.zeros(n, dtype=bool)
        rep_keep[survivors[keep_survivor]] = True

    return times[rep_keep], warped[rep_keep], rep_keep


def _clean_anchors(
    anchors,
    max_tempo_jump_pct,
    jump_floor_seconds,
    absolute_deviation_seconds,
    exclusion_radius_seconds,
    side_search_radius_seconds,
    side_min_points,
    position_outlier_floor_seconds,
    position_outlier_z,
    contiguous_run_min_points,
    max_gap_seconds,
    chunk_seconds,
):
    """Dedup, outlier-reject, and thin raw (score_time, player_time) anchors.

    Thin wrapper around _dedup_anchors + _reject_and_thin -- see those two
    for the actual algorithm.

    Returns (good_times, good_warped): strictly increasing float arrays,
    length >= 1.
    """
    times, warped = _dedup_anchors(anchors)
    if len(times) < 2:
        # Nothing left to build a curve from -- flat pass-through pinned at
        # the one surviving anchor.
        return times, warped

    good_times, good_warped, _ = _reject_and_thin(
        times,
        warped,
        max_tempo_jump_pct,
        jump_floor_seconds,
        absolute_deviation_seconds,
        exclusion_radius_seconds,
        side_search_radius_seconds,
        side_min_points,
        position_outlier_floor_seconds,
        position_outlier_z,
        contiguous_run_min_points,
        max_gap_seconds,
        chunk_seconds,
    )
    return good_times, good_warped


def _fit_segment(
    seg_times, seg_warped, fallback_speed_before=1.0, fallback_speed_after=1.0
):
    """Build a score_time -> player_time closure over one contiguous run of
    cleaned anchors.

    A single anchor produces a flat pass-through that extrapolates linearly
    using fallback_speed_before/after -- useful when a segment was left with
    only one anchor by a neighboring tutti-rest split, so it can borrow that
    rest's speed instead of an arbitrary default. Two or more anchors fit a
    monotonic PCHIP curve, extrapolating outside its range at the edge speed
    of the two outermost anchors on that side.
    """
    if len(seg_times) == 1:
        anchor_t = seg_times[0]
        anchor_p = seg_warped[0]

        def flat_warp_time(score_time):
            speed = (
                fallback_speed_before
                if score_time <= anchor_t
                else fallback_speed_after
            )
            return anchor_p + (score_time - anchor_t) / speed

        return flat_warp_time

    curve = PchipInterpolator(seg_times, seg_warped)
    edge_speed_start = (seg_times[1] - seg_times[0]) / (seg_warped[1] - seg_warped[0])
    edge_speed_end = (seg_times[-1] - seg_times[-2]) / (seg_warped[-1] - seg_warped[-2])

    def warp_time(score_time):
        # Outside the fitted range, extrapolate at the edge speed instead of
        # calling the curve, which is only defined between seg_times[0] and
        # seg_times[-1].
        if score_time <= seg_times[0]:
            return seg_warped[0] + (score_time - seg_times[0]) / edge_speed_start
        if score_time >= seg_times[-1]:
            return seg_warped[-1] + (score_time - seg_times[-1]) / edge_speed_end
        return float(curve(score_time))

    return warp_time


def _estimate_start_speed(
    good_times, good_warped, window_seconds=10.0, fallback_speed=1.0
):
    """Estimate the tempo (score-seconds per player-second) at the very
    start of the piece, for extrapolating before the first anchor.

    Instead of just the immediate next cleaned anchor, uses the farthest
    cleaned anchor within `window_seconds` of score time from the first
    anchor -- the secant slope over that longer span averages out one
    atypical early note (e.g. a slightly rushed or dragged entrance)
    instead of being fully determined by it. Falls back to the 2-anchor
    slope when only one anchor falls in the window (i.e. the very next
    anchor is already farther than window_seconds away), and to
    `fallback_speed` when there's only one anchor in total.
    """
    if len(good_times) < 2:
        return fallback_speed

    in_window = good_times <= good_times[0] + window_seconds
    last = np.where(in_window)[0][-1]
    if last == 0:
        last = 1

    return (good_times[last] - good_times[0]) / (good_warped[last] - good_warped[0])


def _windowed_edge_speed(
    good_times, good_warped, near_idx, bound_idx, window_seconds, min_points, forward
):
    """Robust tempo estimate on one side of a rest: the Theil-Sen median of
    slopes among `near_idx` and the anchors within `window_seconds` of score
    time on the given side, without crossing `bound_idx`.

    Same idea as _estimate_start_speed, generalized to look in either
    direction and to respect a hard bound (so the window can't reach across
    a neighboring rest). A single secant is sensitive to one atypical note
    right at the rest's edge, so instead the slope is fit with
    scipy.stats.theilslopes (the same robust estimator _detect_erratic_blocks
    uses) over every anchor gathered on this side -- the median of all
    pairwise slopes among those points, insensitive to any one of them being
    an outlier (e.g. DTW having anchored a note across the rest to the wrong
    side).

    `window_seconds` alone can leave too few points for a meaningful median
    when a rest is long and nearby anchors are sparse, so at least
    `min_points` anchors are gathered regardless of window_seconds (falling
    back to every anchor available up to `bound_idx` if fewer than
    `min_points` exist there) -- the window only grows past `min_points` when
    it naturally contains more (dense passages stay capped at
    `window_seconds` as before). With exactly 2 points total this reduces to
    the previous single-secant behavior. Returns None if there is no
    neighbor at all within bound_idx.
    """
    if forward:
        if near_idx >= bound_idx or near_idx >= len(good_times) - 1:
            return None
        idxs = np.arange(near_idx + 1, bound_idx + 1)
        in_window = good_times[idxs] <= good_times[near_idx] + window_seconds
        count = max(int(in_window.sum()), min(min_points, len(idxs)))
        side_idxs = idxs[:count]
    else:
        if near_idx <= bound_idx:
            return None
        idxs = np.arange(bound_idx, near_idx)
        in_window = good_times[idxs] >= good_times[near_idx] - window_seconds
        count = max(int(in_window.sum()), min(min_points, len(idxs)))
        side_idxs = idxs[-count:]

    side_idxs = np.concatenate([[near_idx], side_idxs])
    side_times = good_times[side_idxs]
    side_warped = good_warped[side_idxs]

    if len(side_idxs) == 2:
        return (side_times[1] - side_times[0]) / (side_warped[1] - side_warped[0])
    slope, _, _, _ = theilslopes(side_times, side_warped)
    return cast(float, slope)


def _compute_intro_shift(
    good_times, good_warped, start_speed, score_start=0.0, lead_in_seconds=1.0
):
    """Player-time shift needed so the leading intro has time to play.

    Given `start_speed` (see _estimate_start_speed), estimates how long the
    intro (from score_start to the first anchor) should naturally take, and
    compares that to how much player time is actually available before the
    first anchor (good_warped[0], since player time can't start before 0 --
    the start of the recording). If the natural duration doesn't fit,
    returns the shortfall so every anchor can be pushed forward by that
    much. lead_in_seconds is added unconditionally on top, so there's always
    a beat of silence before the first note even with zero shortfall.
    """
    natural_duration = (good_times[0] - score_start) / start_speed
    shortfall = max(0.0, natural_duration - good_warped[0])
    return shortfall + lead_in_seconds


@dataclass
class RestRecord:
    rest_start_score: float
    rest_end_score: float
    tutti_speed: float
    natural_duration: float
    computed_duration: float
    shortfall: float
    # The real, verified recording-pause boundary when available (see
    # _build_split_warp), else a warp-curve estimate.
    natural_end_player_time: float
    player_start: float
    player_end: float


def _build_split_warp(
    anchors,
    tutti_rests,
    max_tempo_jump_pct,
    jump_floor_seconds,
    absolute_deviation_seconds,
    exclusion_radius_seconds,
    side_search_radius_seconds,
    side_min_points,
    position_outlier_floor_seconds,
    position_outlier_z,
    contiguous_run_min_points,
    max_gap_seconds,
    chunk_seconds,
    tempo_window_seconds=5.0,
    tempo_window_min_points=4,
):
    """Build a warp_time() spanning the whole piece, tutti rests included,
    as a single PCHIP curve -- no constant-speed segment carve-out for
    rests, and no synthetic rest-boundary anchors.

    Each rest's tutti_speed/boundary/shortfall is estimated from a
    preliminary, throwaway outlier-rejection pass over the deduped anchors
    (see the Step 2.5 comment below) rather than the fully raw anchors --
    the near-side anchor bordering a rest is used directly as the
    boundary's extrapolation origin, so it needs to have already survived
    outlier rejection, or a single bad DTW match right at a rest's edge
    (e.g. a repeated note matched to the wrong occurrence) silently
    inflates that rest's computed shortfall. This preliminary cleaning is
    only used for these diagnostics, though, and is discarded immediately
    after -- it is still returned via RestRecord for
    audio_utils.extend_rec_pauses to know how much silence to splice into
    the recording. But instead of treating each rest as its own
    constant-speed segment (which required forcing a synthetic anchor at
    each boundary into the cleaning pool -- a synthetic point derived
    directly from the anchor bordering the rest, which made that anchor's
    own badness tautologically self-consistent and immune to outlier
    detection), the computed shortfall is applied as a plain shift to the
    real (raw, uncleaned) anchors after each rest, and the whole shifted
    set is independently cleaned from scratch and fit with one ordinary
    PCHIP curve, exactly like the no-tutti_rests path. A real anchor
    bordering a rest is judged against its own real neighbors like any
    other anchor when the actual curve is fit -- nothing synthetic to hide
    behind, and nothing carried over from the preliminary pass above.

    Accepted trade-off: PCHIP's derivative at a knot depends on the secants
    to both neighbors (Fritsch-Carlson), so the tempo curve immediately
    around a rest's edges can be mildly influenced by the pace across the
    whole rest rather than purely local tempo. This is a known, deliberate
    trade for simplicity and for avoiding the self-referential-anchor
    problem above -- not something this function tries to eliminate.
    """
    raw_times, raw_warped = _dedup_anchors(anchors)
    N = len(raw_times)
    raw_rests = sorted(tutti_rests, key=lambda r: r[0])
    raw_verified_end = [r[2] if len(r) > 2 else None for r in raw_rests]

    # Step 1: locate each rest against raw_times (pre-cleaning). A genuine
    # score rest can never contain an anchor (tempo_sync.align only anchors
    # at real score events), so right_start_idx == left_end_idx + 1 whenever
    # both exist.
    left_end_idx = [
        int(np.searchsorted(raw_times, r[0], side="right")) - 1 for r in raw_rests
    ]
    right_start_idx = [
        int(np.searchsorted(raw_times, r[1], side="left")) for r in raw_rests
    ]

    # Step 2: coalesce rests that would leave an empty solo segment between
    # them into one combined rest, so every interior segment keeps >= 1
    # anchor.
    rests = []  # (start, end, left_end_idx, right_start_idx, verified_end)
    cur_start, cur_end = raw_rests[0][0], raw_rests[0][1]
    cur_le, cur_rs = left_end_idx[0], right_start_idx[0]
    cur_verified_end = raw_verified_end[0]
    for k in range(1, len(raw_rests)):
        if cur_rs > left_end_idx[k]:
            cur_end = raw_rests[k][1]
            cur_rs = right_start_idx[k]
            cur_verified_end = raw_verified_end[k]
        else:
            rests.append((cur_start, cur_end, cur_le, cur_rs, cur_verified_end))
            cur_start, cur_end = raw_rests[k][0], raw_rests[k][1]
            cur_le, cur_rs = left_end_idx[k], right_start_idx[k]
            cur_verified_end = raw_verified_end[k]
    rests.append((cur_start, cur_end, cur_le, cur_rs, cur_verified_end))
    R = len(rests)

    # Step 2.5: a preliminary, throwaway outlier-rejection pass over the
    # full raw anchor set, used only to make Step 3's boundary/speed
    # estimates below more trustworthy -- it does NOT feed the real curve
    # fit in Step 6, which independently re-cleans the raw (shifted)
    # anchors from scratch, so outlier rejection still runs "for real" on
    # uncleaned data later. It's needed here because _windowed_edge_speed's
    # Theil-Sen slope is only robust to noise *within* its window -- the
    # near-side anchor bordering the rest is still used directly, below, as
    # the extrapolation origin, so if that one anchor is itself a bad DTW
    # match (e.g. a repeated note across the rest matched to the wrong
    # occurrence), the boundary comes out wrong regardless of how robust
    # the slope estimate is.
    prelim_times, prelim_warped, _ = _reject_and_thin(
        raw_times,
        raw_warped,
        max_tempo_jump_pct,
        jump_floor_seconds,
        absolute_deviation_seconds,
        exclusion_radius_seconds,
        side_search_radius_seconds,
        side_min_points,
        position_outlier_floor_seconds,
        position_outlier_z,
        contiguous_run_min_points,
        max_gap_seconds,
        chunk_seconds,
    )
    Np = len(prelim_times)
    prelim_le = [
        int(np.searchsorted(prelim_times, rests[k][0], side="right")) - 1
        for k in range(R)
    ]
    prelim_rs = [
        int(np.searchsorted(prelim_times, rests[k][1], side="left")) for k in range(R)
    ]

    # Step 3: edge speed just before / just after each rest, fit as the
    # Theil-Sen median of slopes over up to tempo_window_seconds of score
    # time on each side (at least tempo_window_min_points anchors,
    # expanding past the time window if the rest is long and nearby anchors
    # are sparse) instead of a single adjacent anchor pair, so one atypical
    # or DTW-mismatched anchor right at the rest's edge doesn't fully
    # determine the estimate. Bounded so the window can't reach across a
    # neighboring rest. Estimated from the preliminarily-cleaned anchors
    # (Step 2.5) rather than the raw ones -- the tutti tempo only needs to
    # be approximate, but the near-side anchor itself is trusted directly
    # (see Step 3b), so it needs to have already survived outlier
    # rejection.
    lo_bounds = [prelim_rs[k - 1] if k > 0 else 0 for k in range(R)]
    hi_bounds = [prelim_le[k + 1] if k < R - 1 else Np - 1 for k in range(R)]

    # Step 3b: boundary player-times, extrapolated one-sided from before_speed
    # /after_speed above -- never a curve spanning the rest. A single PCHIP
    # curve fit across the whole piece would work here too, but its
    # monotone-derivative estimate at a knot depends on the secants on BOTH
    # sides (Fritsch-Carlson), so the derivative right at a rest's edge would
    # get pulled by the secant reaching across the rest's own silence to the
    # anchor on the far side -- an artificially stretched interval reflecting
    # nothing about the real tempo there. Extrapolating linearly from only
    # the near-side anchor avoids that regardless of how wide the rest is.
    tutti_speed = []
    left_boundary = []
    right_boundary = []
    for k in range(R):
        start, end, _, _, _ = rests[k]
        le, rs = prelim_le[k], prelim_rs[k]
        before_speed = _windowed_edge_speed(
            prelim_times,
            prelim_warped,
            le,
            lo_bounds[k],
            tempo_window_seconds,
            tempo_window_min_points,
            forward=False,
        )
        after_speed = _windowed_edge_speed(
            prelim_times,
            prelim_warped,
            rs,
            hi_bounds[k],
            tempo_window_seconds,
            tempo_window_min_points,
            forward=True,
        )
        has_left = before_speed is not None and 0 <= le < Np
        has_right = after_speed is not None and 0 <= rs < Np

        if has_left:
            left_boundary.append(
                prelim_warped[le] + (start - prelim_times[le]) / before_speed
            )
        if has_right:
            right_boundary.append(
                prelim_warped[rs] - (prelim_times[rs] - end) / after_speed
            )

        if has_left and has_right:
            assert before_speed is not None and after_speed is not None
            tutti_speed.append((before_speed + after_speed) / 2)
        elif has_left:
            tutti_speed.append(before_speed)
            right_boundary.append(left_boundary[-1] + (end - start) / before_speed)
        elif has_right:
            tutti_speed.append(after_speed)
            left_boundary.append(right_boundary[-1] - (end - start) / after_speed)
        else:
            print(
                f"Warning: no surrounding anchors to estimate tutti speed for "
                f"rest at {rests[k][0]:.2f}-{rests[k][1]:.2f}s; defaulting to 1.0x"
            )
            tutti_speed.append(1.0)
            # No anchor on either side at all -- only possible if every
            # anchor on that entire side of the rest was dropped by the
            # preliminary cleaning pass (or was already missing from the
            # raw pool). Nothing to extrapolate from; treat the rest as
            # zero-width at whatever player-time is otherwise in scope.
            fallback = (
                prelim_warped[le]
                if 0 <= le < Np
                else (prelim_warped[rs] if 0 <= rs < Np else raw_warped[0])
            )
            left_boundary.append(fallback)
            right_boundary.append(fallback)

    # Step 4: natural/computed/shortfall durations, from the one-sided
    # boundary extrapolations above (not a curve spanning the rest -- see
    # Step 3b).
    natural_duration = []
    computed_duration = []
    shortfall = []
    natural_end_player_time = []
    for k in range(R):
        start, end, _, _, verified_end = rests[k]
        nd = right_boundary[k] - left_boundary[k]
        cd = (end - start) / tutti_speed[k]
        natural_duration.append(nd)
        computed_duration.append(cd)
        shortfall.append(max(0.0, cd - nd))
        # Prefer the real, verified recording-pause boundary (the actual
        # onset of the next transcribed note, from
        # rest_utils.verify_recording_pause) over the extrapolated estimate --
        # right_boundary[k] can land after that onset and cause
        # audio_utils.extend_rec_pauses to splice silence into the middle
        # of the next note instead of before it.
        natural_end_player_time.append(
            verified_end if verified_end is not None else right_boundary[k]
        )

    # Step 5: accumulate shift forward in ascending score-time order, over
    # the raw (not yet cleaned) player-time array.
    shifted_raw_warped = raw_warped.copy()
    running_shift = 0.0
    prev_right_idx = 0
    rest_records = []
    for k in range(R):
        start, end, _, rs, _ = rests[k]
        if rs > prev_right_idx:
            shifted_raw_warped[prev_right_idx:rs] += running_shift

        player_start = left_boundary[k] + running_shift
        player_end = player_start + natural_duration[k] + shortfall[k]

        rest_records.append(
            RestRecord(
                rest_start_score=start,
                rest_end_score=end,
                tutti_speed=tutti_speed[k],
                natural_duration=natural_duration[k],
                computed_duration=computed_duration[k],
                shortfall=shortfall[k],
                natural_end_player_time=natural_end_player_time[k],
                player_start=player_start,
                player_end=player_end,
            )
        )

        running_shift += shortfall[k]
        prev_right_idx = rs

    if prev_right_idx < N:
        shifted_raw_warped[prev_right_idx:] += running_shift

    # Step 6: clean the shifted anchors (dedup already done above) and fit
    # one PCHIP curve across the whole piece, exactly like the
    # no-tutti_rests path -- no synthetic anchors, no segment split.
    good_times, good_warped, _ = _reject_and_thin(
        raw_times,
        shifted_raw_warped,
        max_tempo_jump_pct,
        jump_floor_seconds,
        absolute_deviation_seconds,
        exclusion_radius_seconds,
        side_search_radius_seconds,
        side_min_points,
        position_outlier_floor_seconds,
        position_outlier_z,
        contiguous_run_min_points,
        max_gap_seconds,
        chunk_seconds,
    )
    warp_time = _fit_segment(good_times, good_warped)

    return warp_time, rest_records, good_times, good_warped


@overload
def build_warp_curve(
    anchors,
    max_tempo_jump_pct: float = 0.25,
    jump_floor_seconds: float = 0.07,
    absolute_deviation_seconds: float = 1.0,
    exclusion_radius_seconds: float = 4.0,
    side_search_radius_seconds: float = 10.0,
    side_min_points: int = 6,
    position_outlier_floor_seconds: float = 0.8,
    position_outlier_z: float = 7.0,
    contiguous_run_min_points: int = 5,
    max_gap_seconds: float = 10.0,
    chunk_seconds: float = 1,
    tutti_rests=None,
    tempo_window_seconds: float = 5.0,
    tempo_window_min_points: int = 4,
    extend_intro: bool = False,
    score_start: float = 0.0,
    intro_lead_in_seconds: float = 1.0,
    start_speed_window_seconds: float = 10.0,
    intro_score_tempo_weight_pct: float = 0.0,
    return_diagnostics: Literal[False] = False,
) -> WarpTime: ...


@overload
def build_warp_curve(
    anchors,
    max_tempo_jump_pct: float = 0.25,
    jump_floor_seconds: float = 0.07,
    absolute_deviation_seconds: float = 1.0,
    exclusion_radius_seconds: float = 4.0,
    side_search_radius_seconds: float = 10.0,
    side_min_points: int = 6,
    position_outlier_floor_seconds: float = 0.8,
    position_outlier_z: float = 7.0,
    contiguous_run_min_points: int = 5,
    max_gap_seconds: float = 10.0,
    chunk_seconds: float = 1,
    tutti_rests=None,
    tempo_window_seconds: float = 5.0,
    tempo_window_min_points: int = 4,
    extend_intro: bool = False,
    score_start: float = 0.0,
    intro_lead_in_seconds: float = 1.0,
    start_speed_window_seconds: float = 10.0,
    intro_score_tempo_weight_pct: float = 0.0,
    *,
    return_diagnostics: Literal[True],
) -> tuple[WarpTime, list[RestRecord] | None, float]: ...


def build_warp_curve(
    anchors,
    max_tempo_jump_pct: float = 0.25,
    jump_floor_seconds: float = 0.07,
    absolute_deviation_seconds: float = 1.0,
    exclusion_radius_seconds: float = 4.0,
    side_search_radius_seconds: float = 10.0,
    side_min_points: int = 6,
    position_outlier_floor_seconds: float = 0.8,
    position_outlier_z: float = 7.0,
    contiguous_run_min_points: int = 5,
    max_gap_seconds: float = 10.0,
    chunk_seconds: float = 1,
    tutti_rests=None,
    tempo_window_seconds: float = 5.0,
    tempo_window_min_points: int = 4,
    extend_intro: bool = False,
    score_start: float = 0.0,
    intro_lead_in_seconds: float = 1.0,
    start_speed_window_seconds: float = 10.0,
    intro_score_tempo_weight_pct: float = 0.0,
    return_diagnostics: bool = False,
) -> WarpTime | tuple[WarpTime, list[RestRecord] | None, float]:
    """Build a score_time -> player_time warp function from DTW anchors.

    Cleans `anchors` via _clean_anchors (dedup, outlier rejection, thinning)
    and fits a monotonic PCHIP curve through the survivors, extrapolating
    linearly outside the anchored range from the edge speed of the two
    outermost surviving anchors.

    If `tutti_rests` (a list of (rest_start_score, rest_end_score) score-time
    pairs, or (rest_start_score, rest_end_score, verified_gap_end) triples --
    e.g. from rest_utils.get_verified_tutti_rests, which returns the triple
    form) is given, each rest's tutti_speed/boundary/shortfall is still
    estimated and returned via `return_diagnostics=True`'s per-rest
    RestRecord list (tutti_speed, natural vs. computed duration, shortfall,
    etc.) -- see _build_split_warp -- but the curve itself is still a
    single PCHIP spanning the whole piece, rests included; a rest is not
    treated as its own constant-speed segment. When present, verified_gap_end
    (the real player-time boundary rest_utils.verify_recording_pause found
    in the recording's own transcription) is used as
    RestRecord.natural_end_player_time instead of the warp curve's own
    estimate, since that estimate can land after the next note's true
    onset. Each rest's speed is estimated as a Theil-Sen median of slopes
    over up to `tempo_window_seconds` of score time on either side (at least
    `tempo_window_min_points` anchors, expanding past the time window if
    needed -- see _windowed_edge_speed), not just the single nearest anchor
    pair, falling back to a narrower window near piece boundaries or a
    neighboring rest.

    If `extend_intro=True`, the leading intro -- from `score_start` to the
    first cleaned anchor -- gets its full natural duration instead of
    whatever player time was incidentally available before the first
    anchor, plus a flat `intro_lead_in_seconds` of silence before the first
    note either way (see _estimate_start_speed / _compute_intro_shift). The
    intro region itself (score_time <= the first cleaned anchor) is always
    linear/constant-speed, never the PCHIP curve -- only its speed and
    placement change here. Everything from the first anchor onward is
    shifted forward in player time by the same constant amount, so it
    applies identically regardless of whether `tutti_rests` was used.
    `return_diagnostics=True` additionally returns the computed
    `intro_shift` (0.0 if `extend_intro=False`) as a third tuple element.
    Note: when combined with `tutti_rests`, the returned RestRecords'
    player_start/player_end/natural_end_player_time fields are relative to
    the pre-shift timeline -- add `intro_shift` to them for the true final
    player time.
    """
    if not tutti_rests:
        good_times, good_warped = _clean_anchors(
            anchors,
            max_tempo_jump_pct,
            jump_floor_seconds,
            absolute_deviation_seconds,
            exclusion_radius_seconds,
            side_search_radius_seconds,
            side_min_points,
            position_outlier_floor_seconds,
            position_outlier_z,
            contiguous_run_min_points,
            max_gap_seconds,
            chunk_seconds,
        )
        warp_time = _fit_segment(good_times, good_warped)
        diagnostics = None
    else:
        warp_time, diagnostics, good_times, good_warped = _build_split_warp(
            anchors,
            tutti_rests,
            max_tempo_jump_pct,
            jump_floor_seconds,
            absolute_deviation_seconds,
            exclusion_radius_seconds,
            side_search_radius_seconds,
            side_min_points,
            position_outlier_floor_seconds,
            position_outlier_z,
            contiguous_run_min_points,
            max_gap_seconds,
            chunk_seconds,
            tempo_window_seconds,
            tempo_window_min_points,
        )

    intro_shift = 0.0
    if extend_intro:
        start_speed = _estimate_start_speed(
            good_times, good_warped, start_speed_window_seconds
        )
        # Blend the measured (interpolated) start speed with the score's own
        # tempo (speed 1.0 = score pace): weight% score + remaining% measured.
        w = min(max(intro_score_tempo_weight_pct, 0.0), 100.0) / 100.0
        start_speed = 1.0 * w + start_speed * (1.0 - w)
        intro_shift = _compute_intro_shift(
            good_times, good_warped, start_speed, score_start, intro_lead_in_seconds
        )
        inner_warp_time = warp_time
        intro_boundary = good_times[0]
        intro_boundary_player = good_warped[0] + intro_shift

        def _extended_warp_time(
            score_time,
            _inner=inner_warp_time,
            _boundary=intro_boundary,
            _boundary_player=intro_boundary_player,
            _speed=start_speed,
            _shift=intro_shift,
        ):
            # Before the first anchor, use the robustly-estimated start
            # speed directly (constant-speed line) instead of delegating to
            # _inner, whose own edge extrapolation there is only a naive
            # 2-anchor slope -- otherwise the shift would be sized for one
            # speed but rendered at another.
            if score_time <= _boundary:
                return _boundary_player + (score_time - _boundary) / _speed
            return _inner(score_time) + _shift

        warp_time = _extended_warp_time

    return (warp_time, diagnostics, intro_shift) if return_diagnostics else warp_time


def warp_midi_file(midi_path, warp_time, output_path):
    """Warp a MIDI file's timing according to an already-built warp curve.

    Walks every track of `midi_path`, converting each event's absolute tick
    to score-time seconds, applying `warp_time` (a plain score_time ->
    player_time callable, e.g. from `build_warp_curve`), and converting back
    to ticks against a single fixed REFERENCE_TEMPO.
    """
    # Ticks are re-emitted against one fixed REFERENCE_TEMPO rather than the
    # source file's own tempo curve: pm.tick_to_time is only used to recover
    # each message's original score-time, which is then warped and converted
    # straight back to a tick. Keeping the source's set_tempo messages and
    # just moving their ticks wouldn't reproduce the intended warp, since
    # MIDI tempo is piecewise-constant between messages and the curve isn't.
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
        # Source file had no set_tempo message -- add one so the output's
        # tick<->time meaning is still well-defined.
        new_mid.tracks[0].insert(
            0, mido.MetaMessage("set_tempo", tempo=REFERENCE_TEMPO, time=0)
        )

    new_mid.save(output_path)
