from pathlib import Path
import mido

def findChanges(midi_path):
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    midi_path = PROJECT_ROOT / "data" / "raw" / "SOLO.mid"

    mid = mido.MidiFile(midi_path)
    print(f"Loaded {midi_path} | Resolution: {mid.ticks_per_beat} ticks/beat")
    print(f"This MIDI file is Type: {mid.type}")

    if mid.type != 1:
        print("MIDI is not type 1")

    current = {}
    changes = []

    for idx, track in enumerate(mid.tracks):
        tick = 0
        for msg in track:
            tick += msg.time
            if (msg.type == 'program_change'):
                if (msg.channel not in current or current[msg.channel] != msg.program):
                    current[msg.channel] = msg.program
                    changes.append({"track": idx,
                                    "channel": msg.channel,
                                    "program": msg.program,
                                    "tick": tick})
    print (changes)
    return changes
