"""Reusable embedded and expanded PDF viewer based on PyMuPDF."""

import os

from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtGui import QImage, QKeySequence, QPixmap
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QShortcut,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

try:
    import fitz

    HAS_FITZ = True
except ImportError:
    fitz = None
    HAS_FITZ = False


class PanScrollArea(QScrollArea):
    """Scroll area supporting click-drag panning and Ctrl+wheel zoom."""

    def __init__(self, viewer, parent=None):
        super().__init__(parent)
        self._viewer = viewer
        self._drag_active = False
        self._drag_start = QPoint()
        self.setCursor(Qt.OpenHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_active = True
            self._drag_start = event.pos()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_active:
            delta = event.pos() - self._drag_start
            self._drag_start = event.pos()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._drag_active:
            self._drag_active = False
            self.setCursor(Qt.OpenHandCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            self._viewer.zoom_in() if event.angleDelta().y() > 0 else self._viewer.zoom_out()
            event.accept()
            return
        super().wheelEvent(event)


class PdfViewerDialog(QDialog):
    def __init__(self, file_path: str, page_index=0, zoom_index=2, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"PDF Viewer - {os.path.basename(file_path)}")
        self.setWindowFlags(self.windowFlags() | Qt.WindowMaximizeButtonHint)
        self.resize(1200, 850)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self.viewer = PdfViewerWidget(show_expand=False, parent=self)
        layout.addWidget(self.viewer)
        self.viewer.load_pdf(file_path, page_index=page_index, zoom_index=zoom_index)

    def closeEvent(self, event):
        self.viewer.close_preview()
        super().closeEvent(event)


class PdfViewerWidget(QWidget):
    """PDF preview with navigation, fit modes, zoom, opening, and expansion."""

    ZOOM_LEVELS = [25, 50, 75, 100, 125, 150, 200, 300, 400]
    DEFAULT_ZOOM_INDEX = 3

    def __init__(self, parent=None, show_expand=True):
        super().__init__(parent)
        self._doc = None
        self._page_index = 0
        self._page_count = 0
        self._zoom_index = self.DEFAULT_ZOOM_INDEX
        self._render_mode = "width"
        self._current_path = ""
        self._show_expand = show_expand
        self._expanded_dialog = None
        self._build_ui()

    def _tool_button(self, text, tooltip, slot, width=30):
        button = QPushButton(text)
        button.setFixedSize(width, 28)
        button.setToolTip(tooltip)
        button.clicked.connect(slot)
        return button

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(4)

        header = QFrame()
        header.setStyleSheet(
            """
            QFrame { background:#f8fafc; border:1px solid #d1d5db; border-radius:4px; }
            QLabel { color:#374151; background:transparent; border:none; }
            QPushButton {
                color:#1f2937; background:#ffffff; border:1px solid #cbd5e1;
                border-radius:4px; font-weight:600;
            }
            QPushButton:hover { background:#eef2f7; border-color:#94a3b8; }
            QPushButton:disabled { color:#9ca3af; background:#f3f4f6; }
            QComboBox {
                color:#1f2937; background:#ffffff; border:1px solid #cbd5e1;
                border-radius:4px; padding:2px 5px;
            }
            """
        )
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(6, 3, 6, 3)
        header_layout.setSpacing(5)

        self._title_label = QLabel("PDF Preview")
        self._title_label.setStyleSheet("font-weight:600;color:#374151;border:none;")
        self._title_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        header_layout.addWidget(self._title_label, 1)

        self._prev_btn = self._tool_button("<", "Previous page", self.previous_page)
        self._page_label = QLabel("0 / 0")
        self._page_label.setMinimumWidth(58)
        self._page_label.setAlignment(Qt.AlignCenter)
        self._page_label.setStyleSheet("border:none;")
        self._next_btn = self._tool_button(">", "Next page", self.next_page)
        header_layout.addWidget(self._prev_btn)
        header_layout.addWidget(self._page_label)
        header_layout.addWidget(self._next_btn)

        self._zoom_out_btn = self._tool_button("-", "Zoom out (Ctrl+-)", self.zoom_out)
        self._zoom_combo = QComboBox()
        self._zoom_combo.setFixedWidth(76)
        self._zoom_combo.addItems([f"{zoom}%" for zoom in self.ZOOM_LEVELS])
        self._zoom_combo.setCurrentIndex(self._zoom_index)
        self._zoom_combo.currentIndexChanged.connect(self._on_zoom_changed)
        self._zoom_in_btn = self._tool_button("+", "Zoom in (Ctrl++)", self.zoom_in)
        header_layout.addWidget(self._zoom_out_btn)
        header_layout.addWidget(self._zoom_combo)
        header_layout.addWidget(self._zoom_in_btn)

        self._fit_width_btn = self._tool_button("Width", "Fit page to viewer width", self.fit_width, 48)
        self._fit_page_btn = self._tool_button("Page", "Fit entire page", self.fit_page, 44)
        header_layout.addWidget(self._fit_width_btn)
        header_layout.addWidget(self._fit_page_btn)

        self._open_btn = self._tool_button("Open", "Open PDF in the system viewer", self.open_external, 44)
        header_layout.addWidget(self._open_btn)
        self._expand_btn = self._tool_button("Expand", "Open a large PDF viewer", self.expand_viewer, 58)
        self._expand_btn.setVisible(self._show_expand)
        header_layout.addWidget(self._expand_btn)
        self._close_btn = self._tool_button("X", "Close preview", self.close_preview)
        header_layout.addWidget(self._close_btn)
        layout.addWidget(header)

        self._scroll = PanScrollArea(self)
        self._scroll.setWidgetResizable(False)
        self._scroll.setAlignment(Qt.AlignCenter)
        self._scroll.setStyleSheet(
            "QScrollArea { background:#e5e7eb; border:1px solid #d1d5db; border-radius:4px; }"
        )
        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignCenter)
        self._image_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._scroll.setWidget(self._image_label)
        layout.addWidget(self._scroll, 1)

        self._placeholder = QLabel("Select a PDF attachment to preview")
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._placeholder.setWordWrap(True)
        self._placeholder.setStyleSheet(
            "color:#6b7280;background:#f8fafc;border:1px dashed #cbd5e1;border-radius:4px;padding:24px;"
        )
        layout.addWidget(self._placeholder, 1)

        QShortcut(QKeySequence("Ctrl++"), self, activated=self.zoom_in)
        QShortcut(QKeySequence("Ctrl+-"), self, activated=self.zoom_out)
        QShortcut(QKeySequence(Qt.Key_Left), self, activated=self.previous_page)
        QShortcut(QKeySequence(Qt.Key_Right), self, activated=self.next_page)
        self._set_loaded(False)

    @property
    def current_path(self):
        return self._current_path

    def load_pdf(self, file_path: str, page_index=0, zoom_index=None):
        if not HAS_FITZ:
            self.show_error("PDF preview requires PyMuPDF. Install the PyMuPDF package.")
            return False
        file_path = os.path.abspath(os.path.normpath(str(file_path or "").strip()))
        if not file_path:
            self.close_preview()
            return False
        if not os.path.isfile(file_path):
            self.show_error(f"PDF file not found:\n{file_path}")
            return False
        if not file_path.lower().endswith(".pdf"):
            self.show_error(f"Selected file is not a PDF:\n{file_path}")
            return False
        if file_path == self._current_path and self._doc:
            self._page_index = max(0, min(int(page_index), self._page_count - 1))
            self._render_current_page()
            return True

        self._close_doc()
        try:
            self._doc = fitz.open(file_path)
            if self._doc.needs_pass:
                raise ValueError("Password-protected PDFs are not supported by the embedded preview.")
            self._page_count = len(self._doc)
            if self._page_count < 1:
                raise ValueError("PDF contains no pages.")
            self._page_index = max(0, min(int(page_index), self._page_count - 1))
            if zoom_index is not None:
                self._zoom_index = max(0, min(int(zoom_index), len(self.ZOOM_LEVELS) - 1))
                self._zoom_combo.setCurrentIndex(self._zoom_index)
            self._current_path = file_path
            self._title_label.setText(os.path.basename(file_path))
            self._title_label.setToolTip(file_path)
            self._set_loaded(True)
            self.fit_width()
            return True
        except Exception as exc:
            self._close_doc()
            self.show_error(f"Failed to open PDF:\n{exc}")
            return False

    def show_error(self, message: str):
        self._close_doc()
        self._current_path = ""
        self._title_label.setText("PDF Preview")
        self._set_loaded(False)
        self._placeholder.setText(message)

    def close_preview(self):
        self._close_doc()
        self._current_path = ""
        self._title_label.setText("PDF Preview")
        self._title_label.setToolTip("")
        self._placeholder.setText("Select a PDF attachment to preview")
        self._set_loaded(False)

    def is_loaded(self):
        return self._doc is not None

    def open_external(self):
        if not self._current_path:
            return
        try:
            os.startfile(self._current_path)
        except Exception:
            pass

    def expand_viewer(self):
        if not self._current_path:
            return
        dialog = PdfViewerDialog(
            self._current_path,
            page_index=self._page_index,
            zoom_index=self._zoom_index,
            parent=self.window(),
        )
        self._expanded_dialog = dialog
        dialog.exec_()
        self._expanded_dialog = None

    def previous_page(self):
        if self._page_index > 0:
            self._page_index -= 1
            self._render_current_page()

    def next_page(self):
        if self._page_index < self._page_count - 1:
            self._page_index += 1
            self._render_current_page()

    def zoom_in(self):
        self._set_zoom_index(self._zoom_index + 1)

    def zoom_out(self):
        self._set_zoom_index(self._zoom_index - 1)

    def fit_width(self):
        self._render_current_page("width")

    def fit_page(self):
        self._render_current_page("page")

    def _set_zoom_index(self, index):
        index = max(0, min(int(index), len(self.ZOOM_LEVELS) - 1))
        if index == self._zoom_index:
            return
        self._zoom_index = index
        self._zoom_combo.setCurrentIndex(index)

    def _on_zoom_changed(self, index):
        self._zoom_index = max(0, int(index))
        self._render_current_page("zoom")

    def _close_doc(self):
        if self._doc:
            try:
                self._doc.close()
            except Exception:
                pass
        self._doc = None
        self._page_count = 0
        self._page_index = 0
        self._image_label.clear()
        self._page_label.setText("0 / 0")

    def _set_loaded(self, loaded):
        self._scroll.setVisible(loaded)
        self._placeholder.setVisible(not loaded)
        for control in (
            self._prev_btn,
            self._next_btn,
            self._page_label,
            self._zoom_out_btn,
            self._zoom_combo,
            self._zoom_in_btn,
            self._fit_width_btn,
            self._fit_page_btn,
            self._open_btn,
            self._expand_btn,
            self._close_btn,
        ):
            control.setEnabled(bool(loaded))

    def _render_current_page(self, mode="zoom"):
        if not self._doc or not (0 <= self._page_index < self._page_count):
            return
        try:
            self._render_mode = mode
            page = self._doc[self._page_index]
            viewport = self._scroll.viewport().size()
            width_zoom = max(0.1, (viewport.width() - 24) / float(page.rect.width))
            height_zoom = max(0.1, (viewport.height() - 24) / float(page.rect.height))
            if mode == "width":
                zoom = width_zoom
            elif mode == "page":
                zoom = min(width_zoom, height_zoom)
            else:
                zoom = self.ZOOM_LEVELS[self._zoom_index] / 100.0

            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            image = QImage(
                pix.samples,
                pix.width,
                pix.height,
                pix.stride,
                QImage.Format_RGB888,
            ).copy()
            pixmap = QPixmap.fromImage(image)
            self._image_label.setPixmap(pixmap)
            self._image_label.setFixedSize(pixmap.size())
            self._page_label.setText(f"{self._page_index + 1} / {self._page_count}")
            self._prev_btn.setEnabled(self._page_index > 0)
            self._next_btn.setEnabled(self._page_index < self._page_count - 1)
        except Exception as exc:
            self.show_error(f"Failed to render PDF page:\n{exc}")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._doc and self._render_mode in ("width", "page"):
            self._render_current_page(self._render_mode)

    def closeEvent(self, event):
        self.close_preview()
        super().closeEvent(event)
