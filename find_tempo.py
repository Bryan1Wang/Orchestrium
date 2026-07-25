def map_tempo(
    points: list[tuple[float, float]], precision=0.5, init_speed=1.0
) -> list[tuple[float, float]]:
    tempo_map = [(0.0, init_speed)]

    if not points:
        return tempo_map

    unique_dict = {}
    for pt in points:
        unique_dict[pt[0]] = pt[1]

    score_times = sorted(unique_dict.keys())

    i = 0

    while i < len(score_times):
        start_score = score_times[i]

        # Find all notes that fall within 1 second of this start note
        chunk_indices = [i]
        j = i + 1
        if j >= len(score_times):
            break

        chunk_indices.append(j)
        j += 1

        while j < len(score_times) and (score_times[j] - start_score) <= precision:
            chunk_indices.append(j)
            j += 1

        x1 = score_times[chunk_indices[0]]
        x2 = score_times[chunk_indices[-1]]

        y1 = unique_dict[x1]
        y2 = unique_dict[x2]

        delta_score = x2 - x1
        delta_player = y2 - y1

        if delta_score <= 0 or delta_player <= 0:
            speed_factor = tempo_map[-1][1] if tempo_map else 1.0
        else:
            speed_factor = delta_score / delta_player

        # Apply this speed factor to the start of the chunk, check in case starts at beginning
        if start_score == 0.0:
            tempo_map[0] = (0.0, speed_factor)
        else:
            tempo_map.append((start_score, speed_factor))

        # Move the loop counter past all the notes we just processed in this chunk
        i = j - 1

    tempo_map.sort(key=lambda x: x[0])
    return tempo_map
