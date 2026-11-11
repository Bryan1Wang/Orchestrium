# isort: skip_file
# Import order matters here -- keep it (and don't let a formatter re-sort it):
# the splash shows first, then main_gui (which pulls in onnxruntime) must load
# before ANY Qt module, or onnxruntime's native DLL load breaks on Windows.
import sys

import splash

_splash = splash.show()

import main_gui

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

import app_style
from utils.slicer_gui import SlicerPage

_splash.destroy()


class AppWindow(QMainWindow):
    SIDEBAR_WIDTH = 200
    ICON_PADDING = 24  # left/right gap around the sidebar icon

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Orchestrium")
        available = QApplication.primaryScreen().availableGeometry()
        # Start comfortably inside the screen (taskbar/title bar eat into it), and
        # pin an explicit minimum so the pages' own size hints can't force the
        # window past a small screen -- the pages scroll instead.
        self.setMinimumSize(900, 480)
        self.resize(
            min(1000, int(available.width() * 0.85)),
            min(720, int(available.height() * 0.85)),
        )

        central = QWidget()
        central.setObjectName("pageRoot")
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setCentralWidget(central)

        self.pipeline_page = main_gui.PipelinePage()
        self.slicer_page = SlicerPage()
        self.slicer_page.sliced.connect(self._on_sliced)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.pipeline_page)
        self.stack.addWidget(self.slicer_page)

        root.addWidget(self._build_sidebar())
        root.addWidget(self.stack, 1)

    def _build_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(self.SIDEBAR_WIDTH)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 22, 0, 18)
        layout.setSpacing(2)

        brand = QLabel("ORCHESTRIUM")
        brand.setObjectName("brand")
        brand.setContentsMargins(18, 0, 0, 0)
        tagline = QLabel("accompaniment that follows you")
        tagline.setObjectName("brandSub")
        tagline.setContentsMargins(18, 0, 0, 0)
        layout.addWidget(brand)
        layout.addWidget(tagline)

        # Full sidebar width, rendered from the SVG so it stays sharp.
        icon_size = self.SIDEBAR_WIDTH - 2 * self.ICON_PADDING
        icon_pixmap = app_style.load_svg_pixmap(icon_size)
        if icon_pixmap.isNull():
            icon_pixmap = app_style.load_app_icon().pixmap(icon_size, icon_size)
        if not icon_pixmap.isNull():
            layout.addSpacing(16)
            icon_label = QLabel()
            icon_label.setPixmap(icon_pixmap)
            icon_label.setFixedWidth(self.SIDEBAR_WIDTH)
            icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(icon_label)
        layout.addSpacing(24)

        group = QButtonGroup(self)
        group.setExclusive(True)
        for index, text in enumerate(("▶   Accompany", "✂   Slicer")):
            button = QPushButton(text)
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            group.addButton(button, index)
            layout.addWidget(button)
            if index == 0:
                button.setChecked(True)
        group.idClicked.connect(self.stack.setCurrentIndex)

        layout.addStretch()
        hint = QLabel("Designed by Bryan Wang")
        hint.setObjectName("brandSub")
        hint.setContentsMargins(18, 0, 12, 0)
        layout.addWidget(hint)
        return sidebar

    def _on_sliced(self, solo_path, orchestra_path):
        self.pipeline_page.set_midi_paths(solo_path, orchestra_path)

    def showEvent(self, event):
        super().showEvent(event)
        app_style.clamp_max_size_to_screen(self)

    def moveEvent(self, event):
        super().moveEvent(event)
        app_style.clamp_max_size_to_screen(self)

    def closeEvent(self, event):
        if self.pipeline_page.request_close(self):
            event.accept()
        else:
            event.ignore()


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(app_style.STYLESHEET)
    icon = app_style.load_app_icon()
    app.setWindowIcon(icon)
    window = AppWindow()
    window.setWindowIcon(icon)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
