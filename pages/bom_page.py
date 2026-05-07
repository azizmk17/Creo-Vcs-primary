from asyncio.windows_events import NULL
from PyQt5.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QGroupBox, QLineEdit, QPushButton, QTreeWidget,
    QTreeWidgetItem, QSplitter, QTabWidget, QLabel, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QTextEdit, QComboBox, QSpinBox, QDateTimeEdit,
    QMessageBox, QInputDialog, QFileDialog, QMenu, QAction, QDialog, QDialogButtonBox, QFrame,
    QPlainTextEdit, QStackedWidget, QSizePolicy,
    QGraphicsDropShadowEffect
)
from PyQt5.QtCore import Qt, QDateTime, pyqtSignal, QTimer, QObject, QThread, QSize
from PyQt5.QtGui import QColor, QPen, QFont, QBrush
from datetime import datetime, timedelta
from pages.part_dialog import PartDialog
from collections import deque, Counter, defaultdict
import time
import json

import os
import sys
import subprocess
import csv
from datetime import datetime

from core.session_manager import SessionManager
from core.services.diag_service import DiagService
from core.services.project_service import ProjectService
from core.services.part_file_service import PartFileService
from core.services.package_export_service import PackageExportService
from core.services.ui_permission import UIPermissionHelper
from core.services.baseline_service import BaselineService
from core.services.part_doc_ack_service import PartDocAckService
from core.repositories.commit_repository import CommitRepository
from pages.dialogs.package_parts_dialog import PackagePartsDialog
from pages.dialogs.addChild_dialog import AddChildDialog
from pages.pdf_viewer_widget import PdfViewerWidget
from PyQt5.QtGui import QKeyEvent
from PyQt5.QtGui import QKeyEvent, QPalette, QColor
from PyQt5.QtGui import QPainter, QPixmap, QIcon



class FileDropTable(QTableWidget):
    filesDropped = pyqtSignal(list)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DropOnly)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            paths = []
            for url in event.mimeData().urls():
                p = url.toLocalFile()
                if p:
                    paths.append(p)
            if paths:
                self.filesDropped.emit(paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)


class InlineSpinner(QWidget):
    def __init__(self, parent=None, size: int = 24, color: QColor | None = None):
        super().__init__(parent)
        self._angle = 0
        self._size = int(size)
        self._color = color or QColor(107, 114, 128)
        self.setFixedSize(self._size, self._size)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(30)

    def _tick(self):
        self._angle = (self._angle + 25) % 360
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(self._color, 2)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)

        pad = 3
        r = self.rect().adjusted(pad, pad, -pad, -pad)
        start = int(self._angle * 16)
        span = int(120 * 16)
        painter.drawArc(r, start, span)


# ═══════════════════════════════════════════════════════════════════════════
#  EVENT STYLING CONFIG
# ═══════════════════════════════════════════════════════════════════════════
_EVENT_STYLES = {
    "COMMIT":               {"icon": "📦", "color": "#0078d7", "bg": "#e6f2ff", "label": "Commit"},
    "CHECKIN":              {"icon": "🔓", "color": "#16a34a", "bg": "#dcfce7", "label": "Check In"},
    "CHECKOUT":             {"icon": "🔒", "color": "#ea580c", "bg": "#ffedd5", "label": "Check Out"},
    "PART_RELEASED":        {"icon": "🚀", "color": "#7c3aed", "bg": "#ede9fe", "label": "Released"},
    "ATTACHMENT_VERSION":   {"icon": "📎", "color": "#0891b2", "bg": "#e0f2fe", "label": "Attachment"},
    "ATTACHMENT_RELEASED":  {"icon": "✅", "color": "#059669", "bg": "#d1fae5", "label": "Attach Released"},
}
_DEFAULT_STYLE = {"icon": "📋", "color": "#6b7280", "bg": "#f3f4f6", "label": "Event"}


def _style_for(event_type: str) -> dict:
    return _EVENT_STYLES.get((event_type or "").upper().strip(), _DEFAULT_STYLE)


# ═══════════════════════════════════════════════════════════════════════════
#  KPI CARD
# ═══════════════════════════════════════════════════════════════════════════
class _KpiCard(QFrame):
    """Small metrics card with value, label, and accent color strip."""

    def __init__(self, value: str = "0", label: str = "", accent: str = "#0078d7",
                 icon_text: str = "", parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        self.setFixedHeight(72)
        self.setMinimumWidth(110)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setStyleSheet(f"""
            _KpiCard {{
                background: #ffffff;
                border: 1px solid #e5e7eb;
                border-left: 4px solid {accent};
                border-radius: 8px;
                padding: 0px;
            }}
        """)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(12)
        shadow.setOffset(0, 2)
        shadow.setColor(QColor(0, 0, 0, 30))
        self.setGraphicsEffect(shadow)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(2)

        # Top row: icon + value
        top = QHBoxLayout()
        top.setSpacing(6)
        if icon_text:
            ic = QLabel(icon_text)
            ic.setStyleSheet("font-size: 16px; background: transparent; border: none;")
            top.addWidget(ic)
        self._value_lbl = QLabel(str(value))
        self._value_lbl.setStyleSheet(
            f"font-size: 20px; font-weight: bold; color: {accent}; background: transparent; border: none;"
        )
        top.addWidget(self._value_lbl)
        top.addStretch()
        lay.addLayout(top)

        self._label_lbl = QLabel(str(label))
        self._label_lbl.setStyleSheet(
            "font-size: 10px; color: #6b7280; font-weight: 500; background: transparent; border: none;"
        )
        lay.addWidget(self._label_lbl)

    def set_value(self, val: str):
        self._value_lbl.setText(str(val))

    def set_label(self, lbl: str):
        self._label_lbl.setText(str(lbl))


# ═══════════════════════════════════════════════════════════════════════════
#  ACTIVITY SPARKLINE  (mini bar chart in last 30 days)
# ═══════════════════════════════════════════════════════════════════════════
class _ActivitySparkline(QWidget):
    """Tiny 30-day activity bar chart."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(48)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._bars: list[int] = []
        self._accent = QColor("#0078d7")

    def set_data(self, events: list[dict]):
        """Build 30-day histogram from event timestamps."""
        today = datetime.now().date()
        counts = [0] * 30
        for ev in (events or []):
            ts = ev.get("timestamp", "")
            try:
                dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).date()
            except Exception:
                try:
                    dt = datetime.strptime(str(ts)[:19], "%Y-%m-%d %H:%M:%S").date()
                except Exception:
                    continue
            delta = (today - dt).days
            if 0 <= delta < 30:
                counts[29 - delta] += 1
        self._bars = counts
        self.update()

    def paintEvent(self, event):
        if not self._bars:
            return
        from PyQt5.QtGui import QPainter, QColor, QPen
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        h = self.height()

        mx = max(self._bars) if self._bars else 1
        mx = max(mx, 1)
        bar_w = max(2, (w - 4) / len(self._bars) - 1)
        gap = 1

        for i, v in enumerate(self._bars):
            x = int(2 + i * (bar_w + gap))
            bar_h = max(1, int((v / mx) * (h - 8)))
            y = h - 4 - bar_h

            if v == 0:
                color = QColor("#e5e7eb")
            else:
                intensity = min(255, 100 + int(155 * (v / mx)))
                color = QColor(0, 120, 215, intensity)
            p.fillRect(int(x), int(y), int(bar_w), int(bar_h), color)
        p.end()


# ═══════════════════════════════════════════════════════════════════════════
#  EVENT-TYPE FILTER CHIP
# ═══════════════════════════════════════════════════════════════════════════
class _FilterChip(QPushButton):
    """Togglable chip for filtering by event type."""
    toggled_custom = pyqtSignal()

    def __init__(self, label: str, icon_text: str, color: str, bg: str, parent=None):
        super().__init__(f"{icon_text} {label}", parent)
        self._active = True
        self._color = color
        self._bg = bg
        self._label = label
        self.setCheckable(True)
        self.setChecked(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(28)
        self.clicked.connect(lambda: self.toggled_custom.emit())
        self._refresh_style()

    def _refresh_style(self):
        if self.isChecked():
            self.setStyleSheet(f"""
                QPushButton {{
                    background: {self._bg}; color: {self._color};
                    border: 1px solid {self._color}; border-radius: 12px;
                    padding: 2px 10px; font-size: 11px; font-weight: 600;
                }}
                QPushButton:hover {{ background: {self._color}; color: #ffffff; }}
            """)
        else:
            self.setStyleSheet(f"""
                QPushButton {{
                    background: #f3f4f6; color: #9ca3af;
                    border: 1px solid #d1d5db; border-radius: 12px;
                    padding: 2px 10px; font-size: 11px; font-weight: 500;
                }}
                QPushButton:hover {{ background: #e5e7eb; color: #6b7280; }}
            """)

    def toggle_state(self):
        self.setChecked(not self.isChecked())
        self._refresh_style()

    def is_active(self) -> bool:
        return self.isChecked()

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self._refresh_style()


# ═══════════════════════════════════════════════════════════════════════════
#  TIMELINE TABLE DELEGATE  (colored badges in the Event column)
# ═══════════════════════════════════════════════════════════════════════════
class _TimelineTable(QTableWidget):
    """Enhanced table with custom rendering for event badges and relative timestamps."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(6)
        self.setHorizontalHeaderLabels(["", "Timestamp", "Event", "User", "Project", "Details"])
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setWordWrap(True)
        self.setTextElideMode(Qt.ElideRight)
        self.setAlternatingRowColors(True)
        self.verticalHeader().setVisible(False)
        self.setShowGrid(False)
        hh = self.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Fixed)     # timeline dot
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)  # timestamp
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)  # event badge
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)  # user
        hh.setSectionResizeMode(4, QHeaderView.ResizeToContents)  # project
        hh.setSectionResizeMode(5, QHeaderView.Stretch)           # details
        self.setColumnWidth(0, 30)
        self.setStyleSheet("""
            QTableWidget {
                border: 1px solid #e5e7eb;
                border-radius: 8px;
                background: #ffffff;
                alternate-background-color: #fafbfc;
                gridline-color: transparent;
            }
            QTableWidget::item {
                padding: 6px 8px;
                border-bottom: 1px solid #f3f4f6;
            }
            QTableWidget::item:selected {
                background-color: #eff6ff;
                color: #111827;
            }
            QHeaderView::section {
                background: #f8f9fa;
                border: none;
                border-bottom: 2px solid #e5e7eb;
                padding: 8px 6px;
                font-weight: 700;
                font-size: 11px;
                color: #4b5563;
                text-transform: uppercase;
            }
        """)

    def populate(self, rows: list[dict]):
        """Fill the table with event rows, adding visual timeline and badges."""
        self.setRowCount(0)
        self.setRowCount(len(rows))
        for i, ev in enumerate(rows):
            style = _style_for(ev.get("event", ""))
            ev_data = dict(ev)

            # Col 0: timeline dot
            dot = QLabel(f'<span style="color:{style["color"]}; font-size:18px;">●</span>')
            dot.setAlignment(Qt.AlignCenter)
            dot.setStyleSheet("background: transparent; border: none;")
            self.setCellWidget(i, 0, dot)

            # Col 1: timestamp + relative time
            ts_raw = str(ev.get("timestamp", "") or "")
            rel = _relative_time(ts_raw)
            ts_widget = QWidget()
            ts_lay = QVBoxLayout(ts_widget)
            ts_lay.setContentsMargins(4, 2, 4, 2)
            ts_lay.setSpacing(0)
            ts_main = QLabel(ts_raw[:19] if len(ts_raw) >= 19 else ts_raw)
            ts_main.setStyleSheet("font-size: 11px; color: #374151; font-weight: 500; background: transparent; border: none;")
            ts_lay.addWidget(ts_main)
            if rel:
                ts_rel = QLabel(rel)
                ts_rel.setStyleSheet("font-size: 9px; color: #9ca3af; background: transparent; border: none;")
                ts_lay.addWidget(ts_rel)
            self.setCellWidget(i, 1, ts_widget)

            # Store the raw event data on a hidden item for retrieval
            hidden_item = QTableWidgetItem("")
            hidden_item.setData(Qt.UserRole, ev_data)
            self.setItem(i, 0, hidden_item)

            # Col 2: event badge
            badge = QLabel(f'  {style["icon"]} {style["label"]}  ')
            badge.setAlignment(Qt.AlignCenter)
            badge.setStyleSheet(f"""
                background: {style['bg']}; color: {style['color']};
                border: 1px solid {style['color']}40;
                border-radius: 10px; padding: 2px 8px;
                font-size: 11px; font-weight: 600;
            """)
            badge_container = QWidget()
            badge_lay = QHBoxLayout(badge_container)
            badge_lay.setContentsMargins(2, 2, 2, 2)
            badge_lay.addWidget(badge)
            badge_lay.addStretch()
            self.setCellWidget(i, 2, badge_container)

            # Col 3: user
            user_str = str(ev.get("user", "") or "")
            user_item = QTableWidgetItem(user_str)
            user_item.setForeground(QBrush(QColor("#374151")))
            fnt = user_item.font()
            fnt.setBold(True)
            user_item.setFont(fnt)
            self.setItem(i, 3, user_item)

            # Col 4: project / version
            proj = str(ev.get("project", "") or "")
            ver = str(ev.get("version", "") or "")
            proj_str = f"{proj} ({ver})" if proj and ver else proj or ver
            proj_item = QTableWidgetItem(proj_str)
            proj_item.setForeground(QBrush(QColor("#6b7280")))
            self.setItem(i, 4, proj_item)

            # Col 5: details
            details = str(ev.get("details", "") or "")
            det_item = QTableWidgetItem(details)
            det_item.setToolTip(details)
            det_item.setForeground(QBrush(QColor("#4b5563")))
            self.setItem(i, 5, det_item)

            # STEP indicator icon in details if applicable
            step_status = str(ev.get("step_diff_status", "") or "").strip()
            cad_type = str(ev.get("cad_type", "") or "").strip()
            if step_status:
                step_suffix = {"BASELINE": " 🟢 STEP Baseline", "COMPARED": " 🔵 STEP Compared"}.get(
                    step_status.upper(), f" ⚪ STEP:{step_status}"
                )
                det_item.setText(details + step_suffix + f" [{cad_type}]" if cad_type else details + step_suffix)
                det_item.setToolTip(details + step_suffix + f" [{cad_type}]" if cad_type else details + step_suffix)
            else:
                    det_item.setText(details + (f" [{cad_type}]" if cad_type else details))
                    det_item.setToolTip(details + (f" [{cad_type}]" if cad_type else details))    

            self.setRowHeight(i, 48)

        try:
            self.resizeRowsToContents()
            # Enforce a minimum row height
            for r in range(self.rowCount()):
                if self.rowHeight(r) < 44:
                    self.setRowHeight(r, 44)
        except Exception:
            pass

    def get_event_data(self, row: int) -> dict:
        """Retrieve the full event dict for a given row."""
        item = self.item(row, 0)
        if item is None:
            return {}
        return item.data(Qt.UserRole) or {}


def _relative_time(ts: str) -> str:
    """Return a human-readable relative time string."""
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
        return "in the future"
    if secs < 60:
        return "just now"
    if secs < 3600:
        m = secs // 60
        return f"{m}m ago"
    if secs < 86400:
        h = secs // 3600
        return f"{h}h ago"
    days = secs // 86400
    if days == 1:
        return "yesterday"
    if days < 7:
        return f"{days}d ago"
    if days < 30:
        w = days // 7
        return f"{w}w ago"
    if days < 365:
        mo = days // 30
        return f"{mo}mo ago"
    y = days // 365
    return f"{y}y ago"


# ═══════════════════════════════════════════════════════════════════════════
#  USER BREAKDOWN WIDGET
# ═══════════════════════════════════════════════════════════════════════════
class _UserBreakdownBar(QWidget):
    """Horizontal stacked bar showing contribution per user."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(32)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._segments: list[tuple[str, int, QColor]] = []  # (username, count, color)
        self._total = 0

    _USER_PALETTE = [
        "#0078d7", "#16a34a", "#ea580c", "#7c3aed", "#0891b2",
        "#db2777", "#ca8a04", "#4f46e5", "#0d9488", "#e11d48",
    ]

    def set_data(self, events: list[dict]):
        counter = Counter()
        for ev in (events or []):
            u = str(ev.get("user", "") or "").strip()
            if u:
                counter[u] += 1
        self._segments = []
        for idx, (user, cnt) in enumerate(counter.most_common(10)):
            color = QColor(self._USER_PALETTE[idx % len(self._USER_PALETTE)])
            self._segments.append((user, cnt, color))
        self._total = sum(c for _, c, _ in self._segments) or 1
        self.setToolTip("\n".join(f"{u}: {c} events" for u, c, _ in self._segments))
        self.update()

    def paintEvent(self, event):
        if not self._segments:
            return
        from PyQt5.QtGui import QPainter, QColor
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = self.width() - 4
        h = self.height() - 4
        x = 2
        for user, cnt, color in self._segments:
            seg_w = max(4, int((cnt / self._total) * w))
            p.setBrush(QBrush(color))
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(int(x), 2, int(seg_w), int(h), 4, 4)
            if seg_w > 40:
                p.setPen(QPen(QColor("#ffffff")))
                fnt = p.font()
                fnt.setPointSize(8)
                fnt.setBold(True)
                p.setFont(fnt)
                p.drawText(int(x) + 4, 2, int(seg_w) - 8, int(h),
                           Qt.AlignLeft | Qt.AlignVCenter, user[:12])
            x += seg_w + 1
        p.end()


# ═══════════════════════════════════════════════════════════════════════════
#  HISTORY PANEL — THE GENIUS COMPOSITE WIDGET
# ═══════════════════════════════════════════════════════════════════════════
class HistoryPanel(QWidget):
    """Professional history / audit trail panel with KPIs, timeline,
    activity sparkline, user breakdown, and advanced filtering."""

    # Signals for BomPage to connect
    open_details_requested = pyqtSignal(dict)     # full event dict
    open_step_requested = pyqtSignal(dict)        # for STEP viewer
    show_diff_requested = pyqtSignal(dict)        # for STEP diff

    def __init__(self, parent=None):
        super().__init__(parent)
        self._all_rows: list[dict] = []
        self._filtered_rows: list[dict] = []
        self._analytics: dict = {}
        self._build_ui()

    # ── UI Construction ──────────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        # ── KPI cards row ─────────────────────────────────────────────
        self._kpi_area = QWidget()
        kpi_lay = QHBoxLayout(self._kpi_area)
        kpi_lay.setContentsMargins(0, 0, 0, 0)
        kpi_lay.setSpacing(8)

        self._kpi_events = _KpiCard("0", "Total Events", "#374151", "📊")
        self._kpi_commits = _KpiCard("0", "Commits", "#0078d7", "📦")
        self._kpi_checkins = _KpiCard("0", "Check Ins", "#16a34a", "🔓")
        self._kpi_checkouts = _KpiCard("0", "Check Outs", "#ea580c", "🔒")
        self._kpi_releases = _KpiCard("0", "Releases", "#7c3aed", "🚀")
        self._kpi_users = _KpiCard("0", "Contributors", "#0891b2", "👥")

        for card in (self._kpi_events, self._kpi_commits, self._kpi_checkins,
                     self._kpi_checkouts, self._kpi_releases, self._kpi_users):
            kpi_lay.addWidget(card)
        root.addWidget(self._kpi_area)

        # ── Activity sparkline + user breakdown ───────────────────────
        spark_row = QHBoxLayout()
        spark_row.setSpacing(12)

        spark_group = QVBoxLayout()
        spark_lbl = QLabel("📈 Last 30 Days Activity")
        spark_lbl.setStyleSheet("font-size: 10px; color: #6b7280; font-weight: 600; border: none;")
        self._sparkline = _ActivitySparkline()
        spark_group.addWidget(spark_lbl)
        spark_group.addWidget(self._sparkline)
        spark_row.addLayout(spark_group, 3)

        user_group = QVBoxLayout()
        user_lbl = QLabel("👥 Contributors")
        user_lbl.setStyleSheet("font-size: 10px; color: #6b7280; font-weight: 600; border: none;")
        self._user_bar = _UserBreakdownBar()
        user_group.addWidget(user_lbl)
        user_group.addWidget(self._user_bar)
        spark_row.addLayout(user_group, 2)
        root.addLayout(spark_row)

        # ── Filter bar ────────────────────────────────────────────────
        filter_frame = QFrame()
        filter_frame.setStyleSheet("""
            QFrame {
                background: #f8f9fa;
                border: 1px solid #e5e7eb;
                border-radius: 8px;
                padding: 4px;
            }
        """)
        filter_lay = QVBoxLayout(filter_frame)
        filter_lay.setContentsMargins(8, 6, 8, 6)
        filter_lay.setSpacing(6)

        # Text search + export row
        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        self._search = QLineEdit()
        self._search.setPlaceholderText("🔍  Search history (event, user, details, project)…")
        self._search.setStyleSheet("""
            QLineEdit {
                border: 1px solid #d1d5db; border-radius: 6px;
                padding: 5px 10px; font-size: 12px;
                background: #ffffff;
            }
            QLineEdit:focus { border-color: #0078d7; }
        """)
        self._search.textChanged.connect(self._apply_filters)
        search_row.addWidget(self._search, 1)

        self._export_btn = QPushButton("📥 Export CSV")
        self._export_btn.setObjectName("neutral")
        self._export_btn.setCursor(Qt.PointingHandCursor)
        self._export_btn.setFixedHeight(30)
        search_row.addWidget(self._export_btn)

        self._sort_btn = QPushButton("↕ Sort")
        self._sort_btn.setObjectName("neutral")
        self._sort_btn.setCursor(Qt.PointingHandCursor)
        self._sort_btn.setFixedHeight(30)
        self._sort_btn.setCheckable(True)
        self._sort_btn.setToolTip("Toggle: newest first ↔ oldest first")
        self._sort_btn.clicked.connect(self._toggle_sort)
        search_row.addWidget(self._sort_btn)

        filter_lay.addLayout(search_row)

        # Event-type chips row
        chips_row = QHBoxLayout()
        chips_row.setSpacing(4)
        chips_lbl = QLabel("Filter:")
        chips_lbl.setStyleSheet("font-size: 10px; color: #6b7280; font-weight: 600; border: none; background: transparent;")
        chips_row.addWidget(chips_lbl)

        self._chips: dict[str, _FilterChip] = {}
        for key, sty in _EVENT_STYLES.items():
            chip = _FilterChip(sty["label"], sty["icon"], sty["color"], sty["bg"])
            chip.toggled_custom.connect(self._apply_filters)
            self._chips[key] = chip
            chips_row.addWidget(chip)

        # Select-all / none
        sel_all = QPushButton("All")
        sel_all.setFixedSize(36, 24)
        sel_all.setStyleSheet("font-size: 10px; border-radius: 8px; background: #e5e7eb; border: none;")
        sel_all.setCursor(Qt.PointingHandCursor)
        sel_all.clicked.connect(lambda: self._set_all_chips(True))
        chips_row.addWidget(sel_all)

        sel_none = QPushButton("None")
        sel_none.setFixedSize(40, 24)
        sel_none.setStyleSheet("font-size: 10px; border-radius: 8px; background: #e5e7eb; border: none;")
        sel_none.setCursor(Qt.PointingHandCursor)
        sel_none.clicked.connect(lambda: self._set_all_chips(False))
        chips_row.addWidget(sel_none)

        chips_row.addStretch()

        # Result count label
        self._count_lbl = QLabel("")
        self._count_lbl.setStyleSheet("font-size: 10px; color: #9ca3af; border: none; background: transparent;")
        chips_row.addWidget(self._count_lbl)

        filter_lay.addLayout(chips_row)
        root.addWidget(filter_frame)

        # ── Timeline table ────────────────────────────────────────────
        self._table = _TimelineTable()
        self._table.itemDoubleClicked.connect(self._on_double_click)
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        root.addWidget(self._table, 1)

        # ── Bottom status ─────────────────────────────────────────────
        self._status_lbl = QLabel("Select a part to view history")
        self._status_lbl.setStyleSheet("font-size: 10px; color: #9ca3af; padding: 2px 4px; border: none;")
        root.addWidget(self._status_lbl)

        self._sort_ascending = False   # default newest-first

    # ── Public API ───────────────────────────────────────────────────────
    def set_data(self, rows: list[dict], analytics: dict):
        """Load history data and refresh everything."""
        self._all_rows = list(rows or [])
        self._analytics = dict(analytics or {})
        self._search.setText("")
        self._sort_ascending = False
        self._sort_btn.setChecked(False)

        # KPI cards
        self._kpi_events.set_value(str(analytics.get("events_total", 0)))
        self._kpi_commits.set_value(str(analytics.get("commits", 0)))
        self._kpi_checkins.set_value(str(analytics.get("checkins", 0)))
        self._kpi_checkouts.set_value(str(analytics.get("checkouts", 0)))
        releases = analytics.get("part_releases", 0) + analytics.get("attachment_releases", 0)
        self._kpi_releases.set_value(str(releases))
        self._kpi_users.set_value(str(analytics.get("unique_users", 0)))

        # Activity & user charts
        self._sparkline.set_data(rows)
        self._user_bar.set_data(rows)

        # Last activity
        last = str(analytics.get("last_activity", "") or "")
        rel = _relative_time(last)
        if last:
            self._status_lbl.setText(f"Last activity: {last[:19]}  ({rel})")
        else:
            self._status_lbl.setText("No activity recorded")

        self._apply_filters()

    def clear(self):
        """Reset to empty state."""
        self._all_rows = []
        self._filtered_rows = []
        self._analytics = {}
        self._table.setRowCount(0)
        self._search.setText("")
        self._kpi_events.set_value("0")
        self._kpi_commits.set_value("0")
        self._kpi_checkins.set_value("0")
        self._kpi_checkouts.set_value("0")
        self._kpi_releases.set_value("0")
        self._kpi_users.set_value("0")
        self._sparkline._bars = []
        self._sparkline.update()
        self._user_bar._segments = []
        self._user_bar.update()
        self._count_lbl.setText("")
        self._status_lbl.setText("Select a part to view history")

    def get_all_rows(self) -> list[dict]:
        return list(self._all_rows)

    def get_filtered_rows(self) -> list[dict]:
        return list(self._filtered_rows)

    # ── Filtering ────────────────────────────────────────────────────────
    def _apply_filters(self):
        q = (self._search.text() or "").strip().lower()

        # Active event-type chips
        active_types = set()
        for key, chip in self._chips.items():
            if chip.is_active():
                active_types.add(key)

        filtered = []
        for ev in self._all_rows:
            et = (ev.get("event", "") or "").upper().strip()
            if et not in active_types:
                continue
            if q:
                blob = " ".join(str(ev.get(k, "")) for k in
                                ("timestamp", "event", "user", "details", "project", "version")).lower()
                if q not in blob:
                    continue
            filtered.append(ev)

        # Sort
        if self._sort_ascending:
            filtered = list(reversed(filtered))

        self._filtered_rows = filtered
        self._table.populate(filtered)
        total = len(self._all_rows)
        shown = len(filtered)
        if total == shown:
            self._count_lbl.setText(f"{total} events")
        else:
            self._count_lbl.setText(f"{shown} / {total} events")

    def _set_all_chips(self, on: bool):
        for chip in self._chips.values():
            chip.setChecked(on)
            chip._refresh_style()
        self._apply_filters()

    def _toggle_sort(self):
        self._sort_ascending = self._sort_btn.isChecked()
        self._sort_btn.setText("↑ Oldest" if self._sort_ascending else "↕ Sort")
        self._apply_filters()

    # ── Interactions ─────────────────────────────────────────────────────
    def _on_double_click(self, item):
        row = item.row()
        ev_data = self._table.get_event_data(row)
        if ev_data:
            self.open_details_requested.emit(ev_data)

    def _on_context_menu(self, pos):
        item = self._table.itemAt(pos)
        if not item:
            return
        row = item.row()
        ev_data = self._table.get_event_data(row)
        if not ev_data:
            return

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background: #ffffff; border: 1px solid #d1d5db;
                border-radius: 8px; padding: 4px 0;
            }
            QMenu::item {
                padding: 6px 20px 6px 12px; font-size: 12px;
            }
            QMenu::item:selected {
                background: #eff6ff; color: #0078d7;
            }
            QMenu::separator {
                height: 1px; background: #e5e7eb; margin: 4px 8px;
            }
        """)

        style = _style_for(ev_data.get("event", ""))
        et = (ev_data.get("event") or "").upper()

        details_act = menu.addAction(f"📋  View Full Details")
        details_act.triggered.connect(lambda: self.open_details_requested.emit(ev_data))

        # Copy details to clipboard
        copy_act = menu.addAction("📝  Copy Details to Clipboard")
        copy_act.triggered.connect(lambda: self._copy_to_clipboard(ev_data))

        menu.addSeparator()

        step_path = str(ev_data.get("step_file_path") or "").strip()
        if step_path:
            step_act = menu.addAction("🔬  Open STEP in 3D Viewer")
            step_act.triggered.connect(lambda: self.open_step_requested.emit(ev_data))

        step_status = str(ev_data.get("step_diff_status") or "").strip().upper()
        if step_status == "COMPARED":
            diff_act = menu.addAction("🔍  Show STEP Diff Zones")
            diff_act.triggered.connect(lambda: self.show_diff_requested.emit(ev_data))

        if et == "COMMIT":
            menu.addSeparator()
            commit_id = str(ev_data.get("commit_id") or "")
            if commit_id:
                cid_act = menu.addAction(f"🏷  Copy Commit ID: {commit_id[:12]}…")
                _cid_val = commit_id  # capture in closure
                def _copy_cid(_checked, _v=_cid_val):
                    from PyQt5.QtWidgets import QApplication as _QApp
                    _QApp.clipboard().setText(_v)
                cid_act.triggered.connect(_copy_cid)

        menu.exec_(self._table.viewport().mapToGlobal(pos))

    def _copy_to_clipboard(self, ev_data: dict):
        from PyQt5.QtWidgets import QApplication
        lines = []
        for k in ("timestamp", "event", "user", "project", "version", "details",
                   "commit_id", "step_diff_status", "step_diff_summary"):
            v = ev_data.get(k)
            if v:
                lines.append(f"{k}: {v}")
        QApplication.clipboard().setText("\n".join(lines))


class _TreeLoadWorker(QObject):
    finished = pyqtSignal(int, object, object)  # seq, tree_data, missing_map
    failed = pyqtSignal(int, str)

    def __init__(self, seq: int, bom_service, project_id: int, missing_files: list):
        super().__init__()
        self._seq = int(seq)
        self._bom_service = bom_service
        self._project_id = int(project_id)
        self._missing_files = list(missing_files or [])
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            if self._cancelled:
                return
            tree_data = self._bom_service.get_bom_tree(self._project_id) or {}

            missing_map: dict[int, set] = {}
            try:
                for bom_id, issue_type, _filename in self._missing_files:
                    try:
                        missing_map.setdefault(int(bom_id), set()).add(str(issue_type))
                    except Exception:
                        continue
            except Exception:
                missing_map = {}

            if self._cancelled:
                return

            self.finished.emit(self._seq, tree_data, missing_map)
        except Exception as e:
            self.failed.emit(self._seq, str(e))


class _SearchWorker(QObject):
    finished = pyqtSignal(int, object)  # seq, results
    failed = pyqtSignal(int, str)

    def __init__(self, seq: int, bom_service, query: str):
        super().__init__()
        self._seq = int(seq)
        self._bom_service = bom_service
        self._query = str(query or "")
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            if self._cancelled:
                return
            results = self._bom_service.search_parts(self._query) or []
            if self._cancelled:
                return
            self.finished.emit(self._seq, results)
        except Exception as e:
            self.failed.emit(self._seq, str(e))


class _InitialDiagWorker(QObject):
    finished = pyqtSignal(object, object)  # missing_files, working_result
    failed = pyqtSignal(str)

    def __init__(self, working_dir: str):
        super().__init__()
        self._working_dir = str(working_dir or "")
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            if self._cancelled:
                return
            diag_service = DiagService()
            missing_files = []
            working_result = None
            if self._working_dir and os.path.isdir(self._working_dir):
                missing_files = diag_service.sync_bom_files(self._working_dir) or []
                if self._cancelled:
                    return
                working_result = diag_service.check_working_directory(self._working_dir)
            if self._cancelled:
                return
            self.finished.emit(missing_files, working_result)
        except Exception as e:
            self.failed.emit(str(e))



class BomPage(QWidget):
    """Full-featured BOM Management Page (restored features)"""

    initial_tree_ready = pyqtSignal()

    def __init__(self, bom_service):
        super().__init__()
        self.bom_service = bom_service
        self.diag_service = DiagService()
        self.project_service = ProjectService()
        self.session = SessionManager()
        self.perm = UIPermissionHelper()
        self.part_file_service = PartFileService()
        self.part_doc_ack_service = PartDocAckService()
        self.package_export_service = PackageExportService()
        self.baseline_service = BaselineService()
        self.commit_repo = CommitRepository()

        self.working_dir = None
        self.commits_dir = None

        if self.session.project_id:
            self.working_dir = self.get_working_dir()
            if self.working_dir:
                self.commits_dir = self.working_dir + "/commits"
        
        self.current_part_aes = None
        self.current_part_id = None
        self._tree_load_seq = 0
        self._initial_tree_target_seq = None
        self._initial_tree_ready_emitted = False
        self._tree_thread = None
        self._tree_worker = None
        self._search_thread = None
        self._search_worker = None
        self._diag_thread = None
        self._diag_worker = None

        self._tree_build_seq = 0
        self._tree_build_queue = deque()
        self._tree_build_missing_map = {}
        self._tree_build_timer = QTimer(self)
        self._tree_build_timer.setInterval(0)
        self._tree_build_timer.timeout.connect(self._tree_build_step)

        self._search_build_seq = 0
        self._search_build_queue = deque()
        self._search_build_timer = QTimer(self)
        self._search_build_timer.setInterval(0)
        self._search_build_timer.timeout.connect(self._search_build_step)

        self._last_tree_node_count = 0
        self._full_tree_cached = False
        self._cached_tree_data = None
        self._cached_missing_map = None
        self._in_search_mode = False     # True while _search_tree (index 2) is visible
        self.init_ui()
        
        # Pre-render indicator icons (fast + consistent colors)
        self._icon_pdf_ok = self._make_indicator_icon(pdf_ok=True, step_ok=False)
        self._icon_pdf_bad = self._make_indicator_icon(pdf_ok=False, step_ok=False)
        self._icon_step_ok = self._make_indicator_icon(pdf_ok=False, step_ok=True)
        self._icon_step_bad = self._make_indicator_icon(pdf_ok=False, step_ok=False, step_present=True)
        self._icon_pdf_step_ok = self._make_indicator_icon(pdf_ok=True, step_ok=True)
        self._icon_pdf_bad_step_ok = self._make_indicator_icon(pdf_ok=False, step_ok=True)
        self._icon_pdf_ok_step_bad = self._make_indicator_icon(pdf_ok=True, step_ok=False, step_present=True)
        self._icon_pdf_step_bad = self._make_indicator_icon(pdf_ok=False, step_ok=False, step_present=True)

        self.missing_files = []
        self.missing_ids = set()

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self._perform_search_now)

        # Start initial diagnostics and first tree load without blocking UI thread.
        if self.session.project_id and self.working_dir and os.path.isdir(self.working_dir):
            QTimer.singleShot(0, self._start_initial_diagnostics_and_load)
        else:
            try:
                self.show_alert("No project loaded. Select a project from the toolbar to begin.", "error")
            except Exception:
                pass
            self.load_tree()

        try:
            self.destroyed.connect(lambda *_: self._cancel_background_work())
        except Exception:
            pass

    def _cancel_background_work(self) -> None:
        for worker in (getattr(self, "_tree_worker", None), getattr(self, "_search_worker", None), getattr(self, "_diag_worker", None)):
            try:
                if worker is not None:
                    worker.cancel()
            except Exception:
                pass

        for thread in (getattr(self, "_tree_thread", None), getattr(self, "_search_thread", None), getattr(self, "_diag_thread", None)):
            try:
                if thread is not None:
                    thread.quit()
            except Exception:
                pass

        try:
            self._tree_build_timer.stop()
        except Exception:
            pass
        try:
            self._search_build_timer.stop()
        except Exception:
            pass

    def _set_tree_loading(self, loading: bool) -> None:
        try:
            stack = getattr(self, "_tree_stack", None)
            if stack is None:
                return
            target = 0 if loading else (2 if getattr(self, "_in_search_mode", False) else 1)
            stack.setCurrentIndex(target)
        except Exception:
            pass

    def _show_tree_placeholder(self, _message: str) -> None:
        # Backward compatible; loader is now a spinner overlay.
        self._set_tree_loading(True)
        
    def auto_check_wd(self):

        if not self.session.project_id or not self.working_dir or not os.path.isdir(self.working_dir):
            try:
                self.show_alert("No project loaded. Select a project from the toolbar to begin.", "error")
            except Exception:
                pass
            return
        
        working_result = self.diag_service.check_working_directory(self.working_dir)
        if working_result == "error_no_snapshot":
            self.show_alert("⚠️ No snapshots found for this project. Please create a snapshot to enable integrity checks.", "error")
            return
        elif working_result:
            self.show_alert("⚠️ Integrity issue detected in working directory. Run a diagnostic before proceeding.", "error")
        else:
            self.hide_alert()

    def _start_initial_diagnostics_and_load(self) -> None:
        # Run sync_bom_files + check_working_directory in a background thread.
        try:
            self._set_tree_loading(True)
        except Exception:
            pass

        try:
            if self._diag_worker is not None:
                self._diag_worker.cancel()
        except Exception:
            pass

        worker = _InitialDiagWorker(str(self.working_dir or ""))
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_initial_diag_finished)
        worker.failed.connect(self._on_initial_diag_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._diag_worker = worker
        self._diag_thread = thread
        thread.start()

    def _on_initial_diag_finished(self, missing_files: object, working_result: object) -> None:
        try:
            self.missing_files = list(missing_files or [])
        except Exception:
            self.missing_files = []

        try:
            ids = set()
            for row in (self.missing_files or []):
                try:
                    bom_id = row[0]
                    ids.add(int(bom_id))
                except Exception:
                    continue
            self.missing_ids = ids
        except Exception:
            self.missing_ids = set()

        # Apply integrity banner result (same behavior as auto_check_wd).
        try:
            if working_result == "error_no_snapshot":
                self.show_alert("⚠️ No snapshots found for this project. Please create a snapshot to enable integrity checks.", "error")
            elif working_result:
                self.show_alert("⚠️ Integrity issue detected in working directory. Run a diagnostic before proceeding.", "error")
            else:
                self.hide_alert()
        except Exception:
            pass

        # Now load the tree with diagnostics info.
        self.load_tree()

    def _on_initial_diag_failed(self, _err: str) -> None:
        # Still proceed with a tree load; don't block the UI.
        try:
            self.missing_files = []
            self.missing_ids = set()
        except Exception:
            pass
        self.load_tree()



    def get_working_dir(self):
        if not self.session.project_id:
            return None
        project = self.project_service.get_project_by_id(self.session.project_id) or {}
        project_working_dir = project.get("working_directory")
        if project_working_dir:
            return project_working_dir

        

    def init_ui(self):
        layout = QHBoxLayout(self)

        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter)

        # Left panel
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)

        # Alert section (hidden by default)
        self.alert_frame = QFrame()
        self.alert_frame.setFrameShape(QFrame.StyledPanel)
        self.alert_frame.setFrameShadow(QFrame.Raised)
        self.alert_frame.setStyleSheet("""
            QFrame {
                background-color: #fee2e2;  /* light red background */
                border: 1px solid #f87171;
                border-radius: 6px;
            }
            QLabel {
                color: #991b1b;
                font-weight: bold;
                padding: 6px;
            }
        """)
        self.alert_label = QLabel("")
        alert_layout = QHBoxLayout(self.alert_frame)
        alert_layout.addWidget(self.alert_label)
        self.alert_frame.hide()  # Hidden by default
        left_layout.addWidget(self.alert_frame)

        

        # Search group
        search_group = QGroupBox("Search")
        search_layout = QHBoxLayout(search_group)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search AES, name or part number...")
        try:
            self.search_input.textChanged.connect(self._schedule_search)
        except Exception:
            pass
        self.search_input.returnPressed.connect(self._perform_search_now)
        self.search_btn = QPushButton("Search")
        self.search_btn.setObjectName("primary")
        self.search_btn.clicked.connect(self._perform_search_now)
        search_layout.addWidget(self.search_input)
        search_layout.addWidget(self.search_btn)
        left_layout.addWidget(search_group)

        filter_btn = QPushButton("Advanced Filter")
        filter_btn.clicked.connect(self.show_advanced_filter_dialog)
        # Add to your layout, e.g. at the top of your main layout
        left_layout.addWidget(filter_btn)

        # Tree
        tree_group = QGroupBox("BOM Structure")
        tree_layout = QVBoxLayout(tree_group)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name", "", "AES Number", "Type", "Rev", "Status", "integrity"])
        self.tree.setColumnWidth(0, 260)
        self.tree.setColumnWidth(1, 70)
        self.tree.setColumnWidth(2, 140)
        self.tree.setColumnWidth(3, 70)
        self.tree.setColumnWidth(4, 50)
        self.tree.setColumnWidth(5, 90)
        self.tree.setColumnWidth(6, 40)
        self.tree.itemClicked.connect(self.on_tree_item_clicked)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.show_tree_context_menu)

        # Tree loading overlay (spinner) inside the tree area
        self._tree_stack = QStackedWidget()
        self._tree_loading_widget = QWidget()
        _loading_layout = QVBoxLayout(self._tree_loading_widget)
        _loading_layout.setContentsMargins(0, 0, 0, 0)
        _loading_layout.setAlignment(Qt.AlignCenter)
        _loading_layout.addWidget(InlineSpinner(size=48))
        self._tree_stack.addWidget(self._tree_loading_widget)  # index 0
        self._tree_stack.addWidget(self.tree)                  # index 1

        # Search-results tree — shown instead of self.tree while a search is active.
        # self.tree is never cleared or rebuilt due to search; switching is a pure UI swap.
        self._search_tree = QTreeWidget()
        self._search_tree.setHeaderLabels(["Name", "", "AES Number", "Type", "Rev", "Status", "integrity"])
        self._search_tree.setColumnWidth(0, 260)
        self._search_tree.setColumnWidth(1, 70)
        self._search_tree.setColumnWidth(2, 140)
        self._search_tree.setColumnWidth(3, 70)
        self._search_tree.setColumnWidth(4, 50)
        self._search_tree.setColumnWidth(5, 90)
        self._search_tree.setColumnWidth(6, 40)
        self._search_tree.itemClicked.connect(self.on_tree_item_clicked)
        self._search_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._search_tree.customContextMenuRequested.connect(self.show_tree_context_menu)
        self._tree_stack.addWidget(self._search_tree)          # index 2

        self._tree_stack.setCurrentIndex(1)

        tree_layout.addWidget(self._tree_stack)
        left_layout.addWidget(tree_group)

        
        # Apply style
        self.tree.setStyleSheet("""
            QTreeWidget {
                background: #ffffff;
                border: 1px solid #d1d5db;
                border-radius: 6px;
                font-size: 14px;
                gridline-color: #e5e7eb;
            }
            QHeaderView::section {
                background-color: #f3f4f6;
                color: #374151;
                font-weight: bold;
                border: none;
                padding: 6px;
                border-right: 1px solid #d1d5db;
            }
            QTreeWidget::item {
                padding: 6px;
            }
            QTreeWidget::item:hover {
                background: #f3f4f6;
            }
            QTreeWidget::item:selected {
                background: #2563eb;
                color: white;
                border-radius: 4px;
            }
        """)

        # Right panel
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)

        # Tabs for details
        self.tabs = QTabWidget()

        # Details tab (key/value)
        details_tab = QWidget()
        details_layout = QVBoxLayout(details_tab)
        self.details_table = QTableWidget()
        self.details_table.setColumnCount(2)
        self.details_table.setHorizontalHeaderLabels(["Property", "Value"])
        self.details_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.details_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.details_table.setEditTriggers(QAbstractItemView.NoEditTriggers)

        # Alert section (hidden by default)
        self.details_alert_frame = QFrame()
        self.details_alert_frame.setFrameShape(QFrame.StyledPanel)
        self.details_alert_frame.setFrameShadow(QFrame.Raised)
        self.details_alert_frame.setStyleSheet("""
            QFrame {
                background-color: #fee2e2;  /* light red background */
                border: 1px solid #f87171;
                border-radius: 6px;
            }
            QLabel {
                color: #991b1b;
                font-weight: bold;
                padding: 6px;
            }
        """)
        self.details_alert_label = QLabel("")
        details_alert_layout = QHBoxLayout(self.details_alert_frame)
        details_alert_layout.addWidget(self.details_alert_label)
        self.details_alert_frame.hide()  # Hidden by default

        details_layout.addWidget(self.details_alert_frame)
        details_layout.addWidget(self.details_table)
        self.tabs.addTab(details_tab, "Details")

        

        # Children tab
        children_tab = QWidget()
        children_layout = QVBoxLayout(children_tab)
        self.children_table = QTableWidget()
        self.children_table.setColumnCount(4)
        self.children_table.setHorizontalHeaderLabels(["AES Number", "Name", "Type", "Quantity"])
        self.children_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.children_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        children_layout.addWidget(QLabel("Child Components:"))
        children_layout.addWidget(self.children_table)
        self.tabs.addTab(children_tab, "Children")

        # Notes tab
        notes_tab = QWidget()
        notes_layout = QVBoxLayout(notes_tab)
        self.notes_view = QTextEdit()
        self.notes_view.setReadOnly(True)
        notes_layout.addWidget(QLabel("Notes:"))
        notes_layout.addWidget(self.notes_view)
        self.tabs.addTab(notes_tab, "Notes")

        # Files tab (Vault attachments + version history)
        files_tab = QWidget()
        files_layout = QVBoxLayout(files_tab)

        files_layout.addWidget(QLabel("Attachments:"))
        self.files_table = FileDropTable()
        self.files_table.setColumnCount(5)
        self.files_table.setHorizontalHeaderLabels(["Name", "Type", "Active", "File", "Updated"])
        self.files_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.files_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.files_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.files_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.files_table.itemSelectionChanged.connect(self.on_attachment_selected)
        self.files_table.filesDropped.connect(self._on_files_dropped)
        self.files_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.files_table.customContextMenuRequested.connect(self.show_files_context_menu)
        files_layout.addWidget(self.files_table)

        files_actions = QHBoxLayout()
        self.add_attachment_btn = QPushButton("Add Attachment")
        self.add_attachment_btn.setObjectName("primary")
        self.add_attachment_btn.clicked.connect(self.add_attachment)
        self.add_version_btn = QPushButton("Add Version")
        self.add_version_btn.setObjectName("neutral")
        self.add_version_btn.clicked.connect(self.add_attachment_version)
        self.set_active_btn = QPushButton("Set Active")
        self.set_active_btn.setObjectName("neutral")
        self.set_active_btn.clicked.connect(self.set_active_version)
        self.open_active_btn = QPushButton("Open Active")
        self.open_active_btn.setObjectName("neutral")
        self.open_active_btn.clicked.connect(self.open_active_attachment)
        self.open_folder_btn = QPushButton("Open Folder")
        self.open_folder_btn.setObjectName("neutral")
        self.open_folder_btn.clicked.connect(self.open_active_attachment_folder)
        self.remove_attachment_btn = QPushButton("Remove")
        self.remove_attachment_btn.setObjectName("danger")
        self.remove_attachment_btn.clicked.connect(self.remove_attachment)

        self.export_package_btn = QPushButton("Export Package")
        self.export_package_btn.setObjectName("primary")
        self.export_package_btn.clicked.connect(self.export_package)

        self.create_baseline_btn = QPushButton("Create Baseline")
        self.create_baseline_btn.setObjectName("neutral")
        self.create_baseline_btn.clicked.connect(self.create_baseline)

        self.export_baseline_btn = QPushButton("Export Baseline")
        self.export_baseline_btn.setObjectName("neutral")
        self.export_baseline_btn.clicked.connect(self.export_baseline)
        files_actions.addWidget(self.add_attachment_btn)
        files_actions.addWidget(self.add_version_btn)
        files_actions.addWidget(self.set_active_btn)
        files_actions.addWidget(self.open_active_btn)
        files_actions.addWidget(self.open_folder_btn)
        files_actions.addWidget(self.remove_attachment_btn)
        files_actions.addWidget(self.export_package_btn)
        files_actions.addWidget(self.create_baseline_btn)
        files_actions.addWidget(self.export_baseline_btn)
        files_layout.addLayout(files_actions)

        files_layout.addWidget(QLabel("Versions:"))
        self.versions_table = QTableWidget()
        self.versions_table.setColumnCount(6)
        self.versions_table.setHorizontalHeaderLabels(["Version", "State", "Filename", "Created", "Released", "Note"])
        self.versions_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.versions_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.versions_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.versions_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.versions_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.versions_table.customContextMenuRequested.connect(self.show_versions_context_menu)
        self.versions_table.itemSelectionChanged.connect(self._on_version_selection_changed)
        files_layout.addWidget(self.versions_table)

        versions_actions = QHBoxLayout()
        self.open_version_btn = QPushButton("Open Version")
        self.open_version_btn.setObjectName("neutral")
        self.open_version_btn.clicked.connect(self.open_selected_version)

        _can_release = self.perm.can("release_files")
        self.add_attachment_btn.setEnabled(_can_release)
        self.add_version_btn.setEnabled(_can_release)
        self.set_active_btn.setEnabled(_can_release)
        self.remove_attachment_btn.setEnabled(_can_release)
        self.create_baseline_btn.setEnabled(_can_release)

        self.release_version_btn = QPushButton("Release Version")
        self.release_version_btn.setObjectName("primary")
        self.release_version_btn.clicked.connect(self.release_selected_version)
        self.release_version_btn.setEnabled(_can_release)

        self.delete_version_btn = QPushButton("Delete Version")
        self.delete_version_btn.setObjectName("danger")
        self.delete_version_btn.clicked.connect(self.delete_selected_version)
        self.delete_version_btn.setEnabled(_can_release)
        versions_actions.addWidget(self.open_version_btn)
        versions_actions.addWidget(self.release_version_btn)
        versions_actions.addWidget(self.delete_version_btn)
        files_layout.addLayout(versions_actions)

        # ── Embedded PDF preview ──────────────────────────────────────
        self.pdf_viewer = PdfViewerWidget()
        self.pdf_viewer.setMinimumHeight(250)
        files_layout.addWidget(self.pdf_viewer)

        self.tabs.addTab(files_tab, "Files")

        # ── History tab (genius panel) ────────────────────────────────
        self.history_panel = HistoryPanel(self)
        self.history_panel.open_details_requested.connect(self._open_history_details_dialog)
        self.history_panel.open_step_requested.connect(self._open_associated_step_for_history_event)
        self.history_panel.show_diff_requested.connect(self._show_step_diff_for_history_event)
        self.history_panel._export_btn.clicked.connect(self.export_history_csv)
        self.tabs.addTab(self.history_panel, "📜 History")

        right_layout.addWidget(self.tabs)

        # Actions
        action_group = QGroupBox("Actions")
        action_layout = QHBoxLayout(action_group)
        self.add_part_btn = QPushButton("Add Part")
        self.add_part_btn.setObjectName("primary")
        self.add_part_btn.clicked.connect(self.add_part)
        self.edit_part_btn = QPushButton("Edit Part")
        self.edit_part_btn.setObjectName("neutral")
        self.edit_part_btn.clicked.connect(self.edit_part)
        self.delete_part_btn = QPushButton("Delete Part")
        self.delete_part_btn.setObjectName("danger")
        self.delete_part_btn.clicked.connect(self.delete_part)
        self.checkin_part_btn = QPushButton("Check In")
        self.checkin_part_btn.setObjectName("neutral")
        self.checkin_part_btn.clicked.connect(self.checkin_part)
        self.checkout_part_btn = QPushButton("Check Out")
        self.checkout_part_btn.setObjectName("neutral")
        self.checkout_part_btn.clicked.connect(self.checkout_part)
        self.add_child_btn = QPushButton("Add Child")
        self.add_child_btn.setObjectName("primary")
        self.add_child_btn.clicked.connect(self.add_child)

        _can_manage = self.perm.can("manage_parts")
        self.add_part_btn.setEnabled(_can_manage)
        self.edit_part_btn.setEnabled(_can_manage)
        self.delete_part_btn.setEnabled(_can_manage)
        self.add_child_btn.setEnabled(_can_manage)

        self.set_revision_btn = QPushButton("Set Revision")
        self.set_revision_btn.setObjectName("neutral")
        self.set_revision_btn.clicked.connect(self.set_part_revision)
        self.set_revision_btn.setEnabled(self.perm.can("set_revision"))

        action_layout.addWidget(self.add_part_btn)
        action_layout.addWidget(self.edit_part_btn)
        action_layout.addWidget(self.delete_part_btn)
        action_layout.addWidget(self.checkin_part_btn)
        action_layout.addWidget(self.checkout_part_btn)
        action_layout.addWidget(self.add_child_btn)
        action_layout.addWidget(self.set_revision_btn)
        right_layout.addWidget(action_group)

        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)

        # Set minimum widths
        left_widget.setMinimumWidth(600)
        right_widget.setMinimumWidth(600)

        # Equal stretch = 50% / 50%
        splitter.setStretchFactor(0, 1)  # left
        splitter.setStretchFactor(1, 1)  # right

    # -------------------------
    # Context menu / tree actions
    # -------------------------
    def show_tree_context_menu(self, position):
        item = self.tree.itemAt(position)
        if not item:
            return
        
        item_id = item.data(0, Qt.UserRole)
        menu = QMenu()
        view_action = QAction("View Details", self)
        view_action.triggered.connect(lambda: self.display_details(item_id))
        edit_action = QAction("Edit Part", self)
        edit_action.triggered.connect(lambda: self.edit_part(item_id))
        delete_action = QAction("Delete Part", self)
        delete_action.triggered.connect(lambda: self.delete_part(item_id))
        add_child_action = QAction("Add Child", self)
        add_child_action.triggered.connect(lambda: self.add_child(item_id))
        add_dwg_action = QAction("Associate Drawing", self)
        add_dwg_action.triggered.connect(lambda: self.add_dwg_to_part(item_id))

        view_3d_action = QAction("🔬 View STEP in 3D Viewer", self)
        view_3d_action.triggered.connect(lambda: self._open_step_in_3d_viewer(item_id))

        menu.addAction(view_action)
        if self.perm.can("manage_parts"):
            menu.addAction(edit_action)
            menu.addAction(delete_action)
            menu.addAction(add_child_action)
        menu.addAction(add_dwg_action)

        # Allow removing a child relation when right-clicking a child node
        try:
            parent_item = item.parent()
            if parent_item is not None and self.perm.can("manage_parts"):
                remove_child_action = QAction("Remove Child", self)

                def _remove_child():
                    parent_id = parent_item.data(0, Qt.UserRole)
                    child_id = item.data(0, Qt.UserRole)
                    if not parent_id or not child_id:
                        return
                    # Confirm
                    reply = QMessageBox.question(
                        self,
                        "Confirm Remove",
                        f"Remove child {item.text(0)} from parent {parent_item.text(0)}?",
                        QMessageBox.Yes | QMessageBox.No,
                    )
                    if reply != QMessageBox.Yes:
                        return

                    # Count occurrences of this child id in the full tree
                    try:
                        occ = 0
                        def _traverse(it):
                            nonlocal occ
                            try:
                                if int(it.data(0, Qt.UserRole)) == int(child_id):
                                    occ += 1
                            except Exception:
                                pass
                            for k in range(it.childCount()):
                                _traverse(it.child(k))

                        for t in range(self.tree.topLevelItemCount()):
                            _traverse(self.tree.topLevelItem(t))
                    except Exception:
                        occ = 0

                    # If this is the last occurrence, block removal
                    if occ <= 1:
                        QMessageBox.critical(self, "Cannot Remove", "Cannot remove this child because it is the last occurrence in the BOM.")
                        return

                    # Call service to remove relation (try common method names)
                    try:
                        if hasattr(self.bom_service, "remove_child_by_id"):
                            self.bom_service.remove_child_by_id(parent_id, child_id)
                        elif hasattr(self.bom_service, "remove_child"):
                            self.bom_service.remove_child(parent_id, child_id)
                        else:
                            raise AttributeError("remove child API not found on bom_service")
                        QMessageBox.information(self, "Removed", "Child removed from parent.")
                        self.load_tree()
                    except Exception as e:
                        QMessageBox.critical(self, "Error", f"Failed to remove child:\n{e}")

                remove_child_action.triggered.connect(_remove_child)
                menu.addAction(remove_child_action)
        except Exception:
            pass

        # Only show 3D viewer action if part has a committed STEP file
        try:
            latest_step = self.commit_repo.get_latest_step_commit_for_part(
                int(item_id), int(self.session.project_id)
            )
            if latest_step and latest_step.step_file_path and os.path.exists(latest_step.step_file_path):
                menu.addSeparator()
                menu.addAction(view_3d_action)
        except Exception:
            pass

        menu.exec_(self.tree.viewport().mapToGlobal(position))

    
    def show_advanced_filter_dialog(self):
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton

        dialog = QDialog(self)
        dialog.setWindowTitle("Advanced BOM Filter")
        layout = QVBoxLayout(dialog)

        # Status filter
        status_label = QLabel("Status:")
        status_combo = QComboBox()
        status_combo.addItem("All")
        status_combo.addItem("Checked In")
        layout.addWidget(status_label)
        layout.addWidget(status_combo)

        # User filter
        user_label = QLabel("User:")
        user_combo = QComboBox()
        user_combo.addItem("All")
        users = self.project_service.get_users_for_project(self.session.project_id) or []
        user_map = {"All": None}
        for u in users:
            label = str(u.get("username") or "").strip()
            if not label:
                continue
            user_combo.addItem(label)
            user_map[label] = int(u.get("id"))
        layout.addWidget(user_label)
        layout.addWidget(user_combo)

        # Buttons
        btns = QHBoxLayout()
        ok_btn = QPushButton("Apply")
        cancel_btn = QPushButton("Cancel")
        btns.addWidget(ok_btn)
        btns.addWidget(cancel_btn)
        layout.addLayout(btns)

        def on_apply():
            status = status_combo.currentText()
            user_label = user_combo.currentText()
            user_id = user_map.get(user_label)
            dialog.accept()
            self.apply_bom_tree_filter(status, user_label)

        ok_btn.clicked.connect(on_apply)
        cancel_btn.clicked.connect(dialog.reject)
        dialog.exec_()

    def apply_bom_tree_filter(self, status: str, user_label: int | None):
        def filter_item(item):
            # Status filter
            locked_txt = item.text(1).lower()
            if status == "Checked In" and "in work" not in locked_txt:
                return False
            # User filter
            if user_label is not None and user_label != "All":
                if str(user_label).lower() not in locked_txt:
                    return False
            return True

        def recurse(item):
            show = filter_item(item)
            for i in range(item.childCount()):
                child = item.child(i)
                child_show = recurse(child)
                show = show or child_show
            item.setHidden(not show)
            return show

        for i in range(self.tree.topLevelItemCount()):
            recurse(self.tree.topLevelItem(i))

    # -------------------------
    # Files (Vault / PLM-like)
    # -------------------------
    def _set_files_tab_enabled(self, enabled: bool):
        # Read-only controls: follow enabled state only
        for w in (
            getattr(self, "files_table", None),
            getattr(self, "versions_table", None),
            getattr(self, "open_active_btn", None),
            getattr(self, "open_folder_btn", None),
            getattr(self, "export_package_btn", None),
            getattr(self, "export_baseline_btn", None),
            getattr(self, "open_version_btn", None),
        ):
            if w is not None:
                w.setEnabled(enabled)

        # Write controls: also require release_files permission
        can_release = self.perm.can("release_files")
        for w in (
            getattr(self, "add_attachment_btn", None),
            getattr(self, "add_version_btn", None),
            getattr(self, "set_active_btn", None),
            getattr(self, "remove_attachment_btn", None),
            getattr(self, "create_baseline_btn", None),
            getattr(self, "release_version_btn", None),
            getattr(self, "delete_version_btn", None),
        ):
            if w is not None:
                w.setEnabled(bool(enabled) and can_release)

    def create_baseline(self):
        if not self.perm.can("release_files"):
            return QMessageBox.warning(self, "Permission", "You do not have permission to manage files.")
        if not getattr(self, "current_part_id", None):
            return QMessageBox.warning(self, "Select", "Select a part first.")

        name, ok = QInputDialog.getText(self, "Create Baseline", "Baseline name:")
        if not ok or not (name or "").strip():
            return

        include_children = (
            QMessageBox.question(
                self,
                "Include Children",
                "Include child parts in this baseline?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            == QMessageBox.Yes
        )

        # Let user pick parts (defaults to current part checked)
        dlg = PackagePartsDialog(self, project_id=self.session.project_id, preselected_ids=[int(self.current_part_id)])
        part_ids = [int(self.current_part_id)]
        if dlg.exec_() == QDialog.Accepted:
            picked = dlg.selected_part_ids()
            if picked:
                part_ids = picked

        try:
            res = self.baseline_service.create_baseline(name=name.strip(), part_ids=part_ids, include_children=include_children)
            missing = res.get("missing") or []
            QMessageBox.information(
                self,
                "Baseline Created",
                f"Baseline #{res.get('baseline_id')} created.\n"
                f"Parts: {len(res.get('expanded_part_ids') or [])}\n"
                f"Issues: {len(missing)} (missing or not released)",
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to create baseline:\n{e}")

    def export_baseline(self):
        baselines = self.baseline_service.list_baselines()
        if not baselines:
            return QMessageBox.warning(self, "No Baselines", "No baselines exist for this project.")

        labels = []
        id_by_label = {}
        for b in baselines:
            label = f"{b['id']} - {b['name']} ({b.get('created_at') or ''})"
            labels.append(label)
            id_by_label[label] = int(b["id"])

        choice, ok = QInputDialog.getItem(self, "Export Baseline", "Select baseline:", labels, 0, False)
        if not ok or not choice:
            return

        baseline_id = id_by_label.get(choice)
        if not baseline_id:
            return

        dest_dir = QFileDialog.getExistingDirectory(self, "Select Destination Folder")
        if not dest_dir:
            return

        try:
            manifest = self.baseline_service.export_baseline(baseline_id, dest_dir)
            missing = manifest.get("missing") or []
            out_dir = (manifest.get("package") or {}).get("output_dir")
            QMessageBox.information(
                self,
                "Baseline Exported",
                f"Export complete.\nOutput: {out_dir}\nMissing: {len(missing)}",
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to export baseline:\n{e}")

    def refresh_files_tab(self):
        if not getattr(self, "current_part_id", None):
            if hasattr(self, "files_table"):
                self.files_table.setRowCount(0)
            if hasattr(self, "versions_table"):
                self.versions_table.setRowCount(0)
            if hasattr(self, "pdf_viewer"):
                self.pdf_viewer.close_preview()
            self._set_files_tab_enabled(False)
            return

        self._set_files_tab_enabled(True)
        attachments = self.part_file_service.list_attachments(self.current_part_id)

        self.files_table.setRowCount(len(attachments))
        for i, f in enumerate(attachments):
            versions = self.part_file_service.list_versions(f.id)
            active_version = None
            for v in versions:
                if f.active_version_id and v.id == f.active_version_id:
                    active_version = v
                    break
            if not active_version and versions:
                active_version = versions[0]

            name_item = QTableWidgetItem(str(f.display_name))
            name_item.setData(Qt.UserRole, f.id)
            self.files_table.setItem(i, 0, name_item)
            self.files_table.setItem(i, 1, QTableWidgetItem(str(f.file_type)))
            self.files_table.setItem(i, 2, QTableWidgetItem(str(active_version.version_no) if active_version else ""))
            self.files_table.setItem(i, 3, QTableWidgetItem(str(active_version.original_filename) if active_version else ""))
            self.files_table.setItem(i, 4, QTableWidgetItem(str(active_version.created_at) if active_version else str(f.created_at or "")))

        # Clear versions view until an attachment is selected
        self.versions_table.setRowCount(0)

    def _selected_attachment_id(self):
        items = getattr(self, "files_table", None).selectedItems() if hasattr(self, "files_table") else []
        if not items:
            return None
        file_id = self.files_table.item(self.files_table.currentRow(), 0).data(Qt.UserRole)
        return file_id

    def _selected_version_id(self):
        items = getattr(self, "versions_table", None).selectedItems() if hasattr(self, "versions_table") else []
        if not items:
            return None
        return self.versions_table.item(self.versions_table.currentRow(), 0).data(Qt.UserRole)

    def on_attachment_selected(self):
        file_id = self._selected_attachment_id()
        if not file_id:
            self.versions_table.setRowCount(0)
            if hasattr(self, "pdf_viewer"):
                self.pdf_viewer.close_preview()
            return
        versions = self.part_file_service.list_versions(file_id)
        self.versions_table.setRowCount(len(versions))
        for i, v in enumerate(versions):
            ver_item = QTableWidgetItem(str(v.version_no))
            ver_item.setData(Qt.UserRole, v.id)
            self.versions_table.setItem(i, 0, ver_item)
            self.versions_table.setItem(i, 1, QTableWidgetItem(str(v.lifecycle_state or "")))
            self.versions_table.setItem(i, 2, QTableWidgetItem(str(v.original_filename)))
            self.versions_table.setItem(i, 3, QTableWidgetItem(str(v.created_at or "")))
            self.versions_table.setItem(i, 4, QTableWidgetItem(str(v.released_at or "")))
            self.versions_table.setItem(i, 5, QTableWidgetItem(str(v.note or "")))

        # Auto-preview the active version if it is a PDF
        self._try_pdf_preview_for_active(file_id)

    # ── PDF preview helpers ─────────────────────────────────────────
    def _try_pdf_preview_for_active(self, file_id):
        """Load the active version into the PDF viewer if it is a PDF."""
        if not hasattr(self, "pdf_viewer"):
            return
        try:
            # Check the attachment file_type
            row = self.files_table.currentRow()
            if row < 0:
                return
            file_type_item = self.files_table.item(row, 1)
            if not file_type_item or file_type_item.text().upper() != "PDF":
                self.pdf_viewer.close_preview()
                return
            path = self.part_file_service.resolve_active_path(file_id)
            resolved = self._resolve_file_path(path)
            if resolved and os.path.isfile(resolved) and resolved.lower().endswith(".pdf"):
                self.pdf_viewer.load_pdf(resolved)
            else:
                self.pdf_viewer.close_preview()
        except Exception:
            self.pdf_viewer.close_preview()

    def _on_version_selection_changed(self):
        """When a specific version row is selected, preview it if it is a PDF."""
        if not hasattr(self, "pdf_viewer"):
            return
        version_id = self._selected_version_id()
        if not version_id:
            return
        try:
            ver = self.part_file_service.repo.get_version_by_id(version_id)
            if not ver:
                return
            fname = (getattr(ver, "original_filename", "") or "").lower()
            if not fname.endswith(".pdf"):
                self.pdf_viewer.close_preview()
                return
            path = self.part_file_service.resolve_version_path(ver)
            resolved = self._resolve_file_path(path)
            if resolved and os.path.isfile(resolved):
                self.pdf_viewer.load_pdf(resolved)
            else:
                self.pdf_viewer.close_preview()
        except Exception:
            self.pdf_viewer.close_preview()

    def release_selected_version(self):
        if not self.perm.can("release_files"):
            return QMessageBox.warning(self, "Permission", "You do not have permission to release files.")

        file_id = self._selected_attachment_id()
        version_id = self._selected_version_id()
        if not file_id or not version_id:
            return QMessageBox.warning(self, "Select", "Select an attachment and a version.")

        try:
            self.part_file_service.release_version(version_id)
            if getattr(self, "current_part_id", None):
                self.bom_service.release_part(self.current_part_id)
            self.refresh_files_tab()
            self.on_attachment_selected()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to release version:\n{e}")

    def set_part_revision(self):
        if not getattr(self, "current_part_id", None):
            return
        if not self.perm.can("set_revision"):
            return QMessageBox.warning(self, "Permission", "You do not have permission to set revisions.")

        current = ""
        try:
            details = self.bom_service.get_part_details(self.current_part_id) or {}
            current = str(details.get("revision") or "").strip().upper()
        except Exception:
            current = ""

        revision, ok = QInputDialog.getText(self, "Set Revision", "Revision (A, B, ...):", text=current)
        if not ok:
            return
        revision = (revision or "").strip().upper()
        if not revision.isalpha():
            return QMessageBox.warning(self, "Invalid", "Revision must be alphabetic (A, B, ...).")

        try:
            self.bom_service.set_revision(self.current_part_id, revision)
            self.display_details(self.current_part_id)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to set revision:\n{e}")

    def add_attachment(self):
        if not self.perm.can("release_files"):
            return QMessageBox.warning(self, "Permission", "You do not have permission to manage files.")
        if not getattr(self, "current_part_id", None):
            return

        file_type, ok = QInputDialog.getItem(self, "File Type", "Select attachment type:", ["PDF", "STEP", "DWG", "OTHER"], 0, False)
        if not ok:
            return

        display_name, ok = QInputDialog.getText(self, "Display Name", "Enter attachment name:")
        if not ok or not display_name.strip():
            return

        note, _ = QInputDialog.getText(self, "Version Note", "Version note (optional):")

        source_path, _ = QFileDialog.getOpenFileName(self, "Select file")
        if not source_path:
            return

        try:
            self.part_file_service.create_attachment(
                part_id=self.current_part_id,
                file_type=file_type,
                display_name=display_name.strip(),
                description="",
                source_path=source_path,
                note=note or "",
            )
            self.refresh_files_tab()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to add attachment:\n{e}")

    def add_attachment_version(self):
        if not self.perm.can("release_files"):
            return QMessageBox.warning(self, "Permission", "You do not have permission to manage files.")
        file_id = self._selected_attachment_id()
        if not file_id:
            QMessageBox.warning(self, "Select", "Select an attachment first.")
            return
        note, _ = QInputDialog.getText(self, "Version Note", "Version note (optional):")
        source_path, _ = QFileDialog.getOpenFileName(self, "Select new version file")
        if not source_path:
            return
        try:
            self.part_file_service.add_new_version(file_id, source_path, note=note or "")
            self.refresh_files_tab()
            # keep versions visible
            self.on_attachment_selected()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to add version:\n{e}")

    def set_active_version(self):
        if not self.perm.can("release_files"):
            return QMessageBox.warning(self, "Permission", "You do not have permission to manage files.")
        file_id = self._selected_attachment_id()
        version_id = self._selected_version_id()
        if not file_id or not version_id:
            QMessageBox.warning(self, "Select", "Select an attachment and a version.")
            return
        try:
            self.part_file_service.set_active_version(file_id, version_id)
            self.refresh_files_tab()
            self.on_attachment_selected()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to set active version:\n{e}")

    def open_active_attachment(self):
        file_id = self._selected_attachment_id()
        if not file_id:
            QMessageBox.warning(self, "Select", "Select an attachment.")
            return
        path = self.part_file_service.resolve_active_path(file_id)
        self._open_file(path, "Attachment")

    def open_active_attachment_folder(self):
        file_id = self._selected_attachment_id()
        if not file_id:
            QMessageBox.warning(self, "Select", "Select an attachment.")
            return
        path = self.part_file_service.resolve_active_path(file_id)
        resolved = self._resolve_file_path(path)
        if not resolved or not os.path.exists(resolved):
            QMessageBox.warning(self, "Missing File", f"Attachment file not found:\n{resolved}")
            return
        try:
            subprocess.Popen(["explorer", "/select,", resolved])
        except Exception:
            try:
                os.startfile(os.path.dirname(resolved))
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to open folder:\n{e}")

    def _infer_file_type_from_path(self, path: str) -> str:
        ext = os.path.splitext(path or "")[1].lower()
        if ext == ".pdf":
            return "PDF"
        if ext in (".step", ".stp"):
            return "STEP"
        if ext in (".dwg", ".dxf"):
            return "DWG"
        return "OTHER"

    def _on_files_dropped(self, paths):
        if not getattr(self, "current_part_id", None):
            QMessageBox.warning(self, "Select", "Select a part first.")
            return
        note, _ = QInputDialog.getText(self, "Version Note", "Version note for dropped files (optional):")

        added = 0
        failed = []
        # cache current attachments for quick type lookup; refresh when we create a new attachment
        attachments = self.part_file_service.list_attachments(self.current_part_id)

        for p in paths:
            try:
                if not p or not os.path.exists(p):
                    raise ValueError("File not found")

                file_type = self._infer_file_type_from_path(p)
                if file_type == "OTHER":
                    file_type, ok = QInputDialog.getItem(
                        self,
                        "File Type",
                        f"Select type for:\n{os.path.basename(p)}",
                        ["PDF", "STEP", "DWG", "OTHER"],
                        3,
                        False,
                    )
                    if not ok:
                        continue

                # If an attachment of this type already exists for the part, add as a new version
                existing = None
                try:
                    for f in (attachments or []):
                        if str((f.file_type or "")).upper() == str(file_type or "").upper():
                            existing = f
                            break
                except Exception:
                    existing = None

                if existing:
                    # add new version to existing attachment
                    self.part_file_service.add_new_version(existing.id, p, note=note or "")
                else:
                    display_name = os.path.splitext(os.path.basename(p))[0]
                    self.part_file_service.create_attachment(
                        part_id=self.current_part_id,
                        file_type=file_type,
                        display_name=display_name,
                        description="",
                        source_path=p,
                        note=note or "",
                    )
                    # refresh cached attachments so subsequent dropped files see the new attachment
                    try:
                        attachments = self.part_file_service.list_attachments(self.current_part_id)
                    except Exception:
                        pass
                added += 1
            except Exception as e:
                failed.append(f"{os.path.basename(p)}: {e}")

        if added:
            self.refresh_files_tab()
        if failed:
            QMessageBox.warning(self, "Some files failed", "\n".join(failed[:15]))

    def open_selected_version(self):
        version_id = self._selected_version_id()
        if not version_id:
            QMessageBox.warning(self, "Select", "Select a version.")
            return
        ver = self.part_file_service.repo.get_version_by_id(version_id)
        if not ver:
            QMessageBox.warning(self, "Missing", "Version not found.")
            return
        path = self.part_file_service.resolve_version_path(ver)
        self._open_file(path, "Attachment")

    def delete_selected_version(self):
        if not self.perm.can("release_files"):
            return QMessageBox.warning(self, "Permission", "You do not have permission to manage files.")
        file_id = self._selected_attachment_id()
        version_id = self._selected_version_id()
        if not file_id or not version_id:
            QMessageBox.warning(self, "Select", "Select an attachment and a version.")
            return
        confirm = QMessageBox.question(self, "Confirm", "Delete selected version?", QMessageBox.Yes | QMessageBox.No)
        if confirm != QMessageBox.Yes:
            return
        try:
            self.part_file_service.delete_version(file_id, version_id)
            self.refresh_files_tab()
            self.on_attachment_selected()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to delete version:\n{e}")

    def remove_attachment(self):
        if not self.perm.can("release_files"):
            return QMessageBox.warning(self, "Permission", "You do not have permission to manage files.")
        file_id = self._selected_attachment_id()
        if not file_id:
            QMessageBox.warning(self, "Select", "Select an attachment.")
            return
        confirm = QMessageBox.question(self, "Confirm", "Remove attachment and all versions?", QMessageBox.Yes | QMessageBox.No)
        if confirm != QMessageBox.Yes:
            return
        try:
            self.part_file_service.delete_attachment(file_id)
            self.refresh_files_tab()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to remove attachment:\n{e}")

    def export_package(self):
        mode, ok = QInputDialog.getItem(
            self,
            "Export Package",
            "Export mode:",
            ["Selected part", "Selected part + children", "Choose parts (any)"],
            1,
            False,
        )
        if not ok:
            return

        part_ids = []
        include_children = False

        if mode.startswith("Selected"):
            if not getattr(self, "current_part_id", None):
                QMessageBox.warning(self, "Select", "Select a part first.")
                return
            part_ids = [self.current_part_id]
            include_children = mode.endswith("children")
        else:
            dlg = PackagePartsDialog(self, project_id=self.session.project_id, preselected_ids=[])
            if dlg.exec_() != QDialog.Accepted:
                return
            part_ids = dlg.selected_part_ids()
            if not part_ids:
                QMessageBox.warning(self, "Select", "No parts selected.")
                return
            include_children = QMessageBox.question(
                self,
                "Export Package",
                "Include all children of the selected parts?",
                QMessageBox.Yes | QMessageBox.No,
            ) == QMessageBox.Yes

        package_name, ok = QInputDialog.getText(self, "Package Name", "Enter package name:")
        if not ok or not package_name.strip():
            return

        dest_dir = QFileDialog.getExistingDirectory(self, "Select destination folder")
        if not dest_dir:
            return

        create_zip = (
            QMessageBox.question(
                self,
                "Export Package",
                "Create a ZIP archive too?",
                QMessageBox.Yes | QMessageBox.No,
            )
            == QMessageBox.Yes
        )

        try:
            manifest = self.package_export_service.export_package_for_parts(
                part_ids=part_ids,
                destination_dir=dest_dir,
                include_children=include_children,
                package_name=package_name.strip(),
                create_zip=create_zip,
            )
            missing_count = len(manifest.get("missing", []))
            out_dir = manifest.get("package", {}).get("output_dir", "")
            zip_path = manifest.get("package", {}).get("zip_path", "")
            QMessageBox.information(
                self,
                "Export Complete",
                f"Package exported to:\n{out_dir}\n\nZIP: {zip_path or 'No'}\nMissing files: {missing_count}",
            )
            if out_dir:
                try:
                    os.startfile(out_dir)
                except Exception:
                    pass
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to export package:\n{e}")

    def _resolve_file_path(self, path: str) -> str:
        if not path:
            return ""
        path = path.strip()
        if not path:
            return ""
        if os.path.isabs(path):
            return path
        if getattr(self, "working_dir", None):
            candidate = os.path.join(self.working_dir, path)
            return candidate
        return path

    def _open_file(self, path: str, label: str):
        resolved = self._resolve_file_path(path)
        if not resolved:
            QMessageBox.warning(self, "Not Found", f"No {label} file associated with this part.")
            return
        if not os.path.exists(resolved):
            QMessageBox.warning(self, "Missing File", f"{label} file not found:\n{resolved}")
            return
        try:
            os.startfile(resolved)  # Windows
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to open {label} file:\n{e}")

    def add_pdf_to_part(self, part_id: int):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select PDF file", "", "PDF Files (*.pdf);;All Files (*)")
        if not file_path:
            return
        try:
            self.bom_service.update_part(part_id, {"pdf_path": file_path})
            self._refresh_part_in_tree(int(part_id))
            QMessageBox.information(self, "Success", "PDF associated with part successfully.")
            self.display_details(part_id)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to attach PDF: {e}")

    def add_step_to_part(self, part_id: int):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select STEP file", "", "STEP Files (*.step *.stp);;All Files (*)")
        if not file_path:
            return
        try:
            self.bom_service.update_part(part_id, {"step_path": file_path})
            self._refresh_part_in_tree(int(part_id))
            QMessageBox.information(self, "Success", "STEP associated with part successfully.")
            self.display_details(part_id)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to attach STEP: {e}")

    def open_part_pdf(self, part_id: int):
        details = self.bom_service.get_part_details(part_id) or {}
        self._open_file(details.get("pdf_path", ""), "PDF")

    def open_part_step(self, part_id: int):
        details = self.bom_service.get_part_details(part_id) or {}
        self._open_file(details.get("step_path", ""), "STEP")

    def _open_step_in_3d_viewer(self, part_id: int):
        """Open the part's latest committed STEP file in the advanced 3D
        viewer with face-lifecycle tracking enabled.

        The viewer uses the STEP copies stored by the commit workflow
        (commits.step_file_path) — these are the same files used for
        the STEP diff engine, so face fingerprints match the lifecycle
        history."""
        try:
            latest_step = self.commit_repo.get_latest_step_commit_for_part(
                int(part_id), int(self.session.project_id)
            )
        except Exception as exc:
            QMessageBox.critical(self, "3D Viewer", f"Failed to query committed STEP:\n{exc}")
            return

        if not latest_step or not latest_step.step_file_path:
            QMessageBox.warning(
                self, "3D Viewer",
                "No committed STEP file found for this part.\n\n"
                "Attach a STEP file when committing to enable 3D viewing\n"
                "and face-level lifecycle tracking.",
            )
            return

        step_path = latest_step.step_file_path
        if not os.path.exists(step_path):
            QMessageBox.warning(
                self, "3D Viewer",
                f"Committed STEP file not found on disk:\n{step_path}\n\n"
                "The file may have been moved or deleted.",
            )
            return

        try:
            from tools.CAD.step_viewer.launcher import launch_viewer
            from config import DB_NAME
            launch_viewer(
                step_path,
                part_id=part_id,
                project_id=self.session.project_id,
                db_path=DB_NAME,
                face_map_path=getattr(latest_step, "step_face_map_path", None),
            )
        except Exception as e:
            QMessageBox.critical(self, "3D Viewer", f"Failed to open 3D viewer:\n{e}")

    def _set_files_controls_enabled(self, enabled: bool):
        for w in (
            getattr(self, "pdf_browse_btn", None),
            getattr(self, "pdf_open_btn", None),
            getattr(self, "pdf_clear_btn", None),
            getattr(self, "step_browse_btn", None),
            getattr(self, "step_open_btn", None),
            getattr(self, "step_clear_btn", None),
        ):
            if w is not None:
                w.setEnabled(enabled)

    def _refresh_files_tab(self, details: dict):
        if not details:
            if hasattr(self, "pdf_path_view"):
                self.pdf_path_view.setText("")
            if hasattr(self, "step_path_view"):
                self.step_path_view.setText("")
            self._set_files_controls_enabled(False)
            return

        self._set_files_controls_enabled(True)
        if hasattr(self, "pdf_path_view"):
            self.pdf_path_view.setText(details.get("pdf_path", "") or "")
        if hasattr(self, "step_path_view"):
            self.step_path_view.setText(details.get("step_path", "") or "")

    def browse_pdf_for_selected(self):
        if not getattr(self, "current_part_id", None):
            return
        self.add_pdf_to_part(self.current_part_id)

    def browse_step_for_selected(self):
        if not getattr(self, "current_part_id", None):
            return
        self.add_step_to_part(self.current_part_id)

    def open_pdf_for_selected(self):
        if not getattr(self, "current_part_id", None):
            return
        self.open_part_pdf(self.current_part_id)

    def open_step_for_selected(self):
        if not getattr(self, "current_part_id", None):
            return
        self.open_part_step(self.current_part_id)

    def clear_pdf_for_selected(self):
        if not getattr(self, "current_part_id", None):
            return
        try:
            self.bom_service.update_part(self.current_part_id, {"pdf_path": ""})
            self._refresh_part_in_tree(int(self.current_part_id))
            self.display_details(self.current_part_id)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to clear PDF: {e}")

    def clear_step_for_selected(self):
        if not getattr(self, "current_part_id", None):
            return
        try:
            self.bom_service.update_part(self.current_part_id, {"step_path": ""})
            self._refresh_part_in_tree(int(self.current_part_id))
            self.display_details(self.current_part_id)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to clear STEP: {e}")


    def add_child(self):
        """Start interactive add-child mode (select parent → child → confirm)."""
        if not self.perm.can("manage_parts"):
            return QMessageBox.warning(self, "Permission", "You do not have permission to add child relations.")
        self.parent_item = None
        self.child_item = None
        self.add_child_mode = True

        # 🔹 Keep your existing design — just give a temporary focus cue
        pal = self.tree.palette()
        self._original_base_color = pal.color(QPalette.Base)
        pal.setColor(QPalette.Base, QColor(230, 245, 255))  # very light blue background
        self.tree.setPalette(pal)

        # Cursor + status hint
        self.setCursor(Qt.PointingHandCursor)
        self.window().statusBar().showMessage("🔹 Select the parent part in the tree (Press Esc to cancel)...")

        # Connect handlers
        self.tree.itemClicked.connect(self._on_tree_click_for_add_child)


    def _on_tree_click_for_add_child(self, item):
        """Handle parent → child selection sequence."""
        if not self.add_child_mode:
            return

        # Step 1: select parent
        if self.parent_item is None:
            part_type = item.text(3).strip().lower()
            if part_type != "asm":
                self.window().statusBar().showMessage("⚠️ Selected part is not an ASM. Please select a valid assembly.")
                return  # Don’t continue

            self.parent_item = item
            self.window().statusBar().showMessage(f"Parent selected: {item.text(0)} | Now select the child part...")
            return

        # Step 2: select child
        if self.child_item is None:
            if item == self.parent_item:
                self.window().statusBar().showMessage("⚠️ Parent and child cannot be the same part. Select a different child or press Esc to cancel.")
                return

            self.child_item = item
            parent_name = self.parent_item.text(0)
            child_name = self.child_item.text(0)

            # Confirm relationship
            confirm = QMessageBox.question(
                self,
                "Confirm Relation",
                f"Add relation:\n\nParent: {parent_name}\nChild: {child_name}\n\nConfirm?",
                QMessageBox.Yes | QMessageBox.No,
            )

            if confirm == QMessageBox.Yes:
                parent_id = self.parent_item.data(0, Qt.UserRole)
                child_id = self.child_item.data(0, Qt.UserRole)
                try:
                    self.bom_service.add_child_by_id(parent_id, child_id)
                    self.window().statusBar().showMessage("Child added successfully.")
                    self.load_tree()
                except Exception as e:
                    self.window().statusBar().showMessage(f"Failed to add child: {str(e)}")
            else:
                self.window().statusBar().showMessage("Add-child operation cancelled.")

            # Exit selection mode
            self._exit_add_child_mode()


    def _exit_add_child_mode(self):
        """Clean up state, visuals, and connections."""
        if hasattr(self, "tree") and self.tree:
            try:
                self.tree.itemClicked.disconnect(self._on_tree_click_for_add_child)
            except Exception:
                pass
            

        self.setCursor(Qt.ArrowCursor)
        #self.window().statusBar().clearMessage()
        self.parent_item = None
        self.child_item = None
        self.add_child_mode = False


    def keyPressEvent(self, event: QKeyEvent):
        """Handle ESC to cancel add-child mode."""
        if getattr(self, "add_child_mode", False) and event.key() == Qt.Key_Escape:
            self.window().statusBar().showMessage("Add-child operation cancelled.")
            self._exit_add_child_mode()
        else:
            # pass to default handler
            super().keyPressEvent(event)

    def add_dwg_to_part(self, parent_aes):
        part = self.bom_service.get_part_details(parent_aes)
        if not part:
            QMessageBox.warning(self, "Not Found", f"Part with AES {parent_aes} not found.")
            return

        file_path, _ = QFileDialog.getOpenFileName(self, "Select drawing file", "", "All Files (*);;PDF Files (*.pdf);;Images (*.png *.jpg *.jpeg *.bmp)")
        if not file_path:
            return

        drawing_no, ok = QInputDialog.getText(self, "Drawing Number", "Enter drawing number (optional):", text=str(part.get("drawing_number", "")))
        if not ok:
            return

        update_payload = {
            "drawing": file_path,
            "drawing_number": drawing_no or part.get("drawing_number")
        }

        try:
            self.bom_service.update_part(parent_aes, update_payload)
            self._refresh_part_in_tree(int(parent_aes))
            QMessageBox.information(self, "Success", "Drawing associated with part successfully.")
            self.display_details(parent_aes)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to attach drawing: {e}")

    # -------------------------
    # Basic CRUD
    # -------------------------
    def _schedule_search(self, _text: str = ""):
        """Debounce search requests to avoid expensive reloads while typing."""
        try:
            self._search_timer.start()
        except Exception:
            self._perform_search_now()

    def perform_search(self):
        """Backward-compatible entrypoint (called elsewhere)."""
        self._perform_search_now()

    def _perform_search_now(self):
        query = self.search_input.text().strip()
        if not query:
            self._exit_search_mode()
            return

        # Show spinner immediately, then fetch results in a background thread.
        self._tree_load_seq += 1
        seq = self._tree_load_seq
        self._set_tree_loading(True)

        try:
            if self._search_worker is not None:
                self._search_worker.cancel()
        except Exception:
            pass

        worker = _SearchWorker(seq, self.bom_service, query)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_search_finished)
        worker.failed.connect(self._on_search_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._search_worker = worker
        self._search_thread = thread
        thread.start()

    def _on_search_finished(self, seq: int, results: object) -> None:
        if int(seq) != int(getattr(self, "_tree_load_seq", 0)):
            return
        self._begin_search_build(int(seq), list(results or []))

    def _on_search_failed(self, seq: int, err: str) -> None:
        if int(seq) != int(getattr(self, "_tree_load_seq", 0)):
            return
        try:
            # Show empty results on error; keep UI responsive
            self._search_tree.clear()
        except Exception:
            pass
        self._set_tree_loading(False)

    def _begin_search_build(self, seq: int, results: list) -> None:
        self._search_build_seq = int(seq)
        try:
            self._search_build_timer.stop()
        except Exception:
            pass
        self._search_build_queue = deque(results or [])

        self._enter_search_mode()   # switch stack to _search_tree (index 2), clears it

        self._set_tree_loading(True)
        try:
            self._search_tree.setUpdatesEnabled(False)
        except Exception:
            pass
        self._search_build_timer.start()

    def _search_build_step(self) -> None:
        if int(self._search_build_seq) != int(getattr(self, "_tree_load_seq", 0)):
            try:
                self._search_build_timer.stop()
            except Exception:
                pass
            return

        start = time.perf_counter()
        processed = 0
        try:
            while self._search_build_queue and processed < 200 and (time.perf_counter() - start) < 0.01:
                part = self._search_build_queue.popleft()
                item = self._make_tree_item(part)
                self._search_tree.addTopLevelItem(item)
                processed += 1

            if not self._search_build_queue:
                self._search_build_timer.stop()
                try:
                    self._search_tree.setUpdatesEnabled(True)
                except Exception:
                    pass
                try:
                    # Search results are flat; no need to expand deeply.
                    self._search_tree.expandToDepth(1)
                except Exception:
                    pass
                self._set_tree_loading(False)
        except RuntimeError:
            try:
                self._search_build_timer.stop()
            except Exception:
                pass
            return

    def _issues_for_part(self, part_id: int | None) -> set:
        issues = set()
        if part_id is None:
            return issues
        try:
            pid = int(part_id)
        except Exception:
            return issues
        for row in (self.missing_files or []):
            try:
                bom_id, issue_type, _filename = row
                if int(bom_id) == pid:
                    issues.add(str(issue_type))
            except Exception:
                continue
        return issues

    def _rebuild_missing_ids(self) -> None:
        ids = set()
        for row in (self.missing_files or []):
            try:
                ids.add(int(row[0]))
            except Exception:
                continue
        self.missing_ids = ids

    def _replace_missing_rows_for_part(self, part_id: int, rows: list | None) -> set:
        try:
            pid = int(part_id)
        except Exception:
            return set()

        kept = []
        for row in (self.missing_files or []):
            try:
                if int(row[0]) == pid:
                    continue
            except Exception:
                pass
            kept.append(row)

        new_rows = []
        for row in (rows or []):
            try:
                if int(row[0]) == pid:
                    new_rows.append(row)
            except Exception:
                continue

        self.missing_files = kept + new_rows
        self._rebuild_missing_ids()

        try:
            if self._cached_missing_map is not None:
                if new_rows:
                    self._cached_missing_map[pid] = {str(row[1]) for row in new_rows}
                else:
                    self._cached_missing_map.pop(pid, None)
        except Exception:
            pass

        return {str(row[1]) for row in new_rows}

    def _refresh_diagnostic_for_part(self, part_id: int) -> set:
        rows = []
        try:
            if self.working_dir and os.path.isdir(self.working_dir):
                rows = self.diag_service.sync_bom_part_files(self.working_dir, int(part_id)) or []
        except Exception:
            rows = []
        return self._replace_missing_rows_for_part(int(part_id), rows)

    def _make_tree_item(self, info: dict) -> QTreeWidgetItem:
        item = QTreeWidgetItem(["", "", "", "", "", "", ""])
        self._apply_tree_item_data(item, info or {})
        return item

    def _apply_tree_item_data(self, item: QTreeWidgetItem, info: dict) -> None:
        part_id = info.get("id")
        issues = self._issues_for_part(part_id)
        locked_txt = ""
        if info.get("locked"):
            who = str(info.get("locked_by_username") or "").strip()
            locked_txt = f"In Work ({who})" if who else "In Work"

        item.setText(0, str(info.get("name", "") or ""))
        item.setText(1, locked_txt)
        item.setText(2, str(info.get("aes_number", "") or ""))
        item.setText(3, str(info.get("type", "") or ""))
        item.setText(4, str(info.get("revision", "") or ""))
        item.setText(5, str(info.get("status", "") or ""))
        try:
            has_issue = int(part_id) in self.missing_ids
        except Exception:
            has_issue = False
        item.setText(6, "!" if has_issue else "")
        item.setData(0, Qt.UserRole, part_id)

        try:
            item.setIcon(0, self._pick_indicator_icon(issues, part_id=part_id))
        except Exception:
            pass

        try:
            item.setForeground(1, QBrush())
            item.setForeground(5, QBrush())
            status = str(info.get("status", "") or "").lower()
            if status == "released":
                item.setForeground(5, QColor(0, 128, 0))
            elif status == "obsolete":
                item.setForeground(5, QColor(255, 0, 0))
            if info.get("locked"):
                item.setForeground(1, QColor(255, 0, 0))
        except Exception:
            pass

    def _iter_tree_items(self, tree_widget: QTreeWidget):
        stack = []
        try:
            for i in range(tree_widget.topLevelItemCount()):
                stack.append(tree_widget.topLevelItem(i))
        except Exception:
            return
        while stack:
            item = stack.pop()
            yield item
            try:
                for i in range(item.childCount() - 1, -1, -1):
                    stack.append(item.child(i))
            except Exception:
                continue

    def _find_tree_items(self, part_id: int, tree_widget: QTreeWidget | None = None) -> list:
        try:
            pid = int(part_id)
        except Exception:
            return []
        widgets = [tree_widget] if tree_widget is not None else [getattr(self, "tree", None), getattr(self, "_search_tree", None)]
        matches = []
        for widget in widgets:
            if widget is None:
                continue
            for item in self._iter_tree_items(widget):
                try:
                    if int(item.data(0, Qt.UserRole)) == pid:
                        matches.append(item)
                except Exception:
                    continue
        return matches

    def _add_node_to_tree(self, tree_widget: QTreeWidget, info: dict, parent_item: QTreeWidgetItem | None = None) -> QTreeWidgetItem:
        item = self._make_tree_item(info or {})
        if parent_item is None:
            tree_widget.addTopLevelItem(item)
        else:
            parent_item.addChild(item)
        for child in (info or {}).get("children", []) or []:
            self._add_node_to_tree(tree_widget, child, item)
        return item

    def _node_from_tree_item(self, item: QTreeWidgetItem) -> dict:
        part_id = item.data(0, Qt.UserRole)
        try:
            node = self.bom_service.get_part_details(int(part_id)) or {}
        except Exception:
            node = {}
        if not node:
            node = {
                "id": part_id,
                "name": item.text(0),
                "aes_number": item.text(2),
                "type": item.text(3),
                "revision": item.text(4),
                "status": item.text(5),
            }
        node = dict(node)
        node["children"] = []
        for i in range(item.childCount()):
            node["children"].append(self._node_from_tree_item(item.child(i)))
        return node

    def _part_matches_current_search(self, info: dict) -> bool:
        try:
            query = self.search_input.text().strip().lower()
        except Exception:
            query = ""
        if not query:
            return False
        for key in ("aes_number", "name", "part_number"):
            if query in str(info.get(key, "") or "").lower():
                return True
        return False

    def _select_part_item(self, part_id: int) -> None:
        target_tree = self._search_tree if getattr(self, "_in_search_mode", False) else self.tree
        matches = self._find_tree_items(part_id, target_tree)
        if not matches and target_tree is not self.tree:
            matches = self._find_tree_items(part_id, self.tree)
            target_tree = self.tree
        if not matches:
            return
        item = matches[0]
        try:
            target_tree.setCurrentItem(item)
            target_tree.scrollToItem(item)
        except Exception:
            pass

    def _add_part_to_tree(self, part_id: int) -> dict:
        self._refresh_diagnostic_for_part(int(part_id))
        info = self.bom_service.get_part_details(int(part_id)) or {}
        if not info:
            return {}
        info = dict(info)
        info["children"] = []

        try:
            if not self._find_tree_items(int(part_id), self.tree):
                self._add_node_to_tree(self.tree, info)
        except Exception:
            pass

        try:
            if getattr(self, "_in_search_mode", False) and self._part_matches_current_search(info):
                if not self._find_tree_items(int(part_id), self._search_tree):
                    self._add_node_to_tree(self._search_tree, info)
        except Exception:
            pass

        self._select_part_item(int(part_id))
        return info

    def _refresh_part_in_tree(self, part_id: int) -> dict:
        self._refresh_diagnostic_for_part(int(part_id))
        info = self.bom_service.get_part_details(int(part_id)) or {}
        if not info:
            return {}
        for item in self._find_tree_items(int(part_id)):
            self._apply_tree_item_data(item, info)
        return info

    def refresh_parts_after_merge(self, part_ids) -> None:
        """Refresh only BOM rows affected by a successful merge."""
        refreshed = set()
        for part_id in (part_ids or []):
            try:
                pid = int(part_id)
            except Exception:
                continue
            if pid in refreshed:
                continue
            refreshed.add(pid)
            self._refresh_part_in_tree(pid)

        try:
            current = getattr(self, "current_part_id", None)
            if current is not None and int(current) in refreshed:
                self.display_details(int(current))
        except Exception:
            pass

    def _remove_part_from_tree_widget(self, tree_widget: QTreeWidget, part_id: int, promote_children: bool = False) -> None:
        matches = self._find_tree_items(int(part_id), tree_widget)
        promoted = {}
        if promote_children:
            for item in matches:
                for i in range(item.childCount()):
                    node = self._node_from_tree_item(item.child(i))
                    try:
                        child_id = int(node.get("id"))
                    except Exception:
                        continue
                    if child_id != int(part_id):
                        promoted.setdefault(child_id, node)

        for item in list(matches):
            parent = item.parent()
            try:
                if parent is not None:
                    parent.removeChild(item)
                else:
                    index = tree_widget.indexOfTopLevelItem(item)
                    if index >= 0:
                        tree_widget.takeTopLevelItem(index)
            except Exception:
                continue

        if promote_children:
            for child_id, node in promoted.items():
                if self._find_tree_items(child_id, tree_widget):
                    continue
                self._add_node_to_tree(tree_widget, node)

    def _remove_part_from_trees(self, part_id: int) -> None:
        self._replace_missing_rows_for_part(int(part_id), [])
        try:
            self._remove_part_from_tree_widget(self.tree, int(part_id), promote_children=True)
        except Exception:
            pass
        try:
            self._remove_part_from_tree_widget(self._search_tree, int(part_id), promote_children=False)
        except Exception:
            pass

    def add_part(self):
        if not self.perm.can("manage_parts"):
            return QMessageBox.warning(self, "Permission", "You do not have permission to add parts.")
        dialog = PartDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            part_data = dialog.get_data()
            if not part_data["aes_number"] or not part_data["name"]:
                QMessageBox.warning(self, "Validation Error", "AES Number and Name are required fields.")
                return
            try:
                added= self.bom_service.add_part(part_data)
                if type(added) == int:
                    self._add_part_to_tree(int(added))
                    self.display_details(int(added))
                    QMessageBox.information(self, "Success", "Part added successfully.")
                    self.window().statusBar().showMessage("Part added successfully.")
                elif added == "existing":
                    QMessageBox.warning(self, "Duplicate AES", "Part with this AES number already exists.")
                    self.window().statusBar().showMessage("Adding part failed.")
                else:
                    QMessageBox.warning(self, "Error", "An unexpected error occurred while adding the part.")
                    self.window().statusBar().showMessage("Adding part failed.")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to add part: {str(e)}")

    def edit_part(self, id=None):
        if not self.perm.can("manage_parts"):
            return QMessageBox.warning(self, "Permission", "You do not have permission to edit parts.")
        if not id:
            if not self.current_part_id:
                QMessageBox.warning(self, "No Selection", "Please select a part to edit.")
                return
            id = self.current_part_id

        part_data = self.bom_service.get_part_details(id)
        if not part_data:
            QMessageBox.warning(self, "Not Found", f"Part with AES {id} not found.")
            return

        dialog = PartDialog(self, part_data)
        if dialog.exec_() == QDialog.Accepted:
            updated_data = dialog.get_data()
            if not updated_data["aes_number"] or not updated_data["name"]:
                QMessageBox.warning(self, "Validation Error", "AES Number and Name are required fields.")
                return
            try:
                self.bom_service.update_part(id, updated_data)
                self._refresh_part_in_tree(int(id))
                QMessageBox.information(self, "Success", "Part updated successfully.")
                self.display_details(id)
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to update part: {str(e)}")

    def delete_part(self, id=None):
        if not self.perm.can("manage_parts"):
            return QMessageBox.warning(self, "Permission", "You do not have permission to delete parts.")
        if not id:
            if not self.current_part_id:
                QMessageBox.warning(self, "No Selection", "Please select a part to delete.")
                return
            id = self.current_part_id

        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Are you sure you want to delete part {id}? This action cannot be undone.",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            try:
                self.bom_service.delete_part(id)
                self._remove_part_from_trees(int(id))
                QMessageBox.information(self, "Success", "Part deleted successfully.")
                self.clear_details()
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to delete part: {str(e)}")

    def _refresh_current_tree_item_lock_state(self, part_id: int | None = None):
        """Fast refresh of just the selected tree item's lock state ('In Work' column).

        Updates column 1 text + its foreground color based on latest DB state.
        """
        try:
            item = self.tree.currentItem()
            if not item:
                return
            pid = part_id or item.data(0, Qt.UserRole)
            if not pid:
                return

            info = self.bom_service.get_part_details(int(pid)) or {}
            locked = bool(info.get("locked"))
            who = str(info.get("locked_by_username") or "").strip()
            if locked:
                item.setText(1, f"In Work ({who})" if who else "In Work")
            else:
                item.setText(1, "")
            if locked:
                item.setForeground(1, QColor(255, 0, 0))
            else:
                # reset to default (black)
                item.setForeground(1, QColor(0, 0, 0))
        except Exception:
            pass

    def checkin_part(self, part_id=None):
        if not part_id:
            if not self.current_part_id:
                QMessageBox.warning(self, "No Selection", "Please select a part to check in.")
                return
            part_id = self.current_part_id
        try:
            as_user_id = None
            # Master/Admin can check in as a project-assigned user
            if self.perm.can("merge") and self.session.project_id:
                users = self.project_service.get_users_for_project(self.session.project_id) or []
                # Build choices: username (email)
                choices = []
                choice_to_id = {}
                for u in users:
                    try:
                        label = str(u.get("username") or "").strip()
                        email = str(u.get("email") or "").strip()
                        uid = int(u.get("id"))
                    except Exception:
                        continue
                    if not label:
                        continue
                    if email:
                        display = f"{label} ({email})"
                    else:
                        display = label
                    choices.append(display)
                    choice_to_id[display] = uid

                if choices:
                    # default to current session user if present
                    default_choice = None
                    for disp, uid in choice_to_id.items():
                        if self.session.user_id is not None and int(uid) == int(self.session.user_id):
                            default_choice = disp
                            break
                    if default_choice is None:
                        default_choice = choices[0]

                    selected, ok = QInputDialog.getItem(
                        self,
                        "Check In As",
                        "Check in this part as:",
                        choices,
                        choices.index(default_choice) if default_choice in choices else 0,
                        False,
                    )
                    if not ok:
                        return
                    as_user_id = choice_to_id.get(selected)

            self.bom_service.checkin_part(part_id, as_user_id=as_user_id)
            QMessageBox.information(self, "Success", "Part checked in successfully.")
            # Fast refresh: update only this row
            self._refresh_current_tree_item_lock_state(int(part_id))
            self._refresh_current_tree_item_indicator()
            try:
                self.display_details(int(part_id))
            except Exception:
                pass
        except ValueError as e:
            QMessageBox.warning(self, "Check In Failed", str(e))
        except PermissionError as e:
            QMessageBox.warning(self, "Permission", str(e))
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to check in part: {str(e)}")

    def checkout_part(self, part_id=None):
        if not part_id:
            if not self.current_part_id:
                QMessageBox.warning(self, "No Selection", "Please select a part to check out.")
                return
            part_id = self.current_part_id

        as_user_id = None
        # Master/Admin can check out as a project-assigned user if part is checked in by another user
        if self.perm.can("merge") and self.session.project_id:
            users = self.project_service.get_users_for_project(self.session.project_id) or []
            choices = []
            choice_to_id = {}
            for u in users:
                try:
                    label = str(u.get("username") or "").strip()
                    email = str(u.get("email") or "").strip()
                    uid = int(u.get("id"))
                except Exception:
                    continue
                if not label:
                    continue
                if email:
                    display = f"{label} ({email})"
                else:
                    display = label
                choices.append(display)
                choice_to_id[display] = uid
            if choices:
                default_choice = None
                for disp, uid in choice_to_id.items():
                    if self.session.user_id is not None and int(uid) == int(self.session.user_id):
                        default_choice = disp
                        break
                if default_choice is None:
                    default_choice = choices[0]
                selected, ok = QInputDialog.getItem(
                    self,
                    "Check Out As",
                    "Check out this part as:",
                    choices,
                    choices.index(default_choice) if default_choice in choices else 0,
                    False,
                )
                if not ok:
                    return
                as_user_id = choice_to_id.get(selected)

        reply = QMessageBox.question(
            self,
            "Confirm Check Out",
            f"Are you sure you want to checkout part {part_id}? This part will be released without modifications.",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            try:
                self.bom_service.checkout_part(part_id, as_user_id=as_user_id)
                QMessageBox.information(self, "Success", "Part checked out successfully [Released].")
                # Fast refresh: update only this row
                self._refresh_current_tree_item_lock_state(int(part_id))
                self._refresh_current_tree_item_indicator()
                try:
                    self.display_details(int(part_id))
                except Exception:
                    pass
            except ValueError as e:
                QMessageBox.warning(self, "Check Out Failed", str(e))
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to check out part: {str(e)}")
    # -------------------------
    # Tree loading / details
    # -------------------------
    def _parse_dt(self, s: str):
        if not s:
            return None
        try:
            return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        except Exception:
            return None

    def _get_part_modified_dt(self, part_id: int):
        try:
            d = self.bom_service.get_part_details(int(part_id)) or {}
            # DB stores as sqlite datetime('now') => 'YYYY-MM-DD HH:MM:SS'
            s = d.get("modified") or d.get("modified_at") or d.get("updated")
            return self._parse_dt(str(s)) if s else None
        except Exception:
            return None

    def _is_committed_part(self, part_id: int) -> bool:
        """Outdated rule: if the part has any Approved commit in the project family.

        NOTE: We treat 'Approved' as committed. Integrated/WIP do not count.
        """
        try:
            proj = self.project_service.get_project_by_id(self.session.project_id) or {}
            root_id = proj.get("root_project_id")
            commits = self.diag_service.repo.get_all_commits_for_root(int(root_id)) if root_id else self.diag_service.repo.get_all_commits(self.session.project_id)
            for c in commits or []:
                if int(getattr(c, "part_id", 0) or 0) == int(part_id) and str(getattr(c, "status", "") or "").lower() == "approved":
                    return True
        except Exception:
            pass
        return False

    def _doc_outdated(self, part_id: int, doc_type: str) -> bool:
        """Return True if document should be red per rules, unless acknowledged."""
        doc_type = str(doc_type).upper()

        # If user acknowledged against current modified timestamp, it's up-to-date.
        part_mod = self._get_part_modified_dt(part_id)
        try:
            ack = self.part_doc_ack_service.get_ack(int(part_id), doc_type)
        except Exception:
            ack = None
        if ack and part_mod:
            ack_dt = self._parse_dt(ack.get("acknowledged_against"))
            if ack_dt and ack_dt >= part_mod:
                return False

        # Rule 1: committed => outdated
        if self._is_committed_part(part_id):
            return True

        # Rule 2: part modified after document creation
        created_dt = None
        try:
            atts = self.part_file_service.list_attachments(int(part_id))
        except Exception:
            atts = []

        for att in atts:
            if (att.file_type or "").upper() != doc_type:
                continue
            ver = self.part_file_service.get_active_version(att.id)
            if not ver:
                continue
            created_dt = self._parse_dt(getattr(ver, "created_at", None) or "")
            if created_dt:
                break

        if part_mod and created_dt and part_mod > created_dt:
            return True

        return False

    def _attachment_status_for_part(self, part_id: int) -> tuple[bool, bool]:
        """Return (pdf_ok, step_ok) based on Files-tab attachments.

        OK only if there is an attachment of that type AND its active version is Released AND the file exists on disk.
        Also turns red if outdated per rules (unless acknowledged).
        """
        try:
            atts = self.part_file_service.list_attachments(int(part_id))
        except Exception:
            atts = []

        def _type_ok(file_type: str) -> bool:
            ok = False
            for att in atts:
                try:
                    if (att.file_type or "").upper() != file_type:
                        continue
                    ver = self.part_file_service.get_active_version(att.id)
                    if not ver:
                        continue
                    #if str(getattr(ver, "lifecycle_state", "") or "").upper() != "RELEASED":
                        #continue
                    p = self.part_file_service.resolve_version_path(ver)
                    if p and os.path.exists(p):
                        ok = True
                        break
                except Exception:
                    continue

            if not ok:
                return False

            # apply outdated rules
            try:
                if self._doc_outdated(int(part_id), file_type):
                    return False
            except Exception:
                pass

            return True

        return _type_ok("PDF"), _type_ok("STEP")

    def _pick_indicator_icon(self, issues: set, part_id: int | None = None) -> QIcon:
        # Prefer real attachment status if we can
        pdf_ok = True
        step_ok = True
        try:
            if part_id is not None:
                pdf_ok, step_ok = self._attachment_status_for_part(int(part_id))
        except Exception:
            pdf_ok = True
            step_ok = True

        # Apply diagnostics as additional "bad" flags
        if ("missing_pdf" in issues) or ("outdated_pdf" in issues):
            pdf_ok = False
        if ("missing_step" in issues) or ("outdated_step" in issues):
            step_ok = False

        if pdf_ok and step_ok:
            return self._icon_pdf_step_ok
        if (not pdf_ok) and step_ok:
            return self._icon_pdf_bad_step_ok
        if pdf_ok and (not step_ok):
            return self._icon_pdf_ok_step_bad
        return self._icon_pdf_step_bad

    def load_tree(self):
        # Show spinner immediately, then fetch tree data in a background thread.
        self._tree_load_seq += 1
        seq = self._tree_load_seq

        # Remember the first load; used to hide the startup loader at the right time.
        if self._initial_tree_target_seq is None:
            self._initial_tree_target_seq = int(seq)
        pid = getattr(self.session, "project_id", None)
        if not pid:
            try:
                self.tree.clear()
            except Exception:
                pass
            self._set_tree_loading(False)
            return

        self._set_tree_loading(True)

        try:
            if self._tree_worker is not None:
                self._tree_worker.cancel()
        except Exception:
            pass

        worker = _TreeLoadWorker(seq, self.bom_service, int(pid), list(self.missing_files or []))
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_tree_loaded)
        worker.failed.connect(self._on_tree_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._tree_worker = worker
        self._tree_thread = thread
        thread.start()

    def _on_tree_loaded(self, seq: int, tree_data: object, missing_map: object) -> None:
        if int(seq) != int(getattr(self, "_tree_load_seq", 0)):
            return
        self._begin_tree_build(int(seq), dict(tree_data or {}), dict(missing_map or {}))

    def _on_tree_failed(self, seq: int, err: str) -> None:
        if int(seq) != int(getattr(self, "_tree_load_seq", 0)):
            return
        try:
            self.tree.clear()
        except Exception:
            pass
        self._set_tree_loading(False)

    def _load_tree_from_data(self, tree_data: dict, missing_map: dict) -> None:
        # Use incremental builder to keep UI responsive.
        self._begin_tree_build(int(getattr(self, "_tree_load_seq", 0)), tree_data or {}, missing_map or {})

    def _begin_tree_build(self, seq: int, tree_data: dict, missing_map: dict) -> None:
        self._tree_build_seq = int(seq)
        self._tree_build_missing_map = missing_map or {}
        self._last_tree_node_count = 0

        # cache for restore after search
        try:
            self._cached_tree_data = tree_data
            self._cached_missing_map = missing_map
            self._full_tree_cached = True
        except Exception:
            pass

        try:
            self._tree_build_timer.stop()
        except Exception:
            pass

        self._tree_build_queue = deque()
        try:
            # preserve dict order
            for _k, info in (tree_data or {}).items():
                self._tree_build_queue.append((None, info))
        except Exception:
            self._tree_build_queue = deque()

        try:
            self.tree.clear()
        except Exception:
            pass

        self._set_tree_loading(True)
        try:
            self.tree.setUpdatesEnabled(False)
        except Exception:
            pass
        self._tree_build_timer.start()

    def _tree_build_step(self) -> None:
        if int(self._tree_build_seq) != int(getattr(self, "_tree_load_seq", 0)):
            try:
                self._tree_build_timer.stop()
            except Exception:
                pass
            return

        missing_map = getattr(self, "_tree_build_missing_map", {}) or {}
        start = time.perf_counter()
        processed = 0
        try:
            while self._tree_build_queue and processed < 50 and (time.perf_counter() - start) < 0.01:
                parent_item, info = self._tree_build_queue.popleft()
                part_id = info.get("id")
                try:
                    issues = missing_map.get(int(part_id), set()) if part_id is not None else set()
                except Exception:
                    issues = set()

                if info.get("id") in self.missing_ids:
                    errorSymbole = "⚠️ "
                else:
                    errorSymbole = ""

                locked_txt = ""
                if info.get("locked"):
                    who = str(info.get("locked_by_username") or "").strip()
                    locked_txt = f"In Work ({who})" if who else "In Work"

                item = QTreeWidgetItem(
                    [
                        info.get("name", ""),
                        locked_txt,
                        info.get("aes_number", ""),
                        info.get("type", ""),
                        str(info.get("revision", "") or ""),
                        info.get("status", ""),
                        errorSymbole,
                    ]
                )

                try:
                    item.setIcon(0, self._pick_indicator_icon(issues, part_id=part_id))
                except Exception:
                    pass

                item.setData(0, Qt.UserRole, info.get("id"))

                status = info.get("status", "").lower()
                if status == "released":
                    item.setForeground(5, QColor(0, 128, 0))
                elif status == "obsolete":
                    item.setForeground(5, QColor(255, 0, 0))

                if info.get("locked"):
                    item.setForeground(1, QColor(255, 0, 0))

                if parent_item is None:
                    self.tree.addTopLevelItem(item)
                else:
                    parent_item.addChild(item)

                children = info.get("children", [])
                if children:
                    try:
                        for c in children:
                            self._tree_build_queue.append((item, c))
                    except Exception:
                        pass

                self._last_tree_node_count += 1
                processed += 1

            if not self._tree_build_queue:
                self._tree_build_timer.stop()
                try:
                    self.tree.setUpdatesEnabled(True)
                except Exception:
                    pass

                # Expanding everything on large trees is very expensive.
                try:
                    if int(self._last_tree_node_count) <= 400:
                        self.tree.expandAll()
                    else:
                        self.tree.expandToDepth(1)
                except Exception:
                    pass

                self._set_tree_loading(False)

                # Fire once when the very first tree load finishes.
                try:
                    if (not self._initial_tree_ready_emitted) and self._initial_tree_target_seq is not None:
                        if int(self._tree_build_seq) == int(self._initial_tree_target_seq):
                            self._initial_tree_ready_emitted = True
                            self.initial_tree_ready.emit()
                except Exception:
                    pass
        except RuntimeError:
            try:
                self._tree_build_timer.stop()
            except Exception:
                pass
            return

    def _load_tree_impl(self, project_id: int) -> None:
        # Legacy internal method name kept for compatibility; now sync-calls the builder.
        if not project_id:
            return
        tree_data = self.bom_service.get_bom_tree(project_id) or {}
        missing_map = {}
        try:
            for bom_id, issue_type, _filename in (self.missing_files or []):
                missing_map.setdefault(int(bom_id), set()).add(str(issue_type))
        except Exception:
            missing_map = {}
        self._load_tree_from_data(tree_data, missing_map)

    def _enter_search_mode(self) -> None:
        """Switch the stack to the search-results view and prepare it for new results."""
        self._in_search_mode = True
        try:
            self._search_tree.clear()
        except Exception:
            pass
        try:
            self._tree_stack.setCurrentIndex(2)
        except Exception:
            pass

    def _exit_search_mode(self) -> None:
        """Restore the full-tree view (index 1). Zero data access — pure UI swap."""
        self._in_search_mode = False
        try:
            self._tree_stack.setCurrentIndex(1)
        except Exception:
            pass
        try:
            self._search_tree.clear()      # free search-result memory
        except Exception:
            pass

    def on_tree_item_clicked(self, item, column):
        item_id = item.data(0, Qt.UserRole)
        self.display_details(item_id)

    def display_details(self, item_id):
        self.current_part_id = item_id
        details = self.bom_service.get_part_details(item_id) or {}
        children = self.bom_service.get_children(item_id) or []
        # Detailed unified history across revisions
        history = self.bom_service.get_history_detailed(item_id, include_all_revisions=True) or []
        analytics = self.bom_service.get_history_analytics(item_id, include_all_revisions=True) or {}

        self.refresh_files_tab()

        if self.missing_files:
            missing_msgs = []
            for bom_id, issue_type, filename in self.missing_files:
                if bom_id == item_id:
                    if issue_type == 'missing_file':
                        missing_msgs.append(f"⚠️ Missing file: {filename} ")
                    elif issue_type == 'outdated_file':
                        missing_msgs.append(f"⚠️ Outdated file : {filename} is not the latest version in working directory.")
                    elif issue_type == 'missing_drawing':
                        missing_msgs.append(f"⚠️ Missing drawing: {filename}")
                    elif issue_type == 'missing_pdf':
                        missing_msgs.append(f"⚠️ Missing PDF: {filename}")
                    elif issue_type == 'missing_step':
                        missing_msgs.append(f"⚠️ Missing STEP: {filename}")
            if missing_msgs:
                self.show_alert(" | ".join(missing_msgs), "warning", "details")
            else:
                self.hide_alert("details")
        else:
            self.hide_alert("details")

        # Details table
        self.details_table.setRowCount(len(details))
        for i, (key, value) in enumerate(details.items()):
            self.details_table.setItem(i, 0, QTableWidgetItem(str(key)))
            self.details_table.setItem(i, 1, QTableWidgetItem(str(value)))

        # Children table
        self.children_table.setRowCount(len(children))
        for i, child in enumerate(children):
            self.children_table.setItem(i, 0, QTableWidgetItem(child.get("aes_number", "")))
            self.children_table.setItem(i, 1, QTableWidgetItem(child.get("name", "")))
            self.children_table.setItem(i, 2, QTableWidgetItem(child.get("type", "")))
            self.children_table.setItem(i, 3, QTableWidgetItem(str(child.get("quantity", ""))))

        # History panel (genius panel)
        self._history_rows = list(history)
        self.history_panel.set_data(history, analytics)

        self.notes_view.setText(details.get("notes", ""))

    def clear_details(self):
        self.current_part_id = None
        self.details_table.setRowCount(0)
        self.children_table.setRowCount(0)
        self.notes_view.clear()
        try:
            self.history_panel.clear()
            self._history_rows = []
        except Exception:
            pass
        self.refresh_files_tab()

    # ═══════════════════════════════════════════════════════════════════════
    #  History Panel Callbacks
    # ═══════════════════════════════════════════════════════════════════════

    def _open_history_details_dialog(self, ev_data: dict):
        """Open a rich details dialog for a history event.
        Called by HistoryPanel.open_details_requested signal with the full event dict."""
        if not ev_data:
            return

        ts = str(ev_data.get("timestamp", "") or "")
        ev_type = str(ev_data.get("event", "") or "")
        user = str(ev_data.get("user", "") or "")
        details_text = str(ev_data.get("details", "") or "")
        style = _style_for(ev_type)

        dlg = QDialog(self)
        dlg.setWindowTitle(f"{style['icon']}  History Details — {style['label']}")
        dlg.setMinimumSize(740, 480)
        layout = QVBoxLayout(dlg)
        layout.setSpacing(12)

        # ── Header card ───────────────────────────────────────────────
        header = QFrame()
        header.setStyleSheet(f"""
            QFrame {{
                background: {style['bg']};
                border: 1px solid {style['color']}40;
                border-left: 5px solid {style['color']};
                border-radius: 8px;
                padding: 10px;
            }}
        """)
        h_lay = QVBoxLayout(header)
        h_lay.setSpacing(4)

        badge_lbl = QLabel(f'  {style["icon"]}  {style["label"].upper()}  ')
        badge_lbl.setStyleSheet(f"""
            background: {style['color']}; color: #ffffff;
            border-radius: 10px; padding: 3px 12px;
            font-size: 12px; font-weight: bold;
        """)
        badge_lbl.setFixedWidth(badge_lbl.sizeHint().width() + 16)
        h_lay.addWidget(badge_lbl)

        rel = _relative_time(ts)
        ts_lbl = QLabel(f"📅  {ts}    ({rel})" if rel else f"📅  {ts}")
        ts_lbl.setStyleSheet(f"color: {style['color']}; font-size: 13px; font-weight: 600; border: none; background: transparent;")
        h_lay.addWidget(ts_lbl)

        info_parts = []
        if user:
            info_parts.append(f"👤 {user}")
        proj = str(ev_data.get("project", "") or "")
        ver = str(ev_data.get("version", "") or "")
        if proj:
            info_parts.append(f"📁 {proj}" + (f" ({ver})" if ver else ""))
        commit_id = str(ev_data.get("commit_id", "") or "")
        
        if commit_id:
            info_parts.append(f"🏷 {commit_id[:16]}")
        if info_parts:
            info_lbl = QLabel("    ".join(info_parts))
            info_lbl.setStyleSheet("color: #4b5563; font-size: 12px; border: none; background: transparent;")
            h_lay.addWidget(info_lbl)
        commit_unique_id = str(ev_data.get("commit_unique_id", "") or "")
        db_commit_info = None
        if commit_unique_id and commit_unique_id != "":
            try:
                project_id = getattr(self.session, "project_id", None)
                if project_id:
                    db_commit = self.commit_repo.get_commit_by_commitid(commit_unique_id, project_id)
                    if db_commit:
                        db_commit_info = db_commit
            except Exception:
                db_commit_info = None


        # --- Add extra commit-related info ---
        shown_keys = {"timestamp", "event", "user", "project", "version", "details", "commit_id", "step_diff_status", "step_diff_summary", "step_error", "step_file_path", "step_prev_file_path"}
        extra_commit_info = []
        for k, v in (ev_data.items() if isinstance(ev_data, dict) else []):
            if k in shown_keys:
                continue
            if v is not None and v != "":
                extra_commit_info.append(f"<b>{k}</b>: {v}")
        if extra_commit_info:
            extra_lbl = QLabel("<br>".join(extra_commit_info))
            extra_lbl.setStyleSheet("color: #374151; font-size: 11px; border: none; background: transparent;")
            extra_lbl.setTextFormat(Qt.RichText)
            h_lay.addWidget(extra_lbl)
            
        if db_commit_info:
            from dataclasses import asdict
            shown_keys = {"id", "commit_id", "part_id", "project_id"}
            extra_commit_info = []
            for k, v in asdict(db_commit_info).items():
                if k in shown_keys:
                    continue
                if v is not None and v != "":
                    extra_commit_info.append(f"<b>{k}</b>: {v}")
            if extra_commit_info:
                extra_lbl = QLabel("<br>".join(extra_commit_info))
                extra_lbl.setStyleSheet("color: #374151; font-size: 11px; border: none; background: transparent;")
                extra_lbl.setTextFormat(Qt.RichText)
                h_lay.addWidget(extra_lbl)
        

        layout.addWidget(header)

        # ── Details text ──────────────────────────────────────────────
        layout.addWidget(QLabel("Details:"))
        txt = QPlainTextEdit()
        txt.setReadOnly(True)
        txt.setStyleSheet("""
            QPlainTextEdit {
                background: #f9fafb; border: 1px solid #e5e7eb;
                border-radius: 6px; font-family: 'Consolas', 'Cascadia Mono', monospace;
                font-size: 11px; padding: 8px;
            }
        """)

        step_status = str(ev_data.get("step_diff_status") or "").strip()
        step_summary = str(ev_data.get("step_diff_summary") or "").strip()
        step_error = str(ev_data.get("step_error") or "").strip()
        extra = []
        if step_status:
            extra.append(f"STEP status: {step_status}")
        if step_summary:
            extra.append(f"STEP summary:\n{step_summary}")
        if step_error:
            extra.append(f"STEP error: {step_error}")
        full_text = details_text if not extra else f"{details_text}\n\n{'─' * 40}\n" + "\n".join(extra)
        txt.setPlainText(full_text)
        layout.addWidget(txt)

        # ── Action buttons ────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        if step_status and ev_type.upper() == "COMMIT":
            step_path = str(ev_data.get("step_file_path") or "").strip()
            if step_path:
                open_step = QPushButton("🔬  Open STEP in Viewer")
                open_step.setObjectName("neutral")
                open_step.clicked.connect(lambda: self._open_associated_step_for_history_event(ev_data))
                btn_row.addWidget(open_step)

            if step_status.upper() == "COMPARED":
                diff_btn = QPushButton("🔍  Show STEP Diff Zones")
                diff_btn.setObjectName("primary")
                diff_btn.clicked.connect(lambda: self._show_step_diff_for_history_event(ev_data))
                btn_row.addWidget(diff_btn)

        # Copy button
        copy_btn = QPushButton("📋  Copy to Clipboard")
        copy_btn.setObjectName("neutral")
        copy_btn.clicked.connect(lambda: self._copy_event_to_clipboard(ev_data))
        btn_row.addWidget(copy_btn)

        btn_row.addStretch()

        close_btn = QPushButton("Close")
        close_btn.setObjectName("neutral")
        close_btn.clicked.connect(dlg.close)
        btn_row.addWidget(close_btn)

        layout.addLayout(btn_row)
        dlg.exec_()

    def _copy_event_to_clipboard(self, ev_data: dict):
        from PyQt5.QtWidgets import QApplication
        lines = []
        for k in ("timestamp", "event", "user", "project", "version", "details",
                   "commit_id", "step_diff_status", "step_diff_summary"):
            v = ev_data.get(k)
            if v:
                lines.append(f"{k}: {v}")
        QApplication.clipboard().setText("\n".join(lines))

    def _open_associated_step_for_history_event(self, event_row: dict):
        step_path = str(event_row.get("step_file_path") or "").strip()
        if not step_path:
            QMessageBox.warning(self, "STEP Viewer", "No STEP file associated with this history row.")
            return
        if not os.path.exists(step_path):
            QMessageBox.warning(self, "STEP Viewer", f"STEP file not found:\n{step_path}")
            return

        try:
            from tools.CAD.step_viewer.launcher import launch_viewer
            launch_viewer(step_path)
        except Exception as e:
            QMessageBox.critical(self, "STEP Viewer", f"Failed to open STEP viewer:\n{e}")

    def _show_step_diff_for_history_event(self, event_row: dict):
        status = str(event_row.get("step_diff_status") or "").strip().upper()
        prev_path = str(event_row.get("step_prev_file_path") or "").strip()
        current_path = str(event_row.get("step_file_path") or "").strip()

        if status != "COMPARED":
            QMessageBox.information(
                self,
                "STEP Diff",
                "This commit has no previous STEP to compare (baseline or unavailable compare).",
            )
            return

        if not prev_path or not current_path:
            QMessageBox.warning(self, "STEP Diff", "STEP paths are missing for this commit.")
            return
        if not os.path.exists(prev_path) or not os.path.exists(current_path):
            QMessageBox.warning(self, "STEP Diff", "One or both STEP files are missing on disk.")
            return

        try:
            commit_id = str(event_row.get("commit_id") or "commit")
            from tools.CAD.step_viewer.launcher import launch_diff_viewer
            launch_diff_viewer(
                prev_path, current_path,
                commit_a=f"{commit_id}_prev", commit_b=commit_id,
            )
        except Exception as e:
            QMessageBox.critical(self, "STEP Diff", f"Failed to visualize STEP diff:\n{e}")

    def export_history_csv(self):
        rows = getattr(self, "_history_rows", []) or []
        if not rows:
            # also try from the panel
            try:
                rows = self.history_panel.get_all_rows()
            except Exception:
                pass
        if not rows:
            QMessageBox.information(self, "Export History", "No history rows to export.")
            return

        path, _ = QFileDialog.getSaveFileName(self, "Export History CSV", "history.csv", "CSV Files (*.csv)")
        if not path:
            return

        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["timestamp", "project", "version", "event", "user", "details",
                            "commit_id", "step_diff_status", "step_diff_summary"])
                for ev in rows:
                    w.writerow(
                        [
                            ev.get("timestamp", ""),
                            ev.get("project", ""),
                            ev.get("version", ""),
                            ev.get("event", ""),
                            ev.get("user", ""),
                            ev.get("details", ""),
                            ev.get("commit_id", ""),
                            ev.get("step_diff_status", ""),
                            ev.get("step_diff_summary", ""),
                        ]
                    )
            QMessageBox.information(self, "Export History", f"✅ Exported {len(rows)} events → {path}")
        except Exception as e:
            QMessageBox.critical(self, "Export History", f"Failed to export CSV:\n{e}")


    def show_alert(self, message: str, alert_type: str = "error", location: str = "global"):
        """Show an alert box with message and style depending on type."""
        if location == "global":
            target_frame = self.alert_frame
            target_label = self.alert_label
        elif location == "details":
            target_frame = self.details_alert_frame
            target_label = self.details_alert_label

        if alert_type == "error":
            target_frame.setStyleSheet("""
                QFrame { background-color: #fee2e2; border: 1px solid #f87171; border-radius: 6px; }
                QLabel { color: #991b1b; font-weight: bold; padding: 6px; }
            """)
        elif alert_type == "success":
            target_frame.setStyleSheet("""
                QFrame { background-color: #dcfce7; border: 1px solid #4ade80; border-radius: 6px; }
                QLabel { color: #166534; font-weight: bold; padding: 6px; }
            """)
        elif alert_type == "warning":
            target_frame.setStyleSheet("""
                QFrame { background-color: #fef9c3; border: 1px solid #facc15; border-radius: 6px; }
                QLabel { color: #713f12; font-weight: bold; padding: 6px; }
            """)
        else:
            target_frame.setStyleSheet("")  # Default

        target_label.setText(message)
        target_frame.show()

    def hide_alert(self, location: str = "global"):
        """Completely hide the alert frame."""
        if location == "global":
            self.alert_frame.hide()
        elif location == "details":
            self.details_alert_frame.hide()


    # -------------------------
    # Export
    # -------------------------
    def export_bom(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export BOM", "", "CSV Files (*.csv);;All Files (*)"
        )
        if not file_path:
            return
        try:
            # If your service supports exporting directly, call it. Otherwise dump tree.
            if hasattr(self.bom_service, "export_bom"):
                self.bom_service.export_bom(file_path)
            else:
                # Fallback: write CSV from current tree
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write("AES,Name,Type,Status\n")
                    # iterate top level
                    def recurse(item):
                        f.write(f'{item.text(0)},{item.text(1)},{item.text(2)},{item.text(3)}\n')
                        for i in range(item.childCount()):
                            recurse(item.child(i))
                    for i in range(self.tree.topLevelItemCount()):
                        recurse(self.tree.topLevelItem(i))
            QMessageBox.information(self, "Success", f"BOM exported to {file_path}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to export BOM: {str(e)}")

    def _make_indicator_icon(self, pdf_ok: bool, step_ok: bool, step_present: bool = True) -> QIcon:
        """Create a compact 2-dot icon: left=PDF, right=STEP. Red when missing/outdated."""
        size = 14
        pad = 1
        dot = 6
        pm = QPixmap(size * 2 + pad, size)
        pm.fill(Qt.transparent)

        painter = QPainter(pm)
        painter.setRenderHint(QPainter.Antialiasing, True)

        def draw_dot(x: int, ok: bool):
            color = QColor(34, 197, 94) if ok else QColor(239, 68, 68)  # green/red
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(x, (size - dot) // 2, dot, dot)

        # PDF dot
        draw_dot(pad, pdf_ok)

        # STEP dot (optionally not drawn if you ever want to hide it)
        if step_present:
            draw_dot(size + pad, step_ok)

        painter.end()
        return QIcon(pm)

    def _refresh_current_tree_item_indicator(self):
        """Fast refresh of just the selected tree item's PDF/STEP indicator."""
        try:
            item = self.tree.currentItem()
            if not item:
                return
            part_id = item.data(0, Qt.UserRole)
            if not part_id:
                return

            # Build issues set from current missing_map snapshot (from last sync_bom_files)
            issues: set = set()
            try:
                for bom_id, issue_type, _fn in (self.missing_files or []):
                    if int(bom_id) == int(part_id):
                        issues.add(str(issue_type))
            except Exception:
                issues = set()

            item.setIcon(0, self._pick_indicator_icon(issues, part_id=int(part_id)))
            # also refresh details warning banner (if open)
            try:
                if getattr(self, "current_part_id", None) == part_id:
                    self.display_details(int(part_id))
            except Exception:
                pass
        except Exception:
            pass

    def show_files_context_menu(self, position):
        if not getattr(self, "current_part_id", None):
            return
        file_id = self._selected_attachment_id()
        if not file_id:
            return

        # Determine doc type from selected attachment row
        try:
            file_type = str(self.files_table.item(self.files_table.currentRow(), 1).text() or "").upper()
        except Exception:
            file_type = ""

        menu = QMenu(self)
        if file_type in ("PDF", "STEP"):
            act = QAction(f"Set {file_type} up to date", self)

            def _do():
                part_mod = self._get_part_modified_dt(int(self.current_part_id))
                if not part_mod:
                    return QMessageBox.warning(self, "Up to date", "Part modified timestamp is not available.")
                self.part_doc_ack_service.mark_up_to_date(
                    int(self.current_part_id), file_type, part_mod.isoformat(sep=" ")
                )
                # fast refresh (avoid full tree rebuild)
                self._refresh_current_tree_item_indicator()

            act.triggered.connect(_do)
            menu.addAction(act)

        menu.exec_(self.files_table.viewport().mapToGlobal(position))

    def show_versions_context_menu(self, position):
        if not getattr(self, "current_part_id", None):
            return
        version_id = self._selected_version_id()
        if not version_id:
            return

        ver = self.part_file_service.repo.get_version_by_id(int(version_id))
        if not ver:
            return
        pf = self.part_file_service.repo.get_file_by_id(int(getattr(ver, "file_id", 0) or 0))
        if not pf:
            return

        file_type = str(getattr(pf, "file_type", "") or "").upper()
        menu = QMenu(self)
        if file_type in ("PDF", "STEP"):
            act = QAction(f"Set {file_type} up to date", self)

            def _do():
                part_mod = self._get_part_modified_dt(int(self.current_part_id))
                if not part_mod:
                    return QMessageBox.warning(self, "Up to date", "Part modified timestamp is not available.")
                self.part_doc_ack_service.mark_up_to_date(
                    int(self.current_part_id), file_type, part_mod.isoformat(sep=" ")
                )
                # fast refresh (avoid full tree rebuild)
                self._refresh_current_tree_item_indicator()

            act.triggered.connect(_do)
            menu.addAction(act)

        menu.exec_(self.versions_table.viewport().mapToGlobal(position))
