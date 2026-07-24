import bisect
import mido
import numpy as np


def make_midi(midi_path, tempo_map, output_path, resolution=0.01, smooth_window=21):
    """
    Smooth a tempo map while preserving total warped duration.

    tempo_map:
        [(score_time_seconds, speed), ...]

    speed > 1 means faster
    speed < 1 means slower

    resolution:
        seconds between samples in the generated curve

    smooth_window:
        size of moving average smoothing window
    """

    score_times = np.array([x for x, _ in tempo_map], dtype=float)
    speeds = np.array([x for _, x in tempo_map], dtype=float)

    # ---------------------------------------------------------
    # 1. Create dense samples of the original piecewise curve
    # ---------------------------------------------------------

    sample_times = np.arange(
        score_times[0],
        score_times[-1],
        resolution
    )

    sample_times = np.append(sample_times, score_times[-1])

    original_speed = np.zeros_like(sample_times)

    for i, t in enumerate(sample_times):
        idx = bisect.bisect_right(score_times, t) - 1
        idx = max(0, min(idx, len(speeds)-1))
        original_speed[i] = speeds[idx]


    # ---------------------------------------------------------
    # 2. Smooth the speed curve
    # ---------------------------------------------------------

    kernel = np.ones(smooth_window) / smooth_window

    smooth_speed = np.convolve(
        original_speed,
        kernel,
        mode="same"
    )

    # fix edges (moving average causes edge shrinkage)
    smooth_speed[:smooth_window//2] = original_speed[:smooth_window//2]
    smooth_speed[-smooth_window//2:] = original_speed[-smooth_window//2:]


    # ---------------------------------------------------------
    # 3. Preserve total warped duration
    #
    # warped time = integral(1/speed) dt
    # ---------------------------------------------------------

    old_duration = np.trapz(
        1 / original_speed,
        sample_times
    )

    new_duration = np.trapz(
        1 / smooth_speed,
        sample_times
    )

    scale = new_duration / old_duration

    smooth_speed *= scale


    # ---------------------------------------------------------
    # 4. Build score_time -> warped_time lookup table
    # ---------------------------------------------------------

    warped = np.zeros_like(sample_times)

    for i in range(1, len(sample_times)):
        dt = sample_times[i] - sample_times[i-1]

        warped[i] = (
            warped[i-1]
            +
            dt / smooth_speed[i-1]
        )


    def warp_time(score_time):
        """
        Convert score time into warped time.
        """

        if score_time <= sample_times[0]:
            return score_time / smooth_speed[0]

        if score_time >= sample_times[-1]:
            return (
                warped[-1]
                +
                (score_time - sample_times[-1])
                / smooth_speed[-1]
            )

        return np.interp(
            score_time,
            sample_times,
            warped
        )


    # ---------------------------------------------------------
    # 5. Apply warp to MIDI
    # ---------------------------------------------------------

    mid = mido.MidiFile(midi_path)

    new_mid = mido.MidiFile()
    new_mid.ticks_per_beat = mid.ticks_per_beat

    for track in mid.tracks:

        abs_score = 0.0
        events = []

        for msg in track:

            abs_score += mido.tick2second(
                msg.time,
                mid.ticks_per_beat,
                500000
            )

            events.append(
                (abs_score, msg)
            )


        new_track = mido.MidiTrack()

        previous_tick = 0

        for score_time, msg in events:

            warped_time = warp_time(score_time)

            tick = round(
                mido.second2tick(
                    warped_time,
                    mid.ticks_per_beat,
                    500000
                )
            )

            delta = tick - previous_tick
            previous_tick = tick

            new_track.append(
                msg.copy(time=delta)
            )

        new_mid.tracks.append(new_track)


    new_mid.save(output_path)