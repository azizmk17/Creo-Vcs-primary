"""
Embedded PDF viewer widget for the Files tab.

Uses PyMuPDF (fitz) to render PDF pages as QPixmap images inside a scrollable
QLabel.  Provides page navigation and zoom controls.
"""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QSizePolicy, QSlider, QFrame, QComboBox,
)
from PyQt5.QtCore import Qt, QSize, QPoint
from PyQt5.QtGui import QImage, QPixmap, QPainter

try:
    import fitz  # PyMuPDF
    HAS_FITZ = True
except ImportError:
    HAS_FITZ = False


class PanScrollArea(QScrollArea):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._drag_active = False
        self._drag_start = QPoint()
        self.setCursor(Qt.ArrowCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_active = True
            self._drag_start = event.pos()
            self.setCursor(Qt.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_active:
            delta = event.pos() - self._drag_start
            self._drag_start = event.pos()
            hbar = self.horizontalScrollBar()
            vbar = self.verticalScrollBar()
            hbar.setValue(hbar.value() - delta.x())
            vbar.setValue(vbar.value() - delta.y())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_active = False
            self.setCursor(Qt.ArrowCursor)
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        # Mouse wheel zoom (Ctrl+wheel or always)
        if event.modifiers() & Qt.ControlModifier or True:
            angle = event.angleDelta().y()
            parent = self.parentWidget()
            while parent and not hasattr(parent, '_zoom_in'):
                parent = parent.parentWidget()
            if parent:
                if angle > 0:
                    parent._zoom_in()
                elif angle < 0:
                    parent._zoom_out()
                return
        super().wheelEvent(event)


class PdfViewerWidget(QWidget):
    """Inline PDF preview panel with page navigation and zoom."""

    ZOOM_LEVELS = [50, 75, 100, 125, 150, 200, 300]
    DEFAULT_ZOOM_INDEX = 2  # 100 %

    def __init__(self, parent=None):
        super().__init__(parent)
        self._doc = None          # fitz.Document
        self._page_index = 0
        self._page_count = 0
        self._zoom_index = self.DEFAULT_ZOOM_INDEX
        self._current_path = ""
        self._build_ui()

    # ── UI construction ───────────────────────────────────────────────
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(4)

        # Header bar
        header = QFrame()
        header.setObjectName("pdfViewerHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(6, 2, 6, 2)
        header_layout.setSpacing(6)

        self._title_label = QLabel("PDF Preview")
        self._title_label.setStyleSheet("font-weight: bold; color: #374151;")
        header_layout.addWidget(self._title_label)

        header_layout.addStretch()

        # Page navigation
        self._prev_btn = QPushButton("◀")
        self._prev_btn.setFixedSize(28, 28)
        self._prev_btn.setToolTip("Previous page")
        self._prev_btn.clicked.connect(self._prev_page)
        header_layout.addWidget(self._prev_btn)

        self._page_label = QLabel("0 / 0")
        self._page_label.setMinimumWidth(60)
        self._page_label.setAlignment(Qt.AlignCenter)
        header_layout.addWidget(self._page_label)

        self._next_btn = QPushButton("▶")
        self._next_btn.setFixedSize(28, 28)
        self._next_btn.setToolTip("Next page")
        self._next_btn.clicked.connect(self._next_page)
        header_layout.addWidget(self._next_btn)

        # Zoom
        self._zoom_combo = QComboBox()
        self._zoom_combo.setFixedWidth(80)
        for z in self.ZOOM_LEVELS:
            self._zoom_combo.addItem(f"{z}%")
        self._zoom_combo.setCurrentIndex(self._zoom_index)
        self._zoom_combo.currentIndexChanged.connect(self._on_zoom_changed)
        header_layout.addWidget(self._zoom_combo)

        # Fit-width toggle
        self._fit_btn = QPushButton("Fit")
        self._fit_btn.setFixedSize(36, 28)
        self._fit_btn.setToolTip("Fit page to viewer width")
        self._fit_btn.setCheckable(True)
        self._fit_btn.clicked.connect(self._render_current_page)
        header_layout.addWidget(self._fit_btn)

        # Close / collapse
        self._close_btn = QPushButton("✕")
        self._close_btn.setFixedSize(28, 28)
        self._close_btn.setToolTip("Close preview")
        self._close_btn.clicked.connect(self.close_preview)
        header_layout.addWidget(self._close_btn)

        layout.addWidget(header)

        # Scroll area containing the rendered page image
        self._scroll = PanScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setAlignment(Qt.AlignCenter)
        self._scroll.setStyleSheet(
            "QScrollArea { background: #e5e7eb; border: 1px solid #d1d5db; border-radius: 4px; }"
        )

        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignCenter)
        self._image_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self._scroll.setWidget(self._image_label)

        layout.addWidget(self._scroll, 1)

        # Placeholder when nothing is loaded
        self._placeholder = QLabel("Select a PDF attachment to preview")
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._placeholder.setStyleSheet("color: #9ca3af; padding: 24px;")
        layout.addWidget(self._placeholder)

        self._set_loaded(False)

    # ── Public API ────────────────────────────────────────────────────
    def load_pdf(self, file_path: str):
        """Open *file_path* and display the first page."""
        if not HAS_FITZ:
            self._placeholder.setText("PyMuPDF not installed – run: pip install PyMuPDF")
            self._set_loaded(False)
            return

        if not file_path:
            self.close_preview()
            return

        # Avoid reload of the same file
        if file_path == self._current_path and self._doc:
            return

        self._close_doc()

        try:
            self._doc = fitz.open(file_path)
            self._page_count = len(self._doc)
            self._page_index = 0
            self._current_path = file_path
            self._set_loaded(True)
            self._render_current_page()
        except Exception as exc:
            self._placeholder.setText(f"Failed to open PDF:\n{exc}")
            self._set_loaded(False)

    def close_preview(self):
        self._close_doc()
        self._current_path = ""
        self._set_loaded(False)
        self._placeholder.setText("Select a PDF attachment to preview")

    def is_loaded(self) -> bool:
        return self._doc is not None

    # ── Internal helpers ──────────────────────────────────────────────
    def _close_doc(self):
        if self._doc:
            try:
                self._doc.close()
            except Exception:
                pass
            self._doc = None
            self._page_count = 0
            self._page_index = 0

    def _set_loaded(self, loaded: bool):
        self._scroll.setVisible(loaded)
        self._prev_btn.setVisible(loaded)
        self._next_btn.setVisible(loaded)
        self._page_label.setVisible(loaded)
        self._zoom_combo.setVisible(loaded)
        self._fit_btn.setVisible(loaded)
        self._placeholder.setVisible(not loaded)
        if loaded:
            self._title_label.setText("PDF Preview")
        else:
            self._image_label.clear()
            self._title_label.setText("PDF Preview")

    def _render_current_page(self):
        if not self._doc or self._page_index >= self._page_count:
            return

        page = self._doc[self._page_index]

        if self._fit_btn.isChecked():
            # Compute zoom to fit viewport width
            viewport_w = self._scroll.viewport().width() - 16  # small margin
            page_w = page.rect.width
            if page_w > 0 and viewport_w > 0:
                zoom = viewport_w / page_w
            else:
                zoom = 1.0
        else:
            zoom = self.ZOOM_LEVELS[self._zoom_index] / 100.0

        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)

        fmt = QImage.Format_RGB888
        qimage = QImage(pix.samples, pix.width, pix.height, pix.stride, fmt)
        pixmap = QPixmap.fromImage(qimage)

        self._image_label.setPixmap(pixmap)
        self._image_label.adjustSize()

        self._page_label.setText(f"{self._page_index + 1} / {self._page_count}")
        self._prev_btn.setEnabled(self._page_index > 0)
        self._next_btn.setEnabled(self._page_index < self._page_count - 1)

    def _prev_page(self):
        if self._page_index > 0:
            self._page_index -= 1
            self._render_current_page()

    def _next_page(self):
        if self._page_index < self._page_count - 1:
            self._page_index += 1
            self._render_current_page()

    def _on_zoom_changed(self, index: int):
        self._zoom_index = index
        if self._fit_btn.isChecked():
            self._fit_btn.setChecked(False)
        self._render_current_page()

    # Re-render on resize when "Fit" is active
    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fit_btn.isChecked() and self._doc:
            self._render_current_page()
