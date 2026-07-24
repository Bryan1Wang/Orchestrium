from pathlib import Path
import pretty_midi

def extract_notes() -> list[tuple[float, set[int]]]:
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    midi_path = PROJECT_ROOT / "data" / "raw" / "solo.mid"
    
    midi_data = pretty_midi.PrettyMIDI(str(midi_path))

    attacks = []
    for instrument in midi_data.instruments:
        for note in instrument.notes:
            attacks.append((note.start, note.pitch))
                
    if not attacks:
        return []

    attacks.sort(key=lambda x: x[0])

    notes = []
    cluster = set()
    
    anchor_time = attacks[0][0]
    flex = 0.025  # 25 milliseconds
    
    for note_time, note in attacks:
        if note_time - anchor_time > flex:
            notes.append((anchor_time, cluster))
            cluster = {note}
            anchor_time = note_time
        else:
            cluster.add(note)
            
    if cluster:
        notes.append((anchor_time, cluster))
        
    return notes