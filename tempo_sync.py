def getAccuracy(notes: set[int], player: set[int]):
    if not notes or not player:
        if notes == player:
            return 0.0
        else:
            return 1.0
    acc = (len(notes - player)) / len(notes) * 0.8 + (
        len(player - notes) / len(player)
    ) * 0.2
    return acc


def align(
    player_data: list[tuple[float, set[int]]], notes_data: list[tuple[float, set[int]]]
) -> list[tuple[float, float]]:
    player = [n[1] for n in player_data]
    notes = [n[1] for n in notes_data]

    diff = [[float("inf")] * (len(player) + 1) for _ in range(len(notes) + 1)]
    diff[0][0] = 0
    flex = min(10, len(player))

    for j in range(1, len(player) + 1):
        for i in range(1, len(notes) + 1):
            diff[i][j] = getAccuracy(notes[i - 1], player[j - 1]) + min(
                diff[i - 1][j], diff[i][j - 1], diff[i - 1][j - 1]
            )

    temp = diff[len(notes)][len(player)]
    idxP = len(player)
    for j in range(len(player), len(player) - flex, -1):
        if diff[len(notes)][j] < temp:
            temp = diff[len(notes)][j]
            idxP = j
    idxN = len(notes)
    path = [(idxN - 1, idxP - 1)]
    while idxP > 0 and idxN > 0:
        if idxN == 1:
            idxP -= 1
            path.append((0, idxP - 1))
            continue

        if idxP == 1:
            idxN -= 1
            path.append((idxN - 1, 0))
            continue

        choices = [
            (diff[idxN - 1][idxP - 1], 0, (1, 1)),
            (diff[idxN][idxP - 1], 1, (0, 1)),
            (diff[idxN - 1][idxP], 2, (1, 0)),
        ]
        _, _, (dN, dP) = min(choices)
        idxN -= dN
        idxP -= dP
        if (idxN - 1) >= 0 and (idxP - 1) >= 0:
            path.append((idxN - 1, idxP - 1))

    path.reverse()
    best = {}
    for n, p in path:
        err = getAccuracy(notes[n], player[p])
        if err <= 0.5:
            if n not in best:
                best[n] = (p, err)
            else:
                if err < best[n][1]:
                    best[n] = (p, err)

    fitting = []
    for i in range(len(notes)):
        if i in best:
            fitting.append((notes_data[i][0], player_data[best[i][0]][0]))
    return fitting
