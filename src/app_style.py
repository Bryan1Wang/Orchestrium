"""Shared look-and-feel and small widgets for the Orchestrium GUIs."""

import tempfile
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

import app_paths

PROJECT_ROOT = app_paths.PROJECT_ROOT


def load_app_icon() -> QIcon:
    """The user drops their own icon.ico/png in assets/ -- until then, no-op."""
    for name in ("icon.ico", "icon.png"):
        icon_path = PROJECT_ROOT / "assets" / name
        if icon_path.exists():
            return QIcon(str(icon_path))
    return QIcon()


def load_svg_pixmap(width: int) -> QPixmap:
    """Render assets/icon.svg crisply at `width` logical px (null pixmap if missing).

    Rendered at the screen's device pixel ratio (at least 2x) rather than
    scaling a small bitmap, so it stays sharp on HiDPI displays.
    """
    svg_path = PROJECT_ROOT / "assets" / "icon.svg"
    if not svg_path.exists():
        return QPixmap()
    renderer = QSvgRenderer(str(svg_path))
    if not renderer.isValid():
        return QPixmap()
    screen = QApplication.primaryScreen()
    dpr = max(2.0, screen.devicePixelRatio() if screen else 1.0)
    # The icon is square (32x32 viewBox); keep the renderer's own aspect anyway.
    size = renderer.defaultSize()
    height = round(width * size.height() / size.width()) if size.width() else width
    pixmap = QPixmap(round(width * dpr), round(height * dpr))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


def clamp_max_size_to_screen(window):
    """Stop a window from being resized/dragged larger than the screen it's on."""
    screen = window.screen()
    if screen is None:
        return
    available = screen.availableGeometry()
    window.setMaximumSize(available.width(), available.height())


STYLESHEET = """
/* Base: everything transparent so cards/sidebar show through; roots and
   dialogs get an explicit background below. */
QWidget {
    background: transparent;
    color: #f2ede8;
    font-size: 10pt;
}

QMainWindow, QDialog, QMessageBox, QFileDialog {
    background-color: #1e1c1a;
}

QWidget#pageRoot {
    background-color: #1e1c1a;
}

QScrollArea, QScrollArea > QWidget > QWidget {
    background: transparent;
    border: none;
}

QLabel {
    background: transparent;
}

QLabel#pageTitle {
    font-size: 20pt;
    font-weight: 700;
    color: #f2ede8;
}

QLabel#pageSubtitle {
    color: #9d948b;
    font-size: 10pt;
}

QLabel#muted {
    color: #9d948b;
}

QLabel#stageLabel {
    font-weight: 600;
}

QLabel#percentLabel {
    color: #ffa31f;
    font-weight: 700;
    font-size: 12pt;
}

/* -- sidebar ------------------------------------------------------- */

QFrame#sidebar {
    background-color: #161412;
    border-right: 1px solid #2f2b27;
}

QLabel#brand {
    font-size: 15pt;
    font-weight: 800;
    color: #ffa31f;
    letter-spacing: 1px;
}

QLabel#brandSub {
    color: #7d756d;
    font-size: 8pt;
}

QPushButton#navButton {
    background-color: transparent;
    color: #b8b0a8;
    text-align: left;
    padding: 11px 16px;
    border: none;
    border-left: 3px solid transparent;
    border-radius: 0;
    font-size: 11pt;
    font-weight: 600;
}

QPushButton#navButton:hover {
    background-color: #211e1b;
    color: #f2ede8;
}

QPushButton#navButton:checked {
    background-color: #2a2420;
    color: #ffa31f;
    border-left: 3px solid #ffa31f;
}

/* -- cards --------------------------------------------------------- */

QFrame#card {
    background-color: #262320;
    border: 1px solid #38332e;
    border-radius: 12px;
}

QLabel#cardTitle {
    color: #ffa31f;
    font-weight: 700;
    font-size: 9pt;
    letter-spacing: 1px;
}

QGroupBox {
    background-color: #2b2724;
    border: 1px solid #3d3833;
    border-radius: 8px;
    margin-top: 14px;
    padding: 10px 8px 8px 8px;
    font-weight: 600;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 4px;
    color: #ffa31f;
}

/* -- buttons ------------------------------------------------------- */

QPushButton {
    background-color: #ffa31f;
    color: #1e1c1a;
    border: none;
    border-radius: 8px;
    padding: 7px 16px;
    font-weight: 600;
}

QPushButton:hover {
    background-color: #ffbb55;
}

QPushButton:pressed {
    background-color: #f08a14;
}

QPushButton:disabled {
    background-color: #4a453f;
    color: #8f877f;
}

QPushButton#ghostButton {
    background-color: #332e29;
    color: #f2ede8;
    border: 1px solid #453f39;
}

QPushButton#ghostButton:hover {
    background-color: #3d3731;
    border: 1px solid #ffa31f;
}

QPushButton#ghostButton:checked {
    background-color: #3d3731;
    border: 1px solid #ffa31f;
    color: #ffa31f;
}

QPushButton#ghostButton:disabled {
    background-color: #2a2724;
    color: #6f6860;
    border: 1px solid #38332e;
}

QPushButton#primaryButton {
    padding: 11px 26px;
    font-size: 11pt;
    border-radius: 10px;
}

QPushButton#dangerButton {
    background-color: #c9453a;
    color: #fff;
    padding: 11px 26px;
    font-size: 11pt;
    border-radius: 10px;
}

QPushButton#dangerButton:hover {
    background-color: #de5a4e;
}

QPushButton#dangerButton:disabled {
    background-color: #4a453f;
    color: #8f877f;
}

/* -- inputs -------------------------------------------------------- */

QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox {
    background-color: #2f2b27;
    color: #f2ede8;
    border: 1px solid #3d3833;
    border-radius: 7px;
    padding: 5px 8px;
    selection-background-color: #ffa31f;
    selection-color: #1e1c1a;
}

QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border: 1px solid #ffa31f;
}

QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {
    color: #8f877f;
    background-color: #262320;
}

QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {
    width: 20px;
    background: #3a352f;
    border: none;
}

QSpinBox::up-button, QDoubleSpinBox::up-button {
    border-top-right-radius: 6px;
    border-bottom: 1px solid #2f2b27;
}

QSpinBox::down-button, QDoubleSpinBox::down-button {
    border-bottom-right-radius: 6px;
}

QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {
    background: #5c4426;
}

QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { image: url("@up@"); width: 8px; }
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { image: url("@down@"); width: 8px; }

QTextEdit#log {
    font-family: Consolas, "Cascadia Mono", monospace;
    font-size: 9pt;
    background-color: #1a1816;
    color: #cfc7be;
}

QCheckBox {
    spacing: 8px;
    background: transparent;
}

QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid #4a443d;
    border-radius: 4px;
    background-color: #2f2b27;
}

QCheckBox::indicator:checked {
    background-color: #ffa31f;
    border: 1px solid #ffa31f;
}

QCheckBox::indicator:hover {
    border: 1px solid #ffa31f;
}

/* -- progress / status -------------------------------------------- */

QProgressBar {
    background-color: #1a1816;
    border: 1px solid #38332e;
    border-radius: 7px;
    min-height: 14px;
    max-height: 14px;
    text-align: center;
}

QProgressBar::chunk {
    border-radius: 6px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 #f08a14, stop:1 #ffb457);
}

QLabel#pill {
    border-radius: 10px;
    padding: 3px 12px;
    font-weight: 700;
    font-size: 8pt;
    background-color: #3a352f;
    color: #b8b0a8;
}

QLabel#pill[state="running"] {
    background-color: #5a3a12;
    color: #ffb457;
}

QLabel#pill[state="done"] {
    background-color: #1f4a2c;
    color: #7fe0a0;
}

QLabel#pill[state="failed"] {
    background-color: #5a2320;
    color: #ff8f86;
}

QLabel#pill[state="cancelled"] {
    background-color: #3a352f;
    color: #d6a96a;
}

/* -- scrollbars / tables / misc ----------------------------------- */

QScrollBar:vertical {
    background: #171513;
    border: none;
    border-radius: 5px;
    width: 10px;
    margin: 0;
}

QScrollBar::handle:vertical {
    background: #7a6f64;
    border-radius: 5px;
    min-height: 36px;
}

QScrollBar::handle:vertical:hover, QScrollBar::handle:vertical:pressed {
    background: #ffa31f;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}

QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: none;
}

QScrollBar:horizontal {
    background: #171513;
    border: none;
    border-radius: 5px;
    height: 10px;
    margin: 0;
}

QScrollBar::handle:horizontal {
    background: #7a6f64;
    border-radius: 5px;
    min-width: 36px;
}

QScrollBar::handle:horizontal:hover, QScrollBar::handle:horizontal:pressed {
    background: #ffa31f;
}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}

QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background: none;
}

QTableWidget {
    background-color: #262320;
    alternate-background-color: #2b2724;
    gridline-color: #38332e;
    border: 1px solid #38332e;
    border-radius: 8px;
    selection-background-color: #ffa31f;
    selection-color: #1e1c1a;
}

QHeaderView::section {
    background-color: #2f2b27;
    color: #ffa31f;
    padding: 6px;
    border: none;
    border-bottom: 1px solid #3d3833;
    font-weight: 600;
}

QTableWidget::item:selected {
    background-color: #ffa31f;
    color: #1e1c1a;
}

QToolTip {
    background-color: #2f2b27;
    color: #f2ede8;
    border: 1px solid #ffa31f;
    padding: 4px;
}
"""




# Qt stylesheets can only load arrow images from a file, so write them out.
for _name, _d in (("up", "M1 4.5 L4 1.5 L7 4.5"), ("down", "M1 1.5 L4 4.5 L7 1.5")):
    _f = Path(tempfile.gettempdir()) / f"orchestrium_{_name}.svg"
    _f.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 8 6"><path d="{_d}" '
        'fill="none" stroke="#f2ede8" stroke-width="1.6" stroke-linecap="round"/></svg>'
    )
    STYLESHEET = STYLESHEET.replace(f"@{_name}@", _f.as_posix())


class NoScrollSpinBox(QSpinBox):
    """A QSpinBox that ignores the mouse wheel so scrolling the page doesn't change it."""

    def wheelEvent(self, event):
        event.ignore()


class NoScrollDoubleSpinBox(QDoubleSpinBox):
    """A QDoubleSpinBox that ignores the mouse wheel so scrolling the page doesn't change it."""

    def wheelEvent(self, event):
        event.ignore()


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


def make_log_edit() -> ClampedZoomTextEdit:
    log = ClampedZoomTextEdit()
    log.setObjectName("log")
    log.setReadOnly(True)
    return log


def make_page_header(title: str, subtitle: str) -> QWidget:
    header = QWidget()
    layout = QVBoxLayout(header)
    layout.setContentsMargins(0, 0, 0, 4)
    layout.setSpacing(2)
    title_label = QLabel(title)
    title_label.setObjectName("pageTitle")
    subtitle_label = QLabel(subtitle)
    subtitle_label.setObjectName("pageSubtitle")
    layout.addWidget(title_label)
    layout.addWidget(subtitle_label)
    return header


def make_card(title: str | None = None):
    """A rounded card with an optional small-caps title. Returns (card, layout)."""
    card = QFrame()
    card.setObjectName("card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(16, 14, 16, 16)
    layout.setSpacing(10)
    if title:
        title_label = QLabel(title.upper())
        title_label.setObjectName("cardTitle")
        layout.addWidget(title_label)
    return card, layout


def make_grid() -> QGridLayout:
    """A label | field | button grid whose middle column stretches."""
    grid = QGridLayout()
    grid.setHorizontalSpacing(10)
    grid.setVerticalSpacing(8)
    grid.setColumnStretch(1, 1)
    return grid


def make_pill() -> QLabel:
    pill = QLabel("IDLE")
    pill.setObjectName("pill")
    pill.setProperty("state", "idle")
    return pill


def set_pill(pill: QLabel, text: str, state: str):
    pill.setText(text.upper())
    pill.setProperty("state", state)
    pill.style().unpolish(pill)
    pill.style().polish(pill)


def add_path_row(
    grid: QGridLayout,
    row: int,
    label_text: str,
    default_value: str,
    *,
    file_filter: str | None = None,
    is_dir: bool = False,
    save: bool = False,
    fallback_dir: Path | None = None,
) -> QLineEdit:
    """Add `label | line edit | Browse…` to a grid row and return the line edit."""
    edit = QLineEdit(default_value)
    browse = QPushButton("Browse…")
    browse.setObjectName("ghostButton")

    def browse_clicked():
        start = edit.text() or str(fallback_dir or PROJECT_ROOT / "data")
        if is_dir:
            path = QFileDialog.getExistingDirectory(edit.window(), "Select folder", start)
        elif save:
            path, _ = QFileDialog.getSaveFileName(
                edit.window(), "Select output file", start, file_filter or ""
            )
        else:
            path, _ = QFileDialog.getOpenFileName(
                edit.window(), "Select file", start, file_filter or ""
            )
        if path:
            edit.setText(str(Path(path)))

    browse.clicked.connect(browse_clicked)
    grid.addWidget(QLabel(label_text), row, 0)
    grid.addWidget(edit, row, 1)
    grid.addWidget(browse, row, 2)
    return edit
