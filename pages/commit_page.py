from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QHBoxLayout, QListWidget, QTextEdit,
    QPushButton, QMessageBox, QGroupBox, QFileDialog, QComboBox, QFormLayout,
    QDialog, QLineEdit, QGraphicsDropShadowEffect,
    QTableWidget, QTableWidgetItem, QHeaderView, QCheckBox, QMenu, QAction,
    QListWidgetItem, QScrollArea, QFrame, QSizePolicy, QSplitter,
    QApplication, QAbstractItemView,
)
from PyQt5.QtCore import (
    Qt, QPropertyAnimation, QEasingCurve, pyqtProperty, pyqtSignal, QTimer, QSize,
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
from core.services.ui_permission import UIPermissionHelper
from core.services.role_service import RoleService
from core.services.project_service import ProjectService
from core.session_manager import SessionManager
from core.services.issue_service import IssueService

from utils import is_creo_file, ensure_dir_exists


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
        self._placeholder_text = "Drag and drop files here\nSupported: .prt, .asm, .drw"

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
            painter.drawText(rect, Qt.AlignCenter, "Drop files here")

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

class _StatCard(QFrame):
    """Mini KPI card for the dashboard row."""

    def __init__(self, icon: str, value: str, label: str, accent: str, parent=None):
        super().__init__(parent)
        self.setFixedHeight(56)
        self.setMinimumWidth(90)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setStyleSheet(f"""
            _StatCard {{
                background: #ffffff;
                border: 1px solid #e5e7eb;
                border-left: 3px solid {accent};
                border-radius: 6px;
            }}
        """)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(8)
        shadow.setOffset(0, 1)
        shadow.setColor(QColor(0, 0, 0, 20))
        self.setGraphicsEffect(shadow)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(6)

        ic = QLabel(icon)
        ic.setStyleSheet("font-size: 18px; background: transparent; border: none;")
        lay.addWidget(ic)

        text_lay = QVBoxLayout()
        text_lay.setSpacing(0)
        self._val = QLabel(str(value))
        self._val.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {accent}; background: transparent; border: none;")
        text_lay.addWidget(self._val)
        self._lbl = QLabel(str(label))
        self._lbl.setStyleSheet("font-size: 9px; color: #6b7280; background: transparent; border: none;")
        text_lay.addWidget(self._lbl)
        lay.addLayout(text_lay)
        lay.addStretch()

    def set_value(self, v):
        self._val.setText(str(v))


# ═══════════════════════════════════════════════════════════════════════════
#  HISTORY TABLE
# ═══════════════════════════════════════════════════════════════════════════

class _HistoryTable(QTableWidget):
    """Rich commit-history table with coloured badges, relative times, STEP markers."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(7)
        self.setHorizontalHeaderLabels([
            "", "Status", "File", "Designer", "Date", "STEP", "Message",
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
        hh.setSectionResizeMode(5, QHeaderView.Fixed)
        hh.setSectionResizeMode(6, QHeaderView.Stretch)
        self.setColumnWidth(0, 8)
        self.setColumnWidth(5, 50)
        self.setStyleSheet("""
            QTableWidget {
                border: 1px solid #e5e7eb; border-radius: 8px;
                background: #ffffff; alternate-background-color: #fafbfc;
                gridline-color: transparent;
            }
            QTableWidget::item { padding: 5px 6px; border-bottom: 1px solid #f3f4f6; }
            QTableWidget::item:selected { background: #eff6ff; color: #111827; }
            QHeaderView::section {
                background: #f8f9fa; border: none; border-bottom: 2px solid #e5e7eb;
                padding: 6px 4px; font-weight: 700; font-size: 10px; color: #4b5563;
            }
        """)

    def populate(self, rows: list):
        self.setRowCount(0)
        self.setRowCount(len(rows))
        for i, c in enumerate(rows):
            sty = _status_style(c.get("status", ""))

            # col 0 — colour dot
            dot_item = QTableWidgetItem("")
            dot_item.setData(Qt.UserRole, dict(c))
            dot_item.setBackground(QBrush(QColor(sty["color"])))
            self.setItem(i, 0, dot_item)

            # col 1 — status badge
            badge = QLabel(f' {sty["icon"]} {sty["label"]} ')
            badge.setAlignment(Qt.AlignCenter)
            badge.setStyleSheet(f"""
                background: {sty['bg']}; color: {sty['color']};
                border: 1px solid {sty['color']}40; border-radius: 9px;
                padding: 1px 6px; font-size: 10px; font-weight: 600;
            """)
            container = QWidget()
            cl = QHBoxLayout(container)
            cl.setContentsMargins(2, 2, 2, 2)
            cl.addWidget(badge)
            cl.addStretch()
            self.setCellWidget(i, 1, container)

            # col 2 — file
            fname = str(c.get("filename", "") or "")
            ficon = _file_icon(fname)
            fi = QTableWidgetItem(f"{ficon} {fname}")
            fi.setToolTip(fname)
            fnt = fi.font()
            fnt.setBold(True)
            fi.setFont(fnt)
            self.setItem(i, 2, fi)

            # col 3 — designer
            self.setItem(i, 3, QTableWidgetItem(str(c.get("designed_by", "") or "")))

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
            self.setCellWidget(i, 4, w)

            # col 5 — STEP indicator
            step_status = str(c.get("step_diff_status", "") or "").strip().upper()
            step_txt = ""
            if step_status == "BASELINE":
                step_txt = "🟢"
            elif step_status == "COMPARED":
                step_txt = "🔵"
            elif step_status:
                step_txt = "⚪"
            si = QTableWidgetItem(step_txt)
            si.setTextAlignment(Qt.AlignCenter)
            si.setToolTip(f"STEP: {step_status}" if step_status else "No STEP")
            self.setItem(i, 5, si)

            # col 6 — message
            msg = str(c.get("message", "") or "")
            mi = QTableWidgetItem(msg)
            mi.setToolTip(msg)
            mi.setForeground(QBrush(QColor("#4b5563")))
            self.setItem(i, 6, mi)

            self.setRowHeight(i, 44)

    def get_data(self, row: int) -> dict:
        item = self.item(row, 0)
        return (item.data(Qt.UserRole) or {}) if item else {}


# ═══════════════════════════════════════════════════════════════════════════
#  PENDING COMMIT CARD
# ═══════════════════════════════════════════════════════════════════════════

class _PendingCard(QFrame):
    """Modern card for a pending commit group."""
    clicked = pyqtSignal(object)
    view_requested = pyqtSignal(object)
    browse_requested = pyqtSignal(object)

    def __init__(self, group: dict, parent=None):
        super().__init__(parent)
        self.group = group
        self._selected = False
        self.setCursor(Qt.PointingHandCursor)
        self.setFrameShape(QFrame.StyledPanel)
        self.setMinimumHeight(70)

        sty = _status_style(group.get("status", ""))
        self._accent = sty["color"]
        self._refresh_style()

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(10)
        shadow.setOffset(0, 2)
        shadow.setColor(QColor(0, 0, 0, 18))
        self.setGraphicsEffect(shadow)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(10)

        # Left: info
        info = QVBoxLayout()
        info.setSpacing(2)

        # Title row
        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        title_lbl = QLabel(group.get("title", "Untitled"))
        title_lbl.setStyleSheet("font-weight: bold; font-size: 12px; color: #111827; background: transparent; border: none;")
        title_row.addWidget(title_lbl)

        badge = QLabel(f' {sty["icon"]} {sty["label"]} ')
        badge.setStyleSheet(f"""
            background: {sty['bg']}; color: {sty['color']};
            border: 1px solid {sty['color']}40; border-radius: 8px;
            padding: 1px 6px; font-size: 9px; font-weight: 600;
        """)
        title_row.addWidget(badge)
        title_row.addStretch()
        info.addLayout(title_row)

        # Meta
        designer = group.get("username", "Unknown")
        num_files = len(group.get("parts", []))
        date_str = str(group.get("date", "") or "")
        rel = _relative_time(date_str)
        meta_parts = [f"👤 {designer}", f"📁 {num_files} file{'s' if num_files != 1 else ''}"]
        if rel:
            meta_parts.append(f"🕐 {rel}")
        meta = QLabel("   ".join(meta_parts))
        meta.setStyleSheet("font-size: 10px; color: #6b7280; background: transparent; border: none;")
        info.addWidget(meta)

        # File chips (first 3)
        if group.get("parts"):
            chips_text = ", ".join(group["parts"][:3])
            if num_files > 3:
                chips_text += f"  +{num_files - 3} more"
            files_lbl = QLabel(f"  {chips_text}")
            files_lbl.setStyleSheet("font-size: 9px; color: #9ca3af; background: transparent; border: none;")
            files_lbl.setWordWrap(True)
            info.addWidget(files_lbl)

        lay.addLayout(info, 1)

        # Right: action buttons
        btn_col = QVBoxLayout()
        btn_col.setSpacing(4)

        view_btn = QPushButton("👁 View")
        view_btn.setFixedSize(72, 26)
        view_btn.setCursor(Qt.PointingHandCursor)
        view_btn.setStyleSheet("""
            QPushButton {
                background: #eff6ff; color: #2563eb; border: 1px solid #bfdbfe;
                border-radius: 5px; font-size: 10px; font-weight: 600;
            }
            QPushButton:hover { background: #dbeafe; }
        """)
        view_btn.clicked.connect(lambda: self.view_requested.emit(self.group))
        btn_col.addWidget(view_btn)

        browse_btn = QPushButton("📂 Browse")
        browse_btn.setFixedSize(72, 26)
        browse_btn.setCursor(Qt.PointingHandCursor)
        browse_btn.setStyleSheet("""
            QPushButton {
                background: #f3f4f6; color: #374151; border: 1px solid #d1d5db;
                border-radius: 5px; font-size: 10px; font-weight: 600;
            }
            QPushButton:hover { background: #e5e7eb; }
        """)
        browse_btn.clicked.connect(lambda: self.browse_requested.emit(self.group))
        btn_col.addWidget(browse_btn)

        lay.addLayout(btn_col)

    def _refresh_style(self):
        bg = "#f0f7ff" if self._selected else "#ffffff"
        border_color = self._accent if self._selected else "#e5e7eb"
        border_width = "2px" if self._selected else "1px"
        self.setStyleSheet(f"""
            _PendingCard {{
                background: {bg};
                border: {border_width} solid {border_color};
                border-left: 4px solid {self._accent};
                border-radius: 8px;
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
        self.commit_service = CommitService()
        self.bom_service = bom_service
        self.project_service = ProjectService()
        self.issue_service = IssueService()

        self.merge_repo = MergeRepository()
        self.uncommitted_parts = []
        self.perm = UIPermissionHelper()
        self.role = RoleService()
        self.session = SessionManager()

        self.working_dir = None
        self.commits_dir = None
        self.pr_dir = None
        self.merge_service = None

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
        self.push_dev_btn.setEnabled(project_loaded and self.perm.can("merge"))
        self.merge_master_btn.setEnabled(project_loaded and self.perm.can("merge"))
        self.snapshot_btn.setEnabled(project_loaded)
        self.revert_btn.setEnabled(project_loaded)

        if self.session.project_id:
            self.load_commit_history()
            self.load_pending_commits()

    # ═══════════════════════════════════════════════════════════════════
    #  UI BUILD
    # ═══════════════════════════════════════════════════════════════════

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # ── Stats dashboard ──────────────────────────────────────────
        stats_row = QHBoxLayout()
        stats_row.setSpacing(6)
        self._stat_staged = _StatCard("📂", "0", "Staged Files", "#0078d7")
        self._stat_pending = _StatCard("⏳", "0", "Pending", "#ca8a04")
        self._stat_validated = _StatCard("✅", "0", "Validated", "#16a34a")
        self._stat_total = _StatCard("📊", "0", "Total Commits", "#374151")
        self._stat_step = _StatCard("🧊", "0", "STEP Commits", "#7c3aed")
        for w in (self._stat_staged, self._stat_pending, self._stat_validated,
                  self._stat_total, self._stat_step):
            stats_row.addWidget(w)
        root.addLayout(stats_row)

        # ── Main splitter (top: staging + pending | bottom: history) ─
        main_splitter = QSplitter(Qt.Vertical)
        main_splitter.setHandleWidth(3)

        # ── TOP AREA ─────────────────────────────────────────────────
        top_widget = QWidget()
        top_layout = QHBoxLayout(top_widget)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(8)

        # ── Left: staging + metadata ─────────────────────────────────
        left_splitter = QSplitter(Qt.Vertical)

        # Staging area
        staging_group = QGroupBox("📂 Staging Area")
        staging_group.setStyleSheet("""
            QGroupBox {
                font-weight: bold; border: 1px solid #d1d5db;
                border-radius: 8px; margin-top: 8px; padding-top: 14px;
                background: #fafbfc;
            }
            QGroupBox::title {
                subcontrol-origin: margin; left: 12px; padding: 0 6px;
                color: #374151;
            }
        """)
        staging_lay = QVBoxLayout(staging_group)
        staging_lay.setSpacing(6)

        self.changes_list = DropListWidget(callback=self.add_files_from_drop)
        self.changes_list.setStyleSheet("""
            QListWidget {
                border: 2px dashed #d1d5db; border-radius: 8px;
                background: #ffffff; padding: 4px;
                font-size: 11px;
            }
            QListWidget::item { padding: 4px 6px; border-bottom: 1px solid #f3f4f6; }
            QListWidget::item:selected { background: #eff6ff; color: #1e40af; }
        """)
        self.changes_list.setMinimumHeight(100)
        staging_lay.addWidget(self.changes_list)

        # File action buttons
        file_btns = QHBoxLayout()
        file_btns.setSpacing(6)

        self.add_file_btn = QPushButton("➕ Add Files")
        self.add_file_btn.setObjectName("primary")
        self.add_file_btn.setCursor(Qt.PointingHandCursor)
        self.add_file_btn.clicked.connect(self.add_files)
        file_btns.addWidget(self.add_file_btn)

        self.remove_part_btn = QPushButton("🗑 Remove")
        self.remove_part_btn.setObjectName("neutral")
        self.remove_part_btn.setCursor(Qt.PointingHandCursor)
        self.remove_part_btn.clicked.connect(self.remove_part)
        file_btns.addWidget(self.remove_part_btn)

        clear_all_btn = QPushButton("✖ Clear All")
        clear_all_btn.setObjectName("neutral")
        clear_all_btn.setCursor(Qt.PointingHandCursor)
        clear_all_btn.clicked.connect(self._clear_staging)
        file_btns.addWidget(clear_all_btn)

        file_btns.addStretch()
        self._staged_count_lbl = QLabel("0 files staged")
        self._staged_count_lbl.setStyleSheet("font-size: 10px; color: #6b7280;")
        file_btns.addWidget(self._staged_count_lbl)

        staging_lay.addLayout(file_btns)
        left_splitter.addWidget(staging_group)

        # Commit metadata
        meta_group = QGroupBox("📝 Commit Details")
        meta_group.setStyleSheet("""
            QGroupBox {
                font-weight: bold; border: 1px solid #d1d5db;
                border-radius: 8px; margin-top: 8px; padding-top: 14px;
                background: #fafbfc;
            }
            QGroupBox::title {
                subcontrol-origin: margin; left: 12px; padding: 0 6px;
                color: #374151;
            }
        """)
        meta_lay = QFormLayout(meta_group)
        meta_lay.setSpacing(6)
        meta_lay.setContentsMargins(12, 16, 12, 8)

        self.commit_title = QLineEdit()
        self.commit_title.setPlaceholderText("Brief summary of changes…")
        self.commit_title.setMaxLength(120)
        self._title_counter = QLabel("0/120")
        self._title_counter.setStyleSheet("font-size: 9px; color: #9ca3af;")
        self.commit_title.textChanged.connect(
            lambda t: self._title_counter.setText(f"{len(t)}/120")
        )
        title_row = QHBoxLayout()
        title_row.addWidget(self.commit_title, 1)
        title_row.addWidget(self._title_counter)
        meta_lay.addRow("Title:", title_row)

        self.designed_by = QComboBox()
        if self.is_designer:
            self.designed_by.addItem(self.username)
        else:
            self.designed_by.addItems(self.usernames)
        meta_lay.addRow("Designer:", self.designed_by)

        self.commit_message = QTextEdit()
        self.commit_message.setPlaceholderText(
            "Describe your changes in detail…\n\n"
            "• What was modified?\n"
            "• Why was it changed?\n"
            "• Any related part numbers?"
        )
        self.commit_message.setMaximumHeight(90)
        self.commit_message.setStyleSheet("""
            QTextEdit { border: 1px solid #d1d5db; border-radius: 6px; padding: 6px; font-size: 11px; }
            QTextEdit:focus { border-color: #0078d7; }
        """)
        meta_lay.addRow("Message:", self.commit_message)

        # STEP compare row
        step_frame = QFrame()
        step_frame.setStyleSheet("QFrame { background: transparent; border: none; }")
        step_lay = QVBoxLayout(step_frame)
        step_lay.setContentsMargins(0, 0, 0, 0)
        step_lay.setSpacing(4)

        self.step_compare_checkbox = QCheckBox("🧊 Process STEP comparison for this commit")
        self.step_compare_checkbox.setChecked(False)
        self.step_compare_checkbox.toggled.connect(self._toggle_step_controls)
        step_lay.addWidget(self.step_compare_checkbox)

        step_file_row = QHBoxLayout()
        self.step_file_input = QLineEdit()
        self.step_file_input.setPlaceholderText("Select STEP file (*.step, *.stp)…")
        self.step_file_input.setEnabled(False)
        self.step_browse_btn = QPushButton("Browse")
        self.step_browse_btn.setObjectName("neutral")
        self.step_browse_btn.setEnabled(False)
        self.step_browse_btn.setFixedWidth(70)
        self.step_browse_btn.clicked.connect(self._browse_step_file)
        step_file_row.addWidget(self.step_file_input, 1)
        step_file_row.addWidget(self.step_browse_btn)
        step_lay.addLayout(step_file_row)

        meta_lay.addRow("STEP:", step_frame)

        self.resolved_issues_list = QListWidget()
        self.resolved_issues_list.setMaximumHeight(115)
        self.resolved_issues_list.setAlternatingRowColors(True)
        self.resolved_issues_list.setToolTip(
            "Select active engineering issues addressed by this commit."
        )
        meta_lay.addRow("Resolved Issues:", self.resolved_issues_list)
        left_splitter.addWidget(meta_group)

        top_layout.addWidget(left_splitter, 2)

        # ── Right: pending commits ───────────────────────────────────
        pending_group = QGroupBox("⏳ Pending Commits")
        pending_group.setStyleSheet("""
            QGroupBox {
                font-weight: bold; border: 1px solid #d1d5db;
                border-radius: 8px; margin-top: 8px; padding-top: 14px;
                background: #fafbfc;
            }
            QGroupBox::title {
                subcontrol-origin: margin; left: 12px; padding: 0 6px;
                color: #374151;
            }
        """)
        pending_lay = QVBoxLayout(pending_group)
        pending_lay.setSpacing(4)

        pending_scroll = QScrollArea()
        pending_scroll.setWidgetResizable(True)
        pending_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        pending_content = QWidget()
        pending_content.setStyleSheet("background: transparent;")
        self.pending_container_layout = QVBoxLayout(pending_content)
        self.pending_container_layout.setAlignment(Qt.AlignTop)
        self.pending_container_layout.setSpacing(6)
        pending_scroll.setWidget(pending_content)
        pending_lay.addWidget(pending_scroll, 1)

        self.revert_btn = QPushButton("🔄 Revert Selected")
        self.revert_btn.setObjectName("danger")
        self.revert_btn.setCursor(Qt.PointingHandCursor)
        self.revert_btn.clicked.connect(self.revert_commit)
        pending_lay.addWidget(self.revert_btn)

        top_layout.addWidget(pending_group, 1)
        main_splitter.addWidget(top_widget)

        # ── Action buttons row ───────────────────────────────────────
        actions_frame = QFrame()
        actions_frame.setStyleSheet("""
            QFrame {
                background: #f8f9fa; border: 1px solid #e5e7eb;
                border-radius: 8px; padding: 4px;
            }
        """)
        actions_lay = QHBoxLayout(actions_frame)
        actions_lay.setContentsMargins(8, 4, 8, 4)
        actions_lay.setSpacing(8)

        self.commit_btn = QPushButton("📦 Commit")
        self.commit_btn.setObjectName("primary")
        self.commit_btn.setCursor(Qt.PointingHandCursor)
        self.commit_btn.setFixedHeight(32)
        self.commit_btn.clicked.connect(self.commit_changes)
        actions_lay.addWidget(self.commit_btn)

        self.snapshot_btn = QPushButton("📸 Snapshot")
        self.snapshot_btn.setObjectName("neutral")
        self.snapshot_btn.setCursor(Qt.PointingHandCursor)
        self.snapshot_btn.setFixedHeight(32)
        self.snapshot_btn.clicked.connect(self.create_snapshot)
        actions_lay.addWidget(self.snapshot_btn)

        actions_lay.addStretch()

        self.push_dev_btn = QPushButton("⬆ Push to Dev")
        self.push_dev_btn.setObjectName("neutral")
        self.push_dev_btn.setCursor(Qt.PointingHandCursor)
        self.push_dev_btn.setFixedHeight(32)
        self.push_dev_btn.clicked.connect(self.push_to_dev)
        actions_lay.addWidget(self.push_dev_btn)

        self.merge_master_btn = QPushButton("🔀 Merge to Master")
        self.merge_master_btn.setObjectName("neutral")
        self.merge_master_btn.setCursor(Qt.PointingHandCursor)
        self.merge_master_btn.setFixedHeight(32)
        self.merge_master_btn.clicked.connect(self.merge_to_master)
        actions_lay.addWidget(self.merge_master_btn)

        root.addWidget(actions_frame)

        # ── BOTTOM AREA: History ─────────────────────────────────────
        history_group = QGroupBox("📋 Commit History")
        history_group.setStyleSheet("""
            QGroupBox {
                font-weight: bold; border: 1px solid #d1d5db;
                border-radius: 8px; margin-top: 8px; padding-top: 14px;
                background: #fafbfc;
            }
            QGroupBox::title {
                subcontrol-origin: margin; left: 12px; padding: 0 6px;
                color: #374151;
            }
        """)
        history_lay = QVBoxLayout(history_group)
        history_lay.setSpacing(6)

        # Filter row
        filter_row = QHBoxLayout()
        filter_row.setSpacing(6)

        self.history_search = QLineEdit()
        self.history_search.setPlaceholderText("🔍  Search (file, user, message, status)…")
        self.history_search.setStyleSheet("""
            QLineEdit {
                border: 1px solid #d1d5db; border-radius: 6px;
                padding: 5px 8px; font-size: 11px; background: #ffffff;
            }
            QLineEdit:focus { border-color: #0078d7; }
        """)
        filter_row.addWidget(self.history_search, 1)

        self.history_status_filter = QComboBox()
        self.history_status_filter.addItems([
            "All", "Pending", "Validated", "Approved", "Pushed",
            "Integrated", "Released", "Reverted", "WIP",
        ])
        self.history_status_filter.setFixedWidth(110)
        filter_row.addWidget(self.history_status_filter)

        self.history_clear_btn = QPushButton("✖")
        self.history_clear_btn.setFixedSize(28, 28)
        self.history_clear_btn.setToolTip("Clear filters")
        self.history_clear_btn.setCursor(Qt.PointingHandCursor)
        self.history_clear_btn.setStyleSheet("""
            QPushButton { background: #f3f4f6; border: 1px solid #d1d5db; border-radius: 6px; font-size: 12px; }
            QPushButton:hover { background: #e5e7eb; }
        """)
        filter_row.addWidget(self.history_clear_btn)

        self._hist_count_lbl = QLabel("")
        self._hist_count_lbl.setStyleSheet("font-size: 10px; color: #9ca3af;")
        filter_row.addWidget(self._hist_count_lbl)

        history_lay.addLayout(filter_row)

        # Table
        self.history_table = _HistoryTable()
        self.history_table.itemDoubleClicked.connect(self._on_history_double_click)
        self.history_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.history_table.customContextMenuRequested.connect(self._show_history_context_menu)
        history_lay.addWidget(self.history_table)

        main_splitter.addWidget(history_group)
        main_splitter.setStretchFactor(0, 3)
        main_splitter.setStretchFactor(1, 2)

        root.addWidget(main_splitter, 1)

        # ── Search debounce ──────────────────────────────────────────
        self._history_search_timer = QTimer(self)
        self._history_search_timer.setSingleShot(True)
        self._history_search_timer.setInterval(250)
        self.history_search.textChanged.connect(lambda _: self._history_search_timer.start())
        self._history_search_timer.timeout.connect(self._apply_history_filter)
        self.history_status_filter.currentTextChanged.connect(lambda _: self._apply_history_filter())
        self.history_clear_btn.clicked.connect(self._clear_history_filter)

        # Track selected pending card
        self.selected_card = None
        self.selected_group = None
        self._pending_cards = []

    # ═══════════════════════════════════════════════════════════════════
    #  HELPERS
    # ═══════════════════════════════════════════════════════════════════

    def _toggle_step_controls(self, enabled):
        self.step_file_input.setEnabled(bool(enabled))
        self.step_browse_btn.setEnabled(bool(enabled))
        if not enabled:
            self.step_file_input.clear()

    def _browse_step_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select STEP file", "",
            "STEP Files (*.step *.stp);;All Files (*.*)",
        )
        if path:
            self.step_file_input.setText(path)

    def _clear_staging(self):
        self.uncommitted_parts.clear()
        self.changes_list.clear()
        self._update_staged_count()

    def _update_staged_count(self):
        n = len(self.uncommitted_parts)
        self._staged_count_lbl.setText(f"{n} file{'s' if n != 1 else ''} staged")
        self._stat_staged.set_value(str(n))
        self._refresh_resolved_issues()

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

        # Clear old cards
        for i in reversed(range(self.pending_container_layout.count())):
            w = self.pending_container_layout.itemAt(i).widget()
            if w:
                w.deleteLater()

        self.selected_card = None
        self.selected_group = None
        self._pending_cards = []

        for group in commits:
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

        filtered = []
        for c in self._history_cache:
            st = (c.get("status") or "").strip().lower()
            if status_filter != "all" and st != status_filter:
                continue
            if q:
                hay = " ".join(str(c.get(k, "")) for k in
                               ("status", "filename", "date", "designed_by",
                                "checked_by", "message", "commit_id")).lower()
                if q not in hay:
                    continue
            filtered.append(c)

        self.history_table.populate(filtered)
        total = len(self._history_cache)
        shown = len(filtered)
        if total == shown:
            self._hist_count_lbl.setText(f"{total} commits")
        else:
            self._hist_count_lbl.setText(f"{shown} / {total}")

    def _clear_history_filter(self):
        self.history_search.setText("")
        self.history_status_filter.setCurrentText("All")
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
        sty = _status_style(data.get("status", ""))
        dlg = QDialog(self)
        dlg.setWindowTitle(f"{sty['icon']}  Commit Details — {data.get('filename', '')}")
        dlg.setMinimumSize(760, 480)
        layout = QVBoxLayout(dlg)
        layout.setSpacing(10)

        # Header
        header = QFrame()
        header.setStyleSheet(f"""
            QFrame {{
                background: {sty['bg']}; border: 1px solid {sty['color']}40;
                border-left: 5px solid {sty['color']}; border-radius: 8px;
            }}
        """)
        hl = QVBoxLayout(header)
        hl.setSpacing(4)

        badge = QLabel(f'  {sty["icon"]} {sty["label"]}  ')
        badge.setStyleSheet(f"""
            background: {sty['color']}; color: #ffffff;
            border-radius: 10px; padding: 2px 12px;
            font-size: 11px; font-weight: bold;
        """)
        badge.setFixedWidth(badge.sizeHint().width() + 16)
        hl.addWidget(badge)

        fname = str(data.get("filename", ""))
        hl.addWidget(QLabel(f"<b style='font-size:13px'>{_file_icon(fname)} {fname}</b>"))

        meta_parts = []
        if data.get("designed_by"):
            meta_parts.append(f"👤 {data['designed_by']}")
        if data.get("checked_by") and data["checked_by"] != "Unknown":
            meta_parts.append(f"🔍 {data['checked_by']}")
        if data.get("date"):
            rel = _relative_time(str(data["date"]))
            meta_parts.append(f"📅 {str(data['date'])[:19]}  ({rel})")
        if data.get("commit_id"):
            meta_parts.append(f"🏷 {data['commit_id'][:16]}")
        if meta_parts:
            m = QLabel("    ".join(meta_parts))
            m.setStyleSheet("color: #4b5563; font-size: 11px; background: transparent; border: none;")
            m.setWordWrap(True)
            hl.addWidget(m)

        layout.addWidget(header)

        # Details grid
        grid = QTableWidget()
        grid.setColumnCount(2)
        grid.setHorizontalHeaderLabels(["Field", "Value"])
        grid.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        grid.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        grid.setEditTriggers(QAbstractItemView.NoEditTriggers)
        grid.verticalHeader().setVisible(False)
        grid.setAlternatingRowColors(True)
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

        layout.addWidget(grid)

        # Action buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        if step_status:
            sp = str(data.get("step_file_path") or "").strip()
            if sp:
                open_s = QPushButton("🔬 Open STEP")
                open_s.setObjectName("neutral")
                open_s.clicked.connect(lambda _c=False, d=data: self._open_step_file_in_viewer(d))
                btn_row.addWidget(open_s)
            if step_status.upper() == "COMPARED":
                diff_b = QPushButton("🔍 STEP Diff")
                diff_b.setObjectName("primary")
                diff_b.clicked.connect(lambda _c=False, d=data: self._show_step_diff_for_commit_history(d))
                btn_row.addWidget(diff_b)

        copy_btn = QPushButton("📋 Copy Info")
        copy_btn.setObjectName("neutral")
        copy_btn.clicked.connect(lambda _c=False, d=data: self._copy_commit_to_clipboard(d))
        btn_row.addWidget(copy_btn)

        btn_row.addStretch()
        close = QPushButton("Close")
        close.setObjectName("neutral")
        close.clicked.connect(dlg.accept)
        btn_row.addWidget(close)
        layout.addLayout(btn_row)

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
            QMenu { background: #fff; border: 1px solid #d1d5db; border-radius: 8px; padding: 4px 0; }
            QMenu::item { padding: 6px 16px; font-size: 11px; }
            QMenu::item:selected { background: #eff6ff; color: #0078d7; }
            QMenu::separator { height: 1px; background: #e5e7eb; margin: 4px 8px; }
        """)

        det = menu.addAction("📋  View Full Details")
        det.triggered.connect(lambda _c, d=data: self._show_history_details_dialog(d))

        cpy = menu.addAction("📝  Copy Info to Clipboard")
        cpy.triggered.connect(lambda _c, d=data: self._copy_commit_to_clipboard(d))

        step_path = str(data.get("step_file_path") or "").strip()
        if step_path:
            menu.addSeparator()
            sa = menu.addAction("🔬  Open STEP in 3D Viewer")
            sa.triggered.connect(lambda _c, d=data: self._open_step_file_in_viewer(d))

        step_status = str(data.get("step_diff_status") or "").strip().upper()
        if step_status == "COMPARED":
            da = menu.addAction("🔍  Show STEP Diff Zones")
            da.triggered.connect(lambda _c, d=data: self._show_step_diff_for_commit_history(d))

        cid = str(data.get("commit_id") or "")
        if cid:
            menu.addSeparator()
            ca = menu.addAction(f"🏷  Copy Commit ID: {cid[:12]}…")
            ca.triggered.connect(lambda _c, _v=cid: QApplication.clipboard().setText(_v))

        menu.exec_(self.history_table.viewport().mapToGlobal(position))

    def _show_history_item_details(self, item):
        data = item.data(Qt.UserRole) or {}
        if data:
            self._show_history_details_dialog(data)

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

    def show_commit_details(self, group):
        screen = QApplication.primaryScreen().availableGeometry()
        max_w = int(screen.width() * 0.85)
        max_h = int(screen.height() * 0.85)

        sty = _status_style(group.get("status", ""))
        dialog = QDialog(self)
        dialog.setWindowTitle(f"{sty['icon']}  Review — {group.get('title', 'Commit')}")
        dialog.setMinimumSize(700, 500)
        dialog.setMaximumSize(max_w, max_h)
        dialog.resize(int(max_w * 0.75), int(max_h * 0.75))
        dialog.setStyleSheet("""
            QDialog { background: #F8FAFC; font-family: 'Segoe UI', sans-serif; }
            QLabel { background: transparent; border: none; }
        """)

        outer = QVBoxLayout(dialog)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(10)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        scroll_content = QWidget()
        main = QVBoxLayout(scroll_content)
        main.setSpacing(14)
        main.setContentsMargins(16, 16, 16, 16)

        # ── Header ────────────────────────────────────────────────────
        header = QFrame()
        header.setStyleSheet(f"""
            QFrame {{
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 {sty['color']}, stop:1 {sty['color']}cc);
                border-radius: 10px;
            }}
        """)
        header.setFixedHeight(72)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(16, 10, 16, 10)

        left_info = QVBoxLayout()
        designer_name = group.get('username', 'Unknown')
        left_info.addWidget(QLabel(
            f"<span style='color: rgba(255,255,255,0.7); font-size: 10px;'>Designer</span>"
            f"  <b style='color: white; font-size: 12px;'>{designer_name}</b>"
        ))
        cid = group.get('commit_id', '')
        left_info.addWidget(QLabel(
            f"<span style='color: rgba(255,255,255,0.7); font-size: 10px;'>Commit</span>"
            f"  <code style='color: white; font-size: 11px;'>{cid[:14]}</code>"
        ))
        hl.addLayout(left_info, 1)

        status_text = group.get("status", "Pending")
        status_badge = QLabel(f"  {sty['icon']}  {status_text}  ")
        status_badge.setStyleSheet("""
            background: rgba(255,255,255,0.25); color: white;
            border-radius: 12px; padding: 4px 12px;
            font-weight: bold; font-size: 12px;
        """)
        hl.addWidget(status_badge)
        main.addWidget(header)

        # ── Info cards ────────────────────────────────────────────────
        cards_row = QHBoxLayout()
        cards_row.setSpacing(8)
        cards_row.addWidget(self._make_info_card("📝", "Title", group.get('title', '')))
        cards_row.addWidget(self._make_info_card("📅", "Date",
                                                 str(group.get('date', ''))[:19]))
        num_parts = len(group.get("parts", []))
        cards_row.addWidget(self._make_info_card("🔧", "Parts",
                                                 f"{num_parts} file{'s' if num_parts != 1 else ''}"))
        main.addLayout(cards_row)

        # ── Parts list ────────────────────────────────────────────────
        main.addWidget(QLabel("<b style='font-size: 11px; color: #374151;'>📦 Committed Parts</b>"))
        parts_list = QListWidget()
        parts_list.setStyleSheet("""
            QListWidget { border: 1px solid #e5e7eb; border-radius: 8px; background: #fff; }
            QListWidget::item { padding: 6px 8px; border-bottom: 1px solid #f3f4f6; }
            QListWidget::item:selected { background: #eff6ff; }
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
            main.addWidget(QLabel("<b style='font-size: 11px; color: #374151;'>Issues claimed as resolved</b>"))
            issue_checks.setMaximumHeight(160)
            for issue in related_issues:
                item = QListWidgetItem(
                    f"{issue['issue_number']}  {issue['title']}  [{issue['priority']}]"
                )
                item.setData(Qt.UserRole, int(issue["id"]))
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked)
                issue_checks.addItem(item)
            main.addWidget(issue_checks)

        # ── Action row ────────────────────────────────────────────────
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        warning_label = QLabel("⚠️ Reverting cannot be undone")
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
                new_sty = _status_style('Validated')
                status_badge.setText(f"  {new_sty['icon']}  Validated  ")
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

        revert_btn = QPushButton("🔄 Revert")
        revert_btn.setObjectName("danger")
        revert_btn.setCursor(Qt.PointingHandCursor)
        revert_btn.clicked.connect(handle_revert)

        validate_btn = QPushButton("✅ Validate")
        validate_btn.setObjectName("neutral")
        validate_btn.setCursor(Qt.PointingHandCursor)
        validate_btn.clicked.connect(handle_validate)
        validate_btn.setEnabled(self.perm.can("validate"))

        push_btn = QPushButton("🚀 Push to Master")
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
                background: #ffffff; border: 1px solid #e5e7eb;
                border-radius: 8px; padding: 0;
            }
        """)
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        card.setFixedHeight(56)
        cl = QHBoxLayout(card)
        cl.setContentsMargins(10, 6, 10, 6)
        cl.setSpacing(8)
        il = QLabel(icon)
        il.setStyleSheet("font-size: 18px; background: transparent; border: none;")
        cl.addWidget(il)
        tv = QVBoxLayout()
        tv.setSpacing(0)
        lbl = QLabel(label)
        lbl.setStyleSheet("font-size: 9px; color: #9ca3af; font-weight: 600; background: transparent; border: none;")
        tv.addWidget(lbl)
        val = QLabel(text)
        val.setStyleSheet("font-size: 11px; color: #374151; font-weight: 500; background: transparent; border: none;")
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

    def browse_commit_directory(self, group):
        safe_title = re.sub(r'[<>:"/\\|?*]', '_', group.get("title", ""))
        folder_name = f"{safe_title}_{group.get('commit_id', '')}"
        path = os.path.join(self.commits_dir, group.get("username", ""), folder_name)
        path = os.path.normpath(path)
        if os.path.exists(path):
            subprocess.Popen(["explorer", path])
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

        # Auto-detect STEP: if user drops a .step/.stp, offer to enable STEP compare
        fn_lower = (part_dict.get("filename") or "").lower()
        if (".step" in fn_lower or ".stp" in fn_lower) and not self.step_compare_checkbox.isChecked():
            self.step_compare_checkbox.setChecked(True)
            self.step_file_input.setText(part_dict["path"])

    # ═══════════════════════════════════════════════════════════════════
    #  COMMIT ACTION
    # ═══════════════════════════════════════════════════════════════════

    def commit_changes(self):
        title = self.commit_title.text().strip()
        message = self.commit_message.toPlainText().strip()
        designer = self.designed_by.currentText()
        step_compare_enabled = bool(self.step_compare_checkbox.isChecked())
        step_file_path = (self.step_file_input.text() or "").strip()

        if not title:
            QMessageBox.warning(self, "Validation", "Commit title is required.")
            self.commit_title.setFocus()
            return
        if not message:
            QMessageBox.warning(self, "Validation", "Commit message is required.")
            self.commit_message.setFocus()
            return
        if not designer:
            QMessageBox.warning(self, "Validation", "Designer must be selected.")
            return
        if not self.uncommitted_parts:
            QMessageBox.warning(self, "Validation", "No files staged for commit.")
            return
        if step_compare_enabled and not step_file_path:
            QMessageBox.warning(self, "Validation",
                "Select a STEP file or uncheck STEP compare.")
            return

        uncommitted_filenames = [p['path'] for p in self.uncommitted_parts]
        resolved_issue_ids = [
            int(self.resolved_issues_list.item(i).data(Qt.UserRole))
            for i in range(self.resolved_issues_list.count())
            if self.resolved_issues_list.item(i).checkState() == Qt.Checked
        ]

        if self.commit_service:
            try:
                self.commit_service.commit_file(
                    self.commits_dir,
                    uncommitted_filenames,
                    designer,
                    message,
                    title,
                    step_compare_enabled=step_compare_enabled,
                    step_file_path=step_file_path,
                    resolved_issue_ids=resolved_issue_ids,
                )
                QMessageBox.information(self, "Success",
                    "✅ Changes committed successfully!")
                parent_window = self.window()
                parent_window.statusBar().showMessage(
                    "Changes committed successfully.")
                self.commit_title.clear()
                self.commit_message.clear()
                self.step_compare_checkbox.setChecked(False)
                self.step_file_input.clear()
                self.uncommitted_parts.clear()
                self.changes_list.clear()
                self._update_staged_count()
                self._refresh_issue_views()
                self.refresh()
            except ValueError as e:
                error_str = str(e)
                if error_str.startswith("cad404:"):
                    missing_file = error_str.split(":", 1)[1]
                    self.ask_new_part_action(missing_file)
                elif error_str.startswith("drw404:"):
                    missing_file = error_str.split(":", 1)[1]
                    QMessageBox.warning(self, "Commit Failed",
                        f"Drawing file {missing_file} is not associated to any part.")
                else:
                    QMessageBox.warning(self, "Commit Failed", error_str)
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Commit Failed: {str(e)}")

    def ask_new_part_action(self, base_file_name: str):
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Warning)
        msg.setWindowTitle("Missing BOM Entry")
        msg.setText(f"No BOM item found for:\n{base_file_name}")
        msg.setInformativeText(
            "Would you like to create a new part for this file?")
        create_btn = msg.addButton("New Part", QMessageBox.AcceptRole)
        msg.addButton("Cancel", QMessageBox.RejectRole)
        msg.exec_()
        if msg.clickedButton() == create_btn:
            self.add_part(base_file_name)

    def add_part(self, filename):
        dialog = PartDialog(self, filename=filename)
        if dialog.exec_() == QDialog.Accepted:
            part_data = dialog.get_data()
            if not part_data["aes_number"] or not part_data["name"]:
                QMessageBox.warning(self, "Validation",
                    "AES Number and Name are required.")
                return
            try:
                self.bom_service.add_part(part_data)
                QMessageBox.information(self, "Success",
                    "Part added successfully.")
                self.bom_service.checkin_part(part_data["aes_number"])
                self.commit_changes()
            except Exception as e:
                QMessageBox.critical(self, "Error",
                    f"Failed to add part: {str(e)}")

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
        if (group.get("project_id") and self.session.project_id and
                int(group.get("project_id")) != int(self.session.project_id)):
            QMessageBox.warning(
                self, "Cannot Push",
                "This commit belongs to a different project version.\n"
                "Switch to that project to push/merge it.",
            )
            return
        try:
            affected_part_ids = self.merge_service.excute_merge_by_commit_id(group["commit_id"])
            self._refresh_bom_rows_for_parts(affected_part_ids)
            self.load_pending_commits()
            self.load_commit_history()
        except Exception as exc:
            QMessageBox.warning(self, "Merge Blocked", str(exc))

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

    def _refresh_issue_views(self):
        try:
            main_window = self.window()
            issue_page = getattr(main_window, "issue_page", None)
            bom_page = getattr(main_window, "bom_page", None)
            if issue_page:
                issue_page.refresh()
            if bom_page:
                bom_page.load_tree()
        except Exception:
            pass

    def revert_commit(self, group=None):
        if not group:
            if not getattr(self, "selected_group", None):
                QMessageBox.warning(self, "Error",
                    "Select a commit group to revert.")
                return False
            group = self.selected_group

        commit_id = group.get("id") or group.get("commit_id")
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
        QMessageBox.critical(self, "Error", "Push failed")

    def merge_to_master(self):
        self.open_merge_dialog()

    def open_merge_dialog(self):
        dlg = MergeDialog(self.merge_service, self.merge_repo, self)
        dlg.exec_()

    def refresh(self):
        self.load_commit_history()
        self.load_pending_commits()
