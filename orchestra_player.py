import os
import subprocess
import time
from pathlib import Path

import mido
from pydub import AudioSegment

import app_paths


def render_precise_gm_orchestra(sfizz_path, midi_filename, final_master_filename, vpo_dir=None):

    PROJECT_ROOT = app_paths.PROJECT_ROOT

    sfizz_path = os.path.abspath(PROJECT_ROOT / "bin" / sfizz_path)
    if os.path.isabs(midi_filename):
        midi_path = midi_filename
    else:
        midi_path = os.path.abspath(PROJECT_ROOT / "data" / "raw" / midi_filename)
    final_master = os.path.abspath(PROJECT_ROOT / final_master_filename)

    output_dir = os.path.dirname(final_master)

    # -----------------------------
    # SFZ LIBRARY PATHS
    # -----------------------------

    VPO = Path(vpo_dir) if vpo_dir else PROJECT_ROOT / "VPO"

    STRINGS = VPO / "Strings" / "all-strings-SEC-normal-mod-wheel.sfz"
    PIZZICATO = VPO / "Strings" / "all-strings-SEC-pizzicato.sfz"
    TREMOLO = VPO / "Strings" / "all-strings-SEC-tremolo.sfz"
    HARP = VPO / "Strings" / "harp-sustain.sfz"

    FLUTE = VPO / "Woodwinds" / "flute-SEC-NORMAL-mod-wheel.sfz"
    OBOE = VPO / "Woodwinds" / "oboe-SEC-NORMAL-mod-wheel.sfz"
    CLARINET = VPO / "Woodwinds" / "clarinet-SEC-NORMAL-mod-wheel.sfz"
    BASSOON = VPO / "Woodwinds" / "bassoon-SEC-NORMAL-mod-wheel.sfz"

    HORN = VPO / "Brass" / "french-horn-SEC-normal-mod-wheel.sfz"
    TRUMPET = VPO / "Brass" / "trumpet-SEC-normal-mod-wheel.sfz"
    TRUMPET_MUTED = VPO / "Brass" / "trumpet-SEC-normal-DXF.sfz"
    TROMBONE = VPO / "Brass" / "trombone-SEC-normal-mod-wheel.sfz"
    TUBA = VPO / "Brass" / "tuba-SOLO-normal-mod-wheel.sfz"
    BRASS_ENSEMBLE = VPO / "Brass" / "all-brass-SEC-normal-mod-wheel.sfz"

    TIMPANI = VPO / "Percussion" / "timpani-hit-n-roll.sfz"
    BASSDRUM = VPO / "Percussion" / "bassdrum.sfz"
    CYMBALS = VPO / "Percussion" / "cymbals.sfz"
    SNARE = VPO / "Percussion" / "snare.sfz"
    PERCUSSION_KIT = VPO / "Percussion" / "bassdrum-snare-cymbals.sfz"

    def select_instrument(program, channel, track_name=""):

        # GM mapping

        if 0 <= program <= 7:
            # Skip piano / keyboard GM programs
            return None

        # Percussion: this score spreads percussion across channels 9 AND 10
        # (not just the strict-GM channel 9), and reuses program 47
        # ("Timpani") for both Timpani and Cassa/bass-drum tracks -- program
        # and channel alone can't tell them apart, so fall back to the
        # track name.
        if channel in (9, 10) or program == 47:
            name = (track_name or "").lower()

            if "timpani" in name:
                return TIMPANI

            if (
                "cassa" in name
                or "bass drum" in name
                or "bassdrum" in name
                or "grancassa" in name
            ):
                return BASSDRUM

            if "piatt" in name or "cymbal" in name:
                return CYMBALS

            if "snare" in name or "rullante" in name or "tamburo" in name:
                return SNARE

            return PERCUSSION_KIT

        if program == 44:
            return TREMOLO

        if program == 45:
            return PIZZICATO

        if program == 46:
            return HARP

        if program in (40, 41, 42, 43, 48, 49, 50, 51):
            return STRINGS

        if program == 56:
            return TRUMPET

        if program == 57:
            return TROMBONE

        if program == 58:
            return TUBA

        if program == 59:
            return TRUMPET_MUTED

        if program in (60, 61):
            return HORN

        if program in (62, 63):
            return BRASS_ENSEMBLE

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

        track_name = ""
        for msg in track:
            if msg.type == "track_name":
                track_name = msg.name
                break

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

            sfz = select_instrument(program, channel, track_name)

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
                if msg.is_meta and msg.type in (
                    "set_tempo",
                    "time_signature",
                    "key_signature",
                ):
                    # msg.time is a delta relative to the previous message
                    # in track 0's own sequence -- meaningless once copied
                    # into this new, otherwise-empty track. Keeping it
                    # would accumulate as a silent preamble before any
                    # real note, pushing this region's audible content
                    # later by however much time elapses between track
                    # 0's meta messages (e.g. a meter change partway
                    # through the piece).
                    new_track.append(msg.copy(time=0))

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

            result = subprocess.run(command, capture_output=True, text=True, check=True)

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
        return None

    print("\nMixing", len(rendered), "layers")

    longest = max(len(x) for x in rendered)

    mix = AudioSegment.silent(duration=longest)

    for layer in rendered:
        mix = mix.overlay(layer)

    print("Final length:", round(mix.duration_seconds, 2), "seconds")

    mix.export(final_master, format="mp3", bitrate="192k")

    #print("\nDONE:", final_master)

    return mix
