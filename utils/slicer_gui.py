import math
import sys
from pathlib import Path

import mido
from meta_track import crop_meta_track
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from track_crop import crop_note_track

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

GM_INSTRUMENTS = [
    "Acoustic Grand Piano",
    "Bright Acoustic Piano",
    "Electric Grand Piano",
    "Honky-tonk Piano",
    "Electric Piano 1",
    "Electric Piano 2",
    "Harpsichord",
    "Clavinet",
    "Celesta",
    "Glockenspiel",
    "Music Box",
    "Vibraphone",
    "Marimba",
    "Xylophone",
    "Tubular Bells",
    "Dulcimer",
    "Drawbar Organ",
    "Percussive Organ",
    "Rock Organ",
    "Church Organ",
    "Reed Organ",
    "Accordion",
    "Harmonica",
    "Tango Accordion",
    "Acoustic Guitar (nylon)",
    "Acoustic Guitar (steel)",
    "Electric Guitar (jazz)",
    "Electric Guitar (clean)",
    "Electric Guitar (muted)",
    "Overdriven Guitar",
    "Distortion Guitar",
    "Guitar Harmonics",
    "Acoustic Bass",
    "Electric Bass (finger)",
    "Electric Bass (pick)",
    "Fretless Bass",
    "Slap Bass 1",
    "Slap Bass 2",
    "Synth Bass 1",
    "Synth Bass 2",
    "Violin",
    "Viola",
    "Cello",
    "Contrabass",
    "Tremolo Strings",
    "Pizzicato Strings",
    "Orchestral Harp",
    "Timpani",
    "String Ensemble 1",
    "String Ensemble 2",
    "Synth Strings 1",
    "Synth Strings 2",
    "Choir Aahs",
    "Voice Oohs",
    "Synth Voice",
    "Orchestra Hit",
    "Trumpet",
    "Trombone",
    "Tuba",
    "Muted Trumpet",
    "French Horn",
    "Brass Section",
    "Synth Brass 1",
    "Synth Brass 2",
    "Soprano Sax",
    "Alto Sax",
    "Tenor Sax",
    "Baritone Sax",
    "Oboe",
    "English Horn",
    "Bassoon",
    "Clarinet",
    "Piccolo",
    "Flute",
    "Recorder",
    "Pan Flute",
    "Blown Bottle",
    "Shakuhachi",
    "Whistle",
    "Ocarina",
    "Lead 1 (square)",
    "Lead 2 (sawtooth)",
    "Lead 3 (calliope)",
    "Lead 4 (chiff)",
    "Lead 5 (charang)",
    "Lead 6 (voice)",
    "Lead 7 (fifths)",
    "Lead 8 (bass + lead)",
    "Pad 1 (new age)",
    "Pad 2 (warm)",
    "Pad 3 (polysynth)",
    "Pad 4 (choir)",
    "Pad 5 (bowed)",
    "Pad 6 (metallic)",
    "Pad 7 (halo)",
    "Pad 8 (sweep)",
    "FX 1 (rain)",
    "FX 2 (soundtrack)",
    "FX 3 (crystal)",
    "FX 4 (atmosphere)",
    "FX 5 (brightness)",
    "FX 6 (goblins)",
    "FX 7 (echoes)",
    "FX 8 (sci-fi)",
    "Sitar",
    "Banjo",
    "Shamisen",
    "Koto",
    "Kalimba",
    "Bag pipe",
    "Fiddle",
    "Shanai",
    "Tinkle Bell",
    "Agogo",
    "Steel Drums",
    "Woodblock",
    "Taiko Drum",
    "Melodic Tom",
    "Synth Drum",
    "Reverse Cymbal",
    "Guitar Fret Noise",
    "Breath Noise",
    "Seashore",
    "Bird Tweet",
    "Telephone Ring",
    "Helicopter",
    "Applause",
    "Gunshot",
]


def instrument_label(program, channel):
    # This score (like orchestra_player.select_instrument) spreads percussion
    # across channels 9 AND 10 (0-indexed), not just the strict-GM channel 9.
    if channel in (9, 10):
        return "Percussion"
    if program is None:
        return "—"
    if 0 <= program < len(GM_INSTRUMENTS):
        return GM_INSTRUMENTS[program]
    return f"Program {program}"


class ClampedZoomTextEdit(QTextEdit):
    """A QTextEdit whose built-in Ctrl+Wheel zoom is clamped to a sane range."""

    MIN_POINT_SIZE = 6.0
    MAX_POINT_SIZE = 28.0

    def wheelEvent(self, event):
        super().wheelEvent(event)
        doc = self.document()
        font = doc.defaultFont()
        size = font.pointSizeF()
        if size <= 0:
            return
        clamped = max(self.MIN_POINT_SIZE, min(self.MAX_POINT_SIZE, size))
        if clamped != size:
            font.setPointSizeF(clamped)
            doc.setDefaultFont(font)


class SlicerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Score Slicer")
        self.resize(900, 700)

        self.mid = None
        self.ticks_per_bar = None
        self.solo_track_index = None

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        input_row = QHBoxLayout()
        default_score = PROJECT_ROOT / "data" / "raw" / "SCORE.mid"
        self.input_path_edit = QLineEdit(
            str(default_score) if default_score.exists() else ""
        )
        browse_input_btn = QPushButton("Browse…")
        browse_input_btn.clicked.connect(self.browse_input)
        scan_btn = QPushButton("Scan Tracks")
        scan_btn.clicked.connect(self.scan_tracks)
        input_row.addWidget(QLabel("Master score MIDI:"))
        input_row.addWidget(self.input_path_edit)
        input_row.addWidget(browse_input_btn)
        input_row.addWidget(scan_btn)
        layout.addLayout(input_row)

        self.file_info_label = QLabel(
            'Load a file and click "Scan Tracks" to see its tracks.'
        )
        layout.addWidget(self.file_info_label)

        self.track_table = QTableWidget(0, 4)
        self.track_table.setHorizontalHeaderLabels(
            ["Index", "Name", "Instrument", "Notes"]
        )
        self.track_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.track_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.track_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.track_table.itemSelectionChanged.connect(self.on_track_selected)
        layout.addWidget(self.track_table)

        self.solo_label = QLabel("Solo track: (none selected)")
        layout.addWidget(self.solo_label)

        self.advanced_toggle = QPushButton("▸ Advanced")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.toggled.connect(self.toggle_advanced_panel)
        layout.addWidget(self.advanced_toggle)

        self.advanced_panel = QWidget()
        range_row = QHBoxLayout(self.advanced_panel)
        range_row.setContentsMargins(0, 0, 0, 0)
        self.start_measure_edit = QLineEdit()
        self.start_measure_edit.setPlaceholderText("from beginning")
        self.end_measure_edit = QLineEdit()
        self.end_measure_edit.setPlaceholderText("to end of file")
        full_file_btn = QPushButton("Use Full File")
        full_file_btn.clicked.connect(self.use_full_file)
        range_row.addWidget(QLabel("Start measure:"))
        range_row.addWidget(self.start_measure_edit)
        range_row.addWidget(QLabel("End measure:"))
        range_row.addWidget(self.end_measure_edit)
        range_row.addWidget(full_file_btn)
        self.advanced_panel.setVisible(False)
        layout.addWidget(self.advanced_panel)

        solo_row = QHBoxLayout()
        default_solo = PROJECT_ROOT / "data" / "raw" / "SOLO.mid"
        self.solo_output_edit = QLineEdit(str(default_solo))
        solo_browse_btn = QPushButton("Browse…")
        solo_browse_btn.clicked.connect(
            lambda: self.browse_output(self.solo_output_edit)
        )
        solo_row.addWidget(QLabel("Solo output:"))
        solo_row.addWidget(self.solo_output_edit)
        solo_row.addWidget(solo_browse_btn)
        layout.addLayout(solo_row)

        orch_row = QHBoxLayout()
        default_orch = PROJECT_ROOT / "data" / "raw" / "ORCHESTRA.mid"
        self.orchestra_output_edit = QLineEdit(str(default_orch))
        orch_browse_btn = QPushButton("Browse…")
        orch_browse_btn.clicked.connect(
            lambda: self.browse_output(self.orchestra_output_edit)
        )
        orch_row.addWidget(QLabel("Orchestra output:"))
        orch_row.addWidget(self.orchestra_output_edit)
        orch_row.addWidget(orch_browse_btn)
        layout.addLayout(orch_row)

        slice_btn = QPushButton("Slice")
        slice_btn.clicked.connect(self.slice_score)
        layout.addWidget(slice_btn)

        self.log = ClampedZoomTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log)

    def log_line(self, text):
        self.log.append(text)

    def browse_input(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select master score MIDI",
            str(PROJECT_ROOT / "data" / "raw"),
            "MIDI files (*.mid *.midi)",
        )
        if path:
            self.input_path_edit.setText(str(Path(path)))

    def browse_output(self, line_edit):
        path, _ = QFileDialog.getSaveFileName(
            self, "Select output file", line_edit.text(), "MIDI files (*.mid *.midi)"
        )
        if path:
            line_edit.setText(str(Path(path)))

    def use_full_file(self):
        self.start_measure_edit.clear()
        self.end_measure_edit.clear()

    def toggle_advanced_panel(self, checked):
        self.advanced_panel.setVisible(checked)
        self.advanced_toggle.setText(
            "▾ Advanced (measure range)" if checked else "▸ Advanced (measure range)"
        )

    @staticmethod
    def _collect_track_rows(mid, ticks_per_bar):
        rows = []
        file_max_ticks = 0
        for idx, track in enumerate(mid.tracks):
            time_accumulated = 0
            track_name = ""
            program = None
            channel = None
            note_count = 0
            for msg in track:
                time_accumulated += msg.time
                if msg.type == "track_name" and not track_name:
                    track_name = msg.name
                if hasattr(msg, "channel"):
                    if channel is None:
                        channel = msg.channel
                    if msg.type == "program_change" and program is None:
                        program = msg.program
                if msg.type == "note_on" and msg.velocity > 0:
                    note_count += 1
            file_max_ticks = max(file_max_ticks, time_accumulated)
            rows.append(
                {
                    "index": idx,
                    "name": track_name or "[No Name]",
                    "instrument": instrument_label(program, channel),
                    "notes": note_count,
                }
            )
        total_bars = math.ceil(file_max_ticks / ticks_per_bar) if ticks_per_bar else 0
        return rows, total_bars

    def scan_tracks(self):
        input_path = self.input_path_edit.text().strip()
        if not input_path:
            QMessageBox.warning(
                self, "No file", "Choose a master score MIDI file first."
            )
            return
        try:
            mid = mido.MidiFile(input_path)
        except Exception as exc:  # noqa: BLE001 - surface any parse failure as a dialog, not a crash
            QMessageBox.critical(self, "Failed to load MIDI", str(exc))
            return

        self.mid = mid
        self.ticks_per_bar = 4 * mid.ticks_per_beat
        rows, total_bars = self._collect_track_rows(mid, self.ticks_per_bar)

        self.track_table.setRowCount(len(rows))
        for row_idx, row in enumerate(rows):
            values = [
                str(row["index"]),
                row["name"],
                row["instrument"],
                str(row["notes"]),
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if row["index"] == 0:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                self.track_table.setItem(row_idx, col, item)
        self.track_table.resizeColumnsToContents()

        self.solo_track_index = None
        self.solo_label.setText("Solo track: (none selected)")

        self.file_info_label.setText(
            f"{Path(input_path).name}: {len(rows)} tracks, ~{total_bars} measures "
            f"(assumes 4/4, {mid.ticks_per_beat} ticks/beat). "
            "Track 0 is the tempo/meta track and can't be picked as solo."
        )
        self.log_line(
            f"Scanned {input_path}: {len(rows)} tracks, ~{total_bars} measures."
        )

    def on_track_selected(self):
        selected = self.track_table.selectedItems()
        if not selected:
            return
        row = selected[0].row()
        index_item = self.track_table.item(row, 0)
        name_item = self.track_table.item(row, 1)
        self.solo_track_index = int(index_item.text())
        self.solo_label.setText(f"Solo track: {index_item.text()} — {name_item.text()}")

    def _resolve_range(self):
        assert self.ticks_per_bar is not None
        start_text = self.start_measure_edit.text().strip()
        end_text = self.end_measure_edit.text().strip()

        start_measure = int(start_text) if start_text else 1
        if start_measure < 1:
            raise ValueError("Start measure must be 1 or greater.")

        start_tick = (start_measure - 1) * self.ticks_per_bar

        if end_text:
            end_measure = int(end_text)
            if end_measure < start_measure:
                raise ValueError("End measure must be >= start measure.")
            max_ticks = end_measure * self.ticks_per_bar
        else:
            max_ticks = math.inf

        return start_tick, max_ticks

    def slice_score(self):
        if self.mid is None:
            QMessageBox.warning(
                self, "No file scanned", "Scan a MIDI file's tracks first."
            )
            return
        if self.solo_track_index is None:
            QMessageBox.warning(
                self, "No solo track", "Select a solo track from the table first."
            )
            return

        try:
            start_tick, max_ticks = self._resolve_range()
        except ValueError as exc:
            QMessageBox.critical(self, "Invalid measure range", str(exc))
            return

        solo_output = self.solo_output_edit.text().strip()
        orchestra_output = self.orchestra_output_edit.text().strip()
        if not solo_output or not orchestra_output:
            QMessageBox.warning(
                self, "Missing output path", "Choose both output paths first."
            )
            return

        mid = self.mid
        meta = crop_meta_track(mid.tracks[0], start_tick, max_ticks)

        solo_track = crop_note_track(
            mid.tracks[self.solo_track_index], start_tick, max_ticks
        )
        solo_mid = mido.MidiFile(ticks_per_beat=mid.ticks_per_beat)
        solo_mid.tracks.append(meta)
        solo_mid.tracks.append(solo_track)
        solo_mid.save(solo_output)
        self.log_line(f"Wrote solo track ({len(solo_track)} events) to {solo_output}")

        orchestra_mid = mido.MidiFile(ticks_per_beat=mid.ticks_per_beat)
        orchestra_mid.tracks.append(meta)
        included = 0
        for idx, track in enumerate(mid.tracks):
            if idx == 0 or idx == self.solo_track_index:
                continue
            cropped = crop_note_track(track, start_tick, max_ticks)
            if len(cropped) > 0:
                orchestra_mid.tracks.append(cropped)
                included += 1
        orchestra_mid.save(orchestra_output)
        self.log_line(f"Wrote orchestra file ({included} tracks) to {orchestra_output}")

        QMessageBox.information(
            self, "Done", "Slicing complete — see the log for details."
        )


def main():
    app = QApplication(sys.argv)
    window = SlicerWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
