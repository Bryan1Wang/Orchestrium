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
    final_master_mp3 = os.path.abspath(PROJECT_ROOT / final_master_filename)
    output_dir = os.path.dirname(final_master_mp3)

    # --- DEFINE VPO FILES ACCORDING TO YOUR SCANNER ---
    # Update these filenames to match the exact files in your VPO folder paths
    VPO_STRINGS   = os.path.join(PROJECT_ROOT, "VPO", "Strings", "all-strings-SEC-normal-mod-wheel.sfz")
    VPO_TIMPANI   = os.path.join(PROJECT_ROOT, "VPO", "Percussion", "timpani.sfz") # Or percussion-orchestral-kit.sfz
    VPO_FLUTE     = os.path.join(PROJECT_ROOT, "VPO", "Woodwinds", "flute-NORMAL-mod-wheel.sfz")
    VPO_OBOE      = os.path.join(PROJECT_ROOT, "VPO", "Woodwinds", "oboe-NORMAL-mod-wheel.sfz")
    VPO_CLARINET  = os.path.join(PROJECT_ROOT, "VPO", "Woodwinds", "clarinet-NORMAL-mod-wheel.sfz")
    VPO_BASSOON   = os.path.join(PROJECT_ROOT, "VPO", "Woodwinds", "bassoon-NORMAL-mod-wheel.sfz")
    VPO_HORN      = os.path.join(PROJECT_ROOT, "VPO", "Brass", "french-horn-SEC-normal-mod-wheel.sfz")
    VPO_TRUMPET   = os.path.join(PROJECT_ROOT, "VPO", "Brass", "trumpet-SEC-normal-mod-wheel.sfz")
    VPO_TROMBONE  = os.path.join(PROJECT_ROOT, "VPO", "Brass", "trombone-SEC-normal-mod-wheel.sfz")

    mid = mido.MidiFile(midi_path)
    rendered_audio_tracks = []
    
    print(f"🎵 STEP 1: Scanning Dynamic Instrument Changes inside {midi_filename}...")
    
    # Isolate global tempo maps
    global_meta = []
    if len(mid.tracks) > 0:
        for msg in mid.tracks[0]:
            if msg.is_meta and msg.type in ['set_tempo', 'time_signature', 'key_signature']:
                clean_meta = msg.copy()
                clean_meta.time = 0 
                global_meta.append(clean_meta)

    # Separate notes into channel-specific timelines
    # Separate notes into channel-specific timelines
    channel_programs = {}
    phrase_buckets = {}

    for track_idx, track in enumerate(mid.tracks):
        tick = 0
        # --- CRITICAL FIX 1: Isolate active notes strictly to THIS track index ---
        track_active_notes = {}
        
        for msg in track:
            tick += msg.time
            if msg.is_meta:
                continue
            
            if msg.type == 'program_change':
                channel_programs[msg.channel] = msg.program
                continue
            
            if msg.type in ['note_on', 'note_off']:
                current_prog = channel_programs.get(msg.channel, 0)
                is_note_on = (msg.type == 'note_on' and msg.velocity > 0)
                
                if is_note_on:
                    note_prog = current_prog
                    # Remember which specific program bucket started this note
                    track_active_notes[(msg.channel, msg.note)] = note_prog
                else:
                    # --- CRITICAL FIX 2: Safe track-specific lookup ---
                    # Pop the origin tracking logic, fallback ONLY to current track context
                    note_prog = track_active_notes.pop((msg.channel, msg.note), current_prog)
                
                # Partition by track index so instruments across tracks never pool events together
                bucket_id = (track_idx, msg.channel, note_prog)
                
                if bucket_id not in phrase_buckets:
                    phrase_buckets[bucket_id] = []
                
                phrase_buckets[bucket_id].append({"msg": msg, "abs_tick": tick})


    print(f"📊 Identified {len(phrase_buckets)} distinct channel/instrument sections.")

    # 2. Loop and map each group explicitly based on the General MIDI table
    for (track_idx, channel, program), events in phrase_buckets.items():
        selected_sfz = None
        instrument_label = f"Channel {channel} (Prog {program})"

        # --- KEYBOARDS & MALLETS (0 - 15) ---
        if program == 0:
            # Solo part
            continue
        elif 1 <= program <= 7:
            selected_sfz = os.path.join(PROJECT_ROOT, "VPO", "Keyboards", "grand-piano.sfz") # Change to your piano path if you have one
            instrument_label = f"Ch {channel} Orchestral Piano/Keyboard"
        elif 8 <= program <= 15:
            selected_sfz = os.path.join(PROJECT_ROOT, "VPO", "Percussion", "marimba.sfz") # Glop together mallets/celesta
            instrument_label = f"Ch {channel} Mallet Percussion/Celesta"
            
        # --- STRINGS ENSEMBLES & SOLOS (40 - 45, 110) ---
        elif 40 <= program <= 45:
            selected_sfz = VPO_STRINGS
            instrument_label = f"Ch {channel} VPO Orchestral Strings (Sustain)"
        elif program == 46: 
            selected_sfz = os.path.join(PROJECT_ROOT, "VPO", "Strings", "1st-violins-SEC-pizz.sfz")
            instrument_label = f"Ch {channel} VPO Pizzicato Strings"
        elif program == 105: # Banjo/Harp slot
            selected_sfz = os.path.join(PROJECT_ROOT, "VPO", "Strings", "harp.sfz")
            instrument_label = f"Ch {channel} Orchestral Harp"

        # --- ORCHESTRAL BRASS (56 - 63) ---
        elif program == 56: 
            selected_sfz = VPO_TRUMPET
            instrument_label = f"Ch {channel} VPO Trumpet Section"
        elif program == 57: 
            selected_sfz = VPO_TROMBONE
            instrument_label = f"Ch {channel} VPO Trombone Section"
        elif program == 58: # Tuba
            selected_sfz = os.path.join(PROJECT_ROOT, "VPO", "Brass", "tuba-SEC-normal-mod-wheel.sfz")
            instrument_label = f"Ch {channel} VPO Tuba Section"
        elif 60 <= program <= 61: # French Horns
            selected_sfz = VPO_HORN
            instrument_label = f"Ch {channel} VPO French Horns"

        # --- WOODWINDS (64 - 79) ---
        elif program == 68: 
            selected_sfz = VPO_OBOE
            instrument_label = f"Ch {channel} VPO Oboe"
        elif program == 70: 
            selected_sfz = VPO_BASSOON
            instrument_label = f"Ch {channel} VPO Bassoon"
        elif program == 71: 
            selected_sfz = VPO_CLARINET
            instrument_label = f"Ch {channel} VPO Clarinet"
        elif 72 <= program <= 75: 
            selected_sfz = VPO_FLUTE
            instrument_label = f"Ch {channel} VPO Flute/Piccolo"

        # --- PERCUSSION & TIMPANI (47, Channel 10) ---
        elif program == 47 or channel == 9: # General MIDI Channel 10 is indexed as byte 9 in Python!
            # Points to a standard master hit kit that triggers snares, bass drums, and cymbals based on notes
            selected_sfz = os.path.join(PROJECT_ROOT, "VPO", "Percussion", "percussion-orchestral-kit.sfz")
            instrument_label = f"Ch {channel} VPO Full Orchestral Percussion/Timpani"

        # =====================================================================

        # Safe fallback if instrument folder path does not exist
        if not selected_sfz or not os.path.exists(selected_sfz):
            selected_sfz = VPO_STRINGS # Fall back to strings instead of crashing

        print(f"⏳ Rendering -> {instrument_label}...")
        
        # Recompile clean time-accurate MIDI segment
        single_segment_midi = mido.MidiFile()
        single_segment_midi.ticks_per_beat = mid.ticks_per_beat
        new_track = mido.MidiTrack()
        
        for meta_msg in global_meta:
            new_track.append(meta_msg)

        events.sort(key=lambda x: (
            x["abs_tick"], 
            0 if (x["msg"].type == 'note_on' and x["msg"].velocity > 0) else 1
        ))
        last_tick = 0
        # Convert absolute ticks back into correct delta time values for output
        last_tick = 0
        for ev in events:
            msg_copy = ev["msg"].copy()
            msg_copy.time = ev["abs_tick"] - last_tick
            
            # --- THE TIME SHIELD FIX ---
            # If it's a sustain pedal message, turn it into a harmless text marker
            # This keeps the timeline length exactly identical down to the millisecond
            if msg_copy.type == 'control_change' and msg_copy.control == 64:
                msg_copy = mido.MetaMessage('text', text='sustain_anchor', time=msg_copy.time)
            
            elif hasattr(msg_copy, 'channel'):
                msg_copy.channel = 0  # Force onto Channel 0 for Sfizz compatibility
                
            new_track.append(msg_copy)
            last_tick = ev["abs_tick"]


        # Append a 2-beat safety padding BEFORE the End of Track message.
        # This prevents sfizz --use-eot from choking off notes on the final tick.
        safety_buffer_ticks = mid.ticks_per_beat * 2
        new_track.append(mido.MetaMessage('end_of_track', time=safety_buffer_ticks))
        single_segment_midi.tracks.append(new_track)
        
        temp_midi = os.path.join(output_dir, f"temp_ch{channel}_p{program}.mid")
        temp_wav = os.path.join(output_dir, f"temp_ch{channel}_p{program}.wav")
        single_segment_midi.save(temp_midi)
        # =====================================================================

        # Run Sfizz
        command = [sfizz_path, "--sfz", selected_sfz, "--midi", temp_midi, "--wav", temp_wav, "--use-eot"]
        try:
            subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(0.3)
            if os.path.exists(temp_wav):
                rendered_audio_tracks.append(AudioSegment.from_wav(temp_wav))
        except Exception as e:
            print(f"   ⚠️ Error rendering segment: {e}")
        finally:
            if os.path.exists(temp_midi): os.remove(temp_midi)
            if os.path.exists(temp_wav): os.remove(temp_wav)

    # 3. Final Multi-Instrument Accumulation Mix
    if not rendered_audio_tracks:
        print("❌ Pipeline Error: No channels were successfully extracted.")
        return

    print(f"\n🎛️ STEP 2: Blending all {len(rendered_audio_tracks)} audio layers together...")
    try:
        orchestra_mix = rendered_audio_tracks[0]
        if len(rendered_audio_tracks) > 1:
            for audio_track in rendered_audio_tracks[1:]:
                orchestra_mix = orchestra_mix.overlay(audio_track)
                
        print(f"💾 STEP 3: Exporting full orchestrated arrangement file...")
        orchestra_mix.export(final_master_mp3, format="mp3", bitrate="192k")
        print(f"\n🎉 SUCCESS! Your precise multi-instrument master is complete:\n     -> {final_master_mp3}")
    except Exception as e:
        print(f"❌ Export failed! Error: {e}")

if __name__ == "__main__":
    # Force close sfizz_render.exe in Task Manager Details tab before running
    render_precise_gm_orchestra(
        sfizz_path="sfizz_render.exe", 
        midi_filename="score_cropped.mid", 
        final_master_filename="orchestra_only_master.mp3"
    )
