import mido

KEEP_TYPES = ("set_tempo", "time_signature", "key_signature", "track_name")


def crop_meta_track(track0, start_tick, max_ticks):
    """
    Crop a MIDI file's Track 0 (tempo/time-signature/key-signature meta
    events) to [start_tick, max_ticks], the same way the note tracks are
    windowed elsewhere in these slicer scripts.

    Copying Track 0 unmodified (as these scripts used to) leaves every
    tempo/time-signature event for the *entire* source file in the sliced
    output, which makes mido report (and downstream tools treat) the sliced
    file as running the full original duration regardless of how many bars
    of notes were actually sliced.

    The tempo/time-signature in effect at start_tick (even if set earlier
    in the source file) is carried forward as time-0 events, so a crop that
    starts mid-piece doesn't silently default to 120 BPM / 4-4.
    """

    last_tempo = None
    last_time_sig = None
    last_key_sig = None

    cropped = mido.MidiTrack()
    time_accumulated = 0
    last_appended_time = start_tick

    for msg in track0:
        time_accumulated += msg.time

        if msg.type not in KEEP_TYPES:
            continue

        if time_accumulated < start_tick:
            if msg.type == "set_tempo":
                last_tempo = msg
            elif msg.type == "time_signature":
                last_time_sig = msg
            elif msg.type == "key_signature":
                last_key_sig = msg
            continue

        if time_accumulated > max_ticks:
            break

        if len(cropped) == 0:
            for carried in (last_time_sig, last_tempo, last_key_sig):
                if carried is not None:
                    cropped.append(carried.copy(time=0))

        msg_copy = msg.copy(time=int(time_accumulated - last_appended_time))
        cropped.append(msg_copy)
        last_appended_time = time_accumulated

    if len(cropped) == 0:
        for carried in (last_time_sig, last_tempo, last_key_sig):
            if carried is not None:
                cropped.append(carried.copy(time=0))

    cropped.append(mido.MetaMessage("end_of_track", time=0))
    return cropped
