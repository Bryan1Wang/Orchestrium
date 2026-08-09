# 🎻 Orchestrium

**Play with a full orchestra — even when you're practicing alone.**

Orchestrium takes a recording of you playing a piece and a "perfect" orchestral score, then automatically builds a real, richly-sampled orchestral accompaniment that follows *your* tempo — speeding up, slowing down, and pausing exactly where you do — and mixes it together with your own recording into one finished track.

No conductor, no backing band, no manual editing required — just record yourself, point Orchestrium at your files, and get back a performance with a full orchestra behind you.

🌐 **[Visit the project site](https://bryan1wang.github.io/Orchestrium/)**

---

## ✨ What it does

- 🎯 **Follows your tempo automatically** — the orchestra speeds up, slows down, and breathes with your playing, instead of sticking to a rigid click track.
- 🎼 **Real orchestral sound** — the accompaniment is rendered with sampled orchestral instruments, not a flat MIDI/synth playback.
- 🤫 **Handles pauses naturally** — rests and held notes in your performance are detected and respected, so the orchestra doesn't rush ahead or cut you off.
- 🖱️ **Simple, point-and-click tools** — two easy desktop apps handle everything; no coding or audio engineering experience needed.
- 🎚️ **You stay in control** — independent volume sliders let you balance your recording against the orchestra before the final mix.

## 🚀 Getting Started

1. Set up the environment (one-time):
   ```
   env\Scripts\activate.bat
   pip install -r requirements.txt
   ```
2. Launch the app:
   ```
   python src\main_gui.py
   ```
3. Pick your solo instrument's score (MIDI), the full orchestra score (MIDI), and your recording of yourself playing, then hit **Run Pipeline**.

Don't have a separate solo/orchestra score yet? Use the companion **Score Slicer** tool (`python src\utils\slicer_gui.py`) to split one combined score into a solo part and an orchestra part first.

Your finished, mixed track will show up in your chosen output folder as an MP3, ready to listen to or share.

## 📄 License

Orchestrium is released under the [GNU General Public License v3.0](LICENSE).

## 🙏 Built with

Orchestrium wouldn't be possible without these open tools and libraries:

- [Basic Pitch](https://github.com/spotify/basic-pitch) (Spotify) — audio-to-MIDI transcription
- [sfizz](https://sfz.tools/sfizz/) — SFZ sample-based audio synthesis
- [Virtual Playing Orchestra (VPO)](http://virtualplaying.com/) — free orchestral sample library, by Paul Battersby
