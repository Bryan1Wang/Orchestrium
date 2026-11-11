import subprocess
import threading
import time
from pathlib import Path

import mido
from pydub import AudioSegment

import app_paths

# Last-resort safety net -- a single per-instrument-region sfizz render
# taking longer than this almost certainly means the render is hung (e.g. a
# stuck note/pedal that survived slicing), not just a slow legitimate render.
SFIZZ_RENDER_TIMEOUT_SECONDS = 1200

_active_process = None
_active_process_lock = threading.Lock()
_cancel_requested = threading.Event()


def terminate_active_render():
    """
    Kill the in-flight sfizz_render subprocess (if any) and stop any further
    regions in the current render from starting. Safe to call from another
    thread -- this is the hook the GUI uses to make sure closing the window
    doesn't leave an orphaned sfizz_render.exe running.
    """
    _cancel_requested.set()
    with _active_process_lock:
        proc = _active_process
    if proc is not None and proc.poll() is None:
        proc.kill()


def render_precise_gm_orchestra(
    sfizz_path,
    midi_filename,
    final_master_filename,
    vpo_dir=None,
    skip_track=None,
    progress_callback=None,
):
    global _active_process

    _cancel_requested.clear()

    PROJECT_ROOT = app_paths.PROJECT_ROOT

    sfizz_path = (PROJECT_ROOT / "bin" / sfizz_path).resolve()
    if Path(midi_filename).is_absolute():
        midi_path = Path(midi_filename)
    else:
        midi_path = (PROJECT_ROOT / "data" / "raw" / midi_filename).resolve()
    final_master = (PROJECT_ROOT / final_master_filename).resolve()

    output_dir = final_master.parent

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
            # Skip piano / keyboard GM programs or fallback to Strings
            return STRINGS

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

    mid = mido.MidiFile(str(midi_path))

    print("Original MIDI length:", round(mid.length, 2), "seconds")

    rendered = []

    # -----------------------------
    # SPLIT BY PROGRAM CHANGES
    # -----------------------------

    for track_index, track in enumerate(mid.tracks):
        # Per-track granularity is enough for a progress bar; the callback may
        # raise to abort the render (the finally blocks below clean up temps).
        if progress_callback is not None:
            progress_callback(track_index / len(mid.tracks))

        if skip_track is not None and track_index == skip_track:
            print(f"Skipping track {track_index} (user-specified skip)")
            continue

        print(f"\nScanning track {track_index}")

        track_name = ""
        for msg in track:
            if msg.type == "track_name":
                track_name = msg.name
                break

        # Each channel tracks its own current program independently -- a
        # single track can multiplex several simultaneous instruments
        # across channels (e.g. Violin I/II/Viola all packed into one
        # track), and a program_change on one channel must not steal
        # messages that belong to a different channel. A single shared
        # "active region" here used to do exactly that: once a later
        # channel's program_change fired, every subsequent message from
        # an earlier channel (including its note-off) got misfiled into
        # the wrong region, leaving one region with an unclosed note_on
        # that sfizz would then hold open forever.
        channel_programs = {}
        regions = {}

        # Create absolute timeline

        absolute = 0

        for msg in track:
            absolute += msg.time

            if msg.type == "program_change":
                channel_programs[msg.channel] = msg.program

            if not hasattr(msg, "channel"):
                continue

            region_id = (channel_programs.get(msg.channel, 0), msg.channel)
            regions.setdefault(region_id, []).append((absolute, msg.copy()))

        if not regions:
            continue

        # -----------------------------
        # RENDER EACH PROGRAM REGION
        # -----------------------------

        for region_id, events in regions.items():
            if _cancel_requested.is_set():
                print("Render cancelled")
                return None

            program, channel = region_id

            sfz = select_instrument(program, channel, track_name)

            if sfz is None:
                print("Skipping piano/keyboard track", track_index)
                continue

            if not Path(sfz).exists():
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

            temp_mid = output_dir / f"render_{track_index}_{channel}_{program}.mid"

            temp_wav = temp_mid.with_suffix(".wav")

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
            held_notes = set()

            for tick, msg in events:
                copy = msg.copy()

                copy.time = tick - last_tick

                last_tick = tick

                # Sfizz works best on channel 0

                if hasattr(copy, "channel"):
                    copy.channel = 0

                if copy.type == "note_on" and copy.velocity > 0:
                    held_notes.add(copy.note)
                elif copy.type == "note_off" or (
                    copy.type == "note_on" and copy.velocity == 0
                ):
                    held_notes.discard(copy.note)

                new_track.append(copy)

            # Last-resort safety net: if any note is still open at the end
            # of this region (e.g. a note whose matching off ended up in a
            # different region, or any other desync), force-close it and
            # reset pedal state so sfizz doesn't hold the voice open
            # forever. The per-channel region splitting above should
            # prevent this in the first place -- this only catches
            # anything that isn't accounted for by that fix.
            if held_notes:
                print(
                    f"Warning: {len(held_notes)} note(s) still open at end of "
                    f"track {track_index} program {program} channel {channel} "
                    "-- force-closing before render."
                )
                for note in sorted(held_notes):
                    new_track.append(
                        mido.Message(
                            "note_off", note=note, velocity=0, channel=0, time=0
                        )
                    )
                for control in (64, 66, 67, 123):
                    new_track.append(
                        mido.Message(
                            "control_change",
                            control=control,
                            value=0,
                            channel=0,
                            time=0,
                        )
                    )

            # DO NOT ADD MANUAL EOT
            # Mido writes it safely

            out.tracks.append(new_track)

            try:
                out.save(str(temp_mid))

                command = [
                    str(sfizz_path),
                    "--sfz",
                    str(sfz),
                    "--midi",
                    str(temp_mid),
                    "--wav",
                    str(temp_wav),
                ]

                process = subprocess.Popen(
                    command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
                )
                with _active_process_lock:
                    _active_process = process
                try:
                    _, stderr = process.communicate(
                        timeout=SFIZZ_RENDER_TIMEOUT_SECONDS
                    )
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
                    raise RuntimeError(
                        f"sfizz_render timed out after {SFIZZ_RENDER_TIMEOUT_SECONDS}s "
                        f"rendering track {track_index} program {program} channel "
                        f"{channel} -- likely a stuck note or held pedal that "
                        "survived slicing."
                    )
                finally:
                    with _active_process_lock:
                        _active_process = None

                if _cancel_requested.is_set():
                    print("Render cancelled")
                    return None

                if process.returncode != 0:
                    print(stderr)
                else:
                    time.sleep(0.2)

                    if temp_wav.exists():
                        audio = AudioSegment.from_wav(str(temp_wav))

                        print(
                            "Rendered length:",
                            round(audio.duration_seconds, 2),
                            "sec",
                        )

                        rendered.append(audio)
            finally:
                temp_mid.unlink(missing_ok=True)
                temp_wav.unlink(missing_ok=True)

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

    mix.export(str(final_master), format="mp3", bitrate="192k")

    # print("\nDONE:", final_master)

    return mix
