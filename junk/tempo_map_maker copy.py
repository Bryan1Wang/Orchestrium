import math
from scipy.stats import linregress

def map_tempo(points: list[tuple[float, float]]) -> list[tuple[float, float]]:

    tempo_map = [(0.0, 1.0)]

    if not points:
        return tempo_map
    
    first_orch_time = points[0][0]
    if first_orch_time > 0.0:
        tempo_map.append((first_orch_time, 1.0))

    for i, current_pt in enumerate(points):
        current_score_time = current_pt[0]
        
        # --- THE FIX ---
        # If a note is right at 0.0, skip calculating a speed factor for it.
        # This keeps our hardcoded (0.0, 1.0) completely pristine and unique.
        if current_score_time == 0.0:
            continue
            
        # CENTERED WINDOW: Grab points 0.5s before and 0.5s after current note
        search = [
            pt for pt in points 
            if (current_score_time - 0.5) <= pt[0] <= (current_score_time + 0.5)
        ]
        
        # FIX OVERLAPS
        unique_points = {pt[0]: pt[1] for pt in search}
        x_music = sorted(list(unique_points.keys()))
        y_player = [unique_points[x] for x in x_music]
        
        if len(x_music) < 2:
            speed_factor = tempo_map[-1][1] if tempo_map else 1.0
        else:
            result = linregress(x_music, y_player)
            slope = float(result[0])  # type: ignore
            
            if math.isnan(slope) or slope <= 0:
                speed_factor = tempo_map[-1][1] if tempo_map else 1.0
            else:
                speed_factor = 1.0 / slope

        tempo_map.append((current_score_time, speed_factor))
    tempo_map.sort(key=lambda x: x[0])
    return tempo_map