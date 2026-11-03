import mido


def crop_note_track(track, start_tick, max_ticks):
    """
    Crop a single note track to [start_tick, max_ticks], shifting its first
    event back to time 0, force-closing any notes still held at max_ticks
    (or at the natural end of the track), and resetting sustain/sostenuto/
    soft-pedal plus issuing an all-notes-off for every channel touched.

    A note_off alone doesn't silence a note still held down by a sustain
    pedal (CC 64) -- that's what the pedal is for. If a crop lands after a
    pedal-down with no matching pedal-up, the forced note_off at the
    boundary does nothing, and a synth honoring the still-down pedal can
    leave a looped/sustained sample playing forever with nothing left in
    the file to release it -- this is what was causing renders to hang.
    """
    cropped = mido.MidiTrack()
    time_accumulated = 0
    active_notes = {}
    channels_seen = set()

    for msg in track:
        time_accumulated += msg.time

        if time_accumulated < start_tick:
            continue

        if time_accumulated > max_ticks:
            break

        if hasattr(msg, "channel"):
            channels_seen.add(msg.channel)

        if msg.type == "note_on" and msg.velocity > 0:
            active_notes.setdefault(msg.channel, set()).add(msg.note)
        elif (
            (msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0))
            and msg.channel in active_notes
            and msg.note in active_notes[msg.channel]
        ):
            active_notes[msg.channel].remove(msg.note)

        if len(cropped) == 0:
            msg_copy = msg.copy(time=int(time_accumulated - start_tick))
        else:
            msg_copy = msg.copy()

        cropped.append(msg_copy)

    for channel, notes_down in active_notes.items():
        for note in sorted(notes_down):
            cropped.append(
                mido.Message(
                    "note_off", note=note, velocity=0, channel=channel, time=0
                )
            )

    for channel in sorted(channels_seen):
        for control in (64, 66, 67, 123):
            cropped.append(
                mido.Message(
                    "control_change",
                    control=control,
                    value=0,
                    channel=channel,
                    time=0,
                )
            )

    if len(cropped) > 0:
        cropped.append(mido.MetaMessage("end_of_track", time=0))

    return cropped
