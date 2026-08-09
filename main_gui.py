import contextlib
import inspect
import sys
import traceback
from pathlib import Path


def _show_loading_splash():
    """A tiny tkinter box shown while `main` (and onnxruntime) import.

    Uses tkinter rather than Qt because Qt must not be imported before
    `main` (see note below) -- tkinter doesn't touch Qt's DLL search path,
    so it can safely appear first and be torn down before PySide6 loads.
    """
    import tkinter as tk

    splash = tk.Tk()
    splash.overrideredirect(True)
    width, height = 220, 70
    screen_w = splash.winfo_screenwidth()
    screen_h = splash.winfo_screenheight()
    x, y = (screen_w - width) // 2, (screen_h - height) // 2
    splash.geometry(f"{width}x{height}+{x}+{y}")
    tk.Label(splash, text="Loading…", font=("Segoe UI", 12)).pack(expand=True, fill="both")
    splash.update()
    return splash


_splash = _show_loading_splash()

# Must be imported before PySide6/Qt: importing Qt first changes the Windows
# DLL search path in a way that breaks onnxruntime's native module load
# (basic_pitch -> onnxruntime, pulled in transitively via main -> transcribe).
import app_paths
import main as pipeline
import orchestra_player

_splash.destroy()

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

PROJECT_ROOT = app_paths.PROJECT_ROOT


def _load_app_icon() -> QIcon:
    """The user drops their own icon.ico/png in assets/ -- until then, no-op."""
    for name in ("icon.ico", "icon.png"):
        icon_path = PROJECT_ROOT / "assets" / name
        if icon_path.exists():
            return QIcon(str(icon_path))
    return QIcon()


# (param_name, label, kind, decimals, minimum, maximum, step)
REST_DETECTION_SPECS = [
    ("min_score_rest_seconds", "Min score rest length (s):", "float", 2, 1.5, 60.0, 0.1),
    (
        "min_recording_pause_seconds",
        "Min recording pause length (s):",
        "float",
        2,
        1.5,
        60.0,
        0.1,
    ),
    ("onset_leeway_seconds", "Note-onset leeway (s):", "float", 2, 0.0, 5.0, 0.05),
]

GHOST_FILTER_SPECS = [
    ("ghost_min_silence_seconds", "Min silence length (s):", "float", 2, 0.1, 30.0, 0.1),
    (
        "ghost_silence_thresh_offset_db",
        "Silence threshold offset (dB):",
        "float",
        1,
        -80.0,
        0.0,
        1.0,
    ),
]

WARP_CURVE_SPECS = [
    ("max_tempo_jump_pct", "Max tempo jump (fraction):", "float", 3, 0.0, 5.0, 0.01),
    ("jump_floor_seconds", "Jump floor (s):", "float", 3, 0.0, 10.0, 0.01),
    ("absolute_deviation_seconds", "Absolute deviation floor (s):", "float", 2, 0.0, 30.0, 0.05),
    ("exclusion_radius_seconds", "Exclusion radius (s):", "float", 2, 0.0, 120.0, 0.5),
    ("side_search_radius_seconds", "Side search radius (s):", "float", 2, 0.0, 120.0, 0.5),
    ("side_min_points", "Side min points:", "int", 0, 1, 100, 1),
    (
        "position_outlier_floor_seconds",
        "Position outlier floor (s):",
        "float",
        3,
        0.0,
        10.0,
        0.05,
    ),
    ("position_outlier_z", "Position outlier z-score:", "float", 2, 0.1, 50.0, 0.1),
    ("contiguous_run_min_points", "Contiguous run min points:", "int", 0, 1, 100, 1),
    ("max_gap_seconds", "Max gap (s):", "float", 2, 0.0, 300.0, 0.5),
    ("chunk_seconds", "Chunk size (s):", "float", 2, 0.1, 60.0, 0.1),
    ("tempo_window_seconds", "Tempo window (s):", "float", 2, 0.1, 120.0, 0.5),
    ("tempo_window_min_points", "Tempo window min points:", "int", 0, 1, 50, 1),
    ("start_speed_window_seconds", "Start speed window (s):", "float", 2, 0.1, 120.0, 0.5),
]


DARK_ORANGE_STYLESHEET = """
QWidget {
    background-color: #1e1c1a;
    color: #f2ede8;
    font-size: 10pt;
}

QMainWindow, QScrollArea, QAbstractScrollArea {
    background-color: #1e1c1a;
    border: none;
}

QLabel {
    background: transparent;
    color: #f2ede8;
}

QGroupBox {
    background-color: #262320;
    border: 1px solid #3d3833;
    border-radius: 6px;
    margin-top: 14px;
    padding: 10px 8px 8px 8px;
    font-weight: 600;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 4px;
    color: #ff8c1a;
}

QPushButton {
    background-color: #ff8c1a;
    color: #1e1c1a;
    border: none;
    border-radius: 5px;
    padding: 6px 14px;
    font-weight: 600;
}

QPushButton:hover {
    background-color: #ff9f40;
}

QPushButton:pressed {
    background-color: #e67512;
}

QPushButton:disabled {
    background-color: #5a544d;
    color: #b8b0a8;
}

QPushButton:checkable:checked {
    background-color: #e67512;
}

QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox {
    background-color: #2f2b27;
    color: #f2ede8;
    border: 1px solid #3d3833;
    border-radius: 4px;
    padding: 3px 6px;
    selection-background-color: #ff8c1a;
    selection-color: #1e1c1a;
}

QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border: 1px solid #ff8c1a;
}

QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {
    color: #b8b0a8;
    background-color: #262320;
}

QCheckBox {
    spacing: 8px;
}

QCheckBox::indicator {
    width: 15px;
    height: 15px;
    border: 1px solid #3d3833;
    border-radius: 3px;
    background-color: #2f2b27;
}

QCheckBox::indicator:checked {
    background-color: #ff8c1a;
    border: 1px solid #ff8c1a;
}

QCheckBox::indicator:hover {
    border: 1px solid #ff8c1a;
}

QScrollBar:vertical {
    background: #1e1c1a;
    width: 12px;
    margin: 0;
}

QScrollBar::handle:vertical {
    background: #5a544d;
    border-radius: 5px;
    min-height: 24px;
}

QScrollBar::handle:vertical:hover {
    background: #ff8c1a;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}

QScrollBar:horizontal {
    background: #1e1c1a;
    height: 12px;
    margin: 0;
}

QScrollBar::handle:horizontal {
    background: #5a544d;
    border-radius: 5px;
    min-width: 24px;
}

QScrollBar::handle:horizontal:hover {
    background: #ff8c1a;
}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}

QTableWidget {
    background-color: #262320;
    alternate-background-color: #2a2724;
    gridline-color: #3d3833;
    border: 1px solid #3d3833;
    border-radius: 4px;
    selection-background-color: #ff8c1a;
    selection-color: #1e1c1a;
}

QHeaderView::section {
    background-color: #2f2b27;
    color: #ff8c1a;
    padding: 4px;
    border: none;
    border-bottom: 1px solid #3d3833;
    font-weight: 600;
}

QTableWidget::item:selected {
    background-color: #ff8c1a;
    color: #1e1c1a;
}

QMessageBox, QFileDialog {
    background-color: #1e1c1a;
}

QToolTip {
    background-color: #2f2b27;
    color: #f2ede8;
    border: 1px solid #ff8c1a;
    padding: 4px;
}
"""


class EmittingStream(QObject):
    """A stdout-like object that emits one signal per complete printed line.

    print() issues separate write() calls for the text and the trailing
    newline, so writes are buffered and only emitted once a full line has
    accumulated -- forwarding raw write() calls 1:1 would double every line
    once QTextEdit.append() adds its own newline on top.
    """

    text_written = Signal(str)

    def __init__(self):
        super().__init__()
        self._buffer = ""

    def write(self, text: str) -> int:
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self.text_written.emit(line)
        return len(text)

    def flush(self):
        pass

    def flush_remaining(self):
        if self._buffer:
            self.text_written.emit(self._buffer)
            self._buffer = ""


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


class PipelineWorker(QObject):
    log_line = Signal(str)
    finished = Signal(bool, str)

    def __init__(self, kwargs):
        super().__init__()
        self.kwargs = kwargs

    def run(self):
        stream = EmittingStream()
        stream.text_written.connect(self.log_line)
        try:
            with contextlib.redirect_stdout(stream):
                pipeline.run_pipeline(**self.kwargs)
            stream.flush_remaining()
            self.finished.emit(True, "Pipeline completed successfully.")
        except Exception:  # noqa: BLE001 - surface any pipeline failure as a dialog, not a silent thread death
            stream.flush_remaining()
            self.finished.emit(False, traceback.format_exc())


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Orchestra Warp — Run Pipeline")
        available = QApplication.primaryScreen().availableGeometry()
        self.resize(min(860, available.width()), min(780, available.height()))

        self.pipeline_thread = None
        self.worker = None

        defaults = inspect.signature(pipeline.run_pipeline).parameters

        central = QWidget()
        layout = QVBoxLayout(central)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(central)
        self.setCentralWidget(scroll_area)

        self.solo_edit = self._add_path_row(
            layout,
            "Solo MIDI:",
            self._default_path("data/raw/SOLO.mid"),
            "MIDI files (*.mid *.midi)",
        )
        self.orchestra_edit = self._add_path_row(
            layout,
            "Orchestra MIDI:",
            self._default_path("data/raw/ORCHESTRA.mid"),
            "MIDI files (*.mid *.midi)",
        )
        self.rec_edit = self._add_path_row(
            layout,
            "Recording:",
            self._default_path("data/raw/REC.mp3"),
            "Audio files (*.mp3 *.wav *.m4a)",
        )
        self.output_dir_edit = self._add_path_row(
            layout,
            "Output Folder:",
            str(app_paths.DEFAULT_OUTPUT_DIR),
            is_dir=True,
        )
        self.output_filename_edit = self._add_text_row(
            layout,
            "Output Filename:",
            Path(pipeline.DEFAULT_OUTPUT_FILENAME).stem,
            suffix=".mp3",
        )

        gain_row = QHBoxLayout()
        self.orchestra_gain_spin = self._make_double_spin(
            defaults["orchestra_gain_db"].default, -24.0, 24.0, 1, 0.5
        )
        self.orchestra_gain_spin.setMinimumWidth(90)
        self.solo_gain_spin = self._make_double_spin(
            defaults["solo_gain_db"].default, -24.0, 24.0, 1, 0.5
        )
        self.solo_gain_spin.setMinimumWidth(90)
        gain_row.addWidget(QLabel("Orchestra Gain (dB):"))
        gain_row.addWidget(self.orchestra_gain_spin)
        gain_row.addWidget(QLabel("Solo Gain (dB):"))
        gain_row.addWidget(self.solo_gain_spin)
        self.polyphony_checkbox = QCheckBox("Polyphony Detection")
        self.polyphony_checkbox.setChecked(False)
        gain_row.addWidget(self.polyphony_checkbox)
        gain_row.addStretch()
        layout.addLayout(gain_row)

        self.advanced_toggle = QPushButton("▸ Advanced")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.toggled.connect(self.toggle_advanced_panel)
        layout.addWidget(self.advanced_toggle)

        self.advanced_panel = QWidget()
        advanced_layout = QVBoxLayout(self.advanced_panel)
        advanced_layout.setContentsMargins(0, 0, 0, 0)

        self.sfizz_edit = self._add_path_row(
            advanced_layout,
            "Sfizz_Render Executable:",
            self._default_path("bin/sfizz_render_1.2.2.exe"),
            "Executable (*.exe)",
        )

        self.vpo_edit = self._add_path_row(
            advanced_layout,
            "VPO Sample Library Folder:",
            self._default_path("VPO"),
            is_dir=True,
        )

        self.keep_temp_checkbox = QCheckBox("Keep temporary/intermediate files")
        self.keep_temp_checkbox.setChecked(False)
        advanced_layout.addWidget(self.keep_temp_checkbox)

        self.ghost_filter_checkbox = QCheckBox("Filter ghost pitches in quiet regions")
        self.ghost_filter_checkbox.setChecked(True)
        advanced_layout.addWidget(self.ghost_filter_checkbox)

        skip_track_row = QHBoxLayout()
        self.skip_track_spin = QSpinBox()
        self.skip_track_spin.setRange(-1, 999)
        self.skip_track_spin.setValue(-1)
        self.skip_track_spin.setMinimumWidth(90)
        skip_track_row.addWidget(QLabel("Skip track # (-1 = none):"))
        skip_track_row.addWidget(self.skip_track_spin)
        skip_track_row.addStretch()
        advanced_layout.addLayout(skip_track_row)

        self.advanced_spins = {}
        advanced_layout.addWidget(
            self._build_spec_group("Ghost Pitch Filtering", GHOST_FILTER_SPECS, defaults)
        )
        advanced_layout.addWidget(
            self._build_spec_group("Rest Detection", REST_DETECTION_SPECS, defaults)
        )
        advanced_layout.addWidget(
            self._build_spec_group("Warp Curve Fitting", WARP_CURVE_SPECS, defaults)
        )

        self.advanced_panel.setVisible(False)
        layout.addWidget(self.advanced_panel)

        self.run_button = QPushButton("Run Pipeline")
        self.run_button.clicked.connect(self.run_pipeline_clicked)
        layout.addWidget(self.run_button)

        self.log = ClampedZoomTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log)

    # -- screen-size cap --------------------------------------------------

    def _clamp_max_size_to_screen(self):
        screen = self.screen()
        if screen is None:
            return
        available = screen.availableGeometry()
        self.setMaximumSize(available.width(), available.height())

    def showEvent(self, event):
        super().showEvent(event)
        self._clamp_max_size_to_screen()

    def moveEvent(self, event):
        super().moveEvent(event)
        self._clamp_max_size_to_screen()

    def closeEvent(self, event):
        if self.pipeline_thread is not None and self.pipeline_thread.isRunning():
            reply = QMessageBox.question(
                self,
                "Pipeline running",
                "A pipeline run is still in progress. Exit and cancel it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return

            orchestra_player.terminate_active_render()
            self.pipeline_thread.quit()
            self.pipeline_thread.wait(5000)

        event.accept()

    # -- construction helpers --------------------------------------------

    @staticmethod
    def _default_path(relative):
        candidate = PROJECT_ROOT / relative
        return str(candidate) if candidate.exists() else ""

    @staticmethod
    def _make_double_spin(value, minimum, maximum, decimals, step):
        spin = QDoubleSpinBox()
        spin.setDecimals(decimals)
        spin.setRange(minimum, maximum)
        spin.setSingleStep(step)
        spin.setValue(float(value))
        return spin

    def _add_path_row(self, layout, label_text, default_value, file_filter=None, is_dir=False):
        row = QHBoxLayout()
        edit = QLineEdit(default_value)
        browse_btn = QPushButton("Browse…")
        if is_dir:
            browse_btn.clicked.connect(lambda: self._browse_for_dir(edit))
        else:
            browse_btn.clicked.connect(lambda: self._browse_for(edit, file_filter))
        row.addWidget(QLabel(label_text))
        row.addWidget(edit)
        row.addWidget(browse_btn)
        layout.addLayout(row)
        return edit

    def _add_text_row(self, layout, label_text, default_value, suffix=None):
        row = QHBoxLayout()
        edit = QLineEdit(default_value)
        row.addWidget(QLabel(label_text))
        row.addWidget(edit)
        if suffix:
            row.addWidget(QLabel(suffix))
        layout.addLayout(row)
        return edit

    def _browse_for(self, line_edit, file_filter):
        start_dir = line_edit.text() or str(PROJECT_ROOT / "data" / "raw")
        path, _ = QFileDialog.getOpenFileName(self, "Select file", start_dir, file_filter)
        if path:
            line_edit.setText(str(Path(path)))

    def _browse_for_dir(self, line_edit):
        start_dir = line_edit.text() or str(PROJECT_ROOT / "data" / "output")
        path = QFileDialog.getExistingDirectory(self, "Select output folder", start_dir)
        if path:
            line_edit.setText(str(Path(path)))

    def _build_spec_group(self, title, specs, defaults):
        group = QGroupBox(title)
        form = QFormLayout(group)
        for name, label, kind, decimals, minimum, maximum, step in specs:
            default_value = defaults[name].default
            if kind == "int":
                spin = QSpinBox()
                spin.setRange(int(minimum), int(maximum))
                spin.setSingleStep(int(step))
                spin.setValue(int(default_value))
            else:
                spin = self._make_double_spin(default_value, minimum, maximum, decimals, step)
            self.advanced_spins[name] = spin
            form.addRow(label, spin)
        return group

    # -- behavior -----------------------------------------------------------

    def toggle_advanced_panel(self, checked):
        self.advanced_panel.setVisible(checked)
        self.advanced_toggle.setText("▾ Advanced" if checked else "▸ Advanced")

    def run_pipeline_clicked(self):
        required = {
            "Solo MIDI": self.solo_edit.text().strip(),
            "Orchestra MIDI": self.orchestra_edit.text().strip(),
            "Recording": self.rec_edit.text().strip(),
            "sfizz_render executable": self.sfizz_edit.text().strip(),
            "VPO Sample Library Folder": self.vpo_edit.text().strip(),
        }
        for label, path in required.items():
            if not path or not Path(path).exists():
                QMessageBox.warning(
                    self, "Missing input", f"{label} path is missing or doesn't exist."
                )
                return

        output_dir_text = self.output_dir_edit.text().strip()
        if output_dir_text and Path(output_dir_text).exists() and not Path(output_dir_text).is_dir():
            QMessageBox.warning(
                self, "Missing input", "Output Folder path is not a directory."
            )
            return

        kwargs = {
            "solo_midi_path": required["Solo MIDI"],
            "orchestra_midi_path": required["Orchestra MIDI"],
            "rec_audio_path": required["Recording"],
            "output_dir": output_dir_text or None,
            "output_filename": self.output_filename_edit.text().strip() or None,
            "keep_temp_files": self.keep_temp_checkbox.isChecked(),
            "filter_ghost_pitches": self.ghost_filter_checkbox.isChecked(),
            "sfizz_exe_path": required["sfizz_render executable"],
            "vpo_dir": required["VPO Sample Library Folder"],
            "orchestra_gain_db": self.orchestra_gain_spin.value(),
            "solo_gain_db": self.solo_gain_spin.value(),
            "polyphony_detection": self.polyphony_checkbox.isChecked(),
            "skip_track": None if self.skip_track_spin.value() < 0 else self.skip_track_spin.value(),
        }
        for name, spin in self.advanced_spins.items():
            kwargs[name] = spin.value()

        self.log.clear()
        self.run_button.setEnabled(False)

        self.pipeline_thread = QThread()
        self.worker = PipelineWorker(kwargs)
        self.worker.moveToThread(self.pipeline_thread)
        self.pipeline_thread.started.connect(self.worker.run)
        self.worker.log_line.connect(self.append_log)
        self.worker.finished.connect(self.on_pipeline_finished)
        self.worker.finished.connect(self.pipeline_thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.pipeline_thread.finished.connect(self.pipeline_thread.deleteLater)
        self.pipeline_thread.start()

    def append_log(self, text):
        self.log.append(text)

    def on_pipeline_finished(self, success, message):
        self.run_button.setEnabled(True)
        if success:
            QMessageBox.information(self, "Done", message)
        else:
            QMessageBox.critical(self, "Pipeline failed", message)


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_ORANGE_STYLESHEET)
    icon = _load_app_icon()
    app.setWindowIcon(icon)
    window = MainWindow()
    window.setWindowIcon(icon)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
