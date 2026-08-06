from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QHBoxLayout, QListWidget, QTextEdit,
    QPushButton, QMessageBox, QGroupBox, QFileDialog, QComboBox, QFormLayout,
    QDialog, QLineEdit, QGraphicsDropShadowEffect, QTabWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QCheckBox, QMenu, QAction,
    QListWidgetItem, QScrollArea, QFrame, QSizePolicy, QSplitter,
    QTreeWidget, QTreeWidgetItem,
    QApplication, QAbstractItemView, QInputDialog,
)
from PyQt5.QtCore import (
    Qt, QPropertyAnimation, QEasingCurve, pyqtProperty, pyqtSignal, QTimer, QSize, QRect,
    QObject, QThread, QEvent,
)
from PyQt5.QtGui import (
    QColor, QPainter, QPen, QFont, QCursor, QBrush, QTransform,
)
import os
import sys
import re
import subprocess
import json
from datetime import datetime
from collections import Counter

from core.services.user_service import UserService
from core.repositories.user_repository import UserRepository
from core.services.commit_service import CommitService
from pages.part_dialog import PartDialog
from pages.dialogs.merge_dialog import MergeDialog
from core.services.merge_service import MergeService
from core.repositories.merge_repository import MergeRepository
from core.repositories.bom_repository import BomRepository
from core.services.part_file_service import PartFileService
from core.services.ui_permission import UIPermissionHelper
from core.services.role_service import RoleService
from core.services.project_service import ProjectService
from core.session_manager import SessionManager
from core.services.issue_service import IssueService
from pages.rich_text_image_editor import RichTextImageEditor, html_to_plain_text, looks_like_html
from pages.dialogs.cad_workspace_dialogs import (
    WorkspaceSelectionDialog,
    WorkspaceStagingDialog,
)
from core.services.cad_workspace_service import CadWorkspaceService

from utils import is_creo_file, ensure_dir_exists, safe_exists, safe_isfile, safe_startfile


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  STYLING CONSTANTS
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

_STATUS_STYLES = {
    "approved":   {"icon": "âœ…", "color": "#16a34a", "bg": "#dcfce7", "label": "Approved"},
    "validated":  {"icon": "ðŸ”µ", "color": "#2563eb", "bg": "#dbeafe", "label": "Validated"},
    "pending":    {"icon": "ðŸŸ¡", "color": "#ca8a04", "bg": "#fef9c3", "label": "Pending"},
    "integrated": {"icon": "âš™ï¸", "color": "#6b7280", "bg": "#f3f4f6", "label": "Integrated"},
    "reverted":   {"icon": "ðŸ”´", "color": "#dc2626", "bg": "#fee2e2", "label": "Reverted"},
    "pushed":     {"icon": "ðŸš€", "color": "#0284c7", "bg": "#e0f2fe", "label": "Pushed"},
    "released":   {"icon": "ðŸ", "color": "#7c3aed", "bg": "#ede9fe", "label": "Released"},
    "wip":        {"icon": "ðŸ”§", "color": "#ea580c", "bg": "#ffedd5", "label": "WIP"},
}
_DEFAULT_STATUS = {"icon": "ðŸ“‹", "color": "#6b7280", "bg": "#f3f4f6", "label": "Unknown"}


# Override fragile emoji/mojibake glyphs with ASCII labels for consistent PyQt rendering.
_STATUS_STYLES = {
    "approved":   {"icon": "[OK]", "color": "#16a34a", "bg": "#dcfce7", "label": "Approved"},
    "validated":  {"icon": "[VAL]", "color": "#2563eb", "bg": "#dbeafe", "label": "Validated"},
    "pending":    {"icon": "[PEND]", "color": "#ca8a04", "bg": "#fef9c3", "label": "Pending"},
    "integrated": {"icon": "[INT]", "color": "#6b7280", "bg": "#f3f4f6", "label": "Integrated"},
    "reverted":   {"icon": "[REV]", "color": "#dc2626", "bg": "#fee2e2", "label": "Reverted"},
    "pushed":     {"icon": "[PUSH]", "color": "#0284c7", "bg": "#e0f2fe", "label": "Pushed"},
    "released":   {"icon": "[REL]", "color": "#7c3aed", "bg": "#ede9fe", "label": "Released"},
    "wip":        {"icon": "[WIP]", "color": "#ea580c", "bg": "#ffedd5", "label": "WIP"},
}
_DEFAULT_STATUS = {"icon": "[?]", "color": "#6b7280", "bg": "#f3f4f6", "label": "Unknown"}


def _status_style(status: str) -> dict:
    return _STATUS_STYLES.get((status or "").strip().lower(), _DEFAULT_STATUS)


def _file_icon(filename: str) -> str:
    fn = (filename or "").lower()
    if ".prt" in fn:
        return "ðŸ”©"
    if ".asm" in fn:
        return "ðŸ—ï¸"
    if ".drw" in fn:
        return "ðŸ“"
    if ".step" in fn or ".stp" in fn:
        return "ðŸ§Š"
    if ".pdf" in fn:
        return "ðŸ“„"
    return "ðŸ“"


def _file_icon(filename: str) -> str:
    fn = (filename or "").lower()
    if ".prt" in fn:
        return "[PRT]"
    if ".asm" in fn:
        return "[ASM]"
    if ".drw" in fn:
        return "[DRW]"
    if ".step" in fn or ".stp" in fn:
        return "[STEP]"
    if ".pdf" in fn:
        return "[PDF]"
    return "[FILE]"


def _relative_time(ts: str) -> str:
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        try:
            dt = datetime.strptime(str(ts)[:19], "%Y-%m-%d %H:%M:%S")
        except Exception:
            return ""
    try:
        delta = datetime.now() - dt.replace(tzinfo=None)
    except Exception:
        delta = datetime.now() - dt
    secs = int(delta.total_seconds())
    if secs < 0:
        return "future"
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    days = secs // 86400
    if days == 1:
        return "yesterday"
    if days < 7:
        return f"{days}d ago"
    if days < 30:
        return f"{days // 7}w ago"
    if days < 365:
        return f"{days // 30}mo ago"
    return f"{days // 365}y ago"



class DropListWidget(QListWidget):
    def __init__(self, parent=None, callback=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.callback = callback
        self._drag_active = False
        self._border_alpha = 0
        self._placeholder_text = "WORKING SET EMPTY\nAccepted native content: PRT / ASM / DRW"

        # Border animation
        self.border_anim = QPropertyAnimation(self, b"border_alpha")
        self.border_anim.setDuration(250)
        self.border_anim.setEasingCurve(QEasingCurve.InOutQuad)

    def get_border_alpha(self):
        return self._border_alpha

    def set_border_alpha(self, value):
        self._border_alpha = value
        self.viewport().update()  # repaint the viewport

    border_alpha = pyqtProperty(int, fget=get_border_alpha, fset=set_border_alpha)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._drag_active = True
            self.animate_border(True)
            self.viewport().update()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self._drag_active = False
        self.animate_border(False)
        self.viewport().update()

    def dropEvent(self, event):
        self._drag_active = False
        self.animate_border(False)
        self.viewport().update()
        if event.mimeData().hasUrls():
            files = [url.toLocalFile() for url in event.mimeData().urls() if os.path.isfile(url.toLocalFile())]
            if self.callback:
                self.callback(files)
            event.acceptProposedAction()

    def animate_border(self, enable: bool):
        self.border_anim.stop()
        start = self._border_alpha
        end = 255 if enable else 0
        self.border_anim.setStartValue(start)
        self.border_anim.setEndValue(end)
        self.border_anim.start()

    def paintEvent(self, event):
        super().paintEvent(event)
        rect = self.viewport().rect().adjusted(1, 1, -2, -2)
        painter = QPainter(self.viewport())

        # Draw placeholder if empty
        if self.count() == 0 and not self._drag_active:
            painter.setPen(QColor(150, 150, 150))
            font = QFont()
            font.setPointSize(10)
            font.setBold(False)
            painter.setFont(font)
            painter.drawText(rect, Qt.AlignCenter, self._placeholder_text)

        # Semi-transparent overlay
        if self._drag_active:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(45, 137, 239, 80))
            painter.drawRect(rect)

            painter.setPen(QColor(45, 137, 239, 200))
            painter.setFont(self.font())
            painter.drawText(rect, Qt.AlignCenter, "ADD TO WORKING SET")

        # Animated border
        if self._border_alpha > 0:
            pen = QPen(QColor(45, 137, 239, int(self._border_alpha)))
            pen.setWidth(3)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(rect)


# ═══════════════════════════════════════════════════════════════════════════
#  STAT CARD WIDGET
# ═══════════════════════════════════════════════════════════════════════════

class AffectedBomDropCard(QFrame):
    filesDropped = pyqtSignal(int, list)
    docRemoveRequested = pyqtSignal(int, int)

    def __init__(self, part_info: dict, parent=None):
        super().__init__(parent)
        self.part_info = dict(part_info or {})
        self.part_id = int(self.part_info.get("id") or 0)
        self.setAcceptDrops(True)
        self.setMinimumHeight(76)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setStyleSheet("""
            AffectedBomDropCard {
                background: #ffffff;
                border: 1px solid #aeb8c2;
                border-radius: 0;
            }
            AffectedBomDropCard:hover {
                border-color: #55768f;
                background: #f1f4f6;
            }
            QLabel {
                background: transparent;
                border: none;
            }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(2)

        item_number = str(self.part_info.get("part_number") or "No Number").strip()
        item_name = str(self.part_info.get("name") or "Item").strip()
        aes_number = str(self.part_info.get("aes_number") or "").strip()
        identity = f"{item_number} — {item_name}"
        if aes_number:
            identity += f"  |  AES {aes_number}"
        title = QLabel(identity)
        title.setStyleSheet("font-size: 11px; font-weight: 700; color: #111827;")
        title.setFixedHeight(18)
        title.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout.addWidget(title, 0)

        meta = QLabel(
            f"{self.part_info.get('type') or ''}"
            f"  |  {self.part_info.get('filename') or ''}"
            f"  |  {self.part_info.get('drawing') or ''}"
        )
        meta.setStyleSheet("font-size: 9px; color: #64748b;")
        meta.setFixedHeight(15)
        meta.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout.addWidget(meta, 0)

        hint = QLabel("Drop PDF, STEP, or validation docs here to stage with this commit")
        hint.setStyleSheet("font-size: 9px; color: #2563eb;")
        hint.setFixedHeight(15)
        hint.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout.addWidget(hint, 0)

        self.preview = QListWidget()
        self.preview.setContextMenuPolicy(Qt.CustomContextMenu)
        self.preview.customContextMenuRequested.connect(self._show_doc_context_menu)
        self.preview.setMaximumHeight(110)
        self.preview.setMinimumHeight(42)
        self.preview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.preview.setStyleSheet("""
            QListWidget {
                border: 1px solid #e5e7eb;
                border-radius: 0;
                background: #f8fafc;
                font-size: 9px;
                color: #374151;
            }
            QListWidget::item { padding: 3px 5px; }
            QListWidget::item:selected { background: #dbeafe; color: #1e3a8a; }
        """)
        layout.addWidget(self.preview, 0)

    def set_attachments(self, attachments: list):
        self.preview.clear()
        rows = list(attachments or [])
        if not rows:
            item = QListWidgetItem("No staged documents")
            item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
            self.preview.addItem(item)
            self._sync_preview_height()
            return
        for index, item in enumerate(rows):
            name = os.path.basename(item.get("source_path") or item.get("filename") or "")
            ftype = (item.get("file_type") or "").upper()
            role = (item.get("file_role") or "").replace("_", " ")
            rev = (item.get("revision") or "").strip()
            note = (item.get("note") or "").strip()
            suffix = []
            if role:
                suffix.append(role)
            if rev:
                suffix.append(f"rev {rev}")
            if note:
                suffix.append(note)
            label = f"{ftype}: {name}" if ftype else name
            if suffix:
                label = f"{label} ({', '.join(suffix)})"
            row = QListWidgetItem(label)
            row.setToolTip(item.get("source_path") or name)
            row.setData(Qt.UserRole, index)
            self.preview.addItem(row)
        self._sync_preview_height()

    def _sync_preview_height(self):
        rows = max(1, self.preview.count())
        row_h = 22
        frame_h = 10
        self.preview.setFixedHeight(max(42, min(110, frame_h + rows * row_h)))
        self.adjustSize()

    def _show_doc_context_menu(self, pos):
        item = self.preview.itemAt(pos)
        if not item:
            return
        index = item.data(Qt.UserRole)
        if index is None:
            return
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background: #ffffff;
                color: #111827;
                border: 1px solid #cbd5e1;
                border-radius: 0;
                padding: 2px 0;
            }
            QMenu::item {
                background: transparent;
                color: #111827;
                padding: 4px 16px;
                font-size: 9px;
            }
            QMenu::item:selected {
                background: #dbeafe;
                color: #1e3a8a;
            }
            QMenu::item:disabled {
                color: #9ca3af;
            }
        """)
        remove = menu.addAction("Delete staged document")
        chosen = menu.exec_(self.preview.viewport().mapToGlobal(pos))
        if chosen == remove:
            self.docRemoveRequested.emit(self.part_id, int(index))

    def _accepted_paths(self, event):
        paths = []
        if not event.mimeData().hasUrls():
            return paths
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            ext = os.path.splitext(path or "")[1].lower()
            if path and os.path.isfile(path) and ext not in (".prt", ".asm", ".drw"):
                paths.append(path)
        return paths

    def dragEnterEvent(self, event):
        if self._accepted_paths(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self._accepted_paths(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        paths = self._accepted_paths(event)
        if paths:
            self.filesDropped.emit(self.part_id, paths)
            event.acceptProposedAction()
        else:
            event.ignore()


class _PageProcessingOverlay(QWidget):
    """Full-page busy veil used while commit/merge operations run."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._angle = 0
        self._message = "Processing..."
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.setCursor(Qt.WaitCursor)
        self._timer = QTimer(self)
        self._timer.setInterval(45)
        self._timer.timeout.connect(self._tick)
        self.hide()

    def show_overlay(self, message: str):
        self._message = message or "Processing..."
        if self.parent():
            self.setGeometry(self.parent().rect())
        self.raise_()
        self.show()
        self._timer.start()
        self.update()

    def hide_overlay(self):
        self._timer.stop()
        self.hide()

    def _tick(self):
        self._angle = (self._angle + 18) % 360
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # "Voile" overlay: soft translucent layer above the whole page.
        painter.fillRect(self.rect(), QColor(248, 250, 252, 190))

        card_w = min(380, max(280, self.width() - 48))
        card_h = 156
        card_x = (self.width() - card_w) // 2
        card_y = (self.height() - card_h) // 2
        card = QRect(card_x, card_y, card_w, card_h)

        painter.setPen(QPen(QColor("#bfdbfe"), 1))
        painter.setBrush(QBrush(QColor(255, 255, 255, 238)))
        painter.drawRoundedRect(card, 10, 10)

        spinner_size = 50
        spinner_x = card_x + (card_w - spinner_size) // 2
        spinner_y = card_y + 30
        spinner_rect = QRect(spinner_x, spinner_y, spinner_size, spinner_size)
        arc_rect = spinner_rect.adjusted(5, 5, -5, -5)
        painter.setPen(QPen(QColor("#dbeafe"), 5, Qt.SolidLine, Qt.RoundCap))
        painter.drawArc(arc_rect, 0, 360 * 16)
        painter.setPen(QPen(QColor("#2563eb"), 5, Qt.SolidLine, Qt.RoundCap))
        painter.drawArc(arc_rect, int(self._angle * 16), int(110 * 16))

        painter.setPen(QColor("#111827"))
        title_font = QFont()
        title_font.setPointSize(11)
        title_font.setBold(True)
        painter.setFont(title_font)
        text_rect = QRect(card_x + 20, spinner_y + spinner_size + 18, card_w - 40, 28)
        painter.drawText(text_rect, Qt.AlignCenter, self._message)

        painter.setPen(QColor("#64748b"))
        hint_font = QFont()
        hint_font.setPointSize(9)
        painter.setFont(hint_font)
        hint_rect = QRect(card_x + 20, text_rect.bottom() + 4, card_w - 40, 24)
        painter.drawText(hint_rect, Qt.AlignCenter, "Please wait. The commit page is locked while this finishes.")

    def mousePressEvent(self, event):
        event.accept()

    def mouseReleaseEvent(self, event):
        event.accept()

    def keyPressEvent(self, event):
        event.accept()


class _ProcessingWorker(QObject):
    finished = pyqtSignal(object)
    failed = pyqtSignal(object)

    def __init__(self, fn):
        super().__init__()
        self._fn = fn

    def run(self):
        try:
            self.finished.emit(self._fn())
        except Exception as exc:
            self.failed.emit(exc)


class _StatCard(QFrame):
    """Compact enterprise status cell used in the workspace summary strip."""

    def __init__(self, icon: str, value: str, label: str, accent: str, parent=None):
        super().__init__(parent)
        self.setFixedHeight(40)
        self.setMinimumWidth(96)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setStyleSheet(f"""
            _StatCard {{
                background: #ffffff;
                border: 1px solid #b8bec7;
                border-top: 2px solid {accent};
                border-radius: 1px;
            }}
        """)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 3, 8, 3)
        lay.setSpacing(7)

        if icon:
            ic = QLabel(icon)
            ic.setStyleSheet(
                "font-size: 9px; font-weight: 700; color: #5b6573; "
                "background: transparent; border: none;"
            )
            lay.addWidget(ic)

        text_lay = QVBoxLayout()
        text_lay.setSpacing(0)
        self._val = QLabel(str(value))
        self._val.setStyleSheet(
            f"font-size: 13px; font-weight: 700; color: {accent}; "
            "background: transparent; border: none;"
        )
        text_lay.addWidget(self._val)
        self._lbl = QLabel(str(label).upper())
        self._lbl.setStyleSheet(
            "font-size: 8px; color: #5f6976; letter-spacing: 0.4px; "
            "background: transparent; border: none;"
        )
        text_lay.addWidget(self._lbl)
        lay.addLayout(text_lay)
        lay.addStretch()

    def set_value(self, v):
        self._val.setText(str(v))


# ═══════════════════════════════════════════════════════════════════════════
#  HISTORY TABLE
# ═══════════════════════════════════════════════════════════════════════════

class _HistoryTable(QTableWidget):
    """Dense audit table with restrained state and STEP indicators."""

    restore_requested = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(8)
        self.setHorizontalHeaderLabels([
            "", "Action", "State", "Object / Commit", "Designer", "Modified", "STEP", "Description",
        ])
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setAlternatingRowColors(True)
        self.verticalHeader().setVisible(False)
        self.setShowGrid(False)
        self.setWordWrap(True)
        hh = self.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Fixed)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(6, QHeaderView.Fixed)
        hh.setSectionResizeMode(7, QHeaderView.Stretch)
        self.setColumnWidth(0, 8)
        self.setColumnWidth(6, 50)
        self.setStyleSheet("""
            QTableWidget {
                border: 1px solid #aeb5bf; border-radius: 0;
                background: #ffffff; alternate-background-color: #f5f6f7;
                gridline-color: #e0e3e7;
            }
            QTableWidget::item { padding: 2px 5px; border-bottom: 1px solid #e0e3e7; }
            QTableWidget::item:selected { background: #d9e7f5; color: #17212b; }
            QHeaderView::section {
                background: #e2e5e9; border: none; border-right: 1px solid #c6cbd2;
                border-bottom: 1px solid #9ca3ad;
                padding: 4px 5px; font-weight: 700; font-size: 9px; color: #2f3945;
            }
        """)

    def populate(self, rows: list):
        self.setRowCount(0)
        self.setRowCount(len(rows))
        for i, c in enumerate(rows):
            if i and i % 20 == 0:
                QApplication.processEvents()
            sty = _status_style(c.get("status", ""))

            # col 0 — colour dot
            dot_item = QTableWidgetItem("")
            dot_item.setData(Qt.UserRole, dict(c))
            dot_item.setBackground(QBrush(QColor(sty["color"])))
            self.setItem(i, 0, dot_item)

            if c.get("is_commit_group"):
                action = QPushButton("Restore")
                action.setCursor(Qt.PointingHandCursor)
                action.setEnabled(bool(c.get("can_restore")))
                action.setToolTip(
                    "Restore this approved commit group"
                    if c.get("can_restore")
                    else "Restore is available only for commits pushed to master"
                )
                action.setStyleSheet("""
                    QPushButton {
                        background: #f5f6f7; border: 1px solid #aeb5bf; border-radius: 1px;
                        padding: 2px 7px; font-size: 9px; color: #27313c;
                    }
                    QPushButton:hover:enabled { background: #e4edf6; border-color: #738ba3; }
                    QPushButton:disabled { color: #9299a2; background: #eceef0; }
                """)
                action.clicked.connect(lambda _checked=False, d=dict(c): self.restore_requested.emit(d))
                self.setCellWidget(i, 1, action)
            else:
                self.setItem(i, 1, QTableWidgetItem(""))

            # col 1 — status badge
            badge = QLabel(f' {sty["label"].upper()} ')
            badge.setAlignment(Qt.AlignCenter)
            badge.setStyleSheet(f"""
                background: {sty['bg']}; color: {sty['color']};
                border: 1px solid {sty['color']}70; border-radius: 1px;
                padding: 1px 5px; font-size: 8px; font-weight: 700;
            """)
            container = QWidget()
            cl = QHBoxLayout(container)
            cl.setContentsMargins(2, 2, 2, 2)
            cl.addWidget(badge)
            cl.addStretch()
            self.setCellWidget(i, 2, container)

            # col 2 — file
            fname = str(c.get("filename", "") or "")
            ficon = _file_icon(fname)
            fi = QTableWidgetItem(f"{ficon} {fname}")
            fi.setToolTip(fname)
            fnt = fi.font()
            fnt.setBold(True)
            fi.setFont(fnt)
            self.setItem(i, 3, fi)

            # col 3 — designer
            self.setItem(i, 4, QTableWidgetItem(str(c.get("designed_by", "") or "")))

            # col 4 — date + relative
            raw_ts = str(c.get("date", "") or "")
            rel = _relative_time(raw_ts)
            w = QWidget()
            wl = QVBoxLayout(w)
            wl.setContentsMargins(2, 2, 2, 2)
            wl.setSpacing(0)
            d1 = QLabel(raw_ts[:19] if len(raw_ts) >= 19 else raw_ts)
            d1.setStyleSheet("font-size: 10px; color: #374151; background: transparent; border: none;")
            wl.addWidget(d1)
            if rel:
                d2 = QLabel(rel)
                d2.setStyleSheet("font-size: 8px; color: #9ca3af; background: transparent; border: none;")
                wl.addWidget(d2)
            self.setCellWidget(i, 5, w)

            # col 5 — STEP indicator
            step_status = str(c.get("step_diff_status", "") or "").strip().upper()
            step_txt = ""
            if step_status == "BASELINE":
                step_txt = "BASE"
            elif step_status == "COMPARED":
                step_txt = "DIFF"
            elif step_status:
                step_txt = "YES"
            si = QTableWidgetItem(step_txt)
            si.setTextAlignment(Qt.AlignCenter)
            si.setToolTip(f"STEP: {step_status}" if step_status else "No STEP")
            self.setItem(i, 6, si)

            # col 6 — message
            msg = html_to_plain_text(str(c.get("message", "") or ""))
            mi = QTableWidgetItem(msg)
            mi.setToolTip(msg)
            mi.setForeground(QBrush(QColor("#4b5563")))
            self.setItem(i, 7, mi)

            self.setRowHeight(i, 34)

    def get_data(self, row: int) -> dict:
        item = self.item(row, 0)
        return (item.data(Qt.UserRole) or {}) if item else {}


# ═══════════════════════════════════════════════════════════════════════════
#  PENDING COMMIT CARD
# ═══════════════════════════════════════════════════════════════════════════

class _PendingCard(QFrame):
    """Compact routing-queue record for a logical commit group."""
    clicked = pyqtSignal(object)
    view_requested = pyqtSignal(object)
    browse_requested = pyqtSignal(object)

    def __init__(self, group: dict, parent=None):
        super().__init__(parent)
        self.group = group
        self._selected = False
        self.setCursor(Qt.PointingHandCursor)
        self.setFrameShape(QFrame.StyledPanel)
        self.setMinimumHeight(62)

        sty = _status_style(group.get("status", ""))
        self._accent = sty["color"]
        self._refresh_style()

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(8)

        # Left: info
        info = QVBoxLayout()
        info.setSpacing(2)

        # Title row
        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        title_lbl = QLabel(group.get("title", "Untitled"))
        title_lbl.setStyleSheet(
            "font-weight: 700; font-size: 10px; color: #202a35; "
            "background: transparent; border: none;"
        )
        title_row.addWidget(title_lbl)

        badge = QLabel(f' {sty["label"].upper()} ')
        badge.setStyleSheet(f"""
            background: {sty['bg']}; color: {sty['color']};
            border: 1px solid {sty['color']}70; border-radius: 1px;
            padding: 1px 5px; font-size: 8px; font-weight: 700;
        """)
        title_row.addWidget(badge)
        title_row.addStretch()
        info.addLayout(title_row)

        # Meta
        designer = group.get("username", "Unknown")
        num_files = len(group.get("parts", []))
        date_str = str(group.get("date", "") or "")
        rel = _relative_time(date_str)
        meta_parts = [str(designer), f"{num_files} file{'s' if num_files != 1 else ''}"]
        if rel:
            meta_parts.append(rel)
        meta = QLabel("  |  ".join(meta_parts))
        meta.setStyleSheet("font-size: 9px; color: #65707d; background: transparent; border: none;")
        info.addWidget(meta)

        # File chips (first 3)
        if group.get("parts"):
            chips_text = ", ".join(group["parts"][:3])
            if num_files > 3:
                chips_text += f"  +{num_files - 3} more"
            files_lbl = QLabel(f"  {chips_text}")
            files_lbl.setStyleSheet("font-size: 8px; color: #77818d; background: transparent; border: none;")
            files_lbl.setWordWrap(True)
            info.addWidget(files_lbl)

        lay.addLayout(info, 1)

        # Right: action buttons
        btn_col = QVBoxLayout()
        btn_col.setSpacing(4)

        view_btn = QPushButton("Open")
        view_btn.setFixedSize(58, 23)
        view_btn.setCursor(Qt.PointingHandCursor)
        view_btn.setStyleSheet("""
            QPushButton {
                background: #e4edf6; color: #244b70; border: 1px solid #9aabbc;
                border-radius: 1px; font-size: 9px; font-weight: 600;
            }
            QPushButton:hover { background: #d4e2ef; }
        """)
        view_btn.clicked.connect(lambda: self.view_requested.emit(self.group))
        btn_col.addWidget(view_btn)

        browse_btn = QPushButton("Folder")
        browse_btn.setFixedSize(58, 23)
        browse_btn.setCursor(Qt.PointingHandCursor)
        browse_btn.setStyleSheet("""
            QPushButton {
                background: #f2f3f4; color: #37414c; border: 1px solid #aeb5bf;
                border-radius: 1px; font-size: 9px; font-weight: 600;
            }
            QPushButton:hover { background: #e4e7ea; }
        """)
        browse_btn.clicked.connect(lambda: self.browse_requested.emit(self.group))
        btn_col.addWidget(browse_btn)

        lay.addLayout(btn_col)

    def _refresh_style(self):
        bg = "#e8f0f7" if self._selected else "#ffffff"
        border_color = self._accent if self._selected else "#b8bec7"
        border_width = "1px"
        self.setStyleSheet(f"""
            _PendingCard {{
                background: {bg};
                border: {border_width} solid {border_color};
                border-left: 3px solid {self._accent};
                border-radius: 1px;
            }}
        """)

    def set_selected(self, sel: bool):
        self._selected = sel
        self._refresh_style()

    def mousePressEvent(self, event):
        self.clicked.emit(self.group)
        super().mousePressEvent(event)


# ═══════════════════════════════════════════════════════════════════════════
#  COMMIT PAGE
# ═══════════════════════════════════════════════════════════════════════════

class CommitPage(QWidget):
    def __init__(self, bom_service):
        super().__init__()
        self.setFont(QFont("Segoe UI", 8))
        self.commit_service = CommitService()
        self.bom_service = bom_service
        self.bom_repo = BomRepository()
        self.part_file_service = PartFileService()
        self.project_service = ProjectService()
        self.issue_service = IssueService()
        self.cad_workspace_service = CadWorkspaceService()

        self.merge_repo = MergeRepository()
        self.uncommitted_parts = []
        self.perm = UIPermissionHelper()
        self.role = RoleService()
        self.session = SessionManager()

        self.working_dir = None
        self.commits_dir = None
        self.pr_dir = None
        self.merge_service = None
        self._processing_action = False
        self._processing_thread = None
        self._processing_worker = None
        self._pending_engineering_attachments = {}
        self._affected_dialog_cards = {}

        if self.session.project_id:
            self.working_dir = self.get_working_dir()
            if self.working_dir:
                self.commits_dir = self.working_dir + "/commits"
                self.pr_dir = self.working_dir + "/pull resuests"
                self.merge_service = MergeService(self.working_dir, self.commits_dir, self.pr_dir)

        # --- User & role info ---
        self.user_service = UserService(UserRepository())
        self.usernames = self.user_service.list_usernames()
        self.username = self.user_service.get_user_by_id(self.session.user_id).username

        try:
            roles = self.role.get_role_for_user(self.session.user_id) or []
        except Exception:
            roles = []

        role_level = {"designer": 1, "checker": 2, "master": 3, "admin": 4}
        role_names = [
            str(r.get("name") or "").strip().lower()
            for r in roles if isinstance(r, dict)
        ]
        max_level = max((role_level.get(rn, 0) for rn in role_names), default=0)
        self.is_designer = (max_level == role_level["designer"])

        self._build_ui()

        project_loaded = bool(self.session.project_id)
        if project_loaded:
            self.check_working_dir_existance()

        self.commit_btn.setEnabled(project_loaded and self.perm.can("commit"))
        self.add_file_btn.setEnabled(project_loaded and self.perm.can("commit"))
        self.workspace_file_btn.setEnabled(project_loaded and self.perm.can("commit"))
        self.push_dev_btn.setEnabled(project_loaded and self.perm.can("merge"))
        self.merge_master_btn.setEnabled(project_loaded and self.perm.can("merge"))
        self.snapshot_btn.setEnabled(project_loaded)
        self.revert_btn.setEnabled(project_loaded)

        if self.session.project_id:
            QTimer.singleShot(0, self.load_commit_history)
            QTimer.singleShot(75, self.load_pending_commits)

    # ═══════════════════════════════════════════════════════════════════
    #  UI BUILD
    # ═══════════════════════════════════════════════════════════════════

    def _build_ui(self):
        self.setObjectName("commitWorkspace")
        self.setStyleSheet("""
            QWidget#commitWorkspace {
                background: #e7e9ec;
                color: #27313c;
            }
            QFrame#workspaceHeader {
                background: #f6f7f8;
                border: 1px solid #aeb5bf;
                border-bottom: 2px solid #5e7184;
            }
            QFrame#commandBar {
                background: #dfe3e7;
                border: 1px solid #a7aeb7;
            }
            QLabel#commandGroupTitle {
                color: #4f5a66;
                font-size: 8px;
                font-weight: 700;
                letter-spacing: 0.5px;
            }
            QFrame#commandSeparator {
                color: #a7aeb7;
                background: #a7aeb7;
            }
            QGroupBox#enterprisePanel {
                background: #f4f5f6;
                border: 1px solid #aeb5bf;
                border-radius: 1px;
                margin-top: 20px;
                padding-top: 5px;
                font-weight: 700;
            }
            QGroupBox#enterprisePanel::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 0;
                top: 0;
                min-height: 18px;
                padding: 4px 9px;
                color: #26313c;
                background: #d9dde2;
                border-right: 1px solid #aeb5bf;
                border-bottom: 1px solid #aeb5bf;
                font-size: 9px;
                font-weight: 700;
                letter-spacing: 0.4px;
            }
            QPushButton#primary {
                min-height: 25px;
                padding: 2px 11px;
                background: #315f87;
                color: #ffffff;
                border: 1px solid #244866;
                border-radius: 1px;
                font-size: 9px;
                font-weight: 700;
            }
            QPushButton#primary:hover { background: #264f72; }
            QPushButton#primary:disabled {
                background: #aeb5bc; color: #eceeef; border-color: #9da4ac;
            }
            QPushButton#neutral {
                min-height: 25px;
                padding: 2px 9px;
                background: #f4f5f6;
                color: #2f3944;
                border: 1px solid #9fa7b1;
                border-radius: 1px;
                font-size: 9px;
                font-weight: 600;
            }
            QPushButton#neutral:hover { background: #e2e8ee; border-color: #75879a; }
            QPushButton#neutral:disabled { color: #969da5; background: #e5e7e9; }
            QPushButton#danger {
                min-height: 25px;
                padding: 2px 9px;
                background: #f4f5f6;
                color: #9b2f2f;
                border: 1px solid #b88787;
                border-radius: 1px;
                font-size: 9px;
                font-weight: 700;
            }
            QPushButton#danger:hover { background: #f3dddd; }
            QLineEdit, QComboBox, QTextEdit, QListWidget {
                background: #ffffff;
                color: #202a35;
                border: 1px solid #aeb5bf;
                border-radius: 1px;
                selection-background-color: #cbdceb;
                selection-color: #17212b;
                font-size: 9px;
            }
            QLineEdit, QComboBox { min-height: 23px; padding: 1px 5px; }
            QLineEdit:focus, QComboBox:focus, QTextEdit:focus, QListWidget:focus {
                border: 1px solid #4f7598;
            }
            QCheckBox { color: #394450; font-size: 9px; spacing: 5px; }
            QSplitter::handle { background: #c2c7cd; }
        """)
        root = QVBoxLayout(self)
        # The commit history panel is a floating overlay at the bottom of this page.
        # Keep normal commit controls out from under the collapsed history header.
        root.setContentsMargins(6, 6, 6, 54)
        root.setSpacing(5)

        header = QFrame()
        header.setObjectName("workspaceHeader")
        header_lay = QHBoxLayout(header)
        header_lay.setContentsMargins(10, 6, 10, 6)
        header_lay.setSpacing(10)
        header_text = QVBoxLayout()
        header_text.setSpacing(0)
        workspace_title = QLabel("CHECK-IN AND PROMOTION")
        workspace_title.setStyleSheet(
            "font-size: 12px; font-weight: 700; color: #253341; "
            "letter-spacing: 0.6px; background: transparent; border: none;"
        )
        workspace_subtitle = QLabel(
            "Prepare the working set, record change intent, and route controlled content."
        )
        workspace_subtitle.setStyleSheet(
            "font-size: 8px; color: #626d78; background: transparent; border: none;"
        )
        header_text.addWidget(workspace_title)
        header_text.addWidget(workspace_subtitle)
        header_lay.addLayout(header_text)
        header_lay.addStretch()
        session_label = QLabel(f"ACTIVE USER  {self.username}")
        session_label.setStyleSheet(
            "font-size: 8px; font-weight: 700; color: #52606d; "
            "background: #e5e8eb; border: 1px solid #b8bec7; padding: 3px 7px;"
        )
        header_lay.addWidget(session_label)
        root.addWidget(header)

        # ── Stats dashboard ──────────────────────────────────────────
        stats_row = QHBoxLayout()
        stats_row.setSpacing(4)
        self._stat_staged = _StatCard("", "0", "Working Set", "#315f87")
        self._stat_pending = _StatCard("", "0", "Pending Review", "#9a6a20")
        self._stat_validated = _StatCard("", "0", "Validated", "#39704c")
        self._stat_total = _StatCard("", "0", "Recorded", "#536171")
        self._stat_step = _StatCard("", "0", "STEP Compared", "#665287")
        for w in (self._stat_staged, self._stat_pending, self._stat_validated,
                  self._stat_total, self._stat_step):
            stats_row.addWidget(w)
        root.addLayout(stats_row)

        # ── Main splitter (top: staging + pending | bottom: history) ─
        # Grouped command bar: object actions are placed next to their workflow
        # peers, following the compact ribbon convention used by commercial PDM.
        command_bar = QFrame()
        command_bar.setObjectName("commandBar")
        command_lay = QHBoxLayout(command_bar)
        command_lay.setContentsMargins(7, 4, 7, 4)
        command_lay.setSpacing(7)

        def command_group(title, widgets):
            group_widget = QWidget()
            group_lay = QVBoxLayout(group_widget)
            group_lay.setContentsMargins(0, 0, 0, 0)
            group_lay.setSpacing(2)
            button_lay = QHBoxLayout()
            button_lay.setContentsMargins(0, 0, 0, 0)
            button_lay.setSpacing(3)
            for widget in widgets:
                button_lay.addWidget(widget)
            group_lay.addLayout(button_lay)
            caption = QLabel(str(title).upper())
            caption.setObjectName("commandGroupTitle")
            caption.setAlignment(Qt.AlignCenter)
            group_lay.addWidget(caption)
            command_lay.addWidget(group_widget)

        def command_separator():
            separator = QFrame()
            separator.setObjectName("commandSeparator")
            separator.setFrameShape(QFrame.VLine)
            separator.setFrameShadow(QFrame.Plain)
            separator.setFixedWidth(1)
            separator.setMinimumHeight(38)
            command_lay.addWidget(separator)

        self.add_file_btn = QPushButton("Add Files")
        self.add_file_btn.setObjectName("neutral")
        self.add_file_btn.setCursor(Qt.PointingHandCursor)
        self.add_file_btn.setToolTip("Add controlled files to the current working set")
        self.add_file_btn.clicked.connect(self.add_files)
        self.workspace_file_btn = QPushButton("From Workspace")
        self.workspace_file_btn.setObjectName("neutral")
        self.workspace_file_btn.setCursor(Qt.PointingHandCursor)
        self.workspace_file_btn.setToolTip(
            "Review modified CAD in a named local workspace and stage selected files"
        )
        self.workspace_file_btn.clicked.connect(self.add_files_from_workspace)
        self.remove_part_btn = QPushButton("Remove")
        self.remove_part_btn.setObjectName("neutral")
        self.remove_part_btn.setCursor(Qt.PointingHandCursor)
        self.remove_part_btn.setToolTip("Remove the selected entry from the working set")
        self.remove_part_btn.clicked.connect(self.remove_part)
        clear_all_btn = QPushButton("Clear")
        clear_all_btn.setObjectName("neutral")
        clear_all_btn.setCursor(Qt.PointingHandCursor)
        clear_all_btn.setToolTip("Clear the current working set")
        clear_all_btn.clicked.connect(self._clear_staging)
        command_group(
            "Working Set",
            (self.add_file_btn, self.workspace_file_btn, self.remove_part_btn, clear_all_btn),
        )
        command_separator()

        self.attach_affected_btn = QPushButton("Associate Outputs")
        self.attach_affected_btn.setObjectName("neutral")
        self.attach_affected_btn.setCursor(Qt.PointingHandCursor)
        self.attach_affected_btn.setToolTip(
            "Associate engineering documents with affected BOM Items"
        )
        self.attach_affected_btn.clicked.connect(self._open_affected_bom_attachment_dialog)
        self.attach_affected_btn.setEnabled(False)
        self.step_compare_checkbox = QCheckBox("Run STEP comparison")
        self.step_compare_checkbox.setChecked(False)
        self.step_compare_checkbox.setEnabled(False)
        self.step_compare_checkbox.setToolTip(
            "Compare the STEP attachment associated with each affected Item"
        )
        self.attached_step_compare_checkbox = self.step_compare_checkbox
        command_group("Engineering Content", (self.attach_affected_btn, self.step_compare_checkbox))
        command_separator()

        self.commit_btn = QPushButton("Check In")
        self.commit_btn.setObjectName("primary")
        self.commit_btn.setCursor(Qt.PointingHandCursor)
        self.commit_btn.setToolTip("Create a controlled commit from the current working set")
        self.commit_btn.clicked.connect(self.commit_changes)
        self.snapshot_btn = QPushButton("Create Snapshot")
        self.snapshot_btn.setObjectName("neutral")
        self.snapshot_btn.setCursor(Qt.PointingHandCursor)
        self.snapshot_btn.clicked.connect(self.create_snapshot)
        command_group("Check In", (self.commit_btn, self.snapshot_btn))
        command_separator()

        self.push_dev_btn = QPushButton("Push to Development")
        self.push_dev_btn.setObjectName("neutral")
        self.push_dev_btn.setCursor(Qt.PointingHandCursor)
        self.push_dev_btn.clicked.connect(self.push_to_dev)
        self.merge_master_btn = QPushButton("Merge to Master")
        self.merge_master_btn.setObjectName("neutral")
        self.merge_master_btn.setCursor(Qt.PointingHandCursor)
        self.merge_master_btn.clicked.connect(self.merge_to_master)
        command_group("Promotion", (self.push_dev_btn, self.merge_master_btn))
        command_lay.addStretch()
        root.addWidget(command_bar)

        main_splitter = QSplitter(Qt.Vertical)
        main_splitter.setHandleWidth(3)
        main_splitter.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Ignored)
        self.main_splitter = main_splitter
        # ── TOP AREA ─────────────────────────────────────────────────
        top_widget = QWidget()
        self.commit_sections_widget = top_widget
        top_widget.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Ignored)
        top_layout = QHBoxLayout(top_widget)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(5)

        # ── Left: staging + metadata ─────────────────────────────────
        left_splitter = QSplitter(Qt.Vertical)
        left_splitter.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Ignored)

        # Staging area
        staging_group = QGroupBox("WORKING SET")
        staging_group.setObjectName("enterprisePanel")
        staging_group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Ignored)
        staging_lay = QVBoxLayout(staging_group)
        staging_lay.setContentsMargins(7, 8, 7, 6)
        staging_lay.setSpacing(4)

        self.changes_list = DropListWidget(callback=self.add_files_from_drop)
        self.changes_list.setStyleSheet("""
            QListWidget {
                border: 1px solid #aeb5bf; border-radius: 0;
                background: #ffffff; padding: 1px;
                font-size: 9px;
            }
            QListWidget::item { padding: 3px 5px; border-bottom: 1px solid #e2e5e8; }
            QListWidget::item:selected { background: #d8e6f2; color: #1f2a35; }
        """)
        self.changes_list.setMinimumHeight(120)
        staging_lay.addWidget(self.changes_list)

        staging_footer = QHBoxLayout()
        staging_footer.setContentsMargins(0, 0, 0, 0)
        staging_footer.addWidget(QLabel("Drop files here or use WORKING SET commands."))
        staging_footer.addStretch()
        self._staged_count_lbl = QLabel("0 FILES")
        self._staged_count_lbl.setStyleSheet(
            "font-size: 8px; font-weight: 700; color: #56616d; "
            "background: transparent; border: none;"
        )
        staging_footer.addWidget(self._staged_count_lbl)
        staging_lay.addLayout(staging_footer)
        left_splitter.addWidget(staging_group)

        # Commit metadata
        meta_group = QGroupBox("CHECK-IN ATTRIBUTES")
        meta_group.setObjectName("enterprisePanel")
        meta_group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Ignored)
        meta_lay = QFormLayout(meta_group)
        meta_lay.setHorizontalSpacing(8)
        meta_lay.setVerticalSpacing(4)
        meta_lay.setContentsMargins(9, 9, 9, 7)
        meta_lay.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.commit_title = QLineEdit()
        self.commit_title.setPlaceholderText("Required controlled-change summary")
        self.commit_title.setMaxLength(120)
        self._title_counter = QLabel("0/120")
        self._title_counter.setStyleSheet("font-size: 8px; color: #707b87;")
        self.commit_title.textChanged.connect(
            lambda t: self._title_counter.setText(f"{len(t)}/120")
        )
        title_row = QHBoxLayout()
        title_row.addWidget(self.commit_title, 1)
        title_row.addWidget(self._title_counter)
        meta_lay.addRow("Change Summary:", title_row)

        self.designed_by = QComboBox()
        if self.is_designer:
            self.designed_by.addItem(self.username)
        else:
            self.designed_by.addItems(self.usernames)
        self.designed_by.currentTextChanged.connect(lambda *_: self._refresh_affected_bom_items())
        meta_lay.addRow("Authoring User:", self.designed_by)

        self.commit_message = RichTextImageEditor()
        self.commit_message.setPlaceholderText(
            "Required change description\n"
            "Scope: affected Items and CAD Documents\n"
            "Reason: engineering intent and expected result"
        )
        self.commit_message.setMinimumHeight(105)
        self.commit_message.setMaximumHeight(165)
        self.commit_message.setStyleSheet("""
            QTextEdit { border: 1px solid #aeb5bf; border-radius: 0; padding: 4px; font-size: 9px; }
            QTextEdit:focus { border-color: #4f7598; }
        """)
        meta_lay.addRow("Description:", self.commit_message)

        self.resolved_issues_list = QListWidget()
        self.resolved_issues_list.setMaximumHeight(82)
        self.resolved_issues_list.setAlternatingRowColors(True)
        self.resolved_issues_list.setToolTip(
            "Select engineering issues linked to this commit. Only the 'solves' relation "
            "can close an issue after the commit is validated."
        )
        meta_lay.addRow("Affected Issues:", self.resolved_issues_list)

        self.issue_relation_combo = QComboBox()
        self.issue_relation_combo.addItems(["solves", "partial_fix", "related", "regression"])
        self.issue_relation_combo.setToolTip(
            "solves: ready for validation, then closed when confirmed; "
            "partial_fix: remains in progress; related: no status change; "
            "regression: reopens the issue."
        )
        meta_lay.addRow("Relationship:", self.issue_relation_combo)

        self.commit_jira_key = QLineEdit()
        self.commit_jira_key.setPlaceholderText("External identifier, e.g. ENG-123")
        meta_lay.addRow("External ID:", self.commit_jira_key)

        self.commit_jira_url = QLineEdit()
        self.commit_jira_url.setPlaceholderText("External system URL")
        meta_lay.addRow("External Link:", self.commit_jira_url)

        create_issue_btn = QPushButton("Create Related Issue")
        create_issue_btn.setObjectName("neutral")
        create_issue_btn.clicked.connect(self._create_issue_from_commit)
        meta_lay.addRow("", create_issue_btn)
        left_splitter.addWidget(meta_group)
        left_splitter.setStretchFactor(0, 1)
        left_splitter.setStretchFactor(1, 2)

        top_layout.addWidget(left_splitter, 2)

        # ── Right: pending commits ───────────────────────────────────
        pending_group = QGroupBox("ROUTING QUEUE")
        pending_group.setObjectName("enterprisePanel")
        pending_group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Ignored)
        pending_lay = QVBoxLayout(pending_group)
        pending_lay.setContentsMargins(7, 8, 7, 6)
        pending_lay.setSpacing(4)

        pending_scroll = QScrollArea()
        pending_scroll.setWidgetResizable(True)
        pending_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        pending_content = QWidget()
        pending_content.setStyleSheet("background: transparent;")
        self.pending_container_layout = QVBoxLayout(pending_content)
        self.pending_container_layout.setAlignment(Qt.AlignTop)
        self.pending_container_layout.setContentsMargins(0, 0, 0, 0)
        self.pending_container_layout.setSpacing(4)
        pending_scroll.setWidget(pending_content)
        pending_lay.addWidget(pending_scroll, 1)

        self.revert_btn = QPushButton("Revert Selected Commit")
        self.revert_btn.setObjectName("danger")
        self.revert_btn.setCursor(Qt.PointingHandCursor)
        self.revert_btn.clicked.connect(self.revert_commit)
        pending_lay.addWidget(self.revert_btn)

        top_layout.addWidget(pending_group, 1)
        main_splitter.addWidget(top_widget)

        # ── BOTTOM AREA: History ─────────────────────────────────────
        history_group = QGroupBox("COMMIT HISTORY")
        self.history_group = history_group
        history_group.setParent(self)
        history_group.setTitle("")
        history_group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Ignored)
        history_group.setStyleSheet("""
            QGroupBox {
                font-weight: bold; border: 1px solid #7d8792;
                border-radius: 1px; margin-top: 0; padding-top: 0;
                background: #eef0f2;
            }
            QGroupBox::title {
                subcontrol-origin: margin; left: 12px; padding: 0 6px;
                color: transparent;
            }
        """)
        history_lay = QVBoxLayout(history_group)
        history_lay.setContentsMargins(0, 0, 0, 5)
        history_lay.setSpacing(4)

        self.history_header_btn = QPushButton("HIDE  |  COMMIT HISTORY")
        self.history_header_btn.setCursor(Qt.PointingHandCursor)
        self.history_header_btn.setToolTip("Click to expand/collapse. Drag up or down to resize when expanded.")
        self.history_header_btn.setFixedHeight(34)
        self.history_header_btn.setStyleSheet("""
            QPushButton {
                background: #4f5d6a; color: #ffffff; border: none;
                border-radius: 0;
                padding: 0 10px; text-align: left; font-size: 9px; font-weight: 700;
                letter-spacing: 0.5px;
            }
            QPushButton:hover { background: #43515e; }
        """)
        self.history_header_btn.clicked.connect(
            lambda: self._collapse_history_section()
            if getattr(self, "_history_expanded", False)
            else self._expand_history_section()
        )
        history_lay.addWidget(self.history_header_btn)

        # Filter row
        self.history_filter_frame = QFrame()
        self.history_filter_frame.setFixedHeight(32)
        self.history_filter_frame.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.history_filter_frame.setStyleSheet("QFrame { background: transparent; border: none; }")
        filter_row = QHBoxLayout(self.history_filter_frame)
        filter_row.setContentsMargins(8, 0, 8, 0)
        filter_row.setSpacing(6)

        self.history_search = QLineEdit()
        self.history_search.setPlaceholderText("Search object, user, description, or state")
        self.history_search.setStyleSheet("""
            QLineEdit {
                border: 1px solid #aeb5bf; border-radius: 0;
                padding: 3px 6px; font-size: 9px; background: #ffffff;
            }
            QLineEdit:focus { border-color: #4f7598; }
        """)
        filter_row.addWidget(self.history_search, 1)

        self.history_status_filter = QComboBox()
        self.history_status_filter.addItems([
            "All", "Pending", "Validated", "Approved", "Pushed",
            "Integrated", "Released", "Reverted", "WIP",
        ])
        self.history_status_filter.setFixedWidth(110)
        filter_row.addWidget(self.history_status_filter)

        self.history_view_filter = QComboBox()
        self.history_view_filter.addItems(["File Records", "Commit Groups"])
        self.history_view_filter.setToolTip(
            "Switch between controlled-file records and logical commit groups"
        )
        self.history_view_filter.setFixedWidth(135)
        filter_row.addWidget(self.history_view_filter)

        self.history_clear_btn = QPushButton("Reset")
        self.history_clear_btn.setFixedSize(48, 24)
        self.history_clear_btn.setToolTip("Clear filters")
        self.history_clear_btn.setCursor(Qt.PointingHandCursor)
        self.history_clear_btn.setStyleSheet("""
            QPushButton { background: #f3f4f5; border: 1px solid #aeb5bf; border-radius: 1px; font-size: 9px; }
            QPushButton:hover { background: #e1e5e9; }
        """)
        filter_row.addWidget(self.history_clear_btn)

        self._hist_count_lbl = QLabel("")
        self._hist_count_lbl.setStyleSheet("font-size: 8px; color: #65707c;")
        filter_row.addWidget(self._hist_count_lbl)

        history_lay.addWidget(self.history_filter_frame)

        # Table
        self.history_table = _HistoryTable()
        self.history_table.setMinimumHeight(0)
        self.history_table.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Ignored)
        self.history_table.itemDoubleClicked.connect(self._on_history_double_click)
        self.history_table.restore_requested.connect(self._restore_commit_group_from_history)
        self.history_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.history_table.customContextMenuRequested.connect(self._show_history_context_menu)
        history_lay.addWidget(self.history_table, 1)

        main_splitter.setStretchFactor(0, 3)

        root.addWidget(main_splitter, 1)
        self._history_content_widgets = [
            self.history_filter_frame,
            self.history_table,
        ]
        self._history_expanded = True
        self._history_overlay_height = None
        self._history_resize_drag = None
        self._set_history_expanded(False)
        QApplication.instance().installEventFilter(self)

        # ── Search debounce ──────────────────────────────────────────
        self._history_search_timer = QTimer(self)
        self._history_search_timer.setSingleShot(True)
        self._history_search_timer.setInterval(250)
        self.history_search.textChanged.connect(lambda _: self._history_search_timer.start())
        self._history_search_timer.timeout.connect(self._apply_history_filter)
        self.history_status_filter.currentTextChanged.connect(lambda _: self._apply_history_filter())
        self.history_view_filter.currentTextChanged.connect(lambda _: self._apply_history_filter())
        self.history_clear_btn.clicked.connect(self._clear_history_filter)

        # Track selected pending card
        self.selected_card = None
        self.selected_group = None
        self._pending_cards = []
        self.processing_overlay = _PageProcessingOverlay(self)
        self.processing_overlay.setGeometry(self.rect())
        self.processing_overlay.hide()

    # ═══════════════════════════════════════════════════════════════════
    #  HELPERS
    # ═══════════════════════════════════════════════════════════════════

    def eventFilter(self, obj, event):
        if obj is getattr(self, "history_header_btn", None):
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self._history_resize_drag = {
                    "start_y": event.globalPos().y(),
                    "start_h": getattr(self, "history_group", self).height(),
                    "moved": False,
                }
            elif event.type() == QEvent.MouseMove and getattr(self, "_history_resize_drag", None):
                if getattr(self, "_history_expanded", False):
                    drag = self._history_resize_drag
                    delta = drag["start_y"] - event.globalPos().y()
                    if abs(delta) > 3:
                        drag["moved"] = True
                    self._history_overlay_height = self._clamp_history_overlay_height(
                        int(drag["start_h"] + delta)
                    )
                    self._position_history_overlay()
                    return True
            elif event.type() == QEvent.MouseButtonRelease and getattr(self, "_history_resize_drag", None):
                moved = bool(self._history_resize_drag.get("moved"))
                self._history_resize_drag = None
                if moved:
                    return True

        if event.type() == QEvent.MouseButtonPress:
            try:
                history_group = getattr(self, "history_group", None)
                commit_sections = getattr(self, "commit_sections_widget", None)
                if isinstance(obj, QWidget) and history_group and (
                    obj is history_group or history_group.isAncestorOf(obj)
                ):
                    if not getattr(self, "_history_expanded", False):
                        QTimer.singleShot(0, self._expand_history_section)
                elif (
                    getattr(self, "_history_expanded", False)
                    and isinstance(obj, QWidget)
                    and commit_sections
                    and (obj is commit_sections or commit_sections.isAncestorOf(obj))
                ):
                    QTimer.singleShot(0, self._collapse_history_section)
            except Exception:
                pass
        return super().eventFilter(obj, event)

    def _set_history_expanded(self, expanded: bool):
        self._history_expanded = bool(expanded)
        history_group = getattr(self, "history_group", None)
        if not history_group:
            return
        if hasattr(self, "history_header_btn"):
            self.history_header_btn.setText(
                "HIDE  |  COMMIT HISTORY" if expanded else "SHOW  |  COMMIT HISTORY"
            )
        for widget in getattr(self, "_history_content_widgets", []):
            widget.setVisible(bool(expanded))
        self._position_history_overlay()
        history_group.show()
        history_group.raise_()

    def _position_history_overlay(self):
        history_group = getattr(self, "history_group", None)
        if not history_group:
            return
        margin = 8
        header_h = 42
        available_h = max(header_h, self.height() - (margin * 2))
        if getattr(self, "_history_expanded", False):
            default_h = max(220, int(available_h * 0.34))
            panel_h = self._clamp_history_overlay_height(
                getattr(self, "_history_overlay_height", None) or default_h
            )
            self._history_overlay_height = panel_h
        else:
            panel_h = header_h
        history_group.setMinimumHeight(0)
        history_group.setMaximumHeight(panel_h)
        history_group.setGeometry(
            margin,
            max(margin, self.height() - panel_h - margin),
            max(120, self.width() - (margin * 2)),
            panel_h,
        )
        history_group.raise_()

    def _clamp_history_overlay_height(self, requested_height: int) -> int:
        margin = 8
        header_h = 42
        available_h = max(header_h, self.height() - (margin * 2))
        max_h = max(header_h, available_h - 70)
        min_h = min(max_h, 170)
        return max(min_h, min(int(requested_height), max_h))

    def _expand_history_section(self):
        if not getattr(self, "_history_expanded", False):
            self._set_history_expanded(True)

    def _collapse_history_section(self):
        if getattr(self, "_history_expanded", False):
            self._set_history_expanded(False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        try:
            self._position_history_overlay()
            self.processing_overlay.setGeometry(self.rect())
            if self.processing_overlay.isVisible():
                self.processing_overlay.raise_()
        except Exception:
            pass

    def _clear_staging(self):
        self.uncommitted_parts.clear()
        self._pending_engineering_attachments.clear()
        self.changes_list.clear()
        self._update_staged_count()

    def _update_staged_count(self):
        n = len(self.uncommitted_parts)
        self._staged_count_lbl.setText(
            f"{n} FILE{'S' if n != 1 else ''} IN WORKING SET"
        )
        self._stat_staged.set_value(str(n))
        self._refresh_resolved_issues()
        self._refresh_affected_bom_items()

    def _refresh_resolved_issues(self):
        if not hasattr(self, "resolved_issues_list"):
            return
        checked = {
            int(self.resolved_issues_list.item(i).data(Qt.UserRole))
            for i in range(self.resolved_issues_list.count())
            if self.resolved_issues_list.item(i).checkState() == Qt.Checked
        }
        self.resolved_issues_list.clear()
        try:
            paths = [p["path"] for p in self.uncommitted_parts]
            issues = self.issue_service.active_issues_for_paths(paths)
        except Exception:
            issues = []
        for issue in issues:
            item = QListWidgetItem(
                f"{issue['issue_number']}  {issue['title']}  [{issue['priority']}]"
            )
            item.setData(Qt.UserRole, int(issue["id"]))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if int(issue["id"]) in checked else Qt.Unchecked)
            self.resolved_issues_list.addItem(item)

    def _designer_id_for_commit(self):
        try:
            user = self.user_service.get_user_by_username(self.designed_by.currentText())
            return int(user.id) if user else None
        except Exception:
            return None

    def _clear_affected_bom_items(self):
        return

    @staticmethod
    def _optional_int(value):
        try:
            return int(value) if value is not None else None
        except Exception:
            return None

    def _staged_creo_files_for_commit(self):
        rows = []
        for staged in self.uncommitted_parts or []:
            path = str(staged.get("path") or staged.get("filename") or "")
            filename = os.path.basename(path)
            if not filename or not is_creo_file(filename):
                continue
            clean_filename = self._clean_creo_file_name(filename)
            rows.append({
                "path": path,
                "filename": filename,
                "clean_filename": clean_filename,
                "base_stem": os.path.splitext(clean_filename)[0],
            })
        return rows

    def _pdm_document_indexes_for_affected_items(self, project_id: int):
        documents = []
        try:
            documents = self.bom_service.list_pdm_cad_documents(int(project_id)) or []
        except Exception:
            documents = []
        by_file = {}
        by_base = {}
        by_id = {}
        for document in documents:
            doc_id = self._optional_int(document.get("id"))
            if doc_id is not None:
                by_id[doc_id] = document
            file_name = self._clean_creo_file_name(document.get("file_name") or "")
            if file_name:
                by_file[file_name.casefold()] = document
                by_base[os.path.splitext(file_name)[0].casefold()] = document
            base_name = str(document.get("base_file_name") or "").strip()
            if base_name:
                by_base[base_name.casefold()] = document
        return by_file, by_base, by_id

    def _item_ids_for_pdm_document_in_commit_page(
        self,
        document: dict,
        documents_by_id: dict,
    ) -> list[int]:
        """Resolve every EBOM Item affected by a staged CAD Document.

        Models may legitimately describe several Items.  Drawings are more
        specific: their direct Item associations are the user's explicit
        drawing selections and must not be inherited by every Item that uses
        the owning model.
        """
        associations = list(document.get("associations") or [])
        item_ids = {
            item_id
            for association in associations
            if (item_id := self._optional_int(association.get("item_id"))) is not None
        }
        if item_ids:
            return sorted(item_ids)

        # Compatibility for databases/documents loaded before association
        # aggregation was introduced.
        item_id = self._optional_int(document.get("item_id"))
        if item_id is not None:
            return [item_id]

        owner_id = self._optional_int(document.get("drawing_owner_cad_document_id"))
        if owner_id is None:
            return []
        owner_document = documents_by_id.get(owner_id) or {}
        owner_associations = list(owner_document.get("associations") or [])
        # An unassigned legacy drawing falls back only to the model OWNER.  It
        # never becomes content of every secondary IMAGE/CONTENT Item.
        owner_item_ids = {
            item_id
            for association in owner_associations
            if str(association.get("association_type") or "").upper() == "OWNER"
            if (item_id := self._optional_int(association.get("item_id"))) is not None
        }
        if owner_item_ids:
            return sorted(owner_item_ids)
        owner_item_id = self._optional_int(owner_document.get("item_id"))
        return [owner_item_id] if owner_item_id is not None else []

    def _legacy_affected_bom_items_for_staged_file(self, staged_file: dict, project_id: int, designer_id):
        filename = staged_file.get("filename") or ""
        clean_filename = staged_file.get("clean_filename") or filename
        base_name = clean_filename
        ext = os.path.splitext(clean_filename)[1].lstrip(".").lower()
        try:
            if ext == "drw":
                bom = self.bom_repo.get_by_drawing_file_name_for_commit(
                    base_name,
                    int(project_id),
                    designer_id,
                )
                return [bom] if bom else []
            return self.bom_repo.get_all_by_base_file_name_for_commit(
                base_name,
                int(project_id),
                designer_id,
            ) or []
        except Exception:
            return []

    def _affected_bom_items_for_staging(self):
        project_id = self.session.project_id
        if not project_id:
            return []
        designer_id = self._designer_id_for_commit()
        staged_creo_files = self._staged_creo_files_for_commit()
        cad_by_file, cad_by_base, cad_by_id = self._pdm_document_indexes_for_affected_items(int(project_id))
        result = {}
        for staged in staged_creo_files:
            clean_filename = staged.get("clean_filename") or staged.get("filename") or ""
            cad_document = cad_by_file.get(clean_filename.casefold())
            if cad_document is None:
                cad_document = cad_by_base.get((staged.get("base_stem") or "").casefold())
            item_ids = (
                self._item_ids_for_pdm_document_in_commit_page(cad_document, cad_by_id)
                if cad_document else []
            )
            resolved_from_pdm = False
            for item_id in item_ids:
                try:
                    info = self.bom_service.get_part_details(int(item_id)) or {}
                except Exception:
                    info = {}
                if info:
                    result[int(item_id)] = info
                    resolved_from_pdm = True
            if resolved_from_pdm:
                continue

            boms = self._legacy_affected_bom_items_for_staged_file(
                staged,
                int(project_id),
                designer_id,
            )
            for bom in boms:
                if not bom:
                    continue
                info = self.bom_service.get_part_details(int(bom.id)) or {}
                if info:
                    result[int(bom.id)] = info
        return list(result.values())

    def _refresh_affected_bom_items(self):
        if not hasattr(self, "attach_affected_btn"):
            return
        items = self._affected_bom_items_for_staging()
        staged_creo_count = len(self._staged_creo_files_for_commit())
        active_part_ids = {int(item.get("id")) for item in items if item.get("id") is not None}
        pending = getattr(self, "_pending_engineering_attachments", {})
        for part_id in list(pending.keys()):
            if int(part_id) not in active_part_ids:
                pending.pop(part_id, None)
        count = len(items)
        pending_count = sum(len(v or []) for v in getattr(self, "_pending_engineering_attachments", {}).values())
        action_enabled = staged_creo_count > 0 and not getattr(self, "_processing_action", False)
        step_enabled = count > 0 and not getattr(self, "_processing_action", False)
        self.attach_affected_btn.setEnabled(action_enabled)
        if hasattr(self, "attached_step_compare_checkbox"):
            self.attached_step_compare_checkbox.setEnabled(step_enabled)
            if not step_enabled:
                self.attached_step_compare_checkbox.setChecked(False)
        item_unit = "Item" if count == 1 else "Items"
        file_unit = "File" if pending_count == 1 else "Files"
        if count and pending_count:
            text = f"Associate Outputs ({count} {item_unit} / {pending_count} {file_unit})"
        elif count:
            text = f"Associate Outputs ({count} {item_unit})"
        elif staged_creo_count:
            text = "Associate Outputs"
        else:
            text = "Associate Outputs"
        self.attach_affected_btn.setText(text)

    def _open_affected_bom_attachment_dialog(self):
        items = self._affected_bom_items_for_staging()
        if not items:
            if self._staged_creo_files_for_commit():
                QMessageBox.information(
                    self,
                    "Affected Items",
                    "The staged Creo files do not resolve to an associated EBOM Item. "
                    "Register the CAD Document and associate it to an Item, or bind a DRW to its owning PRT/ASM.",
                )
            else:
                QMessageBox.information(
                    self,
                    "Affected Items",
                    "Stage Creo files first to resolve affected Items.",
                )
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Attach Documents to Affected Items")
        dialog.resize(560, 420)
        layout = QVBoxLayout(dialog)
        intro = QLabel(
            "Drop PDF, STEP, or validation documents onto the correct Item. Files are staged with this commit and will be added to the vault only after push to master."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("font-size: 10px; color: #374151;")
        layout.addWidget(intro)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content.setStyleSheet("background: transparent;")
        cards_layout = QVBoxLayout(content)
        cards_layout.setContentsMargins(0, 0, 0, 0)
        cards_layout.setSpacing(6)
        cards_layout.setAlignment(Qt.AlignTop)
        self._affected_dialog_cards = {}
        for info in items:
            card = AffectedBomDropCard(info, dialog)
            part_id = int(info.get("id") or 0)
            card.set_attachments(self._pending_engineering_attachments.get(part_id, []))
            card.filesDropped.connect(self._attach_files_to_affected_bom_item)
            card.docRemoveRequested.connect(self._remove_staged_doc_from_affected_bom_item)
            self._affected_dialog_cards[part_id] = card
            cards_layout.addWidget(card)
        cards_layout.addStretch(1)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        close_btn = QPushButton("Close")
        close_btn.setObjectName("neutral")
        close_btn.clicked.connect(dialog.accept)
        footer = QHBoxLayout()
        footer.addStretch()
        footer.addWidget(close_btn)
        layout.addLayout(footer)
        dialog.exec_()
        self._affected_dialog_cards = {}

    def _commit_attachment_type(self, path: str) -> str:
        ext = os.path.splitext(path or "")[1].lower()
        if ext == ".pdf":
            return "PDF"
        if ext in (".step", ".stp"):
            return "STEP"
        if ext:
            return ext.lstrip(".").upper()
        return "DOC"

    def _default_attachment_role(self, path: str) -> str:
        ext = os.path.splitext(path or "")[1].lower()
        if ext == ".pdf":
            return "exported_pdf"
        if ext in (".step", ".stp"):
            return "exported_step"
        return "validation_doc"

    def _attach_files_to_affected_bom_item(self, part_id: int, paths: list):
        clean_paths = [
            p for p in (paths or [])
            if p and safe_isfile(p) and os.path.splitext(p)[1].lower() not in (".prt", ".asm", ".drw")
        ]
        if not clean_paths:
            QMessageBox.warning(self, "Attachment", "Drop PDF, STEP, or validation/supporting documents only.")
            return

        default_role = self._default_attachment_role(clean_paths[0])
        roles = [
            "exported_pdf",
            "exported_step",
            "validation_doc",
            "inspection_report",
            "screenshot",
            "supporting_doc",
            "other",
        ]
        role, ok = QInputDialog.getItem(
            self,
            "Attachment Role",
            "Traceability role for dropped file(s):",
            roles,
            max(0, roles.index(default_role) if default_role in roles else 0),
            False,
        )
        if not ok:
            return

        revision, ok = QInputDialog.getText(
            self,
            "Attachment Revision",
            "Revision for dropped file(s), optional:",
        )
        if not ok:
            return
        revision = (revision or "").strip().upper()
        note, ok = QInputDialog.getText(
            self,
            "Attachment Note",
            "Note for dropped file(s), optional:",
        )
        if not ok:
            return

        added = 0
        staged = self._pending_engineering_attachments.setdefault(int(part_id), [])
        for path in clean_paths:
            file_type = self._commit_attachment_type(path)
            staged.append({
                "part_id": int(part_id),
                "file_type": file_type,
                "file_role": role,
                "source_path": path,
                "filename": os.path.basename(path),
                "note": note or "",
                "revision": revision,
            })
            added += 1

        if added:
            card = getattr(self, "_affected_dialog_cards", {}).get(int(part_id))
            if card:
                card.set_attachments(staged)
            self._refresh_affected_bom_items()
            try:
                self.window().statusBar().showMessage(
                    f"{added} file(s) staged for the affected Item."
                )
            except Exception:
                pass
            QMessageBox.information(
                self,
                "Staged",
                f"{added} file(s) staged. They will enter the vault after the commit is pushed to master.",
            )

    def _remove_staged_doc_from_affected_bom_item(self, part_id: int, index: int):
        staged = self._pending_engineering_attachments.get(int(part_id), [])
        if index < 0 or index >= len(staged):
            return
        removed = staged.pop(index)
        if not staged:
            self._pending_engineering_attachments.pop(int(part_id), None)
        card = getattr(self, "_affected_dialog_cards", {}).get(int(part_id))
        if card:
            card.set_attachments(staged)
        self._refresh_affected_bom_items()
        try:
            name = os.path.basename(removed.get("source_path") or removed.get("filename") or "document")
            self.window().statusBar().showMessage(f"Removed staged document: {name}")
        except Exception:
            pass

    def _update_dashboard_stats(self):
        cache = getattr(self, "_history_cache", []) or []
        total = len(cache)
        pending = sum(1 for c in cache if (c.get("status") or "").lower() == "pending")
        validated = sum(1 for c in cache if (c.get("status") or "").lower() == "validated")
        step = sum(1 for c in cache if (c.get("step_diff_status") or "").strip())
        self._stat_total.set_value(str(total))
        self._stat_pending.set_value(str(pending))
        self._stat_validated.set_value(str(validated))
        self._stat_step.set_value(str(step))
        self._update_staged_count()

    # ═══════════════════════════════════════════════════════════════════
    #  PENDING COMMITS
    # ═══════════════════════════════════════════════════════════════════

    def load_pending_commits(self):
        commits = self.commit_service.get_pending_commits_grouped(
            self.session.project_id, self.session.user_id, self.is_designer
        )
        self._set_pending_commit_groups(commits)

    def _set_pending_commit_groups(self, commits):
        # Clear old cards
        for i in reversed(range(self.pending_container_layout.count())):
            w = self.pending_container_layout.itemAt(i).widget()
            if w:
                w.deleteLater()

        self.selected_card = None
        self.selected_group = None
        self._pending_cards = []

        for idx, group in enumerate(commits):
            if idx and idx % 10 == 0:
                QApplication.processEvents()
            card = _PendingCard(group)
            card.clicked.connect(self._on_pending_card_clicked)
            card.view_requested.connect(self.show_commit_details)
            card.browse_requested.connect(self.browse_commit_directory)
            self.pending_container_layout.addWidget(card)
            self._pending_cards.append(card)

    def _on_pending_card_clicked(self, group):
        for c in self._pending_cards:
            c.set_selected(c.group is group)
        for c in self._pending_cards:
            if c.group is group:
                self.selected_card = c
                self.selected_group = group
                break

    def select_commit_card(self, card, group):
        """Legacy compat."""
        self._on_pending_card_clicked(group)

    # ═══════════════════════════════════════════════════════════════════
    #  COMMIT HISTORY
    # ═══════════════════════════════════════════════════════════════════

    def load_commit_history(self):
        self._history_cache = []
        if not self.commit_service:
            return

        commits = self.commit_service.get_commit_history() or []
        self._set_commit_history_rows(commits)

    def _set_commit_history_rows(self, commits):
        self._history_cache = commits
        self._apply_history_filter()
        self._update_dashboard_stats()

    def _apply_history_filter(self):
        try:
            q = (self.history_search.text() or "").strip().lower()
        except Exception:
            q = ""
        try:
            status_filter = (self.history_status_filter.currentText() or "All").strip().lower()
        except Exception:
            status_filter = "all"
        try:
            grouped_view = (self.history_view_filter.currentText() == "Commit Groups")
        except Exception:
            grouped_view = False

        filtered = []
        for idx, c in enumerate(self._history_cache):
            if idx and idx % 250 == 0:
                QApplication.processEvents()
            st = (c.get("status") or "").strip().lower()
            if status_filter != "all" and st != status_filter:
                continue
            if q:
                hay = " ".join(str(c.get(k, "")) for k in
                               ("status", "filename", "date", "designed_by",
                                "checked_by", "message", "commit_id", "title")).lower()
                if q not in hay:
                    continue
            filtered.append(c)

        visible_rows = self._group_history_rows(filtered) if grouped_view else filtered
        self.history_table.populate(visible_rows)
        total = len(self._history_cache)
        shown = len(visible_rows)
        if grouped_view:
            self._hist_count_lbl.setText(f"{shown} groups / {len(filtered)} items")
        elif total == shown:
            self._hist_count_lbl.setText(f"{total} commits")
        else:
            self._hist_count_lbl.setText(f"{shown} / {total}")

    def _group_history_rows(self, rows):
        status_rank = {
            "reverted": 90,
            "released": 80,
            "pushed": 70,
            "approved": 70,
            "validated": 60,
            "pending": 50,
            "integrated": 40,
            "wip": 30,
        }
        grouped = {}
        for row in rows:
            commit_id = str(row.get("commit_id") or row.get("id") or "")
            project_id = row.get("project_id")
            key = (project_id, commit_id)
            group = grouped.setdefault(key, {
                "is_commit_group": True,
                "commit_id": commit_id,
                "project_id": project_id,
                "title": row.get("title") or "",
                "filename": row.get("title") or f"Commit {commit_id[:12]}",
                "date": row.get("date") or "",
                "designed_by": row.get("designed_by") or "",
                "checked_by": row.get("checked_by") or "",
                "message": "",
                "status": row.get("status") or "",
                "files": [],
                "step_diff_status": "",
            })
            group["files"].append(row)
            if not group.get("title") and row.get("title"):
                group["title"] = row.get("title")
                group["filename"] = row.get("title")
            if row.get("date") and str(row.get("date")) > str(group.get("date") or ""):
                group["date"] = row.get("date")
            current_rank = status_rank.get(str(group.get("status") or "").lower(), 0)
            row_rank = status_rank.get(str(row.get("status") or "").lower(), 0)
            if row_rank > current_rank:
                group["status"] = row.get("status") or ""
            if row.get("step_diff_status"):
                group["step_diff_status"] = row.get("step_diff_status")

        result = []
        for group in grouped.values():
            files = group.get("files") or []
            filenames = [str(f.get("filename") or "") for f in files if f.get("filename")]
            messages = [html_to_plain_text(str(f.get("message") or "")) for f in files if f.get("message")]
            title = group.get("title") or f"Commit {str(group.get('commit_id') or '')[:12]}"
            group["filename"] = title
            group["message"] = (
                f"{group.get('commit_id', '')} | {len(files)} file(s): "
                + ", ".join(filenames[:5])
                + ("..." if len(filenames) > 5 else "")
            )
            if messages:
                group["message"] += f"\n{messages[0]}"
            all_group_rows = [
                row for row in self._history_cache
                if str(row.get("commit_id") or row.get("id") or "") == str(group.get("commit_id") or "")
                and row.get("project_id") == group.get("project_id")
            ]
            statuses = {str(f.get("status") or "").lower() for f in all_group_rows}
            group["can_restore"] = bool(statuses) and statuses.issubset({"approved", "pushed", "released"})
            result.append(group)
        result.sort(key=lambda g: str(g.get("date") or ""), reverse=True)
        return result

    def _clear_history_filter(self):
        self.history_search.setText("")
        self.history_status_filter.setCurrentText("All")
        self.history_view_filter.setCurrentText("File Records")
        self._apply_history_filter()

    # ═══════════════════════════════════════════════════════════════════
    #  HISTORY INTERACTIONS
    # ═══════════════════════════════════════════════════════════════════

    def _on_history_double_click(self, item):
        row = item.row()
        data = self.history_table.get_data(row)
        if data:
            self._show_history_details_dialog(data)

    def _show_history_details_dialog(self, data: dict):
        group_details = self.commit_service.get_commit_group_details(
            data.get("commit_id") or "",
            data.get("project_id"),
        )
        files = group_details.get("files") or []
        selected_file = None
        if not data.get("is_commit_group"):
            selected_id = data.get("id")
            for item in files:
                if str(item.get("id")) == str(selected_id):
                    selected_file = item
                    break
        if selected_file is None and files and not data.get("is_commit_group"):
            selected_file = files[0]
        sty = _status_style(data.get("status", ""))
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Commit Details | {data.get('filename', '')}")
        dlg.setMinimumSize(760, 480)
        dlg.setStyleSheet("""
            QDialog { background: #e8eaed; color: #27313c; }
            QTableWidget, QTreeWidget, QTextEdit {
                background: #ffffff; border: 1px solid #aeb5bf; border-radius: 0;
                alternate-background-color: #f4f5f6;
            }
            QHeaderView::section {
                background: #dfe3e7; border: none; border-right: 1px solid #b8bec7;
                border-bottom: 1px solid #9fa7b1; padding: 4px; font-size: 9px;
                font-weight: 700; color: #303b46;
            }
        """)
        root = QVBoxLayout(dlg)
        root.setSpacing(6)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(4, 4, 9, 4)
        layout.setSpacing(6)

        # Header
        header = QFrame()
        header.setStyleSheet(f"""
            QFrame {{
                background: {sty['bg']}; border: 1px solid {sty['color']}40;
                border-left: 4px solid {sty['color']}; border-radius: 0;
            }}
        """)
        hl = QVBoxLayout(header)
        hl.setSpacing(4)

        badge = QLabel(f'  {sty["label"].upper()}  ')
        badge.setStyleSheet(f"""
            background: {sty['color']}; color: #ffffff;
            border-radius: 0; padding: 2px 9px;
            font-size: 9px; font-weight: 700;
        """)
        badge.setFixedWidth(badge.sizeHint().width() + 16)
        hl.addWidget(badge)

        fname = str(data.get("filename", ""))
        hl.addWidget(QLabel(f"<b style='font-size:13px'>{_file_icon(fname)} {fname}</b>"))

        meta_parts = []
        if data.get("designed_by"):
            meta_parts.append(f"Designer: {data['designed_by']}")
        if data.get("checked_by") and data["checked_by"] != "Unknown":
            meta_parts.append(f"Checker: {data['checked_by']}")
        if data.get("date"):
            rel = _relative_time(str(data["date"]))
            meta_parts.append(f"Modified: {str(data['date'])[:19]} ({rel})")
        if data.get("commit_id"):
            meta_parts.append(f"Commit: {data['commit_id'][:16]}")
        if meta_parts:
            m = QLabel("  |  ".join(meta_parts))
            m.setStyleSheet("color: #4b5563; font-size: 9px; background: transparent; border: none;")
            m.setWordWrap(True)
            hl.addWidget(m)

        layout.addWidget(header)

        message_value = group_details.get("message") or data.get("message") or ""
        if message_value:
            msg_view = QTextEdit()
            msg_view.setReadOnly(True)
            msg_view.setMinimumHeight(120)
            msg_view.setMaximumHeight(260)
            if looks_like_html(message_value):
                msg_view.setHtml(message_value)
            else:
                msg_view.setPlainText(str(message_value))
            layout.addWidget(msg_view)

        # Details grid
        grid = QTableWidget()
        grid.setColumnCount(2)
        grid.setHorizontalHeaderLabels(["Field", "Value"])
        grid.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        grid.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        grid.setEditTriggers(QAbstractItemView.NoEditTriggers)
        grid.verticalHeader().setVisible(False)
        grid.setAlternatingRowColors(True)
        grid.setMinimumHeight(170)
        grid.setMaximumHeight(300)
        grid.setRowCount(0)

        def add_row(k, v):
            r = grid.rowCount()
            grid.insertRow(r)
            ki = QTableWidgetItem(str(k))
            ki.setFont(QFont("Segoe UI", 10, QFont.Bold))
            grid.setItem(r, 0, ki)
            grid.setItem(r, 1, QTableWidgetItem(str(v if v is not None else "")))

        add_row("Status", data.get("status"))
        add_row("Date", data.get("date"))
        add_row("Designer", data.get("designed_by"))
        add_row("Checker", data.get("checked_by"))
        add_row("Message", data.get("message"))

        step_status = str(data.get("step_diff_status") or "").strip()
        if step_status:
            add_row("STEP Status", step_status)
        step_summary = str(data.get("step_diff_summary") or "").strip()
        if step_summary:
            try:
                obj = json.loads(step_summary)
                if isinstance(obj, dict):
                    for sk, sv in obj.items():
                        add_row(f"STEP: {sk}", sv)
                else:
                    add_row("STEP Summary", step_summary)
            except Exception:
                add_row("STEP Summary", step_summary[:200])

        for k in sorted(data.keys()):
            if k in {"status", "date", "designed_by", "checked_by", "message",
                      "filename", "step_diff_status", "step_diff_summary",
                      "step_diff_path", "step_file_path", "step_prev_file_path",
                      "step_error", "compared_against_commit_id", "id", "commit_id"}:
                continue
            v = data.get(k)
            if v:
                add_row(k, v)

        approved_version_value = ""
        if selected_file:
            approved_version_value = selected_file.get("approved_version") or ""
        elif data.get("is_commit_group"):
            versions = []
            for item in files:
                label = item.get("filename") or item.get("id")
                version = item.get("approved_version")
                if version:
                    versions.append(f"{label}: {version}")
            approved_version_value = "\n".join(versions)

        for k, v in (
            ("Commit Title", group_details.get("title")),
            ("Commit Group Status", group_details.get("status")),
            ("Project", group_details.get("project_name")),
            ("Project Version", group_details.get("project_version_label")),
            ("Author", group_details.get("author")),
            ("Merged By", group_details.get("merged_by")),
            ("Merged At", group_details.get("merged_at")),
            ("Merge ID", group_details.get("merge_id")),
            ("Merge Message", group_details.get("merge_message")),
            ("Approved Version", approved_version_value),
            ("PR Path", (selected_file or {}).get("pr_path") or group_details.get("pr_path")),
            ("Signature", group_details.get("signature")),
            ("Last Snapshot", group_details.get("last_snapshot")),
            ("Snapshotted In", group_details.get("snapshotted_in")),
        ):
            if v:
                add_row(k, v)

        layout.addWidget(grid)

        files_label = QLabel(f"<b>Files in this commit ({len(files)})</b>")
        files_label.setStyleSheet("font-size:12px;color:#111827;")
        layout.addWidget(files_label)

        files_tree = QTreeWidget()
        files_tree.setColumnCount(2)
        files_tree.setHeaderLabels(["File / Field", "Value"])
        files_tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        files_tree.header().setSectionResizeMode(1, QHeaderView.Stretch)
        files_tree.setAlternatingRowColors(True)
        files_tree.setMinimumHeight(230)
        selected_id = str((selected_file or {}).get("id") or "")

        def add_child(parent, label, value):
            if value is None or value == "":
                return
            child = QTreeWidgetItem([str(label), str(value)])
            child.setToolTip(1, str(value))
            parent.addChild(child)

        for item in files:
            filename = str(item.get("filename") or "")
            top_label = f"{_file_icon(filename)} {filename}"
            top_value = (
                f"Status: {item.get('status') or ''}"
                f" | Approved version: {item.get('approved_version') or ''}"
            )
            top = QTreeWidgetItem([top_label, top_value])
            top.setToolTip(0, filename)
            top.setToolTip(1, top_value)
            font = top.font(0)
            font.setBold(True)
            top.setFont(0, font)
            files_tree.addTopLevelItem(top)
            for label, value in (
                ("Row ID", item.get("id")),
                ("Commit ID", item.get("commit_id")),
                ("Status", item.get("status")),
                ("Type", item.get("type")),
                ("Item Number", item.get("part_number")),
                ("AES Number", item.get("aes_number")),
                ("Item", item.get("part_name") or item.get("part_id")),
                ("Internal Item ID", item.get("part_id")),
                ("CAD Document ID", item.get("cad_document_id")),
                ("Approved Creo Version", (
                    f".{item.get('approved_version')}"
                    if item.get("approved_version") is not None else ""
                )),
                ("Drawing Number", item.get("drawing_number")),
                ("Revision", item.get("part_revision")),
                ("Lifecycle", item.get("part_lifecycle_state")),
                ("Source Path", item.get("file_path")),
                ("PR Path", item.get("pr_path")),
                ("Approved Version", item.get("approved_version")),
                ("Merged At", item.get("merged_at")),
                ("Merged By", item.get("merged_by_name")),
                ("STEP Status", item.get("step_diff_status")),
                ("STEP File", item.get("step_file_path")),
                ("Previous STEP", item.get("step_prev_file_path")),
                ("STEP Diff", item.get("step_diff_path")),
                ("STEP Error", item.get("step_error")),
            ):
                add_child(top, label, value)
            top.setExpanded((not data.get("is_commit_group")) and str(item.get("id")) == selected_id)
        layout.addWidget(files_tree, 1)

        issues = group_details.get("issues") or []
        if issues:
            issues_label = QLabel(f"<b>Linked Issues ({len(issues)})</b>")
            issues_label.setStyleSheet("font-size:12px;color:#111827;")
            layout.addWidget(issues_label)
            issues_table = QTableWidget()
            issues_table.setColumnCount(8)
            issues_table.setHorizontalHeaderLabels([
                "Issue", "Title", "Status", "Priority", "Relation", "Validation", "Resolution", "Jira"
            ])
            issues_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
            issues_table.horizontalHeader().setSectionResizeMode(7, QHeaderView.Stretch)
            issues_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            issues_table.setMinimumHeight(150)
            issues_table.setMaximumHeight(260)
            issues_table.setRowCount(len(issues))
            for r, issue in enumerate(issues):
                jira_url = issue.get("jira_url") or ""
                jira_label = issue.get("jira_key") or jira_url
                values = [
                    issue.get("issue_number"),
                    issue.get("title"),
                    issue.get("status"),
                    issue.get("priority"),
                    issue.get("relation_type"),
                    issue.get("validation_status"),
                    issue.get("resolution_comment"),
                    jira_label,
                ]
                for c, value in enumerate(values):
                    item = QTableWidgetItem(str(value or ""))
                    if jira_url:
                        item.setData(Qt.UserRole, jira_url)
                        item.setToolTip(jira_url)
                    issues_table.setItem(r, c, item)
            issues_table.itemDoubleClicked.connect(lambda item, table=issues_table: self._open_jira_link_from_table(table, item))
            layout.addWidget(issues_table)
            open_jira_btn = QPushButton("Open Jira Link")
            open_jira_btn.clicked.connect(lambda _checked=False, table=issues_table: self._open_jira_link_from_table(table))
            layout.addWidget(open_jira_btn, 0, Qt.AlignLeft)

        self._add_engineering_files_section(
            layout,
            group_details.get("engineering_files") or [],
            "Vaulted Engineering Outputs",
        )
        self._add_engineering_files_section(
            layout,
            group_details.get("validation_docs") or [],
            "Validation Documents",
        )

        # Action buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        if step_status:
            sp = str(data.get("step_file_path") or "").strip()
            if sp:
                open_s = QPushButton("Open STEP")
                open_s.setObjectName("neutral")
                open_s.clicked.connect(lambda _c=False, d=data: self._open_step_file_in_viewer(d))
                btn_row.addWidget(open_s)
            if step_status.upper() == "COMPARED":
                diff_b = QPushButton("Compare STEP")
                diff_b.setObjectName("primary")
                diff_b.clicked.connect(lambda _c=False, d=data: self._show_step_diff_for_commit_history(d))
                btn_row.addWidget(diff_b)

        copy_btn = QPushButton("Copy Details")
        copy_btn.setObjectName("neutral")
        copy_btn.clicked.connect(lambda _c=False, d=data: self._copy_commit_to_clipboard(d))
        btn_row.addWidget(copy_btn)

        btn_row.addStretch()
        close = QPushButton("Close")
        close.setObjectName("neutral")
        close.clicked.connect(dlg.accept)
        btn_row.addWidget(close)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)
        root.addLayout(btn_row)

        dlg.exec_()

    def _copy_commit_to_clipboard(self, data: dict):
        lines = []
        for k in ("filename", "status", "date", "designed_by", "message",
                   "commit_id", "step_diff_status", "step_diff_summary"):
            v = data.get(k)
            if v:
                lines.append(f"{k}: {v}")
        QApplication.clipboard().setText("\n".join(lines))

    def _show_history_context_menu(self, position):
        item = self.history_table.itemAt(position)
        if not item:
            return
        row = item.row()
        data = self.history_table.get_data(row)
        if not data:
            return

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background: #fff; border: 1px solid #aeb5bf; border-radius: 0; padding: 2px 0; }
            QMenu::item { padding: 4px 16px; font-size: 9px; }
            QMenu::item:selected { background: #d8e6f2; color: #263746; }
            QMenu::separator { height: 1px; background: #cdd2d7; margin: 3px 7px; }
        """)

        det = menu.addAction("View Full Details")
        det.triggered.connect(lambda _c, d=data: self._show_history_details_dialog(d))

        if data.get("is_commit_group"):
            rst = menu.addAction("Restore Commit Group")
            rst.setEnabled(bool(data.get("can_restore")))
            rst.triggered.connect(lambda _c, d=data: self._restore_commit_group_from_history(d))

        cpy = menu.addAction("Copy Details to Clipboard")
        cpy.triggered.connect(lambda _c, d=data: self._copy_commit_to_clipboard(d))

        step_path = str(data.get("step_file_path") or "").strip()
        if step_path:
            menu.addSeparator()
            sa = menu.addAction("Open STEP in 3D Viewer")
            sa.triggered.connect(lambda _c, d=data: self._open_step_file_in_viewer(d))

        step_status = str(data.get("step_diff_status") or "").strip().upper()
        if step_status == "COMPARED":
            da = menu.addAction("Show STEP Difference Zones")
            da.triggered.connect(lambda _c, d=data: self._show_step_diff_for_commit_history(d))

        cid = str(data.get("commit_id") or "")
        if cid:
            menu.addSeparator()
            ca = menu.addAction(f"Copy Commit ID: {cid[:12]}…")
            ca.triggered.connect(lambda _c, _v=cid: QApplication.clipboard().setText(_v))

        menu.exec_(self.history_table.viewport().mapToGlobal(position))

    def _show_history_item_details(self, item):
        data = item.data(Qt.UserRole) or {}
        if data:
            self._show_history_details_dialog(data)

    def _add_engineering_files_section(self, layout, files: list, title: str = "Attached Files"):
        if not files:
            return
        label = QLabel(f"<b>{title} ({len(files)})</b>")
        label.setStyleSheet("font-size:12px;color:#111827;")
        layout.addWidget(label)

        table = QTableWidget()
        table.setColumnCount(8)
        table.setHorizontalHeaderLabels([
            "Role", "Type", "Filename", "Part", "Version", "Revision", "Exists", "Path"
        ])
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(7, QHeaderView.Stretch)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setMinimumHeight(170)
        table.setMaximumHeight(300)
        table.setRowCount(len(files))
        for r, item in enumerate(files):
            path = item.get("source_path") or ""
            if not path:
                path = item.get("stored_path") or ""
            exists = item.get("exists")
            if exists is None:
                exists = bool(path and safe_exists(path))
            values = [
                item.get("doc_role") or item.get("file_role"),
                item.get("file_type"),
                item.get("original_filename") or item.get("filename") or item.get("display_name"),
                item.get("part_name") or item.get("part_id"),
                item.get("version_no") or item.get("resolved_version_id"),
                item.get("revision"),
                "Yes" if exists else "Missing",
                path,
            ]
            for c, value in enumerate(values):
                cell = QTableWidgetItem(str(value or ""))
                cell.setToolTip(str(value or ""))
                cell.setData(Qt.UserRole, path)
                table.setItem(r, c, cell)
        layout.addWidget(table)

        row = QHBoxLayout()
        row.addStretch()
        open_btn = QPushButton("Open Selected Doc")
        open_btn.setObjectName("neutral")
        open_btn.clicked.connect(lambda _c=False, t=table: self._open_selected_engineering_doc(t))
        row.addWidget(open_btn)
        layout.addLayout(row)

    def _open_selected_engineering_doc(self, table):
        row = table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Open Document", "Select a document first.")
            return
        item = table.item(row, table.columnCount() - 1)
        path = item.data(Qt.UserRole) if item else ""
        if not path or not safe_exists(path):
            QMessageBox.warning(self, "Open Document", f"File not found:\n{path}")
            return
        try:
            safe_startfile(path)
        except Exception as exc:
            QMessageBox.critical(self, "Open Document", f"Failed to open file:\n{exc}")

    def _open_step_file_in_viewer(self, data: dict):
        step_path = str(data.get("step_file_path") or "").strip()
        if not step_path:
            QMessageBox.warning(self, "STEP Viewer", "No STEP file associated with this commit.")
            return
        if not os.path.exists(step_path):
            QMessageBox.warning(self, "STEP Viewer", f"STEP file not found:\n{step_path}")
            return
        try:
            from tools.CAD.step_viewer.launcher import launch_viewer
            launch_viewer(step_path)
        except Exception as e:
            QMessageBox.critical(self, "STEP Viewer", f"Failed to open STEP viewer:\n{e}")

    def _show_step_diff_for_commit_history(self, data: dict):
        status = str(data.get("step_diff_status") or "").strip().upper()
        prev_path = str(data.get("step_prev_file_path") or "").strip()
        current_path = str(data.get("step_file_path") or "").strip()

        if status != "COMPARED":
            QMessageBox.information(self, "STEP Diff",
                "This commit has no previous STEP to compare (baseline or unavailable compare).")
            return
        if not prev_path or not current_path:
            QMessageBox.warning(self, "STEP Diff", "STEP paths are missing for this commit.")
            return
        if not os.path.exists(prev_path) or not os.path.exists(current_path):
            QMessageBox.warning(self, "STEP Diff", "One or both STEP files are missing on disk.")
            return
        try:
            commit_id = str(data.get("commit_id") or "commit")
            from tools.CAD.step_viewer.launcher import launch_diff_viewer
            launch_diff_viewer(
                prev_path, current_path,
                commit_a=f"{commit_id}_prev", commit_b=commit_id,
                common_transparency=0.85,
            )
        except Exception as e:
            QMessageBox.critical(self, "STEP Diff", f"Failed to launch STEP diff:\n{e}")

    # ═══════════════════════════════════════════════════════════════════
    #  COMMIT DETAILS DIALOG (for pending commits)
    # ═══════════════════════════════════════════════════════════════════

    def _open_jira_link_from_table(self, table: QTableWidget, item=None):
        row = item.row() if item is not None else table.currentRow()
        if row < 0:
            return QMessageBox.warning(self, "Jira", "Select a Jira-linked issue first.")
        url = ""
        for col in range(table.columnCount()):
            cell = table.item(row, col)
            if cell:
                url = cell.data(Qt.UserRole) or url
        url = str(url or "").strip()
        if not url:
            return QMessageBox.information(self, "Jira", "The selected issue has no Jira URL.")
        try:
            safe_startfile(url)
        except Exception as exc:
            QMessageBox.warning(self, "Jira", f"Unable to open Jira link:\n{exc}")

    def show_commit_details(self, group):
        group_details = self.commit_service.get_commit_group_details(
            group.get("commit_id") or "",
            group.get("project_id"),
        )
        screen = QApplication.primaryScreen().availableGeometry()
        max_w = int(screen.width() * 0.85)
        max_h = int(screen.height() * 0.85)

        sty = _status_style(group.get("status", ""))
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Workflow Review | {group.get('title', 'Commit')}")
        dialog.setMinimumSize(700, 500)
        dialog.setMaximumSize(max_w, max_h)
        dialog.resize(int(max_w * 0.75), int(max_h * 0.75))
        dialog.setStyleSheet("""
            QDialog { background: #e8eaed; font-family: 'Segoe UI', sans-serif; color: #27313c; }
            QLabel { background: transparent; border: none; }
            QListWidget, QTableWidget, QTreeWidget {
                background: #ffffff; border: 1px solid #aeb5bf; border-radius: 0;
                alternate-background-color: #f4f5f6;
            }
        """)

        outer = QVBoxLayout(dialog)
        outer.setContentsMargins(7, 7, 7, 7)
        outer.setSpacing(6)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        scroll_content = QWidget()
        main = QVBoxLayout(scroll_content)
        main.setSpacing(7)
        main.setContentsMargins(9, 9, 9, 9)

        # ── Header ────────────────────────────────────────────────────
        header = QFrame()
        header.setStyleSheet(f"""
            QFrame {{
                background: #4f5d6a;
                border-left: 4px solid {sty['color']};
                border-radius: 0;
            }}
        """)
        header.setFixedHeight(58)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(16, 10, 16, 10)

        left_info = QVBoxLayout()
        designer_name = group.get('username', 'Unknown')
        left_info.addWidget(QLabel(
            f"<span style='color: #d9e0e6; font-size: 9px;'>DESIGNER</span>"
            f"  <b style='color: white; font-size: 10px;'>{designer_name}</b>"
        ))
        cid = group.get('commit_id', '')
        left_info.addWidget(QLabel(
            f"<span style='color: #d9e0e6; font-size: 9px;'>COMMIT</span>"
            f"  <code style='color: white; font-size: 9px;'>{cid[:14]}</code>"
        ))
        hl.addLayout(left_info, 1)

        status_text = group.get("status", "Pending")
        status_badge = QLabel(f"  {str(status_text).upper()}  ")
        status_badge.setStyleSheet("""
            background: #eef1f3; color: #26313c;
            border: 1px solid #c4cad0; border-radius: 0; padding: 3px 9px;
            font-weight: 700; font-size: 9px;
        """)
        hl.addWidget(status_badge)
        main.addWidget(header)

        # ── Info cards ────────────────────────────────────────────────
        cards_row = QHBoxLayout()
        cards_row.setSpacing(8)
        cards_row.addWidget(self._make_info_card("", "Change Summary", group.get('title', '')))
        cards_row.addWidget(self._make_info_card("", "Created",
                                                 str(group.get('date', ''))[:19]))
        num_parts = len(group.get("parts", []))
        cards_row.addWidget(self._make_info_card("", "Controlled Files",
                                                 f"{num_parts} file{'s' if num_parts != 1 else ''}"))
        main.addLayout(cards_row)

        # ── Parts list ────────────────────────────────────────────────
        main.addWidget(QLabel("<b style='font-size: 9px; color: #374151;'>CONTROLLED FILES</b>"))
        parts_list = QListWidget()
        parts_list.setStyleSheet("""
            QListWidget { border: 1px solid #aeb5bf; border-radius: 0; background: #fff; }
            QListWidget::item { padding: 4px 6px; border-bottom: 1px solid #e2e5e8; }
            QListWidget::item:selected { background: #d8e6f2; }
        """)
        parts_list.setMaximumHeight(180)
        for i, part in enumerate(group.get("parts", [])):
            item = QListWidgetItem(f"  {_file_icon(part)}  {part}")
            if i % 2 == 0:
                item.setBackground(QColor("#F8FAFC"))
            parts_list.addItem(item)
        main.addWidget(parts_list)

        related_issues = group.get("related_issues") or []
        issue_checks = QListWidget()
        if related_issues:
            linked_issues_label = QLabel(
                "<b style='font-size: 11px; color: #374151;'>Linked Issues</b>"
            )
            linked_issues_label.setToolTip(
                "Only a confirmed 'solves' relation closes an issue. "
                "Other relations record traceability without closing it."
            )
            main.addWidget(linked_issues_label)
            issue_checks.setMaximumHeight(160)
            issue_checks.setToolTip(
                "Confirm the relation for each issue. 'solves' can close the issue; "
                "partial_fix keeps it in progress, related keeps its status, and regression reopens it."
            )
            for issue in related_issues:
                item = QListWidgetItem(
                    f"{issue['issue_number']}  {issue['title']}  [{issue.get('relation_type') or 'solves'}]"
                )
                item.setData(Qt.UserRole, int(issue["id"]))
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked)
                issue_checks.addItem(item)
            main.addWidget(issue_checks)

        self._add_engineering_files_section(
            main,
            group_details.get("engineering_files") or [],
            "Vaulted Engineering Outputs",
        )
        self._add_engineering_files_section(
            main,
            group_details.get("validation_docs") or [],
            "Validation Documents",
        )

        # ── Action row ────────────────────────────────────────────────
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        warning_label = QLabel("Revert is irreversible")
        warning_label.setStyleSheet("color: #dc2626; font-size: 10px;")
        warning_label.setVisible(False)

        def show_warning():
            warning_label.setVisible(True)
            QTimer.singleShot(3000, lambda: warning_label.setVisible(False))

        def handle_validate():
            confirmed = []
            rejected = []
            for index in range(issue_checks.count()):
                item = issue_checks.item(index)
                target = confirmed if item.checkState() == Qt.Checked else rejected
                target.append(int(item.data(Qt.UserRole)))
            success = self.validate_commit(group, confirmed, rejected)
            if success:
                group['status'] = 'Validated'
                status_badge.setText("  VALIDATED  ")
                validate_btn.setEnabled(False)
                self.load_pending_commits()
                QMessageBox.information(self, "Validated",
                    f"Commit '{group.get('title')}' validated.")

        def handle_push():
            self.push_to_master(group)
            dialog.accept()

        def handle_revert():
            show_warning()
            success = self.revert_commit(group)
            if success:
                dialog.accept()

        revert_btn = QPushButton("Revert")
        revert_btn.setObjectName("danger")
        revert_btn.setCursor(Qt.PointingHandCursor)
        revert_btn.clicked.connect(handle_revert)

        validate_btn = QPushButton("Validate")
        validate_btn.setObjectName("neutral")
        validate_btn.setCursor(Qt.PointingHandCursor)
        validate_btn.clicked.connect(handle_validate)
        validate_btn.setEnabled(self.perm.can("validate"))

        push_btn = QPushButton("Push to Master")
        push_btn.setObjectName("primary")
        push_btn.setCursor(Qt.PointingHandCursor)
        push_btn.clicked.connect(handle_push)
        push_btn.setEnabled(self.perm.can("merge"))

        close_btn = QPushButton("Close")
        close_btn.setObjectName("neutral")
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(dialog.reject)

        btn_layout.addWidget(warning_label)
        btn_layout.addStretch()
        btn_layout.addWidget(close_btn)
        btn_layout.addWidget(revert_btn)
        btn_layout.addWidget(validate_btn)
        btn_layout.addWidget(push_btn)

        main.addLayout(btn_layout)

        scroll.setWidget(scroll_content)
        outer.addWidget(scroll)
        dialog.exec_()

    def _make_info_card(self, icon: str, label: str, text: str):
        card = QFrame()
        card.setStyleSheet("""
            QFrame {
                background: #ffffff; border: 1px solid #aeb5bf;
                border-top: 2px solid #65798c; border-radius: 0; padding: 0;
            }
        """)
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        card.setFixedHeight(44)
        cl = QHBoxLayout(card)
        cl.setContentsMargins(8, 4, 8, 4)
        cl.setSpacing(6)
        if icon:
            il = QLabel(icon)
            il.setStyleSheet("font-size: 10px; background: transparent; border: none;")
            cl.addWidget(il)
        tv = QVBoxLayout()
        tv.setSpacing(0)
        lbl = QLabel(label)
        lbl.setStyleSheet("font-size: 8px; color: #65707c; font-weight: 700; background: transparent; border: none;")
        tv.addWidget(lbl)
        val = QLabel(text)
        val.setStyleSheet("font-size: 9px; color: #2d3742; font-weight: 600; background: transparent; border: none;")
        val.setWordWrap(True)
        tv.addWidget(val)
        cl.addLayout(tv, 1)
        return card

    def _create_info_card(self, icon, text):
        return self._make_info_card(icon, "", text)

    # ═══════════════════════════════════════════════════════════════════
    #  PROJECT / DIRECTORY HELPERS
    # ═══════════════════════════════════════════════════════════════════

    def get_working_dir(self):
        project_working_dir = self.project_service.get_project_by_id(
            self.session.project_id
        )["working_directory"]
        return project_working_dir if project_working_dir else None

    def check_working_dir_existance(self):
        if not self.working_dir or not os.path.isdir(self.working_dir):
            QMessageBox.critical(
                self, "Error",
                f"Working directory is not set or does not exist.\n\"{self.working_dir}\"",
            )
            self.add_file_btn.setEnabled(False)
            self.commit_btn.setEnabled(False)
        else:
            self.add_file_btn.setEnabled(True)
            self.commit_btn.setEnabled(True)

    def _commit_directory_for_group(self, group):
        title = str(group.get("title") or "")
        commit_id = str(group.get("commit_id") or "")
        username = str(group.get("username") or "")
        candidates = []
        project_working_dir = ""
        try:
            group_project_id = group.get("project_id") or self.session.project_id
            project = self.project_service.get_project_by_id(int(group_project_id)) or {}
            project_working_dir = str(project.get("working_directory") or "").strip()
        except Exception:
            project_working_dir = str(self.working_dir or "").strip()
        commit_dir = str(group.get("commit_dir") or "").strip()
        if commit_dir:
            if not os.path.isabs(commit_dir) and project_working_dir:
                commit_dir = os.path.join(project_working_dir, commit_dir)
            normalized_commit_dir = os.path.normpath(commit_dir)
            leaf = os.path.basename(normalized_commit_dir).lower()
            if commit_id.lower() in leaf and "commits" in {
                part.lower() for part in normalized_commit_dir.split(os.sep)
            }:
                candidates.append(normalized_commit_dir)
        if username and title and commit_id:
            commits_dir = os.path.join(project_working_dir, "commits")
            candidates.append(os.path.join(commits_dir, username, f"{title}_{commit_id}"))
            safe_title = re.sub(r'[<>:"/\\|?*]', '_', title)
            if safe_title != title:
                candidates.append(os.path.join(commits_dir, username, f"{safe_title}_{commit_id}"))
        for path in candidates:
            path = os.path.normpath(path)
            if safe_exists(path):
                return path
        return os.path.normpath(candidates[0]) if candidates else ""

    def browse_commit_directory(self, group):
        path = self._commit_directory_for_group(group)
        if safe_exists(path):
            try:
                safe_startfile(path)
            except Exception as exc:
                QMessageBox.critical(
                    self, "Open Commit Folder",
                    f"Failed to open the exact commit directory:\n{path}\n\n{exc}",
                )
        else:
            QMessageBox.warning(self, "Not Found", f"Commit directory not found:\n{path}")

    # ═══════════════════════════════════════════════════════════════════
    #  FILE STAGING
    # ═══════════════════════════════════════════════════════════════════

    def add_files_from_drop(self, files):
        for fpath in files:
            fname = os.path.basename(fpath)
            ext = os.path.splitext(fname)[1].lower()
            part_entry = {
                "filename": fname,
                "path": fpath,
                "status": "Modified",
                "type": ext.replace(".", "").upper(),
                "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            self.add_to_uncommitted(part_entry)

    def add_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select Files", "",
            "Creo Files (*.prt.* *.asm.* *.drw.*);;All Files (*.*)",
        )
        for fpath in files:
            fname = os.path.basename(fpath)
            ext = os.path.splitext(fname)[1].lower()
            part_entry = {
                "filename": fname,
                "path": fpath,
                "status": "Modified",
                "type": ext.replace(".", "").upper(),
                "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            self.add_to_uncommitted(part_entry)

    def _checkout_workspace_cad(self, row: dict, workspace: dict) -> None:
        cad_document_id = row.get("cad_document_id")
        if cad_document_id is None:
            raise ValueError("This file is not mapped to a managed CAD Document.")
        document = self.bom_service.pdm_service.repo.get_cad_document(
            int(cad_document_id)
        ) or {}
        needs_cad_revision = (
            str(document.get("lifecycle_state") or "").upper() == "RELEASED"
        )
        if needs_cad_revision:
            answer = QMessageBox.question(
                self,
                "Revise CAD Document",
                "This CAD Document is Released. Create its next revision and check it out?",
                QMessageBox.Yes | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            if answer != QMessageBox.Yes:
                return

        try:
            associated_item_ids = list(
                self.bom_service.pdm_service.checkout_target_item_ids(
                    int(cad_document_id)
                ) or []
            )
        except Exception:
            associated_item_ids = []
        released_revision_codes = {}
        for item_id in sorted({int(value) for value in associated_item_ids}):
            details = self.bom_service.get_part_details(item_id) or {}
            if (
                str(details.get("revision_state") or details.get("lifecycle_state") or "").lower()
                == "released" and not details.get("locked")
            ):
                try:
                    suggested = self.bom_service.suggest_next_revision(item_id)
                except Exception:
                    suggested = ""
                revision_code, accepted = QInputDialog.getText(
                    self,
                    "Check Out Related Released Item",
                    (
                        f"{details.get('part_number') or details.get('name') or ('Item ' + str(item_id))} "
                        "is Released. Enter the next Item revision to create:"
                    ),
                    QLineEdit.Normal,
                    suggested,
                )
                if not accepted or not str(revision_code or "").strip():
                    return
                released_revision_codes[item_id] = str(revision_code).strip()

        descriptor = self.cad_workspace_service.checkout_descriptor(workspace["id"])
        if needs_cad_revision:
            self.bom_service.revise_pdm_cad_document(int(cad_document_id))
        self.bom_service.checkout_pdm_cad_document(
            int(cad_document_id),
            released_item_revision_codes=released_revision_codes,
            **descriptor,
        )
        try:
            self.cad_workspace_service.materialize_cad_document_package(
                workspace["id"],
                int(cad_document_id),
                preserve_existing=True,
                source_path=row.get("path"),
            )
        except Exception:
            try:
                self.bom_service.undo_checkout_pdm_cad_document(
                    int(cad_document_id), "Workspace materialization failed"
                )
            except Exception:
                pass
            raise

    def add_files_from_workspace(self):
        if not self.session.project_id or self.session.user_id is None:
            QMessageBox.warning(self, "CAD Workspace", "Select a project and sign in first.")
            return
        checkout_user_id = self._designer_id_for_commit() or int(self.session.user_id)
        dialog = WorkspaceStagingDialog(
            self.cad_workspace_service,
            int(self.session.project_id),
            int(checkout_user_id),
            self,
            checkout_callback=self._checkout_workspace_cad,
        )
        if dialog.exec_() != QDialog.Accepted:
            return
        selected = dialog.selected_rows()
        if not selected:
            QMessageBox.information(self, "CAD Workspace", "No modified CAD files were selected.")
            return
        for row in selected:
            logical = self.cad_workspace_service.logical_name(row["filename"])
            extension = os.path.splitext(logical)[1].lstrip(".").upper()
            self.add_to_uncommitted({
                "filename": row["filename"],
                "path": row["path"],
                "status": "Workspace Ready",
                "type": extension,
                "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "workspace_id": row["workspace_id"],
                "cad_document_id": row.get("cad_document_id"),
                "expected_sha256": row.get("candidate_sha256"),
            })

    def _validate_workspace_staging(self) -> None:
        for staged in self.uncommitted_parts or []:
            expected = str(staged.get("expected_sha256") or "").strip()
            if not expected:
                continue
            path = str(staged.get("path") or "")
            if not safe_isfile(path):
                raise ValueError(
                    f"Workspace staged file is missing: {os.path.basename(path)}"
                )
            current = self.cad_workspace_service._sha256(path)
            if current.casefold() != expected.casefold():
                raise ValueError(
                    f"{os.path.basename(path)} changed after it was selected from the "
                    "workspace. Review the workspace and stage it again."
                )

    def remove_part(self):
        selected = self.changes_list.currentRow()
        if selected >= 0:
            self.changes_list.takeItem(selected)
            self.uncommitted_parts.pop(selected)
            self._update_staged_count()

    def add_to_uncommitted(self, part_dict):
        if any(p['path'] == part_dict['path'] for p in self.uncommitted_parts):
            return
        self.uncommitted_parts.append(part_dict)
        icon = _file_icon(part_dict['filename'])
        display = f"{icon}  {part_dict['filename']}  —  {part_dict['type']}  —  {part_dict['path']}"
        self.changes_list.addItem(display)
        self._update_staged_count()

    # ═══════════════════════════════════════════════════════════════════
    #  COMMIT ACTION
    # ═══════════════════════════════════════════════════════════════════

    def _set_processing_state(self, busy: bool, message: str = "Processing..."):
        self._processing_action = bool(busy)
        try:
            if busy:
                self._close_active_modal_before_processing()
                self.processing_overlay.show_overlay(message)
                QApplication.setOverrideCursor(Qt.WaitCursor)
            else:
                self.processing_overlay.hide_overlay()
                QApplication.restoreOverrideCursor()
        except Exception:
            pass

        project_loaded = bool(self.session.project_id)
        can_commit = project_loaded and self.perm.can("commit")
        can_merge = project_loaded and self.perm.can("merge")
        for widget, enabled in (
            (getattr(self, "commit_btn", None), can_commit),
            (getattr(self, "add_file_btn", None), can_commit),
            (getattr(self, "workspace_file_btn", None), can_commit),
            (getattr(self, "remove_part_btn", None), can_commit),
            (getattr(self, "attach_affected_btn", None), can_commit and bool(self.uncommitted_parts)),
            (getattr(self, "push_dev_btn", None), can_merge),
            (getattr(self, "merge_master_btn", None), can_merge),
            (getattr(self, "revert_btn", None), project_loaded),
            (getattr(self, "snapshot_btn", None), project_loaded),
        ):
            try:
                if widget is not None:
                    widget.setEnabled((not busy) and enabled)
            except Exception:
                pass

        try:
            QApplication.processEvents()
        except Exception:
            pass

    def _close_active_modal_before_processing(self):
        try:
            modal = QApplication.activeModalWidget()
            if modal is not None and modal is not self and modal.window() is not self.window():
                if hasattr(modal, "reject"):
                    modal.reject()
                else:
                    modal.close()
        except Exception:
            pass

    def _run_processing_task(self, message: str, work_fn, success_fn=None, error_fn=None):
        if getattr(self, "_processing_action", False):
            return
        self._set_processing_state(True, message)
        thread = QThread(self)
        worker = _ProcessingWorker(work_fn)
        worker.moveToThread(thread)

        def cleanup():
            self._set_processing_state(False)
            self._processing_worker = None
            self._processing_thread = None

        def handle_success(result):
            post_popup = None
            try:
                if success_fn:
                    post_popup = success_fn(result)
            except Exception as exc:
                cleanup()
                QMessageBox.critical(self, "Error", str(exc))
                return
            if isinstance(post_popup, dict):
                self._run_processing_finish_steps(
                    list(post_popup.get("steps") or []),
                    post_popup.get("popup"),
                    cleanup,
                )
                return
            cleanup()
            if callable(post_popup):
                post_popup()

        def handle_error(exc):
            cleanup()
            if error_fn:
                error_fn(exc)
            else:
                QMessageBox.critical(self, "Error", str(exc))

        thread.started.connect(worker.run)
        worker.finished.connect(handle_success)
        worker.failed.connect(handle_error)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._processing_thread = thread
        self._processing_worker = worker
        thread.start()

    def _run_processing_finish_steps(self, steps, popup_fn, cleanup_fn):
        steps = list(steps or [])

        def run_next():
            if not steps:
                cleanup_fn()
                if callable(popup_fn):
                    popup_fn()
                return
            step = steps.pop(0)
            try:
                if isinstance(step, tuple) and step and step[0] == "async":
                    step[1](run_next)
                    return
                step()
            except Exception as exc:
                cleanup_fn()
                QMessageBox.critical(self, "Error", str(exc))
                return
            QApplication.processEvents()
            QTimer.singleShot(35, run_next)

        QTimer.singleShot(0, run_next)

    def commit_changes(self):
        if getattr(self, "_processing_action", False):
            return

        try:
            self._validate_workspace_staging()
        except ValueError as exc:
            QMessageBox.warning(self, "Workspace Changed", str(exc))
            return

        title = self.commit_title.text().strip()
        message = self.commit_message.content()
        designer = self.designed_by.currentText()
        step_compare_enabled = bool(self.step_compare_checkbox.isChecked())
        step_file_path = None
        issue_relation = self.issue_relation_combo.currentText() if hasattr(self, "issue_relation_combo") else "solves"
        jira_key = self.commit_jira_key.text().strip() if hasattr(self, "commit_jira_key") else ""
        jira_url = self.commit_jira_url.text().strip() if hasattr(self, "commit_jira_url") else ""

        if not title:
            QMessageBox.warning(self, "Validation", "Commit title is required.")
            self.commit_title.setFocus()
            return
        if not self.commit_message.has_content():
            QMessageBox.warning(self, "Validation", "Commit message is required.")
            self.commit_message.setFocus()
            return
        if not designer:
            QMessageBox.warning(self, "Validation", "Designer must be selected.")
            return
        if not self.uncommitted_parts:
            QMessageBox.warning(self, "Validation", "No files staged for commit.")
            return
        uncommitted_filenames = [p['path'] for p in self.uncommitted_parts]
        workspace_expectations = [
            {
                "path": staged.get("path"),
                "sha256": staged.get("expected_sha256"),
                "workspace_id": staged.get("workspace_id"),
                "cad_document_id": staged.get("cad_document_id"),
            }
            for staged in self.uncommitted_parts
            if staged.get("workspace_id")
        ]
        resolved_issue_ids = [
            int(self.resolved_issues_list.item(i).data(Qt.UserRole))
            for i in range(self.resolved_issues_list.count())
            if self.resolved_issues_list.item(i).checkState() == Qt.Checked
        ]
        resolved_issue_part_ids = set()
        for issue_id in resolved_issue_ids:
            try:
                issue = self.issue_service.get_issue(issue_id) or {}
                resolved_issue_part_ids.update(
                    int(part["id"]) for part in issue.get("parts", []) if part.get("id") is not None
                )
            except Exception:
                continue

        if self.commit_service:
            def do_commit():
                commit_result = self.commit_service.commit_file(
                    self.commits_dir,
                    uncommitted_filenames,
                    designer,
                    message,
                    title,
                    step_compare_enabled=step_compare_enabled,
                    step_file_path=step_file_path,
                    resolved_issue_ids=resolved_issue_ids,
                    resolved_issue_relation_type=issue_relation,
                    jira_key=jira_key,
                    jira_url=jira_url,
                    engineering_attachments=self._pending_engineering_attachments,
                    workspace_expectations=workspace_expectations,
                )
                affected_part_ids = set((commit_result or {}).get("affected_part_ids") or [])
                affected_part_ids.update(resolved_issue_part_ids)
                return {
                    "affected_part_ids": sorted(int(pid) for pid in affected_part_ids if pid is not None),
                    "history": self.commit_service.get_commit_history() or [],
                    "pending": self.commit_service.get_pending_commits_grouped(
                        self.session.project_id, self.session.user_id, self.is_designer
                    ) or [],
                }

            def after_commit(result):
                if False: QMessageBox.information(self, "Success",
                    "Changes were checked in successfully.")
                def update_status():
                    self.window().statusBar().showMessage("Changes committed successfully.")

                def clear_commit_form():
                    self.commit_title.clear()
                    self.commit_message.clear()
                    self.step_compare_checkbox.setChecked(False)
                    self.commit_jira_key.clear()
                    self.commit_jira_url.clear()
                    self.uncommitted_parts.clear()
                    self._pending_engineering_attachments.clear()
                    self.changes_list.clear()
                    self._update_staged_count()

                return {
                    "steps": [
                        update_status,
                        clear_commit_form,
                        lambda: self._refresh_bom_rows_for_parts((result or {}).get("affected_part_ids")),
                        lambda: self._refresh_issue_views(resolved_issue_part_ids),
                        lambda: self._set_commit_history_rows((result or {}).get("history") or []),
                        lambda: self._set_pending_commit_groups((result or {}).get("pending") or []),
                    ],
                    "popup": lambda: QMessageBox.information(
                        self, "Check In Complete", "Changes were checked in successfully."
                    ),
                }

            def on_commit_error(exc):
                error_str = str(exc)
                if error_str.startswith("cad_register_required:"):
                    missing_file = error_str.split(":", 1)[1]
                    self.ask_register_cad_document_action(missing_file)
                elif error_str.startswith("cad404:"):
                    missing_file = error_str.split(":", 1)[1]
                    self.ask_register_cad_document_action(missing_file)
                elif error_str.startswith("drw404:"):
                    missing_file = error_str.split(":", 1)[1]
                    self.ask_register_cad_document_action(missing_file)
                elif error_str.startswith(("Commit blocked:", "Error:", "STEP compare failed")):
                    QMessageBox.warning(self, "Commit Blocked", error_str)
                else:
                    QMessageBox.critical(self, "Error", f"Commit Failed: {error_str}")

            self._run_processing_task("Committing changes...", do_commit, after_commit, on_commit_error)

    def _create_issue_from_commit(self):
        try:
            main_window = self.window()
            issue_page = getattr(main_window, "issue_page", None)
            if issue_page and hasattr(issue_page, "create_issue"):
                issue_page.create_issue()
                return
        except Exception:
            pass
        QMessageBox.information(self, "Issue", "Open Issue Center to create a new issue.")

    def _clean_creo_file_name(self, filename: str) -> str:
        name = os.path.basename(str(filename or "").replace("\\", "/")).strip()
        match = re.match(r"^(.*\.(?:asm|prt|drw))\.\d+$", name, flags=re.IGNORECASE)
        return match.group(1) if match else name

    def _staged_path_for_file(self, file_name: str) -> str | None:
        target = os.path.basename(str(file_name or ""))
        for staged in self.uncommitted_parts or []:
            path = str(staged.get("path") or staged.get("filename") or "")
            if os.path.basename(path) == target:
                return path
        return None

    def ask_register_cad_document_action(self, file_name: str):
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Warning)
        msg.setWindowTitle("CAD Document Not Registered")
        msg.setText(f"No managed CAD Document exists for:\n{file_name}")
        msg.setInformativeText(
            "Register this staged Creo file as a CAD Document? "
            "No EBOM Item will be created automatically.")
        register_btn = msg.addButton("Register CAD Document", QMessageBox.AcceptRole)
        msg.addButton("Cancel", QMessageBox.RejectRole)
        msg.exec_()
        if msg.clickedButton() == register_btn:
            self.register_staged_cad_document(file_name)

    def register_staged_cad_document(self, file_name: str):
        source_path = self._staged_path_for_file(file_name)
        if not source_path:
            QMessageBox.warning(
                self,
                "Register CAD Document",
                f"The staged file is no longer available:\n{file_name}",
            )
            return
        clean_file = self._clean_creo_file_name(os.path.basename(source_path))
        stem, extension = os.path.splitext(clean_file)
        category = {
            ".asm": "ASSEMBLY",
            ".prt": "COMPONENT",
            ".drw": "DRAWING",
        }.get(extension.casefold(), "OTHER")
        if category == "OTHER":
            QMessageBox.warning(
                self,
                "Register CAD Document",
                "Only Creo .prt, .asm, and .drw files can be registered here.",
            )
            return

        drawing_owner_id = None
        if category == "DRAWING":
            models = [
                document
                for document in (self.bom_service.list_pdm_cad_documents() or [])
                if str(document.get("category") or "").upper()
                in {"ASSEMBLY", "COMPONENT"}
            ]
            if not models:
                QMessageBox.warning(
                    self,
                    "Register Drawing",
                    "Register the owning PRT/ASM CAD Document first. "
                    "A drawing cannot be registered as an isolated CAD Document.",
                )
                return
            labels = [
                f"{document.get('file_name') or document.get('name') or document.get('id')}"
                + (
                    f" - {document.get('name')}"
                    if document.get("name")
                    and str(document.get("name")).casefold()
                    != str(document.get("file_name") or "").casefold()
                    else ""
                )
                for document in models
            ]
            selected, accepted = QInputDialog.getItem(
                self,
                "Bind Drawing to Model",
                "Owning PRT/ASM CAD Document:",
                labels,
                0,
                False,
            )
            if not accepted:
                return
            drawing_owner_id = int(models[labels.index(selected)]["id"])

        name, accepted = QInputDialog.getText(
            self,
            "Register CAD Document",
            "CAD Document name:",
            text=stem,
        )
        if not accepted:
            return
        workspace = WorkspaceSelectionDialog.choose(
            self.cad_workspace_service,
            self,
            title="Registered CAD Checkout Workspace",
        )
        if not workspace:
            return
        try:
            cad_document_id = self.bom_service.create_pdm_cad_document(
                number=clean_file,
                name=str(name or stem).strip(),
                file_name=clean_file,
                category=category,
                authoring_application="CREO",
                drawing_owner_cad_document_id=drawing_owner_id,
            )
            descriptor = self.cad_workspace_service.checkout_descriptor(workspace["id"])
            self.bom_service.checkout_pdm_cad_document(
                int(cad_document_id), **descriptor
            )
            self.cad_workspace_service.materialize_cad_document(
                workspace["id"],
                int(cad_document_id),
                source_path=source_path,
            )
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Register CAD Document",
                f"Could not register/check out the CAD Document:\n{exc}",
            )
            return
        self.window().statusBar().showMessage(
            f"CAD Document {clean_file} registered and checked out.",
            6000,
        )
        self.commit_changes()

    def add_part(self, filename):
        dialog = PartDialog(self, filename=filename)
        if dialog.exec_() == QDialog.Accepted:
            part_data = dialog.get_data()
            if not part_data["name"]:
                QMessageBox.warning(self, "Item Validation",
                    "Name is required.")
                return
            try:
                added_part_id = self.bom_service.add_part(part_data)
                if not isinstance(added_part_id, int):
                    raise ValueError("The Item could not be created.")
                created = self.bom_service.get_part_details(added_part_id) or {}
                self.window().statusBar().showMessage(
                    f"Item {created.get('part_number') or added_part_id} created and associated.",
                    6000,
                )
                self.bom_service.checkout_part(int(added_part_id))
                self.commit_changes()
            except Exception as e:
                QMessageBox.critical(self, "Create Item",
                    f"Could not create Item: {str(e)}")

    # ═══════════════════════════════════════════════════════════════════
    #  WORKFLOW ACTIONS
    # ═══════════════════════════════════════════════════════════════════

    def validate_commit(self, group, confirmed_issue_ids=None, rejected_issue_ids=None):
        try:
            self.commit_service.validate_commit(
                group["commit_id"],
                project_id=group.get("project_id"),
                confirmed_issue_ids=confirmed_issue_ids,
                rejected_issue_ids=rejected_issue_ids,
            )
            self.load_pending_commits()
            self._refresh_issue_views()
            return True
        except Exception as e:
            QMessageBox.critical(self, "Error",
                f"Failed to validate commit:\n\n{str(e)}")
            return False

    def push_to_master(self, group):
        if getattr(self, "_processing_action", False):
            return
        if (group.get("project_id") and self.session.project_id and
                int(group.get("project_id")) != int(self.session.project_id)):
            QMessageBox.warning(
                self, "Cannot Push",
                "This commit belongs to a different project version.\n"
                "Switch to that project to push/merge it.",
            )
            return
        def do_push():
            merge_result = self.merge_service.excute_merge_by_commit_id(group["commit_id"])
            if isinstance(merge_result, dict):
                affected_part_ids = merge_result.get("affected_part_ids") or []
                affected_cad_document_ids = merge_result.get("affected_cad_document_ids") or []
            else:
                affected_part_ids = merge_result or []
                affected_cad_document_ids = []
            return {
                "affected_part_ids": affected_part_ids,
                "affected_cad_document_ids": affected_cad_document_ids,
                "history": self.commit_service.get_commit_history() or [],
                "pending": self.commit_service.get_pending_commits_grouped(
                    self.session.project_id, self.session.user_id, self.is_designer
                ) or [],
            }

        def after_push(result):
            result = result or {}
            return {
                "steps": [
                    lambda: self._refresh_pdm_rows_after_merge(
                        result.get("affected_part_ids"),
                        result.get("affected_cad_document_ids"),
                    ),
                    lambda: self._set_pending_commit_groups(result.get("pending") or []),
                    lambda: self._set_commit_history_rows(result.get("history") or []),
                ],
                "popup": None,
            }

        def on_push_error(exc):
            QMessageBox.warning(self, "Merge Blocked", str(exc))

        self._run_processing_task("Pushing commit to master...", do_push, after_push, on_push_error)

    def _restore_commit_group_from_history(self, group):
        if getattr(self, "_processing_action", False):
            return
        if not group or not group.get("commit_id"):
            QMessageBox.warning(self, "Restore Commit", "Invalid commit group.")
            return
        if not group.get("can_restore"):
            QMessageBox.information(
                self,
                "Restore Commit",
                "Restore is available only for commit groups already pushed to master.",
            )
            return

        commit_id = str(group.get("commit_id") or "")
        project_id = group.get("project_id")
        title = group.get("title") or group.get("filename") or commit_id
        details = self.commit_service.get_commit_group_details(commit_id, project_id)
        linked_issues = details.get("issues") or []

        issue_warning = ""
        if linked_issues:
            issue_lines = [
                f"- {i.get('issue_number') or i.get('id')}: {i.get('title') or ''} ({i.get('status') or ''})"
                for i in linked_issues[:8]
            ]
            if len(linked_issues) > 8:
                issue_lines.append(f"- ... and {len(linked_issues) - 8} more")
            issue_warning = (
                "\n\nThis commit is linked to issues. Closed linked issues will be reopened "
                "and every linked issue will receive a history entry:\n"
                + "\n".join(issue_lines)
            )

        confirm = QMessageBox.warning(
            self,
            "Restore Commit",
            f"Restore this commit group?\n\n{title}\nID: {commit_id}\n\n"
            "The app will return each affected BOM file to the previous approved version."
            f"{issue_warning}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        def do_restore():
            result = self.merge_service.restore_commit_group(
                commit_id,
                int(project_id) if project_id is not None else None,
                "Restored from Commit History.",
            )
            return {
                "restore": result,
                "history": self.commit_service.get_commit_history() or [],
                "pending": self.commit_service.get_pending_commits_grouped(
                    self.session.project_id, self.session.user_id, self.is_designer
                ) or [],
            }

        def after_restore(result):
            result = result or {}
            restore = result.get("restore") or {}
            restored_files = restore.get("restored_files") or []
            reopened = restore.get("reopened_issues") or []

            def popup():
                QMessageBox.information(
                    self,
                    "Commit Restored",
                    f"Commit {commit_id} was restored.\n\n"
                    f"Files restored: {len(restored_files)}\n"
                    f"Issues reopened: {len(reopened)}",
                )

            return {
                "steps": [
                    lambda: self._refresh_bom_rows_for_parts(restore.get("affected_part_ids")),
                    lambda: self._set_pending_commit_groups(result.get("pending") or []),
                    lambda: self._set_commit_history_rows(result.get("history") or []),
                    lambda: self._refresh_issue_views(restore.get("affected_part_ids")),
                ],
                "popup": popup,
            }

        def on_restore_error(exc):
            QMessageBox.warning(self, "Restore Blocked", str(exc))

        self._run_processing_task("Restoring commit group...", do_restore, after_restore, on_restore_error)

    def _refresh_bom_rows_for_parts(self, part_ids):
        if not part_ids:
            return
        try:
            main_window = self.window()
            bom_page = getattr(main_window, "bom_page", None)
            if bom_page and hasattr(bom_page, "refresh_parts_after_merge"):
                bom_page.refresh_parts_after_merge(part_ids)
        except Exception:
            pass

    def _refresh_pdm_rows_after_merge(self, part_ids=None, cad_document_ids=None):
        try:
            main_window = self.window()
            bom_page = getattr(main_window, "bom_page", None)
            if not bom_page:
                return
            if hasattr(bom_page, "refresh_after_pdm_merge"):
                bom_page.refresh_after_pdm_merge(
                    part_ids or [],
                    cad_document_ids or [],
                )
                return
            if part_ids and hasattr(bom_page, "refresh_parts_after_merge"):
                bom_page.refresh_parts_after_merge(part_ids)
        except Exception:
            pass

    def _refresh_issue_views(self, affected_part_ids=None):
        try:
            main_window = self.window()
            issue_page = getattr(main_window, "issue_page", None)
            bom_page = getattr(main_window, "bom_page", None)
            if issue_page:
                issue_page.refresh()
            if bom_page and hasattr(bom_page, "refresh_issue_indicators"):
                bom_page.refresh_issue_indicators(affected_part_ids)
        except Exception:
            pass

    def revert_commit(self, group=None):
        if not group:
            if not getattr(self, "selected_group", None):
                QMessageBox.warning(self, "Error",
                    "Select a commit group to revert.")
                return False
            group = self.selected_group

        commit_id = group.get("commit_id") or group.get("id")
        title = group.get("title", "Untitled")

        if not commit_id:
            QMessageBox.warning(self, "Error", "Invalid commit data.")
            return False

        confirm = QMessageBox.question(
            self, "Confirm Revert",
            f"Are you sure you want to revert:\n\n🧩 {title}\n(ID: {commit_id})?",
            QMessageBox.Yes | QMessageBox.No,
        )

        if confirm == QMessageBox.Yes:
            try:
                self.commit_service.revert_commit(
                    commit_id, project_id=group.get("project_id"))
                QMessageBox.information(self, "Reverted",
                    f"Commit '{title}' reverted successfully.")
                self.load_pending_commits()
                self.selected_group = None
                self.selected_card = None
                return True
            except Exception as e:
                QMessageBox.critical(self, "Error",
                    f"Failed to revert:\n\n{str(e)}")
        return False

    def create_snapshot(self):
        QMessageBox.critical(self, "Error", "Failed to create snapshot")

    def push_to_dev(self):
        if getattr(self, "_processing_action", False):
            return

        def do_push_dev():
            return None

        def after_push_dev(_result):
            return lambda: QMessageBox.critical(self, "Error", "Push failed")

        self._run_processing_task("Pushing to dev...", do_push_dev, after_push_dev)

    def merge_to_master(self):
        self.open_merge_dialog()

    def open_merge_dialog(self):
        dlg = MergeDialog(self.merge_service, self.merge_repo, self)
        dlg.exec_()

    def refresh(self):
        self.load_commit_history()
        self.load_pending_commits()
