import os
import subprocess
import time
from pydub import AudioSegment

def convert_midi_to_mp3(sfizz_path, sfz_file, midi_file, output_mp3):
    # Enforce strict clean absolute paths
    sfizz_path = os.path.abspath(sfizz_path)
    sfz_file = os.path.abspath(sfz_file)
    midi_file = os.path.abspath(midi_file)
    output_mp3 = os.path.abspath(output_mp3)
    
    output_dir = os.path.dirname(output_mp3)
    temp_wav = os.path.join(output_dir, "temp_render.wav")
    
    # Pre-clear lingering files
    if os.path.exists(temp_wav):
        try:
            os.remove(temp_wav)
        except PermissionError:
            print("❌ File Lock Error: Clear sfizz_render.exe in Task Manager Details tab.")
            return

    print("--- STEP 1: RENDERING FULL STRINGS ---")
    
    # --- FIXED SYNTAX: --use-eot forces rendering to stop at the last MIDI note marker ---
    command = [
        sfizz_path,
        "--sfz", sfz_file,
        "--midi", midi_file,
        "--wav", temp_wav,
        "--use-eot"
    ]
    
    print(f"Executing argument list: {command}\n")
    print("⏳ Processing audio extraction... (Should complete very quickly now!)")
    
    try:
        # Runs natively without the buggy Windows shell loop
        subprocess.run(command, check=True)
        print("✅ Sfizz successfully finished rendering WAV file.")
        
    except subprocess.CalledProcessError as e:
        print(f"❌ STEP 1 FAILED: Engine error code {e.returncode}")
        return
    except Exception as e:
        print(f"❌ Step 1 crashed completely. Error: {e}")
        return

    # Give Windows time to release the file handles
    print("\nWaiting for Windows to release file properties...")
    time.sleep(2.0)

    print("--- STEP 2: CONVERTING TO MP3 VIA PYDUB ---")
    try:
        if os.path.exists(temp_wav):
            print(f"Loading raw audio data from: {temp_wav}")
            audio = AudioSegment.from_wav(temp_wav)
            
            print(f"Exporting final track compressed destination to: {output_mp3}")
            audio.export(output_mp3, format="mp3", bitrate="192k")
            
            os.remove(temp_wav)
            print("🎉 Done! Your MP3 master file is ready.")
        else:
            print("❌ File Missing Error: The temp_render.wav file was never generated.")
    except Exception as e:
        print(f"❌ STEP 2 FAILED: Pydub could not complete compression processing. Error: {e}")

# 1. Get the directory where THIS script lives (project/src)
SRC_DIR = os.path.dirname(os.path.abspath(__file__))

# 2. Step up one level to the main project root folder (project)
PROJECT_ROOT = os.path.dirname(SRC_DIR)

# 3. Define all tool and patch layouts relative to that root
SFIZZ_EXE = os.path.join(PROJECT_ROOT, "bin", "sfizz_render.exe")  
SFZ_INSTRUMENT = os.path.join(PROJECT_ROOT, "VPO", "Strings", "all-strings-SEC-normal-mod-wheel.sfz")

# 4. Route data inputs and destination locations
INPUT_MIDI = os.path.join(PROJECT_ROOT, "data", "raw", "SOLO.mid")
OUTPUT_MP3 = os.path.join(PROJECT_ROOT, "output_song.mp3")

if __name__ == "__main__":
    convert_midi_to_mp3(SFIZZ_EXE, SFZ_INSTRUMENT, INPUT_MIDI, OUTPUT_MP3)


if __name__ == "__main__":
    convert_midi_to_mp3(SFIZZ_EXE, SFZ_INSTRUMENT, INPUT_MIDI, OUTPUT_MP3)
