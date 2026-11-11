import contextlib
import inspect
import sys
import threading
import traceback
from pathlib import Path


import splash

# Only when run directly; orchestrium shows its own splash when it imports this.
_splash = splash.show() if __name__ == "__main__" else None

# Must be imported before PySide6/Qt: importing Qt first changes the Windows
# DLL search path in a way that breaks onnxruntime's native module load
# (basic_pitch -> onnxruntime, pulled in transitively via main -> transcribe).
# isort: off
import app_paths
import main as pipeline
import orchestra_player

# isort: on

if _splash is not None:
    _splash.destroy()

from PySide6.QtCore import (
    QEasingCurve,
    QObject,
    QPropertyAnimation,
    Qt,
    QThread,
    QTimer,
    Signal,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import app_style
from app_style import NoScrollDoubleSpinBox, NoScrollSpinBox

PROJECT_ROOT = app_paths.PROJECT_ROOT


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


class PipelineWorker(QObject):
    log_line = Signal(str)
    progress = Signal(float, str)
    # status is one of "ok", "cancelled", "failed"
    finished = Signal(str, str)

    def __init__(self, kwargs):
        super().__init__()
        self.kwargs = kwargs
        self._cancel = threading.Event()

    def cancel(self):
        self._cancel.set()

    def _on_progress(self, percent, label):
        # Every stage boundary doubles as a cancellation point.
        if self._cancel.is_set():
            raise pipeline.PipelineCancelled()
        self.progress.emit(percent, label)

    def run(self):
        stream = EmittingStream()
        stream.text_written.connect(self.log_line)
        try:
            with contextlib.redirect_stdout(stream):
                pipeline.run_pipeline(progress_callback=self._on_progress, **self.kwargs)
            stream.flush_remaining()
            self.finished.emit("ok", "Pipeline completed successfully.")
        except pipeline.PipelineCancelled:
            stream.flush_remaining()
            self.finished.emit("cancelled", "Run cancelled.")
        except Exception:  # noqa: BLE001 - surface any pipeline failure as a dialog, not a silent thread death
            stream.flush_remaining()
            self.finished.emit("failed", traceback.format_exc())


class PipelinePage(QWidget):
    """The pipeline runner as an embeddable page (used by orchestrium and MainWindow)."""

    def __init__(self):
        super().__init__()
        self.setObjectName("pageRoot")

        self.pipeline_thread = None
        self.worker = None
        self._closing = False

        defaults = inspect.signature(pipeline.run_pipeline).parameters

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(14)
        outer.addWidget(
            app_style.make_page_header(
                "Pipeline", "Warp the orchestra to follow your recording and mix the master."
            )
        )

        # -- scrollable settings -------------------------------------------------
        scroll_area = QScrollArea()
        self.scroll_area = scroll_area
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self._scroll_anim = QPropertyAnimation(scroll_area.verticalScrollBar(), b"value", self)
        self._scroll_anim.setDuration(280)
        self._scroll_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._scroll_before_advanced = 0
        self._scroll_on_finished = None
        self._scroll_anim.finished.connect(self._scroll_anim_finished)
        settings = QWidget()
        self.settings_widget = settings
        settings_layout = QVBoxLayout(settings)
        settings_layout.setContentsMargins(0, 0, 8, 0)
        settings_layout.setSpacing(14)
        scroll_area.setWidget(settings)
        outer.addWidget(scroll_area, 1)

        inputs_card, inputs_layout = app_style.make_card("Inputs")
        grid = app_style.make_grid()
        midi_filter = "MIDI files (*.mid *.midi)"
        self.solo_edit = app_style.add_path_row(
            grid, 0, "Solo MIDI", self._default_path("data/raw/SOLO.mid"), file_filter=midi_filter
        )
        self.orchestra_edit = app_style.add_path_row(
            grid,
            1,
            "Orchestra MIDI",
            self._default_path("data/raw/ORCHESTRA.mid"),
            file_filter=midi_filter,
        )
        self.rec_edit = app_style.add_path_row(
            grid,
            2,
            "Recording",
            self._default_path("data/raw/REC.mp3"),
            file_filter="Audio files (*.mp3 *.wav *.m4a)",
        )
        inputs_layout.addLayout(grid)
        settings_layout.addWidget(inputs_card)

        output_card, output_layout = app_style.make_card("Output")
        grid = app_style.make_grid()
        self.output_dir_edit = app_style.add_path_row(
            grid,
            0,
            "Output folder",
            str(app_paths.DEFAULT_OUTPUT_DIR),
            is_dir=True,
            fallback_dir=PROJECT_ROOT / "data" / "output",
        )
        self.output_filename_edit = QLineEdit(Path(pipeline.DEFAULT_OUTPUT_FILENAME).stem)
        grid.addWidget(QLabel("Filename"), 1, 0)
        grid.addWidget(self.output_filename_edit, 1, 1)
        suffix_label = QLabel(".mp3")
        suffix_label.setObjectName("muted")
        grid.addWidget(suffix_label, 1, 2)
        output_layout.addLayout(grid)
        settings_layout.addWidget(output_card)

        mix_card, mix_layout = app_style.make_card("Mix")
        self.orchestra_gain_spin = self._make_double_spin(
            defaults["orchestra_gain_db"].default, -24.0, 24.0, 1, 0.5
        )
        self.solo_gain_spin = self._make_double_spin(
            defaults["solo_gain_db"].default, -24.0, 24.0, 1, 0.5
        )
        for spin in (self.orchestra_gain_spin, self.solo_gain_spin):
            spin.setMinimumWidth(90)
        # Narrow two-row grid (rather than one wide row) so the card never
        # forces the page wider than a modest window.
        gain_grid = QGridLayout()
        gain_grid.setHorizontalSpacing(10)
        gain_grid.setVerticalSpacing(8)
        gain_grid.addWidget(QLabel("Orchestra gain (dB)"), 0, 0)
        gain_grid.addWidget(self.orchestra_gain_spin, 0, 1)
        gain_grid.addWidget(QLabel("Solo gain (dB)"), 1, 0)
        gain_grid.addWidget(self.solo_gain_spin, 1, 1)
        gain_grid.setColumnStretch(2, 1)
        mix_layout.addLayout(gain_grid)
        self.polyphony_checkbox = QCheckBox("Polyphony detection")
        self.polyphony_checkbox.setChecked(False)
        mix_layout.addWidget(self.polyphony_checkbox)
        settings_layout.addWidget(mix_card)

        self.advanced_toggle = QPushButton("▸  Advanced settings")
        self.advanced_toggle.setObjectName("ghostButton")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.toggled.connect(self.toggle_advanced_panel)
        settings_layout.addWidget(self.advanced_toggle)

        self.advanced_panel = QWidget()
        advanced_layout = QVBoxLayout(self.advanced_panel)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        advanced_layout.setSpacing(14)

        paths_card, paths_layout = app_style.make_card("Tools & samples")
        grid = app_style.make_grid()
        self.sfizz_edit = app_style.add_path_row(
            grid,
            0,
            "sfizz_render",
            self._default_path("bin/sfizz_render_1.2.2.exe"),
            file_filter="Executable (*.exe)",
        )
        self.vpo_edit = app_style.add_path_row(
            grid, 1, "VPO sample library", self._default_path("VPO"), is_dir=True
        )
        paths_layout.addLayout(grid)

        self.keep_temp_checkbox = QCheckBox("Keep temporary/intermediate files")
        self.keep_temp_checkbox.setChecked(False)
        paths_layout.addWidget(self.keep_temp_checkbox)

        self.ghost_filter_checkbox = QCheckBox("Filter ghost pitches in quiet regions")
        self.ghost_filter_checkbox.setChecked(True)
        paths_layout.addWidget(self.ghost_filter_checkbox)

        skip_track_row = QHBoxLayout()
        self.skip_track_spin = NoScrollSpinBox()
        self.skip_track_spin.setRange(-1, 999)
        self.skip_track_spin.setValue(-1)
        self.skip_track_spin.setMinimumWidth(90)
        skip_track_row.addWidget(QLabel("Skip track # (-1 = none)"))
        skip_track_row.addWidget(self.skip_track_spin)
        skip_track_row.addStretch()
        paths_layout.addLayout(skip_track_row)
        advanced_layout.addWidget(paths_card)

        self.advanced_spins = {}
        tuning_card, tuning_layout = app_style.make_card("Tuning")
        tuning_layout.addWidget(
            self._build_spec_group("Ghost Pitch Filtering", GHOST_FILTER_SPECS, defaults)
        )
        tuning_layout.addWidget(
            self._build_spec_group("Rest Detection", REST_DETECTION_SPECS, defaults)
        )
        tuning_layout.addWidget(
            self._build_spec_group("Warp Curve Fitting", WARP_CURVE_SPECS, defaults)
        )
        advanced_layout.addWidget(tuning_card)

        self.advanced_panel.setVisible(False)
        settings_layout.addWidget(self.advanced_panel)
        settings_layout.addStretch()

        # -- run / progress card (always visible) -----------------------------------
        run_card, run_layout = app_style.make_card()

        button_row = QHBoxLayout()
        self.run_button = QPushButton("Generate Recording")
        self.run_button.setObjectName("primaryButton")
        self.run_button.clicked.connect(self.run_pipeline_clicked)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("dangerButton")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_run)
        self.pill = app_style.make_pill()
        button_row.addWidget(self.run_button)
        button_row.addWidget(self.cancel_button)
        button_row.addStretch()
        button_row.addWidget(self.pill)
        run_layout.addLayout(button_row)

        stage_row = QHBoxLayout()
        self.stage_label = QLabel("Ready")
        self.stage_label.setObjectName("stageLabel")
        self.percent_label = QLabel("0%")
        self.percent_label.setObjectName("percentLabel")
        stage_row.addWidget(self.stage_label)
        stage_row.addStretch()
        stage_row.addWidget(self.percent_label)
        run_layout.addLayout(stage_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        run_layout.addWidget(self.progress_bar)

        self.log_toggle = QPushButton("▸  Show log")
        self.log_toggle.setObjectName("ghostButton")
        self.log_toggle.setCheckable(True)
        self.log_toggle.toggled.connect(self.toggle_log)
        run_layout.addWidget(self.log_toggle)

        self.log = app_style.make_log_edit()
        self.log.setMinimumHeight(170)
        self.log.setVisible(False)
        run_layout.addWidget(self.log)

        outer.addWidget(run_card)

    # -- construction helpers --------------------------------------------

    @staticmethod
    def _default_path(relative):
        candidate = PROJECT_ROOT / relative
        return str(candidate) if candidate.exists() else ""

    @staticmethod
    def _make_double_spin(value, minimum, maximum, decimals, step):
        spin = NoScrollDoubleSpinBox()
        spin.setDecimals(decimals)
        spin.setRange(minimum, maximum)
        spin.setSingleStep(step)
        spin.setValue(float(value))
        return spin

    def _build_spec_group(self, title, specs, defaults):
        group = QGroupBox(title)
        form = QFormLayout(group)
        for name, label, kind, decimals, minimum, maximum, step in specs:
            default_value = defaults[name].default
            if kind == "int":
                spin = NoScrollSpinBox()
                spin.setRange(int(minimum), int(maximum))
                spin.setSingleStep(int(step))
                spin.setValue(int(default_value))
            else:
                spin = self._make_double_spin(default_value, minimum, maximum, decimals, step)
            self.advanced_spins[name] = spin
            form.addRow(label, spin)
        return group

    # -- public hooks for the host window ------------------------------------

    def set_midi_paths(self, solo_path=None, orchestra_path=None):
        """Pre-fill the MIDI inputs (e.g. after the Slicer writes new files)."""
        if solo_path:
            self.solo_edit.setText(str(solo_path))
        if orchestra_path:
            self.orchestra_edit.setText(str(orchestra_path))

    def is_running(self):
        return self.pipeline_thread is not None and self.pipeline_thread.isRunning()

    def request_close(self, window) -> bool:
        """Call from a window's closeEvent. Returns False if the user backed out.

        If a run is in flight, cancels it and waits for the worker thread to
        actually finish before returning -- letting Qt tear down a QThread whose
        run() is still executing is what prints the "QThread: Destroyed while
        thread is still running" error on exit.
        """
        if not self.is_running():
            return True
        reply = QMessageBox.question(
            window,
            "In Progress",
            "A task is still running. Exit and cancel it?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return False

        self._closing = True
        self.cancel_run()
        window.hide()
        self.pipeline_thread.wait()
        return True

    # -- behavior -----------------------------------------------------------

    def _animate_scroll_to(self, target, on_finished=None):
        bar = self.scroll_area.verticalScrollBar()
        self._scroll_anim.stop()
        self._scroll_on_finished = on_finished
        self._scroll_anim.setStartValue(bar.value())
        self._scroll_anim.setEndValue(int(target))
        self._scroll_anim.start()

    def _scroll_anim_finished(self):
        callback, self._scroll_on_finished = self._scroll_on_finished, None
        if callback is not None:
            callback()

    def _scroll_to_advanced(self):
        if not self.advanced_toggle.isChecked():
            return  # closed again before this ran
        bar = self.scroll_area.verticalScrollBar()
        target = min(self.advanced_toggle.y() - 4, bar.maximum())
        self._animate_scroll_to(max(target, bar.value()))

    def toggle_advanced_panel(self, checked):
        self.advanced_toggle.setText("▾  Advanced settings" if checked else "▸  Advanced settings")
        bar = self.scroll_area.verticalScrollBar()
        if checked:
            # Remember where the user was so closing the panel can put them back.
            self._scroll_before_advanced = bar.value()
            self.advanced_panel.setVisible(True)
            # The scroll range only grows once the layout has processed the newly
            # visible panel, so compute the target on the next event-loop turn,
            # then bring the toggle button to the top so the new settings are in view.
            QTimer.singleShot(0, self._scroll_to_advanced)
        else:
            # Scroll back first, then hide, so the view doesn't jump.
            self._animate_scroll_to(
                self._scroll_before_advanced,
                on_finished=lambda: self.advanced_panel.setVisible(False),
            )

    def toggle_log(self, checked):
        self.log.setVisible(checked)
        self.log_toggle.setText("▾  Hide log" if checked else "▸  Show log")

    def cancel_run(self):
        if not self.is_running():
            return
        self.cancel_button.setEnabled(False)
        self.stage_label.setText("Cancelling…")
        if self.worker is not None:
            self.worker.cancel()
        orchestra_player.terminate_active_render()

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
                    self.window(), "Missing input", f"{label} path is missing or doesn't exist."
                )
                return

        output_dir_text = self.output_dir_edit.text().strip()
        if output_dir_text and Path(output_dir_text).exists() and not Path(output_dir_text).is_dir():
            QMessageBox.warning(self.window(), "Missing input", "Output Folder path is not a directory.")
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
        self.log_toggle.setChecked(True)
        self.run_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self._set_progress(0.0, "Starting…")
        app_style.set_pill(self.pill, "Running", "running")

        # Both objects stay referenced until the next run replaces them: a
        # QThread must never be garbage-collected while still running.
        self.pipeline_thread = QThread()
        self.worker = PipelineWorker(kwargs)
        self.worker.moveToThread(self.pipeline_thread)
        self.pipeline_thread.started.connect(self.worker.run)
        self.worker.log_line.connect(self.append_log)
        self.worker.progress.connect(self._set_progress)
        self.worker.finished.connect(self.on_pipeline_finished)
        # Direct, not queued: quit() must run on the worker thread itself, since
        # request_close() may be blocking the main thread in wait() -- a queued
        # quit would never be delivered and the exit would deadlock.
        self.worker.finished.connect(
            self.pipeline_thread.quit, Qt.ConnectionType.DirectConnection
        )
        self.pipeline_thread.start()

    def append_log(self, text):
        self.log.append(text)

    def _set_progress(self, percent, label):
        self.progress_bar.setValue(int(percent * 10))
        self.percent_label.setText(f"{percent:.0f}%")
        self.stage_label.setText(label)

    def on_pipeline_finished(self, status, message):
        if self._closing:
            return
        self.run_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        if status == "ok":
            self._set_progress(100.0, "Done")
            app_style.set_pill(self.pill, "Done", "done")
            QMessageBox.information(self.window(), "Done", message)
        elif status == "cancelled":
            self._set_progress(0.0, "Cancelled")
            app_style.set_pill(self.pill, "Cancelled", "cancelled")
            # Collapse the log box again; its text is kept until the next run
            # (run_pipeline_clicked clears it), and "Show log" reopens it.
            self.log_toggle.setChecked(False)
        else:
            self.stage_label.setText("Failed")
            app_style.set_pill(self.pill, "Failed", "failed")
            QMessageBox.critical(self.window(), "Pipeline failed", message)


class MainWindow(QMainWindow):
    """Standalone host for the pipeline page (python src\\main_gui.py)."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Orchestrium")
        available = QApplication.primaryScreen().availableGeometry()
        self.setMinimumSize(700, 480)
        self.resize(
            min(860, int(available.width() * 0.85)),
            min(720, int(available.height() * 0.85)),
        )
        self.page = PipelinePage()
        self.setCentralWidget(self.page)

    def showEvent(self, event):
        super().showEvent(event)
        app_style.clamp_max_size_to_screen(self)

    def moveEvent(self, event):
        super().moveEvent(event)
        app_style.clamp_max_size_to_screen(self)

    def closeEvent(self, event):
        if self.page.request_close(self):
            event.accept()
        else:
            event.ignore()


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(app_style.STYLESHEET)
    icon = app_style.load_app_icon()
    app.setWindowIcon(icon)
    window = MainWindow()
    window.setWindowIcon(icon)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
