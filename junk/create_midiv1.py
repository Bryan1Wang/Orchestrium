import bisect
import mido
import numpy as np

def make_midi(midi_path, tempo_map, output_path):
    score_times = np.array([x for x, _ in tempo_map], dtype=float)
    speeds = np.array([x for _, x in tempo_map], dtype=float)

    # cumulative warped time at every anchor
    warped_times = np.zeros(len(score_times))

    for i in range(1, len(score_times)):
        dt = score_times[i] - score_times[i - 1]

        # constant speed over the interval
        warped_times[i] = warped_times[i - 1] + dt / speeds[i - 1]

    def warp_time(score_time):
        """Convert score time -> warped time exactly."""

        if score_time <= score_times[0]:
            return score_time / speeds[0]

        if score_time >= score_times[-1]:
            return warped_times[-1] + (
                score_time - score_times[-1]
            ) / speeds[-1]

        i = bisect.bisect_right(score_times, score_time) - 1

        return warped_times[i] + (
            score_time - score_times[i]
        ) / speeds[i]

    mid = mido.MidiFile(midi_path)

    new_mid = mido.MidiFile()
    new_mid.ticks_per_beat = mid.ticks_per_beat

    for track in mid.tracks:

        abs_score = 0.0
        events = []

        # score seconds
        for msg in track:
            abs_score += mido.tick2second(
                msg.time,
                mid.ticks_per_beat,
                500000,
            )

            events.append((abs_score, msg))

        new_track = mido.MidiTrack()

        previous_tick = 0

        for score_time, msg in events:

            warped = warp_time(score_time)

            abs_tick = round(
                mido.second2tick(
                    warped,
                    mid.ticks_per_beat,
                    500000,
                )
            )

            delta = abs_tick - previous_tick
            previous_tick = abs_tick

            new_track.append(msg.copy(time=delta))

        new_mid.tracks.append(new_track)

    new_mid.save(output_path)