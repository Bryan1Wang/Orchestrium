import os
import subprocess
import time
from pathlib import Path

import mido
from pydub import AudioSegment


def render_precise_gm_orchestra(sfizz_path, midi_filename, final_master_filename):

    SRC_DIR = Path(__file__).resolve().parent
    PROJECT_ROOT = SRC_DIR.parent

    sfizz_path = os.path.abspath(PROJECT_ROOT / "bin" / sfizz_path)
    midi_path = os.path.abspath(PROJECT_ROOT / "data" / "raw" / midi_filename)
    final_master = os.path.abspath(PROJECT_ROOT / final_master_filename)

    output_dir = os.path.dirname(final_master)

    # -----------------------------
    # SFZ LIBRARY PATHS
    # -----------------------------

    VPO = PROJECT_ROOT / "VPO"

    STRINGS = VPO / "Strings" / "all-strings-SEC-normal-mod-wheel.sfz"

    FLUTE = VPO / "Woodwinds" / "flute-NORMAL-mod-wheel.sfz"
    OBOE = VPO / "Woodwinds" / "oboe-NORMAL-mod-wheel.sfz"
    CLARINET = VPO / "Woodwinds" / "clarinet-NORMAL-mod-wheel.sfz"
    BASSOON = VPO / "Woodwinds" / "bassoon-NORMAL-mod-wheel.sfz"

    HORN = VPO / "Brass" / "french-horn-SEC-normal-mod-wheel.sfz"
    TRUMPET = VPO / "Brass" / "trumpet-SEC-normal-mod-wheel.sfz"
    TROMBONE = VPO / "Brass" / "trombone-SEC-normal-mod-wheel.sfz"

    PERCUSSION = VPO / "Percussion" / "percussion-orchestral-kit.sfz"

    def select_instrument(program, channel):

        # GM mapping

        if 0 <= program <= 7:
            # Skip piano / keyboard GM programs
            return None

        if channel == 9:
            return PERCUSSION

        if 40 <= program <= 45:
            return STRINGS

        if program == 56:
            return TRUMPET

        if program == 57:
            return TROMBONE

        if program in (60, 61):
            return HORN

        if program == 68:
            return OBOE

        if program == 70:
            return BASSOON

        if program == 71:
            return CLARINET

        if program in (72, 73, 74, 75):
            return FLUTE

        return STRINGS

    # -----------------------------
    # LOAD MIDI
    # -----------------------------

    mid = mido.MidiFile(midi_path)

    print("Original MIDI length:", round(mid.length, 2), "seconds")

    rendered = []

    # -----------------------------
    # SPLIT BY PROGRAM CHANGES
    # -----------------------------

    for track_index, track in enumerate(mid.tracks):
        print(f"\nScanning track {track_index}")

        current_program = 0
        current_channel = 0

        regions = {}
        active_region = None

        # Create absolute timeline

        absolute = 0

        for msg in track:
            absolute += msg.time

            if msg.type == "program_change":
                current_program = msg.program
                current_channel = msg.channel

                active_region = (current_program, current_channel)

                if active_region not in regions:
                    regions[active_region] = []

            if active_region:
                regions[active_region].append((absolute, msg.copy()))

        if not regions:
            continue

        # -----------------------------
        # RENDER EACH PROGRAM REGION
        # -----------------------------

        for region_id, events in regions.items():
            program, channel = region_id

            sfz = select_instrument(program, channel)

            if sfz is None:
                print("Skipping piano/keyboard track", track_index)
                continue

            if not os.path.exists(sfz):
                print("Missing SFZ:", sfz)
                continue

            print(
                "Rendering",
                "track",
                track_index,
                "program",
                program,
                "channel",
                channel,
            )

            temp_mid = os.path.join(
                output_dir, f"render_{track_index}_{channel}_{program}.mid"
            )

            temp_wav = temp_mid.replace(".mid", ".wav")

            out = mido.MidiFile(ticks_per_beat=mid.ticks_per_beat)

            new_track = mido.MidiTrack()

            # Copy global metadata

            for msg in mid.tracks[0]:
                if msg.is_meta:
                    if msg.type in ("set_tempo", "time_signature", "key_signature"):
                        new_track.append(msg.copy())

            # rebuild delta timing

            last_tick = 0

            for tick, msg in events:
                copy = msg.copy()

                copy.time = tick - last_tick

                last_tick = tick

                # Sfizz works best on channel 0

                if hasattr(copy, "channel"):
                    copy.channel = 0

                new_track.append(copy)

            # DO NOT ADD MANUAL EOT
            # Mido writes it safely

            out.tracks.append(new_track)

            out.save(temp_mid)

            command = [
                sfizz_path,
                "--sfz",
                str(sfz),
                "--midi",
                temp_mid,
                "--wav",
                temp_wav,
            ]

            result = subprocess.run(command, capture_output=True, text=True)

            if result.returncode != 0:
                print(result.stderr)

            else:
                time.sleep(0.2)

                if os.path.exists(temp_wav):
                    audio = AudioSegment.from_wav(temp_wav)

                    print("Rendered length:", round(audio.duration_seconds, 2), "sec")

                    rendered.append(audio)

            if os.path.exists(temp_mid):
                os.remove(temp_mid)

            if os.path.exists(temp_wav):
                os.remove(temp_wav)

    # -----------------------------
    # MIX
    # -----------------------------

    if not rendered:
        print("Nothing rendered")
        return

    print("\nMixing", len(rendered), "layers")

    longest = max(len(x) for x in rendered)

    mix = AudioSegment.silent(duration=longest)

    for layer in rendered:
        mix = mix.overlay(layer)

    print("Final length:", round(mix.duration_seconds, 2), "seconds")

    mix.export(final_master, format="mp3", bitrate="192k")

    print("\nDONE:", final_master)


if __name__ == "__main__":
    render_precise_gm_orchestra(
        sfizz_path="sfizz_render_1.2.2.exe",
        midi_filename="score_cropped.mid",
        final_master_filename="orchestra_only_master.mp3",
    )
