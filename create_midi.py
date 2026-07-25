import bisect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, overload

import mido
import numpy as np
import pretty_midi
from scipy.interpolate import PchipInterpolator
from scipy.stats import theilslopes

REFERENCE_TEMPO = 500_000  # fixed 120 BPM reference tempo used for tick<->second conversion

WarpTime = Callable[[float], float]


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


def _clean_anchors(
    anchors,
    max_tempo_jump_pct,
    jump_floor_seconds,
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

    `anchors` is deduplicated by score_time (last wins), then collapsed so
    no two anchors share a player_time (ties would otherwise force a flat
    plateau into any curve fit through them). Outliers are then dropped by
    _detect_tempo_jumps and _detect_erratic_blocks, gaps left too large by
    that are partly closed by _restore_large_gaps, and the survivors are
    thinned by chunk_seconds so no chunk_seconds span of score-time keeps
    more than 2 anchors (picking the farthest pair maximizes their spacing,
    which is what keeps a curve fit through them from overfitting a
    densely-anchored span).

    Returns (good_times, good_warped): strictly increasing float arrays,
    length >= 1.
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
        return score_times, player_times

    m = n
    distinct = np.empty(m, dtype=bool)
    distinct[0] = True
    distinct[1:] = player_times[1:] != player_times[:-1]
    cand_times = score_times[distinct]
    cand_warped = player_times[distinct]

    if len(cand_times) < 2:
        # Nothing left to build a curve from -- flat pass-through pinned at
        # the one surviving anchor.
        return cand_times, cand_warped

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

        rep_keep = _restore_large_gaps(rep_times, jump_flag, rep_keep, max_gap_seconds)

    # Thin dense clusters: keep each survivor, then jump to the farthest
    # surviving point still within chunk_seconds (or the next one if none
    # are in range), dropping everything between.
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

    return rep_times[rep_keep], rep_warped[rep_keep]


def _fit_segment(seg_times, seg_warped, fallback_speed_before=1.0, fallback_speed_after=1.0):
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
            speed = fallback_speed_before if score_time <= anchor_t else fallback_speed_after
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


@dataclass
class RestRecord:
    rest_start_score: float
    rest_end_score: float
    tutti_speed: float
    natural_duration: float
    computed_duration: float
    shortfall: float
    natural_end_player_time: float
    player_start: float
    player_end: float


def _build_split_warp(good_times, good_warped, tutti_rests):
    """Build a warp_time() that treats each tutti rest as a constant-speed
    segment instead of letting a single PCHIP curve interpolate through it.

    See create_midi's module-level design notes (plan doc) for the full
    algorithm; summarized inline at each step below.
    """
    N = len(good_times)
    raw_rests = sorted(tutti_rests, key=lambda r: r[0])

    # Step 1: locate each rest against good_times. A genuine score rest can
    # never contain an anchor (tempo_sync.align only anchors at real score
    # events), so right_start_idx == left_end_idx + 1 whenever both exist.
    left_end_idx = [int(np.searchsorted(good_times, s, side="right")) - 1 for s, _ in raw_rests]
    right_start_idx = [int(np.searchsorted(good_times, e, side="left")) for _, e in raw_rests]

    # Step 2: coalesce rests that would leave an empty solo segment between
    # them into one combined rest, so every interior segment keeps >= 1
    # anchor.
    rests = []  # (start, end, left_end_idx, right_start_idx)
    cur_start, cur_end = raw_rests[0]
    cur_le, cur_rs = left_end_idx[0], right_start_idx[0]
    for k in range(1, len(raw_rests)):
        if cur_rs > left_end_idx[k]:
            cur_end = raw_rests[k][1]
            cur_rs = right_start_idx[k]
        else:
            rests.append((cur_start, cur_end, cur_le, cur_rs))
            cur_start, cur_end = raw_rests[k]
            cur_le, cur_rs = left_end_idx[k], right_start_idx[k]
    rests.append((cur_start, cur_end, cur_le, cur_rs))
    R = len(rests)

    # Step 3: local edge speed just before / just after each rest, bounded
    # so it can't reach across a neighboring rest.
    lo_bounds = [rests[k - 1][3] if k > 0 else 0 for k in range(R)]
    hi_bounds = [rests[k + 1][2] if k < R - 1 else N - 1 for k in range(R)]

    tutti_speed = []
    for k in range(R):
        _, _, le, rs = rests[k]
        before_speed = None
        if le >= 1 and le - 1 >= lo_bounds[k]:
            a, b = le - 1, le
            before_speed = (good_times[b] - good_times[a]) / (good_warped[b] - good_warped[a])
        after_speed = None
        if rs <= N - 2 and rs + 1 <= hi_bounds[k]:
            a, b = rs, rs + 1
            after_speed = (good_times[b] - good_times[a]) / (good_warped[b] - good_warped[a])

        if before_speed is not None and after_speed is not None:
            tutti_speed.append((before_speed + after_speed) / 2)
        elif before_speed is not None:
            tutti_speed.append(before_speed)
        elif after_speed is not None:
            tutti_speed.append(after_speed)
        else:
            print(
                f"Warning: no surrounding anchors to estimate tutti speed for "
                f"rest at {rests[k][0]:.2f}-{rests[k][1]:.2f}s; defaulting to 1.0x"
            )
            tutti_speed.append(1.0)

    # Step 4: natural/computed/shortfall durations, from the original
    # unshifted curve -- a uniform shift never changes the slope between two
    # anchors inside the same shifted group, so this stays correct even
    # after step 5 applies shifts.
    base_warp = _fit_segment(good_times, good_warped)

    natural_duration = []
    computed_duration = []
    shortfall = []
    natural_end_player_time = []
    for k in range(R):
        start, end, _, _ = rests[k]
        nd = base_warp(end) - base_warp(start)
        cd = (end - start) / tutti_speed[k]
        natural_duration.append(nd)
        computed_duration.append(cd)
        shortfall.append(max(0.0, cd - nd))
        natural_end_player_time.append(base_warp(end))

    # Step 5: accumulate shift forward in ascending score-time order.
    shifted_warped = good_warped.copy()
    running_shift = 0.0
    prev_right_idx = 0
    rest_records = []
    for k in range(R):
        start, end, _, rs = rests[k]
        if rs > prev_right_idx:
            shifted_warped[prev_right_idx:rs] += running_shift

        player_start = base_warp(start) + running_shift
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
        shifted_warped[prev_right_idx:] += running_shift

    # Step 6: build one _fit_segment closure per solo segment (the anchor
    # ranges between consecutive rests). Each segment gets its bordering
    # rest's own boundary value(s) -- not just a fallback speed -- spliced
    # in as real knots. This is required for correctness, not just a nicety:
    # segment_funcs[i] is fit only from this segment's own local anchors, so
    # PCHIP's derivative at its edge knot (computed from local data only)
    # generally disagrees with the derivative base_warp used to derive the
    # neighboring rest's player_start/player_end (fit across ALL anchors,
    # pulled by the secant slope spanning the whole rest gap). Without an
    # explicit shared knot, segment_funcs[i]'s own edge extrapolation can
    # overshoot past the rest's boundary value, and the rest's closed-form
    # linear interpolation then drops back down to it -- a real decrease,
    # which the downstream MIDI tick conversion correctly rejects. Splicing
    # the rest's own value in as a knot guarantees exact continuity by
    # construction; monotonicity follows because PCHIP preserves
    # monotonicity of its input data, and player_start/player_end are
    # provably on the correct side of this segment's adjacent real anchors
    # (player_start = base_warp(rest_start) > good_warped[le] since
    # base_warp is itself monotonic and good_times[le] < rest_start, and
    # symmetrically for player_end).
    segment_ranges = []
    prev = 0
    for _, _, le, rs in rests:
        segment_ranges.append((prev, le))
        prev = rs
    segment_ranges.append((prev, N - 1))

    segment_funcs = {}
    for i, (lo, hi) in enumerate(segment_ranges):
        if lo <= hi:
            seg_times = list(good_times[lo : hi + 1])
            seg_warped = list(shifted_warped[lo : hi + 1])
        else:
            seg_times, seg_warped = [], []  # edge segment with no real anchors

        # Skip splicing in a boundary knot if it wouldn't be strictly beyond
        # this segment's own adjacent real anchor (possible only in the
        # degenerate case of a zero-duration gap between a note's onset and
        # a rest boundary) -- PCHIP requires strictly increasing x, and the
        # real anchor already covers that exact score_time.
        if i > 0:
            prev_rest = rest_records[i - 1]
            if not seg_times or prev_rest.rest_end_score < seg_times[0]:
                seg_times.insert(0, prev_rest.rest_end_score)
                seg_warped.insert(0, prev_rest.player_end)
        if i < R:
            next_rest = rest_records[i]
            if not seg_times or next_rest.rest_start_score > seg_times[-1]:
                seg_times.append(next_rest.rest_start_score)
                seg_warped.append(next_rest.player_start)

        border_before = tutti_speed[i - 1] if i > 0 else None
        border_after = tutti_speed[i] if i < R else None
        fb = border_before if border_before is not None else (border_after or 1.0)
        fa = border_after if border_after is not None else (border_before or 1.0)

        segment_funcs[i] = _fit_segment(
            np.array(seg_times), np.array(seg_warped), fb, fa
        )

    # Step 7: dispatch. Score times inside a rest use the rest's own
    # closed-form linear interpolation; everything else delegates to the
    # enclosing solo segment. Every segment index 0..R now has a func
    # (each borders at least one rest, so it has at least that rest's own
    # boundary point as a knot), so no separate edge-of-piece fallback is
    # needed here -- _fit_segment's own 1-point extrapolation branch
    # already covers a piece that starts or ends inside a rest.
    rest_starts = [r.rest_start_score for r in rest_records]
    rest_ends = [r.rest_end_score for r in rest_records]

    def warp_time(score_time):
        k = bisect.bisect_right(rest_starts, score_time) - 1
        if k >= 0 and score_time <= rest_ends[k]:
            r = rest_records[k]
            span = r.rest_end_score - r.rest_start_score
            if span <= 0:
                return r.player_start
            frac = (score_time - r.rest_start_score) / span
            return r.player_start + frac * (r.player_end - r.player_start)

        return segment_funcs[k + 1](score_time)

    return warp_time, rest_records


@overload
def build_warp_curve(
    anchors,
    max_tempo_jump_pct: float = 0.25,
    jump_floor_seconds: float = 0.07,
    exclusion_radius_seconds: float = 4.0,
    side_search_radius_seconds: float = 10.0,
    side_min_points: int = 6,
    position_outlier_floor_seconds: float = 0.8,
    position_outlier_z: float = 7.0,
    contiguous_run_min_points: int = 5,
    max_gap_seconds: float = 10.0,
    chunk_seconds: float = 1,
    tutti_rests=None,
    return_diagnostics: Literal[False] = False,
) -> WarpTime: ...


@overload
def build_warp_curve(
    anchors,
    max_tempo_jump_pct: float = 0.25,
    jump_floor_seconds: float = 0.07,
    exclusion_radius_seconds: float = 4.0,
    side_search_radius_seconds: float = 10.0,
    side_min_points: int = 6,
    position_outlier_floor_seconds: float = 0.8,
    position_outlier_z: float = 7.0,
    contiguous_run_min_points: int = 5,
    max_gap_seconds: float = 10.0,
    chunk_seconds: float = 1,
    tutti_rests=None,
    *,
    return_diagnostics: Literal[True],
) -> tuple[WarpTime, list[RestRecord] | None]: ...


def build_warp_curve(
    anchors,
    max_tempo_jump_pct: float = 0.25,
    jump_floor_seconds: float = 0.07,
    exclusion_radius_seconds: float = 4.0,
    side_search_radius_seconds: float = 10.0,
    side_min_points: int = 6,
    position_outlier_floor_seconds: float = 0.8,
    position_outlier_z: float = 7.0,
    contiguous_run_min_points: int = 5,
    max_gap_seconds: float = 10.0,
    chunk_seconds: float = 1,
    tutti_rests=None,
    return_diagnostics: bool = False,
) -> WarpTime | tuple[WarpTime, list[RestRecord] | None]:
    """Build a score_time -> player_time warp function from DTW anchors.

    Cleans `anchors` via _clean_anchors (dedup, outlier rejection, thinning)
    and fits a monotonic PCHIP curve through the survivors, extrapolating
    linearly outside the anchored range from the edge speed of the two
    outermost surviving anchors.

    If `tutti_rests` (a list of (rest_start_score, rest_end_score) score-time
    pairs, e.g. from rest_utils.get_verified_tutti_rests) is given, each rest
    is instead treated as its own constant-speed segment -- see
    _build_split_warp -- and `return_diagnostics=True` additionally returns
    the list of per-rest RestRecord diagnostics (tutti_speed, natural vs.
    computed duration, shortfall, etc.) alongside warp_time.
    """
    good_times, good_warped = _clean_anchors(
        anchors,
        max_tempo_jump_pct,
        jump_floor_seconds,
        exclusion_radius_seconds,
        side_search_radius_seconds,
        side_min_points,
        position_outlier_floor_seconds,
        position_outlier_z,
        contiguous_run_min_points,
        max_gap_seconds,
        chunk_seconds,
    )

    if not tutti_rests:
        warp_time = _fit_segment(good_times, good_warped)
        return (warp_time, None) if return_diagnostics else warp_time

    warp_time, diagnostics = _build_split_warp(good_times, good_warped, tutti_rests)
    return (warp_time, diagnostics) if return_diagnostics else warp_time


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
    tutti_rests=None,
):
    """Warp a MIDI file's timing to follow a performer.

    Convenience wrapper: builds a score_time -> player_time warp curve from
    `anchors` (see `build_warp_curve`; `tutti_rests`, if given, is forwarded
    so score rests are treated as constant-speed segments instead of raw
    PCHIP), then hands it to `warp_midi_file`. If you already have a warp
    curve built (e.g. you needed it for diagnostics or rest verification
    first), call `warp_midi_file` directly instead of this, to avoid
    rebuilding the same curve again.
    """
    warp_time = build_warp_curve(
        anchors,
        max_tempo_jump_pct=max_tempo_jump_pct,
        jump_floor_seconds=jump_floor_seconds,
        exclusion_radius_seconds=exclusion_radius_seconds,
        side_search_radius_seconds=side_search_radius_seconds,
        side_min_points=side_min_points,
        position_outlier_floor_seconds=position_outlier_floor_seconds,
        position_outlier_z=position_outlier_z,
        contiguous_run_min_points=contiguous_run_min_points,
        max_gap_seconds=max_gap_seconds,
        chunk_seconds=chunk_seconds,
        tutti_rests=tutti_rests,
    )
    warp_midi_file(midi_path, warp_time, output_path)


def warp_midi_file(midi_path, warp_time, output_path):
    """Warp a MIDI file's timing according to an already-built warp curve.

    Walks every track of `midi_path`, converting each event's absolute tick
    to score-time seconds, applying `warp_time` (a plain score_time ->
    player_time callable, e.g. from `build_warp_curve`), and converting back
    to ticks against a single fixed REFERENCE_TEMPO. Takes `warp_time`
    directly rather than anchors so a caller that already built a curve
    (e.g. to print diagnostics, or because it needed `tutti_rests`) can
    reuse it here instead of triggering another `_clean_anchors` +
    curve-fit pass through `make_midi`.
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
