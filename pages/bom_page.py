from asyncio.windows_events import NULL
from PyQt5.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QGroupBox, QLineEdit, QPushButton, QListWidget, QTreeWidget,
    QListWidgetItem, QTreeWidgetItem, QSplitter, QTabWidget, QLabel, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QTextEdit, QComboBox, QSpinBox, QDateTimeEdit,
    QMessageBox, QInputDialog, QFileDialog, QMenu, QAction, QDialog, QDialogButtonBox, QFrame,
    QPlainTextEdit, QStackedWidget, QSizePolicy, QCheckBox, QGridLayout, QScrollArea,
    QGraphicsDropShadowEffect, QToolTip, QStyledItemDelegate, QStyle, QStyleOptionViewItem,
    QApplication,
)
from PyQt5.QtCore import Qt, QDateTime, pyqtSignal, QTimer, QObject, QThread, QSize, QRect, QEvent
from PyQt5.QtGui import QColor, QPen, QFont, QBrush, QCursor, QPalette, QFontMetrics
from datetime import datetime, timedelta
from pages.part_dialog import PartDialog
from collections import deque, Counter, defaultdict
import time
import json
import re

import os
import sys
import subprocess
import csv
from datetime import datetime

from core.session_manager import SessionManager
from core.services.diag_service import DiagService
from core.services.project_service import ProjectService
from core.services.part_file_service import PartFileService
from core.services.managed_file_service import ManagedFileService
from core.services.package_export_service import PackageExportService
from core.services.ui_permission import UIPermissionHelper
from core.services.baseline_service import BaselineService
from core.services.part_doc_ack_service import PartDocAckService
from core.services.issue_service import IssueService
from core.services.traceability_service import TraceabilityService
from core.services.assembly_configuration_service import AssemblyConfigurationService
from core.bom_filter import (
    deduplicate_bom_items_by_id,
    matches_bom_filter_text,
    split_bom_filter_terms,
)
from core.repositories.commit_repository import CommitRepository
from core.services.commit_service import CommitService
from pages.dialogs.package_parts_dialog import PackagePartsDialog
from pages.dialogs.assembly_iteration_compare_dialog import AssemblyIterationCompareDialog
from pages.dialogs.checkout_review_dialog import CheckoutReviewDialog
from pages.dialogs.assembly_configuration_dialogs import (
    CreateAssemblyConfigurationDialog,
    ManageAssemblyConfigurationsDialog,
)
from pages.dialogs.windchill_compare_dialog import WindchillCompareSetupDialog
from pages.pdf_viewer_widget import PdfViewerWidget
from utils import safe_exists, safe_startfile
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


class BomTreeWidget(QTreeWidget):
    reorderRequested = pyqtSignal(list, int, int, str)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self._branch_spinner_angle = 0
        self._loading_branch_items = set()
        self._branch_spinner_timer = QTimer(self)
        self._branch_spinner_timer.setInterval(45)
        self._branch_spinner_timer.timeout.connect(self._advance_branch_spinners)

    def setItemLoading(self, item: QTreeWidgetItem, loading: bool) -> None:
        if item is None:
            return
        item.setData(0, BOM_TREE_LOADING_ROLE, bool(loading))
        item_key = id(item)
        if loading:
            self._loading_branch_items.add(item_key)
            if not self._branch_spinner_timer.isActive():
                self._branch_spinner_timer.start()
        else:
            self._loading_branch_items.discard(item_key)
            if not self._loading_branch_items:
                self._branch_spinner_timer.stop()
        if loading:
            self.viewport().repaint()
        else:
            self.viewport().update()

    def resetLoadingIndicators(self) -> None:
        self._loading_branch_items.clear()
        self._branch_spinner_timer.stop()
        self.viewport().update()

    def _advance_branch_spinners(self) -> None:
        self._branch_spinner_angle = (self._branch_spinner_angle + 30) % 360
        self.viewport().update()

    def drawBranches(self, painter: QPainter, rect: QRect, index) -> None:
        super().drawBranches(painter, rect, index)
        try:
            item = self.itemFromIndex(index)
            if item is None or not item.data(0, BOM_TREE_LOADING_ROLE):
                return

            size = 10
            center_x = rect.right() - max(5, self.indentation() // 2)
            spinner_rect = QRect(
                center_x - size // 2,
                rect.center().y() - size // 2,
                size,
                size,
            )
            if item.isSelected():
                background = self.palette().brush(QPalette.Highlight)
            elif self.alternatingRowColors() and index.row() % 2:
                background = self.palette().brush(QPalette.AlternateBase)
            else:
                background = self.palette().brush(QPalette.Base)

            painter.save()
            painter.fillRect(spinner_rect.adjusted(-2, -2, 2, 2), background)
            painter.setRenderHint(QPainter.Antialiasing, True)
            pen = QPen(QColor("#2563eb"), 1.7)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawArc(
                spinner_rect,
                int(self._branch_spinner_angle * 16),
                int(245 * 16),
            )
            painter.restore()
        except Exception:
            return

    def dropEvent(self, event):
        target = self.itemAt(event.pos())
        selected = [item for item in self.selectedItems() if item is not None]
        if not target or not selected:
            event.ignore()
            return

        indicator = self.dropIndicatorPosition()
        if indicator not in (QAbstractItemView.AboveItem, QAbstractItemView.BelowItem):
            event.ignore()
            return

        target_id = target.data(0, Qt.UserRole)
        target_parent = target.parent()
        if target_parent is None:
            event.ignore()
            return
        engineering_parent = target_parent
        while engineering_parent is not None and engineering_parent.data(0, Qt.UserRole) is None:
            engineering_parent = engineering_parent.parent()
        target_parent_id = (
            engineering_parent.data(0, Qt.UserRole)
            if engineering_parent is not None else None
        )
        if any(item.parent() is not target_parent for item in selected):
            event.ignore()
            return
        if target in selected:
            event.ignore()
            return
        selected_ids = []
        for item in selected:
            try:
                selected_ids.append(int(item.data(0, Qt.UserRole)))
            except Exception:
                pass
        if len(selected_ids) != len(selected) or target_id is None or target_parent_id is None:
            event.ignore()
            return

        if indicator == QAbstractItemView.AboveItem:
            where = "above"
        else:
            where = "below"

        self.reorderRequested.emit(selected_ids, int(target_id), int(target_parent_id), where)
        event.acceptProposedAction()


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


# Role storing the optional "In Work" / "In Work (user)" suffix (painted beside name in column 0).
BOM_TREE_INWORK_ROLE = Qt.UserRole + 33
BOM_TREE_IS_ASSEMBLY_ROLE = Qt.UserRole + 35
# Value: dict with "pdf" / "step" -> tuple (kind: str, tooltip: str); kind in ok|outdated|missing|na
BOM_TREE_FILES_ROLE = Qt.UserRole + 36
# Value: dict { "state": "ok"|"warn", "tooltip": str }
BOM_TREE_INTEGRITY_ROLE = Qt.UserRole + 37
# Value: dict {active_count, total_count, critical_count}
BOM_TREE_ISSUE_ROLE = Qt.UserRole + 38
BOM_TREE_CATEGORY_ROLE = Qt.UserRole + 39
BOM_TREE_FOLDER_ROLE = Qt.UserRole + 40
BOM_TREE_CHILDREN_LOADED_ROLE = Qt.UserRole + 41
BOM_TREE_PATH_ROLE = Qt.UserRole + 42
BOM_TREE_PLACEHOLDER_ROLE = Qt.UserRole + 43
BOM_TREE_LOADING_ROLE = Qt.UserRole + 44
BOM_TREE_REPLACE_CHILDREN_ROLE = Qt.UserRole + 45
BOM_TREE_BINDING_UPDATE_ROLE = Qt.UserRole + 46
BOM_TREE_POLICY_ROLE = Qt.UserRole + 47
BOM_TREE_OCCURRENCE_ROLE = Qt.UserRole + 48
BOM_TREE_PROMOTION_ROLE = Qt.UserRole + 49
STRUCTURE_CURRENT_ITERATION_ROLE = Qt.UserRole + 60
STRUCTURE_BOUND_ITERATION_ROLE = Qt.UserRole + 61
STRUCTURE_LATEST_ITERATION_ROLE = Qt.UserRole + 62

_BOM_TYPE_ICON_CACHE = {}


def _bom_type_icon(part_type: str) -> QIcon:
    """Return a compact CAD-style icon for assembly and part tree rows."""
    key = str(part_type or "").strip().lower()
    if key in _BOM_TYPE_ICON_CACHE:
        return _BOM_TYPE_ICON_CACHE[key]
    if key == "folder":
        icon = QApplication.style().standardIcon(QStyle.SP_DirIcon)
        _BOM_TYPE_ICON_CACHE[key] = icon
        return icon
    if key not in {"asm", "assembly", "prt", "part"}:
        return QIcon()

    pixmap = QPixmap(14, 14)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)

    if key in {"asm", "assembly"}:
        painter.setPen(QPen(QColor("#315b7d"), 1))
        painter.setBrush(QBrush(QColor("#70a6c9")))
        painter.drawRoundedRect(1, 1, 6, 6, 1, 1)
        painter.setBrush(QBrush(QColor("#4f86a8")))
        painter.drawRoundedRect(7, 7, 6, 6, 1, 1)
        painter.setPen(QPen(QColor("#315b7d"), 1.2))
        painter.drawLine(6, 6, 8, 8)
    else:
        painter.setPen(QPen(QColor("#536b3f"), 1))
        painter.setBrush(QBrush(QColor("#8db36f")))
        painter.drawRoundedRect(2, 2, 10, 10, 1, 1)
        painter.setPen(QPen(QColor("#6f9254"), 1))
        painter.drawLine(2, 5, 7, 8)
        painter.drawLine(12, 5, 7, 8)
        painter.drawLine(7, 8, 7, 12)

    painter.end()
    icon = QIcon(pixmap)
    _BOM_TYPE_ICON_CACHE[key] = icon
    return icon

BOM_COL_ROW = 0
BOM_COL_NAME = 1
BOM_COL_FILES = 2
BOM_COL_AES = 3
BOM_COL_TYPE = 4
BOM_COL_REV = 5
BOM_COL_STATUS = 6
BOM_COL_INTEGRITY = 7
BOM_TREE_COLUMN_COUNT = 8
EBOM_COL_SOURCE_QTY = 8
EBOM_COL_EFFECTIVE_QTY = 9
EBOM_COL_LEVEL = 10

# Inline "In Work" beside name (spec)
_BOM_INWORK_COLOR = QColor("#BA7517")
_BOM_INWORK_GAP_PX = 6
_BOM_TREE_SEL_BG = "#e8eefc"
_BOM_TREE_ROW_TEXT = "#111827"

_FILE_BADGE_STYLES = {
    "ok": {
        "bg": "#EAF3DE", "fg": "#3B6D11", "dot": "#639922", "dash": False,
    },
    "outdated": {
        "bg": "#FAEEDA", "fg": "#854F0B", "dot": "#BA7517", "dash": False,
    },
    "missing": {
        "bg": "#FCEBEB", "fg": "#A32D2D", "dot": "#E24B4A", "dash": False,
    },
    "na": {
        "bg": "#F0F0F0", "fg": "#888888", "dot": "#AAAAAA", "dash": True,
    },
}

_STATUS_BADGE_STYLES = {
    "released": {"bg": "#EAF3DE", "fg": "#3B6D11"},
    "design": {"bg": "#E6F1FB", "fg": "#185FA5"},
    "in work": {"bg": "#FAEEDA", "fg": "#854F0B"},
    "obsolete": {"bg": "#F5F5F5", "fg": "#888888"},
}
# Modest corner radius — full pill (rx = half height) makes long labels look oblong.
_STATUS_BADGE_CORNER_RADIUS = 6


def _normalize_file_badge(doc_key: str, issues: set, doc_info: dict) -> tuple[str, str]:
    """Map PDF/STEP indicator state + integrity issues to badge kind and tooltip."""
    miss = f"missing_{doc_key}"
    out = f"outdated_{doc_key}"
    if miss in issues:
        tip = str((doc_info or {}).get("tooltip") or f"{doc_key.upper()}: missing — no file attached to this revision")
        return "missing", tip
    if out in issues:
        tip = str((doc_info or {}).get("tooltip") or f"{doc_key.upper()}: outdated — file is not the latest in working directory")
        return "outdated", tip
    state = str((doc_info or {}).get("state") or "absent").lower()
    tip = str((doc_info or {}).get("tooltip") or f"{doc_key.upper()}: unknown")
    tl = tip.lower()
    if state == "ok":
        return "ok", tip
    if state == "ack":
        return "ok", tip
    if state == "absent":
        return "na", tip
    if state == "bad":
        if "no attachment" in tl:
            return "missing", tip
        if "outdated" in tl or "newer" in tl or "review" in tl or "not the latest" in tl:
            return "outdated", tip
        if "missing" in tl:
            return "missing", tip
        return "outdated", tip
    return "na", tip


def _file_badges_payload(part_id, issues: set, summary: dict) -> dict:
    pdf_i = (summary or {}).get("pdf") or {}
    step_i = (summary or {}).get("step") or {}
    pk, pt = _normalize_file_badge("pdf", issues, pdf_i)
    sk, st = _normalize_file_badge("step", issues, step_i)
    return {"pdf": (pk, pt), "step": (sk, st)}


def _integrity_payload(part_id, missing_files, missing_ids: set) -> dict:
    lines = []
    try:
        pid = int(part_id)
    except Exception:
        pid = None
    if pid is not None:
        for row in (missing_files or []):
            try:
                bom_id, issue_type, filename = row
                if int(bom_id) != pid:
                    continue
                fn = str(filename or "")
                it = str(issue_type or "")
                if it == "missing_file":
                    lines.append(f"Missing file: {fn}")
                elif it == "outdated_file":
                    lines.append(f"Outdated file: {fn} is not the latest version in working directory.")
                elif it == "missing_drawing":
                    lines.append(f"Missing drawing: {fn}")
                elif it == "missing_pdf":
                    lines.append(f"Missing PDF: {fn}")
                elif it == "missing_step":
                    lines.append(f"Missing STEP: {fn}")
                else:
                    lines.append(f"{it}: {fn}".strip(": "))
            except Exception:
                continue
    warn = bool(lines) or (pid is not None and pid in (missing_ids or set()))
    if not warn:
        tip = "No integrity issues detected for this item."
    elif lines:
        tip = "\n".join(lines)
    else:
        tip = "BOM structure mismatch between PDF drawing and current assembly."
    return {"state": "warn" if warn else "ok", "tooltip": tip}


def _status_badge_key(raw: str) -> str | None:
    s = (raw or "").strip().lower()
    if not s:
        return None
    if "release" in s:
        return "released"
    if "obsolete" in s:
        return "obsolete"
    if "design" in s or "draft" in s:
        return "design"
    if "work" in s or "check" in s or "wip" in s:
        return "in work"
    return None


def _paint_file_pill(painter: QPainter, rect: QRect, label: str, kind: str) -> int:
    st = _FILE_BADGE_STYLES.get(kind, _FILE_BADGE_STYLES["na"])
    bg = QColor(st["bg"])
    fg = QColor(st["fg"])
    dot_col = QColor(st["dot"])

    f = QFont(painter.font())
    f.setPixelSize(10)
    f.setBold(True)
    painter.setFont(f)
    fm = QFontMetrics(f)
    text = (label or "").upper()
    dot_r = 5
    gap_after_dot = 4
    pad_h = 6
    pad_v = 2
    inner_w = dot_r + gap_after_dot + fm.horizontalAdvance(text)
    w = inner_w + pad_h * 2
    h = max(fm.height() + pad_v * 2, dot_r + pad_v * 2)
    pill = QRect(rect.left(), rect.center().y() - h // 2, w, h)

    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    if st.get("dash"):
        pen = QPen(QColor("#CCCCCC"))
        pen.setStyle(Qt.DashLine)
        pen.setWidthF(0.5)
        painter.setPen(pen)
        painter.setBrush(QColor(st["bg"]))
        painter.drawRoundedRect(pill, 4, 4)
    else:
        painter.setPen(Qt.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(pill, 4, 4)

    cx = pill.left() + pad_h + dot_r // 2
    cy = pill.center().y()
    painter.setPen(Qt.NoPen)
    painter.setBrush(dot_col)
    painter.drawEllipse(QRect(cx - dot_r // 2, cy - dot_r // 2, dot_r, dot_r))

    painter.setPen(fg)
    painter.setFont(f)
    text_rect = QRect(
        pill.left() + pad_h + dot_r + gap_after_dot,
        pill.top(),
        pill.width() - (pad_h * 2 + dot_r + gap_after_dot),
        pill.height(),
    )
    painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, text)
    painter.restore()
    return w


def _files_delegate_pill_rects(option_rect: QRect, payload: dict) -> tuple[QRect | None, QRect | None]:
    pdf = payload.get("pdf") or ("na", "")
    step = payload.get("step") or ("na", "")
    r = option_rect.adjusted(4, 0, -4, 0)
    f = QFont()
    f.setPixelSize(10)
    f.setBold(True)
    fm = QFontMetrics(f)

    def _pill_w(kind: str, label: str) -> int:
        text = label.upper()
        dot_r = 5
        gap_after_dot = 4
        pad_h = 6
        pad_v = 2
        inner_w = dot_r + gap_after_dot + fm.horizontalAdvance(text)
        return inner_w + pad_h * 2

    w_pdf = _pill_w(pdf[0], "PDF")
    w_step = _pill_w(step[0], "STEP")
    gap = 4
    h = max(20, fm.height() + 4)
    y = r.center().y() - h // 2
    x0 = r.left()
    pdf_rect = QRect(x0, y, w_pdf, h)
    step_rect = QRect(x0 + w_pdf + gap, y, w_step, h)
    return pdf_rect, step_rect


class _BomTreeNameDelegate(QStyledItemDelegate):
    """Renders part name + optional inline 'In Work' label in column 0."""

    def __init__(self, tree: QTreeWidget, parent=None):
        super().__init__(parent)
        self._tree = tree

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index):
        if index.column() != BOM_COL_NAME:
            return super().paint(painter, option, index)

        item = self._tree.itemFromIndex(index)
        if item is None:
            return super().paint(painter, option, index)

        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        name = item.text(BOM_COL_NAME) or ""
        suffix = item.data(0, BOM_TREE_INWORK_ROLE) or ""
        issue_summary = item.data(0, BOM_TREE_ISSUE_ROLE) or {}
        active_count = int(issue_summary.get("active_count") or 0)
        total_count = int(issue_summary.get("total_count") or 0)
        direct_active_count = int(issue_summary.get("direct_active_count", active_count) or 0)
        inherited_active_count = int(issue_summary.get("inherited_active_count") or 0)
        direct_total_count = int(issue_summary.get("direct_total_count", total_count) or 0)
        inherited_total_count = int(issue_summary.get("inherited_total_count") or 0)
        binding_update_count = int(item.data(0, BOM_TREE_BINDING_UPDATE_ROLE) or 0)
        policy = item.data(0, BOM_TREE_POLICY_ROLE) or {}
        promotion = item.data(0, BOM_TREE_PROMOTION_ROLE) or []
        widget = opt.widget or self._tree
        style = widget.style()

        opt.text = ""
        painter.save()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, widget)
        painter.restore()

        text_rect = style.subElementRect(QStyle.SE_ItemViewItemText, opt, widget)
        if text_rect.width() <= 0:
            text_rect = opt.rect

        # Keep normal (dark) text on selection — do not use HighlightedText (often white on blue).
        name_pen = QColor(_BOM_TREE_ROW_TEXT)

        is_asm = bool(item.data(0, BOM_TREE_IS_ASSEMBLY_ROLE)) or item.childCount() > 0
        name_font = QFont(opt.font)
        name_font.setPixelSize(11)
        name_font.setBold(bool(is_asm))
        display_inwork = "In Work" if suffix else ""
        issue_badges = []
        if direct_active_count:
            issue_badges.append((f"!{direct_active_count}", QColor("#c62828")))
        if inherited_active_count:
            issue_badges.append((f"+{inherited_active_count}", QColor("#2563eb")))
        if not issue_badges and total_count:
            if direct_total_count:
                issue_badges.append(("●", QColor("#2e7d32")))
            elif inherited_total_count:
                issue_badges.append(("+", QColor("#2563eb")))
        if binding_update_count:
            issue_badges.append((f"v+{binding_update_count}", QColor("#b45309")))
        classification = str(policy.get("classification") or "PHYSICAL").upper()
        is_representation = bool(policy.get("represented_part_id"))
        is_supplier_package = str(
            policy.get("cad_control_mode") or "CONTROLLED"
        ).upper() == "SUPPLIER_PACKAGE"
        if is_supplier_package:
            issue_badges.append(("SUPPLIER PACKAGE", QColor("#9a3412")))
        if is_representation:
            issue_badges.append(("CAD REP", QColor("#6d28d9")))
        elif classification != "PHYSICAL":
            issue_badges.append((classification.replace("_", " "), QColor("#6d28d9")))
        behavior = str(policy.get("resolved_ebom_behavior") or "NORMAL").upper()
        if is_representation:
            pass
        elif behavior == "FLATTEN":
            issue_badges.append(("FLATTEN", QColor("#0369a1")))
        elif behavior == "EXCLUDE":
            issue_badges.append(("NOT FOR DELIVERY", QColor("#b91c1c")))
        if promotion:
            issue_badges.append(("PROMOTED", QColor("#0f766e")))

        painter.save()
        painter.setFont(name_font)
        painter.setPen(name_pen)

        fm = QFontMetrics(name_font)
        issue_font = QFont(opt.font)
        issue_font.setPixelSize(11)
        issue_font.setBold(True)
        issue_fm = QFontMetrics(issue_font)
        issue_w = sum(issue_fm.horizontalAdvance(label) + 4 for label, _color in issue_badges)
        if len(issue_badges) > 1:
            issue_w += _BOM_INWORK_GAP_PX * (len(issue_badges) - 1)

        if display_inwork:
            suf_font = QFont(opt.font)
            suf_font.setPixelSize(10)
            suf_font.setBold(False)
            suf_font.setWeight(QFont.Normal)
            suf_fm = QFontMetrics(suf_font)
            half = max(48, text_rect.width() // 2)
            suf_elided = suf_fm.elidedText(display_inwork, opt.textElideMode, half)
            suf_w = suf_fm.horizontalAdvance(suf_elided)
            name_max = max(24, text_rect.width() - _BOM_INWORK_GAP_PX - suf_w - (_BOM_INWORK_GAP_PX + issue_w if issue_badges else 0))
            name_elided = fm.elidedText(name, opt.textElideMode, name_max)
        else:
            suf_font = None
            suf_elided = ""
            name_elided = fm.elidedText(
                name, opt.textElideMode,
                max(24, text_rect.width() - (_BOM_INWORK_GAP_PX + issue_w if issue_badges else 0)),
            )

        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, name_elided)
        name_w = fm.horizontalAdvance(name_elided)

        if display_inwork and suf_font is not None:
            painter.setFont(suf_font)
            suf_rect = QRect(
                text_rect.left() + name_w + _BOM_INWORK_GAP_PX,
                text_rect.top(),
                max(0, text_rect.right() - (text_rect.left() + name_w + _BOM_INWORK_GAP_PX)),
                text_rect.height(),
            )
            painter.setPen(_BOM_INWORK_COLOR)
            painter.drawText(suf_rect, Qt.AlignVCenter | Qt.AlignLeft, suf_elided)
            name_w += _BOM_INWORK_GAP_PX + QFontMetrics(suf_font).horizontalAdvance(suf_elided)
        if issue_badges:
            painter.setFont(issue_font)
            x = text_rect.left() + name_w + _BOM_INWORK_GAP_PX
            for label, color in issue_badges:
                label_w = issue_fm.horizontalAdvance(label) + 4
                painter.setPen(color)
                issue_rect = QRect(x, text_rect.top(), label_w, text_rect.height())
                painter.drawText(issue_rect, Qt.AlignVCenter | Qt.AlignLeft, label)
                x += label_w + _BOM_INWORK_GAP_PX
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index):
        sh = super().sizeHint(option, index)
        if index.column() != BOM_COL_NAME:
            return sh
        item = self._tree.itemFromIndex(index)
        if not item:
            return sh
        suffix = item.data(0, BOM_TREE_INWORK_ROLE) or ""
        if not suffix:
            return sh
        suf_font = QFont(option.font)
        suf_font.setPixelSize(10)
        extra = _BOM_INWORK_GAP_PX + QFontMetrics(suf_font).horizontalAdvance("In Work")
        return QSize(sh.width() + extra, max(sh.height(), 22, QFontMetrics(suf_font).height()))


class _BomTreeFilesDelegate(QStyledItemDelegate):
    """PDF + STEP pill badges in column 1."""

    def __init__(self, tree: QTreeWidget, parent=None):
        super().__init__(parent)
        self._tree = tree

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index):
        if index.column() != BOM_COL_FILES:
            return super().paint(painter, option, index)
        item = self._tree.itemFromIndex(index)
        if item is None:
            return super().paint(painter, option, index)

        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        opt.icon = QIcon()
        widget = opt.widget or self._tree
        style = widget.style()
        painter.save()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, widget)
        painter.restore()

        if item.data(0, BOM_TREE_FOLDER_ROLE):
            return

        policy = item.data(0, BOM_TREE_POLICY_ROLE) or {}
        if policy.get("represented_part_id"):
            return

        payload = item.data(BOM_COL_FILES, BOM_TREE_FILES_ROLE) or {}
        pdf = payload.get("pdf") or ("na", "")
        step = payload.get("step") or ("na", "")
        r = opt.rect.adjusted(4, 0, -4, 0)
        x = r.left()
        x += _paint_file_pill(painter, QRect(x, r.top(), 0, r.height()), "PDF", pdf[0])
        x += 4
        _paint_file_pill(painter, QRect(x, r.top(), 0, r.height()), "STEP", step[0])

    def helpEvent(self, event, view, option, index):
        if event.type() != QEvent.ToolTip or index.column() != BOM_COL_FILES:
            return super().helpEvent(event, view, option, index)
        item = self._tree.itemFromIndex(index)
        if not item:
            return super().helpEvent(event, view, option, index)
        policy = item.data(0, BOM_TREE_POLICY_ROLE) or {}
        if policy.get("represented_part_id"):
            QToolTip.showText(
                event.globalPos(),
                "CAD-only representation: PDF and STEP delivery files belong to the linked physical part.",
                view,
            )
            return True
        payload = item.data(BOM_COL_FILES, BOM_TREE_FILES_ROLE) or {}
        pdf_rect, step_rect = _files_delegate_pill_rects(option.rect, payload)
        try:
            pos = view.viewport().mapFromGlobal(event.globalPos())
        except Exception:
            try:
                pos = event.pos()
            except Exception:
                return super().helpEvent(event, view, option, index)
        pdf = payload.get("pdf") or ("na", "PDF: unknown")
        step = payload.get("step") or ("na", "STEP: unknown")
        tip = ""
        if pdf_rect.contains(pos):
            tip = str(pdf[1] or "")
        elif step_rect.contains(pos):
            tip = str(step[1] or "")
        if tip:
            QToolTip.showText(event.globalPos(), tip, view)
            return True
        return super().helpEvent(event, view, option, index)


class _BomTreeStatusDelegate(QStyledItemDelegate):
    """Status column as a pill badge."""

    def __init__(self, tree: QTreeWidget, parent=None):
        super().__init__(parent)
        self._tree = tree

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index):
        if index.column() != BOM_COL_STATUS:
            return super().paint(painter, option, index)
        item = self._tree.itemFromIndex(index)
        if item is None:
            return super().paint(painter, option, index)

        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        widget = opt.widget or self._tree
        style = widget.style()
        painter.save()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, widget)
        painter.restore()

        if item.data(0, BOM_TREE_FOLDER_ROLE):
            return

        raw = (item.text(BOM_COL_STATUS) or "").strip()
        key = _status_badge_key(raw)
        if not key:
            painter.save()
            painter.setPen(opt.palette.color(QPalette.Text))
            f = QFont(opt.font)
            f.setPixelSize(11)
            painter.setFont(f)
            painter.drawText(opt.rect.adjusted(6, 0, -6, 0), Qt.AlignVCenter | Qt.AlignLeft, raw)
            painter.restore()
            return

        st = _STATUS_BADGE_STYLES[key]
        bg = QColor(st["bg"])
        fg = QColor(st["fg"])
        f = QFont(opt.font)
        f.setPixelSize(11)
        painter.setFont(f)
        fm = QFontMetrics(f)
        label = raw or key.title()
        pad_h, pad_v = 10, 2
        w = fm.horizontalAdvance(label) + pad_h * 2
        h = fm.height() + pad_v * 2
        pill = QRect(
            opt.rect.left() + 6,
            opt.rect.center().y() - h // 2,
            min(w, opt.rect.width() - 12),
            h,
        )
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(bg)
        rr = min(_STATUS_BADGE_CORNER_RADIUS, max(2, pill.height() // 2))
        painter.drawRoundedRect(pill, rr, rr)
        painter.setPen(fg)
        painter.setFont(f)
        painter.drawText(pill, Qt.AlignCenter, label)
        painter.restore()

    def helpEvent(self, event, view, option, index):
        if event.type() != QEvent.ToolTip or index.column() != BOM_COL_STATUS:
            return super().helpEvent(event, view, option, index)
        item = self._tree.itemFromIndex(index)
        if item and (item.text(BOM_COL_STATUS) or "").strip():
            QToolTip.showText(event.globalPos(), item.text(BOM_COL_STATUS), view)
            return True
        return super().helpEvent(event, view, option, index)


class _BomTreeIntegrityDelegate(QStyledItemDelegate):
    """Integrity column: checkmark or warning."""

    def __init__(self, tree: QTreeWidget, parent=None):
        super().__init__(parent)
        self._tree = tree

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index):
        if index.column() != BOM_COL_INTEGRITY:
            return super().paint(painter, option, index)
        item = self._tree.itemFromIndex(index)
        if item is None:
            return super().paint(painter, option, index)

        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        widget = opt.widget or self._tree
        style = widget.style()
        painter.save()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, widget)
        painter.restore()

        if item.data(0, BOM_TREE_FOLDER_ROLE):
            return

        payload = item.data(BOM_COL_INTEGRITY, BOM_TREE_INTEGRITY_ROLE) or {"state": "ok"}
        state = str(payload.get("state") or "ok")
        sym = "✓" if state == "ok" else "⚠"
        col = QColor("#639922") if state == "ok" else QColor("#BA7517")
        f = QFont(opt.font)
        f.setPixelSize(14)
        painter.save()
        painter.setFont(f)
        painter.setPen(col)
        painter.drawText(opt.rect, Qt.AlignCenter, sym)
        painter.restore()

    def helpEvent(self, event, view, option, index):
        if event.type() != QEvent.ToolTip or index.column() != BOM_COL_INTEGRITY:
            return super().helpEvent(event, view, option, index)
        item = self._tree.itemFromIndex(index)
        if not item:
            return super().helpEvent(event, view, option, index)
        payload = item.data(BOM_COL_INTEGRITY, BOM_TREE_INTEGRITY_ROLE) or {}
        tip = str(payload.get("tooltip") or "")
        if tip:
            QToolTip.showText(event.globalPos(), tip, view)
            return True
        return super().helpEvent(event, view, option, index)


# ═══════════════════════════════════════════════════════════════════════════
#  EVENT STYLING CONFIG
# ═══════════════════════════════════════════════════════════════════════════
_EVENT_STYLES = {
    "COMMIT":               {"icon": "📦", "color": "#0078d7", "bg": "#e6f2ff", "label": "Commit"},
    "CHECKIN":              {"icon": "🔓", "color": "#16a34a", "bg": "#dcfce7", "label": "Check In"},
    "CHECKOUT":             {"icon": "🔒", "color": "#ea580c", "bg": "#ffedd5", "label": "Check Out"},
    "UNDO_CHECKOUT":        {"icon": "U", "color": "#64748b", "bg": "#f1f5f9", "label": "Undo Checkout"},
    "OBJECT_ITERATION":     {"icon": "I", "color": "#0369a1", "bg": "#e0f2fe", "label": "Iteration"},
    "REVISION_CREATED":     {"icon": "R", "color": "#0f766e", "bg": "#ccfbf1", "label": "New Revision"},
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
        self.setFixedHeight(60)
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
            ic.setStyleSheet("font-size: 12px; background: transparent; border: none;")
            top.addWidget(ic)
        self._value_lbl = QLabel(str(value))
        self._value_lbl.setStyleSheet(
            f"font-size: 15px; font-weight: bold; color: {accent}; background: transparent; border: none;"
        )
        top.addWidget(self._value_lbl)
        top.addStretch()
        lay.addLayout(top)

        self._label_lbl = QLabel(str(label))
        self._label_lbl.setStyleSheet(
            "font-size: 8px; color: #6b7280; font-weight: 500; background: transparent; border: none;"
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
                    background: #f3f4f6; color: #475569;
                    border: 1px solid #d1d5db; border-radius: 12px;
                    padding: 2px 10px; font-size: 11px; font-weight: 500;
                }}
                QPushButton:hover {{ background: #e5e7eb; color: #111827; }}
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
        self.setColumnCount(7)
        self.setHorizontalHeaderLabels(
            ["", "Timestamp", "Event", "Rev / Iter", "User", "Project", "Details"]
        )
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
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)  # object revision / iteration
        hh.setSectionResizeMode(4, QHeaderView.ResizeToContents)  # user
        hh.setSectionResizeMode(5, QHeaderView.ResizeToContents)  # project
        hh.setSectionResizeMode(6, QHeaderView.Stretch)           # details
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
                font-size: 8pt;
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
            ts_main.setStyleSheet("font-size: 8pt; color: #374151; font-weight: 500; background: transparent; border: none;")
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
                font-size: 8pt; font-weight: 600;
            """)
            badge_container = QWidget()
            badge_lay = QHBoxLayout(badge_container)
            badge_lay.setContentsMargins(2, 2, 2, 2)
            badge_lay.addWidget(badge)
            badge_lay.addStretch()
            self.setCellWidget(i, 2, badge_container)

            # Col 3: BOM object revision / iteration
            object_version = str(ev.get("object_version", "") or "")
            object_version_item = QTableWidgetItem(object_version)
            object_version_item.setTextAlignment(Qt.AlignCenter)
            object_version_item.setForeground(QBrush(QColor("#1d4ed8")))
            object_version_font = object_version_item.font()
            object_version_font.setBold(True)
            object_version_item.setFont(object_version_font)
            self.setItem(i, 3, object_version_item)

            # Col 4: user
            user_str = str(ev.get("user", "") or "")
            user_item = QTableWidgetItem(user_str)
            user_item.setForeground(QBrush(QColor("#374151")))
            fnt = user_item.font()
            fnt.setBold(True)
            user_item.setFont(fnt)
            self.setItem(i, 4, user_item)

            # Col 5: project / version
            proj = str(ev.get("project", "") or "")
            ver = str(ev.get("version", "") or "")
            proj_str = f"{proj} ({ver})" if proj and ver else proj or ver
            proj_item = QTableWidgetItem(proj_str)
            proj_item.setForeground(QBrush(QColor("#6b7280")))
            self.setItem(i, 5, proj_item)

            # Col 6: details
            details = str(ev.get("details", "") or "")
            det_item = QTableWidgetItem(details)
            det_item.setToolTip(details)
            det_item.setForeground(QBrush(QColor("#4b5563")))
            self.setItem(i, 6, det_item)

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
        self.setFont(QFont("Segoe UI", 8))
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
                padding: 5px 10px; font-size: 8pt;
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
        sel_all.setStyleSheet(
            "font-size: 10px; font-weight: 700; color: #111827; "
            "border-radius: 8px; background: #e5e7eb; border: none;"
        )
        sel_all.setCursor(Qt.PointingHandCursor)
        sel_all.clicked.connect(lambda: self._set_all_chips(True))
        chips_row.addWidget(sel_all)

        sel_none = QPushButton("None")
        sel_none.setFixedSize(40, 24)
        sel_none.setStyleSheet(
            "font-size: 10px; font-weight: 700; color: #111827; "
            "border-radius: 8px; background: #e5e7eb; border: none;"
        )
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
                                (
                                    "timestamp", "event", "object_version", "user",
                                    "details", "project", "version",
                                )).lower()
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
            tree_data = self._bom_service.get_bom_lazy_tree(self._project_id) or {}

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


class _LazyChildrenWorker(QObject):
    finished = pyqtSignal(int, object)
    failed = pyqtSignal(int, str)
    done = pyqtSignal()

    def __init__(self, request_id: int, bom_service, project_id: int, parent_id: int, parent_path: str):
        super().__init__()
        self._request_id = int(request_id)
        self._bom_service = bom_service
        self._project_id = int(project_id)
        self._parent_id = int(parent_id)
        self._parent_path = str(parent_path or "")
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            if self._cancelled:
                return
            nodes = self._bom_service.get_bom_lazy_children(
                self._project_id,
                self._parent_id,
                self._parent_path,
            ) or []
            if self._cancelled:
                return
            self.finished.emit(self._request_id, nodes)
        except Exception as exc:
            self.failed.emit(self._request_id, str(exc))
        finally:
            self.done.emit()


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
    issue_requested = pyqtSignal(int)
    create_issue_requested = pyqtSignal(int)

    def __init__(self, bom_service):
        super().__init__()
        self.setFont(QFont("Segoe UI", 8))
        self.bom_service = bom_service
        self.diag_service = DiagService()
        self.project_service = ProjectService()
        self.session = SessionManager()
        self.perm = UIPermissionHelper()
        self.part_file_service = PartFileService()
        self.managed_file_service = ManagedFileService(
            part_file_service=self.part_file_service
        )
        self.part_doc_ack_service = PartDocAckService()
        self.package_export_service = PackageExportService()
        self.baseline_service = BaselineService()
        self.commit_repo = CommitRepository()
        self.issue_service = IssueService()
        self.traceability_service = TraceabilityService()
        self.assembly_configuration_service = AssemblyConfigurationService()
        self._issue_summary_cache = {}

        self.working_dir = None
        self.commits_dir = None

        if self.session.project_id:
            self.working_dir = self.get_working_dir()
            if self.working_dir:
                self.commits_dir = self.working_dir + "/commits"
        
        self.current_part_aes = None
        self.current_part_id = None
        self._tree_load_seq = 0
        self._initial_tree_ready_emitted = False
        self._tree_thread = None
        self._tree_worker = None
        self._search_thread = None
        self._search_worker = None
        self._diag_thread = None
        self._diag_worker = None
        self._lazy_level_request_seq = 0
        self._lazy_level_requests = {}
        self._retired_lazy_level_requests = []

        self._tree_build_seq = 0
        self._tree_build_queue = deque()
        self._tree_build_missing_map = {}
        self._tree_build_timer = QTimer(self)
        self._tree_build_timer.setInterval(0)
        self._tree_build_timer.timeout.connect(self._tree_build_step)
        self._indicator_refresh_queue = deque()
        self._indicator_refresh_timer = QTimer(self)
        self._indicator_refresh_timer.setInterval(0)
        self._indicator_refresh_timer.timeout.connect(self._indicator_refresh_step)

        self._search_build_seq = 0
        self._search_build_queue = deque()
        self._search_build_timer = QTimer(self)
        self._search_build_timer.setInterval(0)
        self._search_build_timer.timeout.connect(self._search_build_step)

        self._last_tree_node_count = 0
        self._full_tree_cached = False
        self._cached_tree_data = None
        self._cached_missing_map = None
        self._bom_row_numbers = {}
        self._bom_folder_row_numbers = {}
        self._bom_folder_path_rows = {}
        self._lazy_tree_active = True
        self._lazy_tree_materialized = False
        self._in_search_mode = False     # True while _search_tree (index 2) is visible
        self._advanced_filter_flat_mode = False
        self._bom_advanced_filters = self._default_bom_advanced_filters()
        self._active_saved_filter_id = None
        self._active_saved_filter_name = ""
        self._advanced_filter_dialog = None
        self._bom_mode = "cad"
        self.init_ui()

        # Pre-render indicator icons (fast + consistent colors)
        self._indicator_icon_cache = {}
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
        self._doc_indicator_cache = {}

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
        self._cancel_lazy_level_requests(wait=True)
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
        try:
            self._indicator_refresh_timer.stop()
            self._indicator_refresh_queue.clear()
        except Exception:
            pass

    def _cancel_lazy_level_requests(self, wait: bool = False) -> None:
        requests = list((getattr(self, "_lazy_level_requests", {}) or {}).values())
        self._lazy_level_requests = {}
        if wait:
            requests.extend(getattr(self, "_retired_lazy_level_requests", []) or [])
            self._retired_lazy_level_requests = []
        for request in requests:
            item = request.get("item")
            try:
                self.tree.setItemLoading(item, False)
            except Exception:
                pass
            try:
                request["worker"].cancel()
            except Exception:
                pass
            try:
                request["thread"].quit()
            except Exception:
                pass
            if wait:
                try:
                    request["thread"].wait(1500)
                except Exception:
                    pass
            elif request not in self._retired_lazy_level_requests:
                self._retired_lazy_level_requests.append(request)
                try:
                    request["thread"].finished.connect(
                        lambda _request=request: self._forget_retired_lazy_request(_request)
                    )
                except Exception:
                    pass
        try:
            self.tree.resetLoadingIndicators()
        except Exception:
            pass

    def _forget_retired_lazy_request(self, request: dict) -> None:
        try:
            self._retired_lazy_level_requests.remove(request)
        except (ValueError, AttributeError):
            pass

    def _cancel_lazy_requests_for_parts(self, part_ids) -> None:
        wanted = {int(value) for value in (part_ids or [])}
        for request_id, request in list((getattr(self, "_lazy_level_requests", {}) or {}).items()):
            if int(request.get("part_id") or -1) not in wanted:
                continue
            self._lazy_level_requests.pop(int(request_id), None)
            try:
                self.tree.setItemLoading(request.get("item"), False)
            except Exception:
                pass
            try:
                request["worker"].cancel()
                request["thread"].quit()
            except Exception:
                pass
            if request not in self._retired_lazy_level_requests:
                self._retired_lazy_level_requests.append(request)
                try:
                    request["thread"].finished.connect(
                        lambda _request=request: self._forget_retired_lazy_request(_request)
                    )
                except Exception:
                    pass

    def _set_tree_loading(self, loading: bool) -> None:
        try:
            stack = getattr(self, "_tree_stack", None)
            if stack is None:
                return
            if loading:
                target = 0
            elif getattr(self, "_bom_mode", "cad") == "ebom":
                target = 3
            else:
                target = 2 if getattr(self, "_in_search_mode", False) else 1
            stack.setCurrentIndex(target)
        except Exception:
            pass

    def _on_bom_mode_changed(self, _index: int = 0) -> None:
        mode = str(self.bom_mode_selector.currentData() or "cad")
        self._bom_mode = mode
        if mode == "ebom":
            self._in_search_mode = False
            self._load_released_ebom_tree()
        else:
            if self.search_input.text().strip():
                self._perform_search_now()
            else:
                self._tree_stack.setCurrentIndex(1)
            if not self._is_default_bom_advanced_filter():
                self.apply_bom_tree_filter(self._bom_advanced_filters)
        read_only = mode == "ebom"
        try:
            self.add_part_btn.setEnabled(
                not read_only and self.perm.can("manage_parts")
            )
            self.add_folder_btn.setEnabled(
                not read_only and self.perm.can("manage_parts")
            )
        except Exception:
            pass
        if getattr(self, "_current_part_details", None):
            self._update_lifecycle_action_states(self._current_part_details)

    def _add_released_ebom_node(
        self, info: dict, parent_item: QTreeWidgetItem | None = None
    ) -> QTreeWidgetItem:
        payload = dict(info or {})
        payload["id"] = int(payload.get("bom_id") or payload.get("id"))
        payload["current_version"] = str(
            payload.get("version_label") or payload.get("current_version") or ""
        )
        payload["status"] = str(payload.get("state") or payload.get("status") or "")
        payload["relation_parent_id"] = payload.get("effective_parent_bom_id")
        payload["quantity"] = int(payload.get("source_quantity") or 1)
        payload["_has_children"] = bool(payload.get("children"))
        item = QTreeWidgetItem([""] * self._ebom_tree.columnCount())
        self._apply_tree_item_data(item, payload)
        item.setText(EBOM_COL_SOURCE_QTY, str(int(payload.get("source_quantity") or 1)))
        item.setText(
            EBOM_COL_EFFECTIVE_QTY,
            str(int(payload.get("effective_quantity") or 1)),
        )
        item.setText(EBOM_COL_LEVEL, str(int(payload.get("level") or 0)))
        promotion = list(payload.get("promoted_through") or [])
        if promotion:
            labels = " > ".join(
                str(value.get("aes_number") or value.get("name") or value.get("bom_id"))
                for value in promotion
            )
            item.setToolTip(
                EBOM_COL_EFFECTIVE_QTY,
                f"Promoted through {labels}; flattened quantities are multiplied.",
            )
        if parent_item is None:
            self._ebom_tree.addTopLevelItem(item)
        else:
            parent_item.addChild(item)
        for child in payload.get("children") or []:
            self._add_released_ebom_node(child, item)
        return item

    def _load_released_ebom_tree(self) -> None:
        self._set_tree_loading(True)
        try:
            QApplication.processEvents()
            data = self.bom_service.get_released_ebom_project(
                int(self.session.project_id)
            ) if self.session.project_id else {"roots": []}
            self._ebom_tree.setUpdatesEnabled(False)
            self._ebom_tree.clear()
            visible_roots = list(data.get("roots") or [])
            excluded_roots = list(data.get("excluded_roots") or [])
            flattened_roots = list(data.get("flattened_roots") or [])
            for root in visible_roots:
                self._add_released_ebom_node(root)
            self._renumber_tree_rows(self._ebom_tree)
            self._refresh_ebom_filters()
            self._ebom_tree.expandToDepth(1)
            self.bom_mode_selector.setToolTip(
                "Released EBOM contains only NORMAL deliverable rows. "
                f"Visible roots: {len(visible_roots)}; not-for-delivery roots hidden: "
                f"{len(excluded_roots)}; flattened CAD roots hidden: {len(flattened_roots)}."
            )
            if not visible_roots and (excluded_roots or flattened_roots):
                self.show_alert(
                    "Released EBOM has no visible deliverable root. In CAD Structure, "
                    "review items marked NOT FOR DELIVERY or FLATTEN.",
                    "warning",
                )
            else:
                self.hide_alert()
        except Exception as exc:
            self._ebom_tree.clear()
            self.show_alert(f"Released EBOM could not be resolved: {exc}", "error")
        finally:
            self._ebom_tree.setUpdatesEnabled(True)
            self._set_tree_loading(False)

    def _refresh_ebom_filters(self) -> int:
        tree = getattr(self, "_ebom_tree", None)
        if tree is None:
            return 0
        query = str(self.search_input.text() or "").strip()
        filters = self._bom_advanced_filters or self._default_bom_advanced_filters()
        advanced_active = not self._is_default_bom_advanced_filter(filters)
        show_parents = bool(filters.get("show_parent_matches", True))
        visible_count = 0

        def recurse(item):
            nonlocal visible_count
            haystack = " ".join(
                str(item.text(column) or "")
                for column in range(self._ebom_tree.columnCount())
            ) + " " + str(item.toolTip(BOM_COL_NAME) or "")
            basic_match = matches_bom_filter_text(haystack, query)
            advanced_match = (
                self._bom_tree_item_matches_advanced_filter(item, filters)
                if advanced_active else True
            )
            self_match = basic_match and advanced_match
            child_match = False
            for index in range(item.childCount()):
                child_match = recurse(item.child(index)) or child_match
            show = self_match or (show_parents and child_match)
            item.setHidden(not show)
            if self_match:
                visible_count += 1
            if child_match and (query or advanced_active):
                item.setExpanded(True)
            return show

        for index in range(tree.topLevelItemCount()):
            recurse(tree.topLevelItem(index))
        self._renumber_tree_rows(tree)
        return visible_count

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
        # Display the lazy root level immediately. Diagnostics are independent and
        # update badges later; disk scanning must never gate BOM navigation.
        self.load_tree()
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

        self._refresh_loaded_integrity_indicators()

    def _on_initial_diag_failed(self, _err: str) -> None:
        try:
            self.missing_files = []
            self.missing_ids = set()
        except Exception:
            pass

    def _refresh_loaded_integrity_indicators(self) -> None:
        self._invalidate_doc_indicator()
        for tree in (getattr(self, "tree", None), getattr(self, "_search_tree", None)):
            if tree is None:
                continue
            for item in self._iter_tree_items(tree):
                if self._is_folder_tree_item(item) or self._is_lazy_placeholder(item):
                    continue
                part_id = item.data(0, Qt.UserRole)
                item.setData(
                    BOM_COL_INTEGRITY,
                    BOM_TREE_INTEGRITY_ROLE,
                    _integrity_payload(part_id, self.missing_files, self.missing_ids),
                )
                self._indicator_refresh_queue.append((item, part_id))
        if self._indicator_refresh_queue and not self._indicator_refresh_timer.isActive():
            self._indicator_refresh_timer.start()



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

        filter_row = QHBoxLayout()
        self.advanced_filter_btn = QPushButton("Advanced Filter")
        self.advanced_filter_btn.clicked.connect(self.show_advanced_filter_dialog)
        self.saved_filters_btn = QPushButton("Saved Filters")
        self.saved_filters_btn.clicked.connect(self.show_saved_bom_filters_menu)
        self.clear_filter_btn = QPushButton("Clear")
        self.clear_filter_btn.clicked.connect(self.clear_bom_tree_filter)
        self.clear_filter_btn.setEnabled(False)
        filter_row.addWidget(self.advanced_filter_btn)
        filter_row.addWidget(self.saved_filters_btn)
        filter_row.addWidget(self.clear_filter_btn)
        left_layout.addLayout(filter_row)

        # Tree (BOM structure)
        tree_group = QFrame()
        tree_group.setStyleSheet("QFrame { background-color: #F5F5F5; border: none; }")
        tree_layout = QVBoxLayout(tree_group)
        tree_layout.setContentsMargins(8, 8, 8, 8)
        tree_layout.setSpacing(6)
        bom_header = QLabel("BOM STRUCTURE")
        bom_header.setStyleSheet("""
            QLabel {
                font-size: 8pt;
                font-weight: 700;
                color: #6b7280;
                letter-spacing: 0.08em;
                background: transparent;
                border: none;
                padding: 0px;
            }
        """)
        bom_header_row = QHBoxLayout()
        bom_header_row.addWidget(bom_header)
        self.bom_mode_selector = QComboBox()
        self.bom_mode_selector.addItem("CAD Structure", "cad")
        self.bom_mode_selector.addItem("Released EBOM", "ebom")
        self.bom_mode_selector.setFixedWidth(138)
        self.bom_mode_selector.setToolTip(
            "CAD Structure is authoritative and editable. Released EBOM is derived and read-only."
        )
        self.bom_mode_selector.currentIndexChanged.connect(
            self._on_bom_mode_changed
        )
        bom_header_row.addWidget(self.bom_mode_selector)
        self.bom_export_btn = QPushButton("Export")
        self.bom_export_btn.setObjectName("neutral")
        self.bom_export_btn.setFixedHeight(24)
        self.bom_export_btn.clicked.connect(self.export_bom)
        bom_header_row.addWidget(self.bom_export_btn)
        bom_header_row.addStretch()
        self.bom_health_label = QLabel("Health: --")
        self.bom_health_label.setStyleSheet(
            "font-size:8pt;font-weight:700;color:#475569;background:transparent;border:none;"
        )
        bom_header_row.addWidget(self.bom_health_label)
        tree_layout.addLayout(bom_header_row)

        self.tree = BomTreeWidget()
        try:
            self.tree.setIconSize(QSize(12, 12))
        except Exception:
            pass
        try:
            self.tree.setIndentation(14)
            self.tree.setAnimated(True)
            self.tree.setAlternatingRowColors(True)
            self.tree.setUniformRowHeights(True)
            self.tree.setMouseTracking(True)
            self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
            self.tree.itemEntered.connect(self._on_tree_item_entered)
        except Exception:
            pass
        self.tree.setHeaderLabels(["#", "Name", "Files", "AES Number", "Type", "Rev/Iter", "Status", "Integrity"])
        self.tree.setColumnWidth(BOM_COL_ROW, 38)
        self.tree.setColumnWidth(BOM_COL_NAME, 280)
        self.tree.setColumnWidth(BOM_COL_FILES, 100)
        self.tree.setColumnWidth(BOM_COL_AES, 90)
        self.tree.setColumnWidth(BOM_COL_TYPE, 60)
        self.tree.setColumnWidth(BOM_COL_REV, 65)
        self.tree.setColumnWidth(BOM_COL_STATUS, 90)
        self.tree.setColumnWidth(BOM_COL_INTEGRITY, 55)
        try:
            self.tree.setTreePosition(BOM_COL_NAME)
        except Exception:
            pass
        self.tree.itemClicked.connect(self.on_tree_item_clicked)
        self.tree.itemExpanded.connect(self._on_bom_item_expanded)
        try:
            self.tree.itemDoubleClicked.connect(self._on_tree_item_double_clicked)
        except Exception:
            pass
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.show_tree_context_menu)
        self.tree.reorderRequested.connect(self._handle_tree_drag_reorder)

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
        try:
            self._search_tree.setIconSize(QSize(12, 12))
        except Exception:
            pass
        try:
            self._search_tree.setIndentation(14)
            self._search_tree.setAnimated(True)
            self._search_tree.setAlternatingRowColors(True)
            self._search_tree.setUniformRowHeights(True)
            self._search_tree.setMouseTracking(True)
            self._search_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
            self._search_tree.itemEntered.connect(self._on_tree_item_entered)
        except Exception:
            pass
        self._search_tree.setHeaderLabels(["#", "Name", "Files", "AES Number", "Type", "Rev/Iter", "Status", "Integrity"])
        self._search_tree.setColumnWidth(BOM_COL_ROW, 38)
        self._search_tree.setColumnWidth(BOM_COL_NAME, 280)
        self._search_tree.setColumnWidth(BOM_COL_FILES, 100)
        self._search_tree.setColumnWidth(BOM_COL_AES, 90)
        self._search_tree.setColumnWidth(BOM_COL_TYPE, 60)
        self._search_tree.setColumnWidth(BOM_COL_REV, 65)
        self._search_tree.setColumnWidth(BOM_COL_STATUS, 90)
        self._search_tree.setColumnWidth(BOM_COL_INTEGRITY, 55)
        try:
            self._search_tree.setTreePosition(BOM_COL_NAME)
        except Exception:
            pass
        self._search_tree.itemClicked.connect(self.on_tree_item_clicked)
        try:
            self._search_tree.itemDoubleClicked.connect(self._on_tree_item_double_clicked)
        except Exception:
            pass
        self._search_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._search_tree.customContextMenuRequested.connect(self.show_tree_context_menu)
        self._tree_stack.addWidget(self._search_tree)          # index 2

        # The formal EBOM is a derived immutable-iteration view. It has its own
        # tree so CAD lazy-loading, selection, and editing state remain untouched.
        self._ebom_tree = QTreeWidget()
        self._ebom_tree.setHeaderLabels([
            "#", "Name", "Files", "AES Number", "Type", "Rev/Iter",
            "Status", "Integrity", "Source Qty", "Effective Qty", "Level",
        ])
        self._ebom_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._ebom_tree.setAlternatingRowColors(True)
        self._ebom_tree.setUniformRowHeights(True)
        self._ebom_tree.setIndentation(14)
        self._ebom_tree.setAnimated(True)
        self._ebom_tree.setMouseTracking(True)
        self._ebom_tree.setColumnWidth(BOM_COL_ROW, 38)
        self._ebom_tree.setColumnWidth(BOM_COL_NAME, 260)
        self._ebom_tree.setColumnWidth(BOM_COL_FILES, 100)
        self._ebom_tree.setColumnWidth(BOM_COL_AES, 90)
        self._ebom_tree.setColumnWidth(BOM_COL_TYPE, 60)
        self._ebom_tree.setColumnWidth(BOM_COL_REV, 65)
        self._ebom_tree.setColumnWidth(BOM_COL_STATUS, 75)
        self._ebom_tree.setColumnWidth(BOM_COL_INTEGRITY, 55)
        self._ebom_tree.setColumnWidth(EBOM_COL_SOURCE_QTY, 74)
        self._ebom_tree.setColumnWidth(EBOM_COL_EFFECTIVE_QTY, 84)
        self._ebom_tree.setColumnWidth(EBOM_COL_LEVEL, 45)
        self._ebom_tree.setTreePosition(BOM_COL_NAME)
        self._ebom_tree.itemClicked.connect(self.on_tree_item_clicked)
        self._ebom_tree.itemDoubleClicked.connect(self._on_tree_item_double_clicked)
        self._tree_stack.addWidget(self._ebom_tree)             # index 3

        _bom_tree_qss = f"""
            QTreeWidget {{
                background: #FFFFFF;
                border: 1px solid #d1d5db;
                border-radius: 6px;
                font-size: 10px;
                gridline-color: #e5e7eb;
                font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI", system-ui, sans-serif;
                font-weight: 400;
                letter-spacing: 0;
                text-transform: none;
                show-decoration-selected: 1;
            }}
            QHeaderView::section {{
                background-color: #EEEEEE;
                font-size: 10px;
                color: #374151;
                font-weight: 700;
                text-transform: uppercase;
                border-bottom: 0.5px solid #DDDDDD;
                padding: 4px;
                border-right: 1px solid #d1d5db;
            }}
            QTreeWidget::item {{
                height: 22px;
                border: none;
                border-bottom: 1px solid #EEEEEE;
                background: #FFFFFF;
                color: {_BOM_TREE_ROW_TEXT};
            }}
            QTreeWidget::item:alternate {{
                background: #FAFAFA;
            }}
            QTreeWidget::item:hover {{
                background: #f3f4f6;
            }}
            QTreeWidget::item:selected {{
                background: {_BOM_TREE_SEL_BG};
                color: {_BOM_TREE_ROW_TEXT};
            }}
            QTreeWidget::item:selected:active {{
                background: {_BOM_TREE_SEL_BG};
                color: {_BOM_TREE_ROW_TEXT};
            }}
            QTreeWidget::branch:selected {{
                background: {_BOM_TREE_SEL_BG};
            }}
            QTreeWidget::branch:selected:alternate {{
                background: {_BOM_TREE_SEL_BG};
            }}
        """
        self.tree.setStyleSheet(_bom_tree_qss)
        self._search_tree.setStyleSheet(_bom_tree_qss)
        self._ebom_tree.setStyleSheet(_bom_tree_qss)

        def _bom_tree_sel_palette(w):
            try:
                pal = w.palette()
                cbg = QColor(_BOM_TREE_SEL_BG)
                cfg = QColor(_BOM_TREE_ROW_TEXT)
                for cg in (QPalette.Active, QPalette.Inactive):
                    pal.setColor(cg, QPalette.Highlight, cbg)
                    pal.setColor(cg, QPalette.HighlightedText, cfg)
                w.setPalette(pal)
            except Exception:
                pass

        for _tw in (self.tree, self._search_tree, self._ebom_tree):
            try:
                _tw.setShowDecorationSelected(True)
            except Exception:
                pass
            _bom_tree_sel_palette(_tw)

        self.tree.setItemDelegateForColumn(BOM_COL_NAME, _BomTreeNameDelegate(self.tree, self.tree))
        self.tree.setItemDelegateForColumn(BOM_COL_FILES, _BomTreeFilesDelegate(self.tree, self.tree))
        self.tree.setItemDelegateForColumn(BOM_COL_STATUS, _BomTreeStatusDelegate(self.tree, self.tree))
        self.tree.setItemDelegateForColumn(BOM_COL_INTEGRITY, _BomTreeIntegrityDelegate(self.tree, self.tree))
        self._search_tree.setItemDelegateForColumn(BOM_COL_NAME, _BomTreeNameDelegate(self._search_tree, self._search_tree))
        self._search_tree.setItemDelegateForColumn(BOM_COL_FILES, _BomTreeFilesDelegate(self._search_tree, self._search_tree))
        self._search_tree.setItemDelegateForColumn(BOM_COL_STATUS, _BomTreeStatusDelegate(self._search_tree, self._search_tree))
        self._search_tree.setItemDelegateForColumn(BOM_COL_INTEGRITY, _BomTreeIntegrityDelegate(self._search_tree, self._search_tree))
        self._ebom_tree.setItemDelegateForColumn(BOM_COL_NAME, _BomTreeNameDelegate(self._ebom_tree, self._ebom_tree))
        self._ebom_tree.setItemDelegateForColumn(BOM_COL_FILES, _BomTreeFilesDelegate(self._ebom_tree, self._ebom_tree))
        self._ebom_tree.setItemDelegateForColumn(BOM_COL_STATUS, _BomTreeStatusDelegate(self._ebom_tree, self._ebom_tree))
        self._ebom_tree.setItemDelegateForColumn(BOM_COL_INTEGRITY, _BomTreeIntegrityDelegate(self._ebom_tree, self._ebom_tree))

        self._tree_stack.setCurrentIndex(1)

        tree_layout.addWidget(self._tree_stack)
        left_layout.addWidget(tree_group)

        # Right panel
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)

        # Tabs for details
        self.tabs = QTabWidget()

        # Details tab: compact engineering summary with an on-demand full attribute view.
        details_tab = QWidget()
        details_layout = QVBoxLayout(details_tab)
        details_layout.setContentsMargins(8, 8, 8, 8)
        details_layout.setSpacing(8)
        self._current_part_details = {}

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

        self.details_summary_card = QFrame()
        self.details_summary_card.setObjectName("bomDetailsSummary")
        self.details_summary_card.setStyleSheet("""
            QFrame#bomDetailsSummary {
                background: #ffffff;
                border: 1px solid #d7dde5;
                border-left: 4px solid #0b78d0;
                border-radius: 6px;
            }
            QLabel#bomDetailsTitle {
                color: #172033;
                font-size: 13px;
                font-weight: 700;
                background: transparent;
            }
            QLabel#bomDetailsIdentity {
                color: #526071;
                font-size: 10px;
                background: transparent;
            }
            QLabel#bomDetailsField {
                color: #66758a;
                font-size: 9px;
                font-weight: 700;
                background: transparent;
            }
            QLabel#bomDetailsValue {
                color: #172033;
                font-size: 10px;
                font-weight: 600;
                background: transparent;
            }
        """)
        summary_layout = QVBoxLayout(self.details_summary_card)
        summary_layout.setContentsMargins(14, 12, 14, 12)
        summary_layout.setSpacing(10)

        summary_heading = QHBoxLayout()
        heading_text = QVBoxLayout()
        heading_text.setSpacing(2)
        self.details_name_label = QLabel("Select a BOM item")
        self.details_name_label.setObjectName("bomDetailsTitle")
        self.details_identity_label = QLabel("Select a row in the BOM structure to view its summary.")
        self.details_identity_label.setObjectName("bomDetailsIdentity")
        heading_text.addWidget(self.details_name_label)
        heading_text.addWidget(self.details_identity_label)
        summary_heading.addLayout(heading_text, 1)
        self.view_full_details_btn = QPushButton("View Full Details")
        self.view_full_details_btn.setObjectName("neutral")
        self.view_full_details_btn.setEnabled(False)
        self.view_full_details_btn.clicked.connect(self._open_full_bom_details)
        self.edit_categories_btn = QPushButton("Edit Categories")
        self.edit_categories_btn.setObjectName("neutral")
        self.edit_categories_btn.setEnabled(False)
        self.edit_categories_btn.clicked.connect(self._edit_current_part_categories)
        summary_heading.addWidget(self.edit_categories_btn, 0, Qt.AlignTop)
        summary_heading.addWidget(self.view_full_details_btn, 0, Qt.AlignTop)
        summary_layout.addLayout(summary_heading)

        self.details_summary_grid = QGridLayout()
        self.details_summary_grid.setHorizontalSpacing(28)
        self.details_summary_grid.setVerticalSpacing(10)
        self._details_summary_fields = {}
        summary_fields = [
            ("aes_number", "AES Number", ("aes_number",)),
            ("part_number", "Part Number", ("part_number",)),
            ("drawing", "DRW Number", ("drawing_number",)),
            ("type", "Type", ("type",)),
            ("revision", "Revision / Iteration", ("current_version", "revision")),
            ("state", "Lifecycle", ("status", "state", "lifecycle_state")),
            ("material", "Material", ("material",)),
            ("categories", "Categories", ("categories",)),
        ]
        for index, (field_key, label_text, detail_keys) in enumerate(summary_fields):
            row = (index // 2) * 2
            column = (index % 2) * 2
            field_label = QLabel(label_text.upper())
            field_label.setObjectName("bomDetailsField")
            value_label = QLabel("-")
            value_label.setObjectName("bomDetailsValue")
            value_label.setWordWrap(True)
            self.details_summary_grid.addWidget(field_label, row, column)
            self.details_summary_grid.addWidget(value_label, row + 1, column)
            self._details_summary_fields[field_key] = (field_label, value_label, detail_keys)
        self.details_summary_grid.setColumnStretch(0, 1)
        self.details_summary_grid.setColumnStretch(2, 1)
        summary_layout.addLayout(self.details_summary_grid)
        details_layout.addWidget(self.details_summary_card)

        self.associated_files_card = QFrame()
        self.associated_files_card.setObjectName("associatedFilesCard")
        self.associated_files_card.setStyleSheet("""
            QFrame#associatedFilesCard {
                background: #ffffff;
                border: 1px solid #d7dde5;
                border-radius: 6px;
            }
            QLabel#associatedFilesTitle {
                color: #172033;
                font-size: 11px;
                font-weight: 700;
                background: transparent;
            }
            QLabel#associatedFilesLabel {
                color: #66758a;
                font-size: 9px;
                font-weight: 700;
                background: transparent;
            }
            QLabel#associatedFilesValue {
                color: #172033;
                font-size: 10px;
                font-weight: 600;
                background: transparent;
            }
        """)
        files_card_layout = QVBoxLayout(self.associated_files_card)
        files_card_layout.setContentsMargins(14, 10, 14, 10)
        files_card_layout.setSpacing(8)
        files_card_layout.addWidget(self._details_card_label("Associated Files", "associatedFilesTitle"))

        associated_files_grid = QGridLayout()
        associated_files_grid.setHorizontalSpacing(28)
        associated_files_grid.setVerticalSpacing(4)
        associated_files_grid.addWidget(self._details_card_label("CAD FILE", "associatedFilesLabel"), 0, 0)
        associated_files_grid.addWidget(self._details_card_label("DRAWING FILE", "associatedFilesLabel"), 0, 1)
        self.associated_cad_file_label = self._details_card_label("Not linked", "associatedFilesValue")
        self.associated_drawing_file_label = self._details_card_label("Not linked", "associatedFilesValue")
        self.associated_cad_file_label.setWordWrap(True)
        self.associated_drawing_file_label.setWordWrap(True)
        associated_files_grid.addWidget(self.associated_cad_file_label, 1, 0)
        associated_files_grid.addWidget(self.associated_drawing_file_label, 1, 1)
        associated_files_grid.setColumnStretch(0, 1)
        associated_files_grid.setColumnStretch(1, 1)
        files_card_layout.addLayout(associated_files_grid)
        details_layout.addWidget(self.associated_files_card)
        details_layout.addStretch(1)
        self.tabs.addTab(details_tab, "Details")

        

        # Windchill-style structure workspace: recursive Uses and direct Where Used.
        structure_tab = QWidget()
        structure_layout = QVBoxLayout(structure_tab)
        structure_layout.setContentsMargins(8, 8, 8, 8)
        structure_layout.setSpacing(6)
        self.structure_summary_label = QLabel("Select a BOM item")
        self.structure_summary_label.setStyleSheet(
            "font-size:10px;font-weight:700;color:#526071;background:transparent;"
        )
        structure_layout.addWidget(self.structure_summary_label)

        self.structure_views = QTabWidget()
        self.uses_tree = self._create_structure_relation_tree()
        self.where_used_tree = self._create_structure_relation_tree()
        self.effective_where_used_tree = self._create_structure_relation_tree()
        self.uses_tree.itemDoubleClicked.connect(self._open_structure_tree_item)
        self.where_used_tree.itemDoubleClicked.connect(self._open_structure_tree_item)
        self.effective_where_used_tree.itemDoubleClicked.connect(
            self._open_structure_tree_item
        )
        for relation_tree in (
            self.uses_tree, self.where_used_tree, self.effective_where_used_tree
        ):
            relation_tree.setContextMenuPolicy(Qt.CustomContextMenu)
            relation_tree.customContextMenuRequested.connect(self._show_structure_context_menu)
        self.structure_views.addTab(self.uses_tree, "Uses")
        self.structure_views.addTab(self.where_used_tree, "CAD Where Used")
        self.structure_views.addTab(
            self.effective_where_used_tree, "Effective EBOM Where Used"
        )
        structure_layout.addWidget(self.structure_views, 1)
        structure_actions = QHBoxLayout()
        self.compare_iterations_btn = QPushButton("Compare Iterations")
        self.compare_iterations_btn.setObjectName("neutral")
        self.compare_iterations_btn.clicked.connect(self.compare_assembly_iterations)
        self.compare_iterations_btn.setEnabled(False)
        self.update_child_versions_btn = QPushButton("Update Child Versions")
        self.update_child_versions_btn.setObjectName("neutral")
        self.update_child_versions_btn.clicked.connect(self.update_child_versions)
        self.update_child_versions_btn.setEnabled(False)
        structure_actions.addStretch()
        structure_actions.addWidget(self.compare_iterations_btn)
        structure_actions.addWidget(self.update_child_versions_btn)
        structure_layout.addLayout(structure_actions)
        self.tabs.addTab(structure_tab, "Structure")

        # Notes tab
        notes_tab = QWidget()
        notes_layout = QVBoxLayout(notes_tab)
        self.notes_view = QTextEdit()
        self.notes_view.setReadOnly(True)
        notes_layout.addWidget(QLabel("Notes:"))
        notes_layout.addWidget(self.notes_view)
        self.tabs.addTab(notes_tab, "Notes")

        # Engineering issues tab
        issues_tab = QWidget()
        issues_layout = QVBoxLayout(issues_tab)
        self.part_issues_table = QTableWidget()
        self.part_issues_table.setColumnCount(4)
        self.part_issues_table.setHorizontalHeaderLabels(["Issue", "Title", "Status", "Priority"])
        self.part_issues_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.part_issues_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.part_issues_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.part_issues_table.itemDoubleClicked.connect(lambda *_: self._open_current_part_issues())
        issue_actions = QHBoxLayout()
        open_issues_btn = QPushButton("Open Issue List")
        open_issues_btn.clicked.connect(self._open_current_part_issues)
        create_issue_btn = QPushButton("Create Issue")
        create_issue_btn.setObjectName("primary")
        create_issue_btn.clicked.connect(self._create_issue_for_current_part)
        issue_actions.addWidget(open_issues_btn)
        issue_actions.addWidget(create_issue_btn)
        issue_actions.addStretch()
        issues_layout.addWidget(self.part_issues_table)
        issues_layout.addLayout(issue_actions)
        self.tabs.addTab(issues_tab, "Issues")

        # Managed files: Creo content, generated outputs, and document history.
        files_tab = QWidget()
        self.files_tab = files_tab
        files_layout = QVBoxLayout(files_tab)

        files_summary = QFrame()
        files_summary.setObjectName("detailCard")
        summary_layout = QGridLayout(files_summary)
        self.files_summary_labels = {}
        for column, (key, title) in enumerate((
            ("item", "BOM Object"),
            ("version", "Rev / Iter"),
            ("state", "Lifecycle"),
            ("lock", "Checkout"),
        )):
            title_label = QLabel(title)
            title_label.setObjectName("mutedLabel")
            value_label = QLabel("-")
            value_label.setStyleSheet("font-weight: 600;")
            summary_layout.addWidget(title_label, 0, column)
            summary_layout.addWidget(value_label, 1, column)
            self.files_summary_labels[key] = value_label
        files_layout.addWidget(files_summary)

        files_layout.addWidget(QLabel("Current Managed Content"))
        self.files_table = FileDropTable()
        self.files_table.setColumnCount(10)
        self.files_table.setHorizontalHeaderLabels([
            "Role", "File", "File Revision", "Creo Ver", "Rev / Iter", "Source", "State", "Health", "Created By", "Updated"
        ])
        self.files_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.files_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.files_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.files_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.files_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.files_table.itemSelectionChanged.connect(self.on_attachment_selected)
        self.files_table.filesDropped.connect(self._on_files_dropped)
        self.files_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.files_table.customContextMenuRequested.connect(self.show_files_context_menu)
        files_layout.addWidget(self.files_table)

        files_actions = QHBoxLayout()
        self.add_attachment_btn = QPushButton("Attach Document")
        self.add_attachment_btn.setObjectName("primary")
        self.add_attachment_btn.clicked.connect(self.add_attachment)
        self.add_version_btn = QPushButton("Add Document Version")
        self.add_version_btn.setObjectName("neutral")
        self.add_version_btn.clicked.connect(self.add_attachment_version)
        self.set_active_btn = QPushButton("Use in Working Iteration")
        self.set_active_btn.setObjectName("neutral")
        self.set_active_btn.clicked.connect(self.set_active_version)
        self.open_active_btn = QPushButton("Open")
        self.open_active_btn.setObjectName("neutral")
        self.open_active_btn.clicked.connect(self.open_active_attachment)
        self.preview_file_btn = QPushButton("Preview")
        self.preview_file_btn.setObjectName("neutral")
        self.preview_file_btn.setCheckable(True)
        self.preview_file_btn.setToolTip("Show or collapse the selected PDF preview")
        self.preview_file_btn.toggled.connect(self._toggle_managed_preview)
        self.preview_file_btn.setEnabled(False)
        self.open_folder_btn = QPushButton("Open Folder")
        self.open_folder_btn.setObjectName("neutral")
        self.open_folder_btn.clicked.connect(self.open_active_attachment_folder)
        self.remove_attachment_btn = QPushButton("Obsolete Document")
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
        self.link_file_issue_btn = QPushButton("Link to Issue")
        self.link_file_issue_btn.setObjectName("neutral")
        self.link_file_issue_btn.clicked.connect(self.link_selected_file_to_issue)
        files_actions.addWidget(self.add_attachment_btn)
        files_actions.addWidget(self.add_version_btn)
        files_actions.addWidget(self.set_active_btn)
        files_actions.addWidget(self.open_active_btn)
        files_actions.addWidget(self.preview_file_btn)
        files_actions.addWidget(self.open_folder_btn)
        files_actions.addWidget(self.remove_attachment_btn)
        files_actions.addWidget(self.link_file_issue_btn)
        files_actions.addWidget(self.export_package_btn)
        files_actions.addWidget(self.create_baseline_btn)
        files_actions.addWidget(self.export_baseline_btn)
        files_layout.addLayout(files_actions)

        files_layout.addWidget(QLabel("Content History"))
        self.versions_table = QTableWidget()
        self.versions_table.setColumnCount(11)
        self.versions_table.setHorizontalHeaderLabels([
            "Rev / Iter", "Role", "File", "File Revision", "Creo Ver", "Source", "State", "Health", "Created By", "Created", "Note"
        ])
        self.versions_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.versions_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.versions_table.horizontalHeader().setSectionResizeMode(10, QHeaderView.Stretch)
        self.versions_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.versions_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.versions_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.versions_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.versions_table.customContextMenuRequested.connect(self.show_versions_context_menu)
        self.versions_table.itemSelectionChanged.connect(self._on_version_selection_changed)
        files_layout.addWidget(self.versions_table)

        files_layout.addWidget(QLabel("Related Issues:"))
        self.file_related_issues_table = QTableWidget()
        self.file_related_issues_table.setColumnCount(5)
        self.file_related_issues_table.setHorizontalHeaderLabels(["Issue", "Title", "Status", "Role", "Linked"])
        self.file_related_issues_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.file_related_issues_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        files_layout.addWidget(self.file_related_issues_table)

        versions_actions = QHBoxLayout()
        self.open_version_btn = QPushButton("Open Historical Content")
        self.open_version_btn.setObjectName("neutral")
        self.open_version_btn.clicked.connect(self.open_selected_version)

        _can_release = self.perm.can("release_files")
        self.add_attachment_btn.setEnabled(_can_release)
        self.add_version_btn.setEnabled(_can_release)
        self.set_active_btn.setEnabled(_can_release)
        self.remove_attachment_btn.setEnabled(_can_release)
        self.create_baseline_btn.setEnabled(_can_release)

        self.release_version_btn = QPushButton("Approve Document Version")
        self.release_version_btn.setObjectName("primary")
        self.release_version_btn.clicked.connect(self.release_selected_version)
        self.release_version_btn.setEnabled(_can_release)

        self.delete_version_btn = QPushButton("Obsolete Version")
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
        self.pdf_viewer.setVisible(False)
        files_layout.addWidget(self.pdf_viewer)

        self.tabs.addTab(files_tab, "Managed Files")

        # ── History tab (genius panel) ────────────────────────────────
        self.history_panel = HistoryPanel(self)
        self.history_panel.open_details_requested.connect(self._open_history_details_dialog)
        self.history_panel.open_step_requested.connect(self._open_associated_step_for_history_event)
        self.history_panel.show_diff_requested.connect(self._show_step_diff_for_history_event)
        self.history_panel._export_btn.clicked.connect(self.export_history_csv)
        self.tabs.addTab(self.history_panel, "📜 History")

        right_layout.addWidget(self.tabs)

        # Compact, category-based action ribbon.
        action_ribbon = QFrame()
        action_ribbon.setObjectName("actionRibbon")
        action_ribbon.setFixedHeight(52)
        action_layout = QHBoxLayout(action_ribbon)
        action_layout.setContentsMargins(3, 2, 3, 2)
        action_layout.setSpacing(2)
        self.add_part_btn = QPushButton("Add Part")
        self.add_part_btn.setObjectName("primary")
        self.add_part_btn.clicked.connect(self.add_part)
        self.add_folder_btn = QPushButton("Add Folder")
        self.add_folder_btn.setObjectName("neutral")
        self.add_folder_btn.clicked.connect(self.add_bom_folder)
        self.edit_part_btn = QPushButton("Edit Part")
        self.edit_part_btn.setObjectName("neutral")
        self.edit_part_btn.clicked.connect(self.edit_part)
        self.delete_part_btn = QPushButton("Delete Part")
        self.delete_part_btn.setObjectName("danger")
        self.delete_part_btn.clicked.connect(self.delete_part)
        self.undo_checkout_btn = QPushButton("Undo Checkout")
        self.undo_checkout_btn.setObjectName("neutral")
        self.undo_checkout_btn.clicked.connect(self.undo_checkout)
        self.checkout_part_btn = QPushButton("Check Out")
        self.checkout_part_btn.setObjectName("neutral")
        self.checkout_part_btn.clicked.connect(self.checkout_part)
        self.checkin_part_btn = QPushButton("Check In")
        self.checkin_part_btn.setObjectName("neutral")
        self.checkin_part_btn.clicked.connect(self.checkin_part)
        self.add_child_btn = QPushButton("Add Child")
        self.add_child_btn.setObjectName("primary")
        self.add_child_btn.clicked.connect(self.add_child)
        self.compare_structure_btn = QPushButton("Compare Structure")
        self.compare_structure_btn.setObjectName("neutral")
        self.compare_structure_btn.clicked.connect(lambda: self.compare_part_structure(whole_bom=True))
        self.create_configuration_btn = QPushButton("Create Configuration")
        self.create_configuration_btn.setObjectName("primary")
        self.create_configuration_btn.clicked.connect(self.create_assembly_configuration)
        self.create_configuration_btn.setEnabled(False)
        self.manage_configurations_btn = QPushButton("Manage Configurations")
        self.manage_configurations_btn.setObjectName("neutral")
        self.manage_configurations_btn.clicked.connect(self.manage_assembly_configurations)
        self.manage_configurations_btn.setEnabled(bool(self.session.project_id))

        _can_manage = self.perm.can("manage_parts")
        self.add_part_btn.setEnabled(_can_manage)
        self.add_folder_btn.setEnabled(_can_manage)
        self.edit_part_btn.setEnabled(_can_manage)
        self.delete_part_btn.setEnabled(_can_manage)
        self.add_child_btn.setEnabled(_can_manage)

        self.set_revision_btn = QPushButton("Create New Revision")
        self.set_revision_btn.setObjectName("neutral")
        self.set_revision_btn.clicked.connect(self.set_part_revision)
        self.set_revision_btn.setEnabled(self.perm.can("set_revision"))
        self.release_revision_btn = QPushButton("Release Revision")
        self.release_revision_btn.setObjectName("neutral")
        self.release_revision_btn.clicked.connect(self.release_part_revision)
        self.release_revision_btn.setEnabled(self.perm.can("set_revision"))

        action_buttons = (
            self.add_part_btn, self.add_child_btn, self.add_folder_btn,
            self.edit_part_btn, self.compare_structure_btn, self.delete_part_btn,
            self.checkout_part_btn, self.checkin_part_btn, self.undo_checkout_btn,
            self.set_revision_btn, self.release_revision_btn,
            self.create_configuration_btn, self.manage_configurations_btn,
        )
        for button in action_buttons:
            button.setFixedHeight(25)
            button.setIconSize(QSize(14, 14))
            button.setProperty("ribbonAction", True)
            button.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)

        style = self.style()
        action_specs = (
            (self.add_part_btn, "Add Part", QStyle.SP_FileIcon, "Add a new BOM item"),
            (self.edit_part_btn, "Edit", QStyle.SP_FileDialogDetailedView, "Edit the selected item"),
            (self.delete_part_btn, "Delete", QStyle.SP_TrashIcon, "Delete the selected item"),
            (self.checkout_part_btn, "Check Out", QStyle.SP_ArrowForward, "Check out the selected item"),
            (self.checkin_part_btn, "Check In", QStyle.SP_DialogApplyButton, "Review and finish the selected checkout"),
            (self.undo_checkout_btn, "Undo", QStyle.SP_ArrowBack, "Undo checkout"),
            (self.add_child_btn, "Add Child", QStyle.SP_ArrowDown, "Add a child to the selected assembly"),
            (self.add_folder_btn, "Folder", QStyle.SP_DirIcon, "Add an organization folder"),
            (self.compare_structure_btn, "Compare", QStyle.SP_FileDialogContentsView, "Compare BOM structures"),
            (self.set_revision_btn, "Create New Revision", QStyle.SP_FileDialogNewFolder, "Create a new revision"),
            (self.release_revision_btn, "Release Revision", QStyle.SP_DialogApplyButton, "Release the current revision"),
            (self.create_configuration_btn, "Create Configuration", QStyle.SP_FileDialogNewFolder, "Create an assembly configuration"),
            (self.manage_configurations_btn, "Manage Configurations", QStyle.SP_ComputerIcon, "Manage assembly configurations"),
        )
        for button, text_value, icon_type, tooltip in action_specs:
            button.setText(text_value)
            button.setIcon(style.standardIcon(icon_type))
            button.setToolTip(tooltip)

        self._action_ribbon_menu_bindings = []

        def make_menu_button(text_value, tooltip, icon_type, entries):
            button = QPushButton(text_value)
            button.setProperty("ribbonAction", True)
            button.setFixedHeight(25)
            button.setIconSize(QSize(14, 14))
            button.setIcon(style.standardIcon(icon_type))
            button.setToolTip(tooltip)
            button.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
            menu = QMenu(button)
            menu.setStyleSheet("QMenu { font-size: 10px; } QMenu::item { padding: 4px 18px 4px 22px; }")
            bindings = []
            for label, target in entries:
                action = QAction(target.icon(), label, menu)
                action.triggered.connect(lambda _checked=False, target=target: target.click())
                menu.addAction(action)
                bindings.append((action, target))
            self._action_ribbon_menu_bindings.append((button, bindings))
            menu.aboutToShow.connect(self._sync_action_ribbon_menus)
            button.setMenu(menu)
            return button

        self.revision_actions_btn = make_menu_button(
            "Revision",
            "Revision actions",
            QStyle.SP_FileDialogNewFolder,
            (
                ("Create New Revision", self.set_revision_btn),
                ("Release Revision", self.release_revision_btn),
            ),
        )
        self.configuration_actions_btn = make_menu_button(
            "Config",
            "Configuration actions",
            QStyle.SP_ComputerIcon,
            (
                ("Create Configuration", self.create_configuration_btn),
                ("Manage Configurations", self.manage_configurations_btn),
            ),
        )

        def add_action_category(title, buttons):
            category = QFrame()
            category.setObjectName("actionCategory")
            category_layout = QVBoxLayout(category)
            category_layout.setContentsMargins(3, 0, 3, 1)
            category_layout.setSpacing(0)
            title_label = QLabel(title)
            title_label.setObjectName("actionCategoryTitle")
            title_label.setAlignment(Qt.AlignCenter)
            title_label.setFixedHeight(15)
            command_layout = QHBoxLayout()
            command_layout.setContentsMargins(0, 0, 0, 0)
            command_layout.setSpacing(1)
            for button in buttons:
                command_layout.addWidget(button)
            category_layout.addWidget(title_label)
            category_layout.addLayout(command_layout)
            action_layout.addWidget(category)

        add_action_category("Editing", (self.add_part_btn, self.edit_part_btn, self.delete_part_btn))
        add_action_category(
            "Check Out/In",
            (self.checkout_part_btn, self.checkin_part_btn, self.undo_checkout_btn),
        )
        add_action_category("New/Add To", (self.add_child_btn, self.add_folder_btn))
        add_action_category("Lifecycle", (self.revision_actions_btn,))
        add_action_category("Tools", (self.compare_structure_btn, self.configuration_actions_btn))
        action_layout.addStretch(1)
        action_ribbon.setStyleSheet(
            "QFrame#actionRibbon { background: #f8fafc; border: 1px solid #cbd5e1; border-radius: 3px; }"
            "QFrame#actionCategory { background: transparent; border: 0; border-right: 1px solid #cbd5e1; }"
            "QLabel#actionCategoryTitle { color: #4f6f94; font-size: 9px; font-weight: 600; border: 0; }"
            "QPushButton[ribbonAction=\"true\"] { background: transparent; color: #1f2937; border: 1px solid transparent; "
            "font-size: 10px; padding: 1px 4px; }"
            "QPushButton[ribbonAction=\"true\"]:hover { background: #e7f0fb; border-color: #a9c7e8; }"
            "QPushButton[ribbonAction=\"true\"]:pressed { background: #d7e7f8; }"
            "QPushButton[ribbonAction=\"true\"]:disabled { color: #9ca3af; background: transparent; }"
        )
        self._sync_action_ribbon_menus()
        right_layout.addWidget(action_ribbon)

        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)

        # Set minimum widths
        left_widget.setMinimumWidth(600)
        right_widget.setMinimumWidth(600)

        # Equal stretch = 50% / 50%
        splitter.setStretchFactor(0, 1)  # left
        splitter.setStretchFactor(1, 1)  # right

    def _sync_action_ribbon_menus(self) -> None:
        for menu_button, bindings in getattr(self, "_action_ribbon_menu_bindings", []):
            has_enabled_action = False
            for action, target_button in bindings:
                enabled = target_button.isEnabled()
                action.setEnabled(enabled)
                has_enabled_action = has_enabled_action or enabled
            menu_button.setEnabled(has_enabled_action)

    # -------------------------
    # Context menu / tree actions
    # -------------------------
    def _folder_record(self, folder_id: int) -> dict:
        for folder in getattr(self, "_bom_folders_cache", []) or []:
            try:
                if int(folder.get("id")) == int(folder_id):
                    return dict(folder)
            except Exception:
                continue
        try:
            folders = self.bom_service.list_bom_folders() or []
            self._bom_folders_cache = list(folders)
            return next(
                (dict(folder) for folder in folders if int(folder.get("id")) == int(folder_id)),
                {},
            )
        except Exception:
            return {}

    def add_bom_folder(self, parent_item=None, parent_folder_id=None) -> None:
        if not self.perm.can("manage_parts"):
            return
        selected = parent_item if isinstance(parent_item, QTreeWidgetItem) else None
        parent_bom_id = None
        if parent_folder_id is None and selected is not None:
            selected_folder_id = selected.data(0, BOM_TREE_FOLDER_ROLE)
            if selected_folder_id:
                parent_folder_id = int(selected_folder_id)
            else:
                selected_id = selected.data(0, Qt.UserRole)
                selected_type = str(selected.text(BOM_COL_TYPE) or "").strip().lower()
                if selected_id is not None and selected_type in {"asm", "assembly"}:
                    parent_bom_id = int(selected_id)

        if parent_folder_id is not None:
            parent_folder = self._folder_record(int(parent_folder_id))
            location = f"inside folder '{parent_folder.get('name') or 'Folder'}'"
        elif parent_bom_id is not None:
            parent = self.bom_service.get_part_details(int(parent_bom_id)) or {}
            location = f"inside assembly '{parent.get('name') or parent_bom_id}'"
        else:
            location = "at the project root"
        name, accepted = QInputDialog.getText(
            self, "Add BOM Folder", f"Folder name ({location}):"
        )
        if not accepted:
            return
        try:
            folder = self.bom_service.create_bom_folder(
                name, parent_bom_id=parent_bom_id, parent_folder_id=parent_folder_id
            )
            self._refresh_folder_context(folder.get("effective_parent_bom_id"))
        except Exception as exc:
            QMessageBox.critical(self, "Add BOM Folder", f"Could not create folder:\n{exc}")

    def _assign_bom_folder_items(self, folder_id: int) -> None:
        folder = self._folder_record(int(folder_id))
        try:
            eligible = self.bom_service.eligible_bom_folder_items(int(folder_id)) or []
        except Exception as exc:
            QMessageBox.critical(self, "Folder Items", f"Could not load eligible items:\n{exc}")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Items in {folder.get('name') or 'Folder'}")
        dialog.resize(560, 480)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Select the direct BOM items to display in this folder."))
        item_list = QListWidget()
        item_list.setAlternatingRowColors(True)
        for part in eligible:
            label = f"{part.get('aes_number') or '-'}  {part.get('name') or ''}  [{part.get('type') or ''}]"
            list_item = QListWidgetItem(label)
            list_item.setData(Qt.UserRole, int(part["id"]))
            list_item.setFlags(list_item.flags() | Qt.ItemIsUserCheckable)
            list_item.setCheckState(Qt.Checked if part.get("assigned") else Qt.Unchecked)
            item_list.addItem(list_item)
        layout.addWidget(item_list, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        layout.addWidget(buttons)

        def save_items():
            selected_ids = [
                int(item_list.item(row).data(Qt.UserRole))
                for row in range(item_list.count())
                if item_list.item(row).checkState() == Qt.Checked
            ]
            try:
                self.bom_service.set_bom_folder_items(int(folder_id), selected_ids)
                self._refresh_folder_context(folder.get("effective_parent_bom_id"))
                dialog.accept()
            except Exception as exc:
                QMessageBox.critical(dialog, "Folder Items", f"Could not save folder items:\n{exc}")

        buttons.accepted.connect(save_items)
        buttons.rejected.connect(dialog.reject)
        dialog.exec_()

    def _rename_bom_folder(self, folder_id: int) -> None:
        folder = self._folder_record(int(folder_id))
        name, accepted = QInputDialog.getText(
            self, "Rename BOM Folder", "Folder name:", text=str(folder.get("name") or "")
        )
        if not accepted:
            return
        try:
            updated = self.bom_service.rename_bom_folder(int(folder_id), name)
            self._refresh_folder_context(updated.get("effective_parent_bom_id"))
        except Exception as exc:
            QMessageBox.critical(self, "Rename BOM Folder", f"Could not rename folder:\n{exc}")

    def _delete_bom_folder(self, folder_id: int) -> None:
        folder = self._folder_record(int(folder_id))
        answer = QMessageBox.question(
            self,
            "Delete BOM Folder",
            f"Delete folder '{folder.get('name') or 'Folder'}'?\n\n"
            "Its subfolders will also be deleted. BOM items will return to their normal "
            "structure position; no parts or engineering relations will be deleted.",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            return
        context = folder.get("effective_parent_bom_id")
        try:
            self.bom_service.delete_bom_folder(int(folder_id))
            self._refresh_folder_context(context)
            self._clear_folder_selection()
        except Exception as exc:
            QMessageBox.critical(self, "Delete BOM Folder", f"Could not delete folder:\n{exc}")

    def _show_folder_context_menu(self, tree: QTreeWidget, item: QTreeWidgetItem, folder_id: int) -> None:
        menu = QMenu(self)
        assign_action = menu.addAction("Assign Items...")
        subfolder_action = menu.addAction("Add Subfolder")
        rename_action = menu.addAction("Rename Folder")
        menu.addSeparator()
        delete_action = menu.addAction("Delete Folder")
        can_manage = self.perm.can("manage_parts")
        for action in (assign_action, subfolder_action, rename_action, delete_action):
            action.setEnabled(can_manage)
        assign_action.triggered.connect(lambda: self._assign_bom_folder_items(int(folder_id)))
        subfolder_action.triggered.connect(
            lambda: self.add_bom_folder(parent_item=item, parent_folder_id=int(folder_id))
        )
        rename_action.triggered.connect(lambda: self._rename_bom_folder(int(folder_id)))
        delete_action.triggered.connect(lambda: self._delete_bom_folder(int(folder_id)))
        menu.exec_(tree.viewport().mapToGlobal(tree.visualItemRect(item).bottomLeft()))

    def show_tree_context_menu(self, position):
        tree = self.sender()
        if not isinstance(tree, QTreeWidget):
            tree = self.tree
        item = tree.itemAt(position)
        if not item:
            if tree is self.tree and self.perm.can("manage_parts"):
                menu = QMenu(self)
                create_folder_action = menu.addAction("Create Top-Level Folder")
                create_folder_action.triggered.connect(lambda: self.add_bom_folder())
                menu.exec_(tree.viewport().mapToGlobal(position))
            return
        try:
            if item not in tree.selectedItems():
                tree.clearSelection()
                item.setSelected(True)
                tree.setCurrentItem(item)
        except Exception:
            pass

        folder_id = item.data(0, BOM_TREE_FOLDER_ROLE)
        if folder_id:
            self._show_folder_context_menu(tree, item, int(folder_id))
            return

        item_id = item.data(0, Qt.UserRole)
        menu = QMenu()

        open_pdf_act = QAction("Open PDF", self)
        open_pdf_act.triggered.connect(lambda _=False, pid=item_id: self.open_part_pdf(int(pid)))
        preview_pdf_act = QAction("Preview PDF", self)
        preview_pdf_act.triggered.connect(lambda _=False, pid=item_id: self.preview_part_pdf(int(pid)))
        open_step_act = QAction("Open STEP", self)
        open_step_act.triggered.connect(lambda _=False, pid=item_id: self.open_part_step(int(pid)))
        view_action = QAction("View Item Details", self)
        view_action.triggered.connect(lambda: self.display_details(item_id))
        current_version = str(item.text(BOM_COL_REV) or "").strip()
        view_cad_files_action = QAction(
            f"View Latest CAD Files ({current_version})" if current_version else "View Latest CAD Files",
            self,
        )
        view_cad_files_action.triggered.connect(
            lambda _=False, pid=item_id: self._show_latest_bom_cad_files(int(pid))
        )
        compare_iterations_action = QAction("Compare Assembly Iterations...", self)
        compare_iterations_action.triggered.connect(
            lambda _=False, pid=item_id: self.compare_assembly_iterations(int(pid))
        )
        create_configuration_action = QAction("Create Configuration...", self)
        create_configuration_action.triggered.connect(
            lambda _=False, pid=item_id: self.create_assembly_configuration(int(pid))
        )
        compare_action = QAction("Compare to Part Structure", self)
        compare_action.triggered.connect(lambda _=False, pid=item_id: self.compare_part_structure(int(pid)))
        refresh_files_act = QAction("Refresh Files", self)
        refresh_files_act.triggered.connect(lambda _=False, pid=item_id: self._refresh_part_in_tree(int(pid)))

        menu.addAction(preview_pdf_act)
        menu.addAction(open_pdf_act)
        menu.addAction(open_step_act)
        menu.addAction(view_action)
        menu.addAction(view_cad_files_action)
        if str(item.text(BOM_COL_TYPE) or "").strip().lower() in {"asm", "assembly"}:
            menu.addAction(compare_iterations_action)
            menu.addAction(create_configuration_action)
        menu.addAction(compare_action)
        menu.addSeparator()
        menu.addAction(refresh_files_act)
        if (
            tree is self.tree
            and item.parent() is not None
            and not self._is_folder_tree_item(item.parent())
        ):
            reorder_menu = menu.addMenu("Reorder")
            move_up_act = QAction("Move Up", self)
            move_down_act = QAction("Move Down", self)
            move_top_act = QAction("Move To Top", self)
            move_bottom_act = QAction("Move To Bottom", self)
            move_position_act = QAction("Move To Position...", self)
            move_up_act.triggered.connect(lambda _=False: self._reorder_selected_siblings("up"))
            move_down_act.triggered.connect(lambda _=False: self._reorder_selected_siblings("down"))
            move_top_act.triggered.connect(lambda _=False: self._reorder_selected_siblings("top"))
            move_bottom_act.triggered.connect(lambda _=False: self._reorder_selected_siblings("bottom"))
            move_position_act.triggered.connect(lambda _=False: self._reorder_selected_siblings("position"))
            for action in (move_up_act, move_down_act, move_top_act, move_bottom_act, move_position_act):
                action.setEnabled(self.perm.can("manage_parts"))
                reorder_menu.addAction(action)
        menu.addSeparator()
        edit_action = QAction("Edit Part", self)
        edit_action.triggered.connect(lambda: self.edit_part(item_id))
        delete_action = QAction("Delete Part", self)
        delete_action.triggered.connect(lambda: self.delete_part(item_id))
        add_child_action = QAction("Add Child", self)
        add_child_action.triggered.connect(
            lambda _checked=False, parent_item=item: self.add_child(parent_item)
        )
        selected_relation_items = [
            selected for selected in tree.selectedItems()
            if selected is not None
            and not self._is_folder_tree_item(selected)
            and not self._is_lazy_placeholder(selected)
            and selected.data(0, Qt.UserRole) is not None
        ]
        if item not in selected_relation_items:
            selected_relation_items = [item]
        add_to_parent_action = QAction("Add to Parent", self)
        add_to_parent_action.triggered.connect(
            lambda _checked=False, selected=list(selected_relation_items): self.add_selected_to_parent(selected)
        )
        add_folder_action = QAction("Add Folder Here", self)
        add_folder_action.triggered.connect(lambda: self.add_bom_folder(parent_item=item))
        add_dwg_action = QAction("Associate Drawing", self)
        add_dwg_action.triggered.connect(lambda: self.add_dwg_to_part(item_id))

        view_3d_action = QAction("🔬 View STEP in 3D Viewer", self)
        view_3d_action.triggered.connect(lambda: self._open_step_in_3d_viewer(item_id))

        policy = item.data(0, BOM_TREE_POLICY_ROLE) or {}
        is_cad_representation = bool(policy.get("represented_part_id"))

        if self.perm.can("manage_parts"):
            menu.addAction(edit_action)
            menu.addAction(delete_action)
            if str(item.text(BOM_COL_TYPE) or "").strip().lower() in {"asm", "assembly"}:
                menu.addAction(add_child_action)
                menu.addAction(add_folder_action)
            if selected_relation_items:
                menu.addAction(add_to_parent_action)
        if not is_cad_representation:
            menu.addAction(add_dwg_action)

        occurrence = item.data(0, BOM_TREE_OCCURRENCE_ROLE) or {}
        if (
            getattr(self, "_bom_mode", "cad") == "cad"
            and occurrence.get("usage_id") is not None
            and occurrence.get("parent_id") is not None
            and self.perm.can("manage_parts")
        ):
            edit_ebom_behavior_action = QAction("Edit EBOM Behavior", self)
            edit_ebom_behavior_action.triggered.connect(
                lambda _checked=False, parent_id=int(occurrence["parent_id"]),
                       usage_id=int(occurrence["usage_id"]), row_item=item:
                    self._edit_occurrence_ebom_behavior(
                        parent_id, usage_id, row_item
                    )
            )
            menu.addAction(edit_ebom_behavior_action)

        try:
            summary = self._indicator_summary_for_part(int(item_id), self._issues_for_part(int(item_id)))
            ack_actions = []
            for doc_key, doc_type in (("pdf", "PDF"), ("step", "STEP")):
                doc = summary.get(doc_key) or {}
                tip = str(doc.get("tooltip") or "").lower()
                if doc.get("state") == "bad" and ("newer commit" in tip or "needs review" in tip):
                    action = QAction(f"Acknowledge {doc_type} as safe", self)

                    def _ack_doc(_checked=False, _doc_type=doc_type, _part_id=item_id):
                        self.part_doc_ack_service.mark_up_to_date(
                            int(_part_id),
                            _doc_type,
                            self._doc_ack_target(int(_part_id), _doc_type),
                        )
                        self._refresh_part_in_tree(int(_part_id))
                        try:
                            if getattr(self, "current_part_id", None) and int(self.current_part_id) == int(_part_id):
                                self.display_details(int(_part_id))
                        except Exception:
                            pass

                    action.triggered.connect(_ack_doc)
                    ack_actions.append(action)

            if ack_actions:
                menu.addSeparator()
                for action in ack_actions:
                    menu.addAction(action)
        except Exception:
            pass

        # Allow removing a child relation when right-clicking a child node
        try:
            parent_item = item.parent()
            relation_parent_id = parent_item.data(0, Qt.UserRole) if parent_item is not None else None
            relation_parent_name = parent_item.text(BOM_COL_NAME) if parent_item is not None else ""
            if self._is_folder_tree_item(parent_item):
                parent_folder = self._folder_record(int(parent_item.data(0, BOM_TREE_FOLDER_ROLE)))
                relation_parent_id = parent_folder.get("effective_parent_bom_id")
                if relation_parent_id is not None:
                    relation_parent = self.bom_service.get_part_details(int(relation_parent_id)) or {}
                    relation_parent_name = str(relation_parent.get("name") or relation_parent_id)
            if relation_parent_id is not None and self.perm.can("manage_parts"):
                same_parent_items = [
                    selected for selected in selected_relation_items
                    if self._direct_relation_parent_for_item(selected) == int(relation_parent_id)
                ]
                if len(same_parent_items) == len(selected_relation_items):
                    remove_child_action = QAction(
                        "Remove Selected Children" if len(same_parent_items) > 1 else "Remove Child",
                        self,
                    )
                    remove_child_action.triggered.connect(
                        lambda _checked=False, parent_id=int(relation_parent_id),
                               parent_name=str(relation_parent_name), selected=list(same_parent_items):
                            self.remove_selected_children(parent_id, parent_name, selected)
                    )
                    menu.addAction(remove_child_action)
        except Exception:
            pass

        # Only show 3D viewer action if part has a committed STEP file
        try:
            latest_step = self.commit_repo.get_latest_step_commit_for_part(
                int(item_id), int(self.session.project_id)
            )
            if (
                not is_cad_representation
                and latest_step
                and latest_step.step_file_path
                and os.path.exists(latest_step.step_file_path)
            ):
                menu.addSeparator()
                menu.addAction(view_3d_action)
        except Exception:
            pass

        menu.exec_(tree.viewport().mapToGlobal(position))

    def _edit_occurrence_ebom_behavior(
        self, parent_id: int, usage_id: int, item: QTreeWidgetItem
    ) -> None:
        policy = item.data(0, BOM_TREE_POLICY_ROLE) or {}
        current = str(policy.get("ebom_behavior") or "INHERIT").upper()
        choices = [
            ("INHERIT", "INHERIT — use the child object's default"),
            ("NORMAL", "NORMAL — deliver this occurrence"),
            ("FLATTEN", "FLATTEN — hide this occurrence, promote its children"),
            ("EXCLUDE", "EXCLUDE — not for delivery in this parent"),
        ]
        current_index = next(
            (index for index, value in enumerate(choices) if value[0] == current),
            0,
        )
        selected_label, ok = QInputDialog.getItem(
            self,
            "Edit EBOM Behavior",
            "Occurrence delivery behavior:",
            [value[1] for value in choices],
            current_index,
            False,
        )
        if not ok:
            return
        selected = next(
            value for value, label in choices if label == selected_label
        )
        try:
            relation = self.bom_service.set_occurrence_ebom_behavior(
                int(parent_id), int(usage_id), str(selected)
            )
            updated = dict(policy)
            updated["ebom_behavior"] = str(relation["ebom_behavior"])
            updated["resolved_ebom_behavior"] = (
                str(updated.get("default_ebom_behavior") or "NORMAL")
                if updated["ebom_behavior"] == "INHERIT"
                else updated["ebom_behavior"]
            )
            item.setData(0, BOM_TREE_POLICY_ROLE, updated)
            item.treeWidget().viewport().update()
            QMessageBox.information(
                self,
                "EBOM Behavior",
                "The checked-out parent structure was updated. The rule will be "
                "frozen in its next iteration on check-in.",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Edit EBOM Behavior", str(exc))

    def _default_bom_advanced_filters(self) -> dict:
        return {
            "text": "",
            "text_match_mode": "normal",
            "work_state": "Any",
            "work_owner": "All",
            "status": "All",
            "type": "All",
            "revision": "",
            "categories": [],
            "structure": "Any",
            "pdf": "Any",
            "step": "Any",
            "integrity": "Any",
            "issues": "Any",
            "remove_duplicates": False,
            "show_parent_matches": True,
            "expand_matches": True,
        }

    def _is_default_bom_advanced_filter(self, filters: dict | None = None) -> bool:
        filters = filters or getattr(self, "_bom_advanced_filters", {}) or {}
        defaults = self._default_bom_advanced_filters()
        legacy_category = str(filters.get("category") or "").strip()
        if legacy_category not in ("", "All"):
            return False
        return all(
            (key == "text_match_mode" and not str(filters.get("text") or "").strip())
            or filters.get(key, default) == default
            for key, default in defaults.items()
        )

    def _normalize_bom_filter_definition(self, definition: dict | None) -> dict:
        defaults = self._default_bom_advanced_filters()
        source = definition if isinstance(definition, dict) else {}
        normalized = dict(defaults)
        for key in defaults:
            if key in source:
                normalized[key] = source[key]
        categories = normalized.get("categories") or []
        if isinstance(categories, str):
            categories = [categories]
        normalized["categories"] = [
            str(value).strip() for value in categories
            if str(value).strip()
        ]
        legacy_category = str(source.get("category") or "").strip()
        if not normalized["categories"] and legacy_category not in ("", "All"):
            normalized["categories"] = [legacy_category]
        normalized["text"] = ", ".join(split_bom_filter_terms(normalized.get("text")))
        match_mode_aliases = {
            "normal": "normal",
            "normal filter": "normal",
            "whole_word": "whole_word",
            "match whole word": "whole_word",
        }
        normalized["text_match_mode"] = match_mode_aliases.get(
            str(normalized.get("text_match_mode") or "").strip().casefold(),
            defaults["text_match_mode"],
        )
        for key in ("remove_duplicates", "show_parent_matches", "expand_matches"):
            normalized[key] = bool(normalized.get(key, defaults[key]))
        return normalized

    def _prompt_saved_filter_details(self, title: str, name: str = "", shared: bool = False):
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setMinimumWidth(380)
        layout = QVBoxLayout(dialog)
        form = QGridLayout()
        name_input = QLineEdit(str(name or ""))
        name_input.setPlaceholderText("Filter name")
        shared_check = QCheckBox("Share with everyone in this project")
        shared_check.setChecked(bool(shared))
        form.addWidget(QLabel("Name"), 0, 0)
        form.addWidget(name_input, 0, 1)
        form.addWidget(shared_check, 1, 1)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        name_input.selectAll()
        name_input.setFocus()
        if dialog.exec_() != QDialog.Accepted:
            return None
        clean_name = " ".join(name_input.text().split())
        if not clean_name:
            QMessageBox.warning(self, "Saved Filter", "Enter a name for the filter.")
            return None
        return clean_name, shared_check.isChecked()

    def _save_bom_filter_definition(self, definition: dict, suggested_name: str = ""):
        normalized = self._normalize_bom_filter_definition(definition)
        if self._is_default_bom_advanced_filter(normalized):
            QMessageBox.information(self, "Saved Filter", "Set at least one filter criterion before saving.")
            return None
        details = self._prompt_saved_filter_details("Save BOM Filter", suggested_name)
        if not details:
            return None
        try:
            return self.bom_service.create_saved_bom_filter(details[0], normalized, details[1])
        except Exception as exc:
            QMessageBox.warning(self, "Saved Filter", str(exc))
            return None

    def save_current_bom_filter(self):
        saved = self._save_bom_filter_definition(self._bom_advanced_filters)
        if saved:
            self._active_saved_filter_id = int(saved["id"])
            self._active_saved_filter_name = str(saved.get("name") or "")
            self._update_advanced_filter_button_state()
        return saved

    def apply_saved_bom_filter(self, filter_id: int):
        try:
            saved = self.bom_service.get_saved_bom_filter(int(filter_id))
            definition = self._normalize_bom_filter_definition(saved.get("definition"))
            active_dialog = getattr(self, "_advanced_filter_dialog", None)
            if active_dialog is not None:
                active_dialog.close()
            self.apply_bom_tree_filter(definition)
            if self._is_default_bom_advanced_filter():
                return
            self._active_saved_filter_id = int(saved["id"])
            self._active_saved_filter_name = str(saved.get("name") or "")
            self._update_advanced_filter_button_state()
        except Exception as exc:
            QMessageBox.warning(self, "Saved Filter", str(exc))

    def show_saved_bom_filters_menu(self):
        menu = QMenu(self)
        try:
            saved_filters = self.bom_service.list_saved_bom_filters()
        except Exception as exc:
            QMessageBox.warning(self, "Saved Filters", str(exc))
            return
        if saved_filters:
            for saved in saved_filters:
                owner_id = int(saved.get("owner_user_id") or 0)
                own = owner_id == int(self.session.user_id or 0)
                suffix = ""
                if saved.get("is_shared"):
                    suffix = " [Shared]" if own else f" [Shared by {saved.get('owner_name') or 'user'}]"
                action = menu.addAction(f"{saved.get('name') or 'Unnamed'}{suffix}")
                action.setCheckable(True)
                action.setChecked(int(saved["id"]) == int(self._active_saved_filter_id or 0))
                action.triggered.connect(
                    lambda _checked=False, filter_id=int(saved["id"]): self.apply_saved_bom_filter(filter_id)
                )
        else:
            empty_action = menu.addAction("No saved filters")
            empty_action.setEnabled(False)
        menu.addSeparator()
        menu.addAction("Save Current Filter...", self.save_current_bom_filter)
        menu.addAction("Manage Saved Filters...", self.show_saved_bom_filters_manager)
        button = self.saved_filters_btn
        menu.exec_(button.mapToGlobal(button.rect().bottomLeft()))

    def show_saved_bom_filters_manager(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Saved BOM Filters")
        dialog.resize(720, 430)
        layout = QVBoxLayout(dialog)
        info = QLabel("Private filters are visible only to you. Shared filters can be applied by everyone in this project.")
        info.setWordWrap(True)
        info.setStyleSheet("color:#475569;")
        layout.addWidget(info)

        table = QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(["Name", "Owner", "Visibility", "Updated"])
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        layout.addWidget(table)

        command_row = QHBoxLayout()
        apply_btn = QPushButton("Apply")
        save_btn = QPushButton("Save Current As...")
        update_btn = QPushButton("Update from Current")
        rename_btn = QPushButton("Rename")
        duplicate_btn = QPushButton("Duplicate")
        share_btn = QPushButton("Share")
        for button in (apply_btn, save_btn, update_btn, rename_btn, duplicate_btn, share_btn):
            command_row.addWidget(button)
        layout.addLayout(command_row)

        order_row = QHBoxLayout()
        up_btn = QPushButton("Move Up")
        down_btn = QPushButton("Move Down")
        delete_btn = QPushButton("Delete")
        delete_btn.setObjectName("danger")
        close_btn = QPushButton("Close")
        order_row.addWidget(up_btn)
        order_row.addWidget(down_btn)
        order_row.addStretch()
        order_row.addWidget(delete_btn)
        order_row.addWidget(close_btn)
        layout.addLayout(order_row)

        rows_by_id = {}

        def selected_filter():
            row = table.currentRow()
            if row < 0:
                return None
            item = table.item(row, 0)
            return rows_by_id.get(int(item.data(Qt.UserRole) or 0)) if item else None

        def refresh(preferred_id=None):
            nonlocal rows_by_id
            try:
                rows = self.bom_service.list_saved_bom_filters()
            except Exception as exc:
                QMessageBox.warning(dialog, "Saved Filters", str(exc))
                rows = []
            rows_by_id = {int(row["id"]): row for row in rows}
            table.setRowCount(len(rows))
            selected_row = -1
            for index, saved in enumerate(rows):
                filter_id = int(saved["id"])
                values = (
                    str(saved.get("name") or ""),
                    str(saved.get("owner_name") or ""),
                    "Shared" if saved.get("is_shared") else "Private",
                    str(saved.get("updated_at") or ""),
                )
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    if column == 0:
                        item.setData(Qt.UserRole, filter_id)
                    table.setItem(index, column, item)
                if filter_id == int(preferred_id or 0):
                    selected_row = index
            if selected_row < 0 and rows:
                selected_row = 0
            if selected_row >= 0:
                table.selectRow(selected_row)
            update_actions()

        def update_actions():
            saved = selected_filter()
            own = bool(saved) and int(saved.get("owner_user_id") or 0) == int(self.session.user_id or 0)
            for button in (apply_btn, duplicate_btn):
                button.setEnabled(bool(saved))
            for button in (update_btn, rename_btn, share_btn, up_btn, down_btn, delete_btn):
                button.setEnabled(own)
            share_btn.setText("Make Private" if own and saved.get("is_shared") else "Share")

        def apply_selected():
            saved = selected_filter()
            if saved:
                dialog.accept()
                self.apply_saved_bom_filter(int(saved["id"]))

        def save_current():
            saved = self.save_current_bom_filter()
            if saved:
                refresh(saved["id"])

        def update_selected():
            saved = selected_filter()
            if not saved:
                return
            answer = QMessageBox.question(
                dialog, "Update Saved Filter",
                f"Replace the criteria in '{saved['name']}' with the current BOM filter?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
            try:
                updated = self.bom_service.update_saved_bom_filter(
                    int(saved["id"]), definition=self._normalize_bom_filter_definition(self._bom_advanced_filters)
                )
                refresh(updated["id"])
            except Exception as exc:
                QMessageBox.warning(dialog, "Saved Filter", str(exc))

        def rename_selected():
            saved = selected_filter()
            if not saved:
                return
            name, ok = QInputDialog.getText(dialog, "Rename Saved Filter", "Name:", text=str(saved["name"]))
            if not ok:
                return
            try:
                updated = self.bom_service.update_saved_bom_filter(int(saved["id"]), name=name)
                if int(saved["id"]) == int(self._active_saved_filter_id or 0):
                    self._active_saved_filter_name = str(updated.get("name") or "")
                    self._update_advanced_filter_button_state()
                refresh(updated["id"])
            except Exception as exc:
                QMessageBox.warning(dialog, "Saved Filter", str(exc))

        def duplicate_selected():
            saved = selected_filter()
            if not saved:
                return
            details = self._prompt_saved_filter_details("Duplicate BOM Filter", f"Copy of {saved['name']}")
            if not details:
                return
            try:
                created = self.bom_service.duplicate_saved_bom_filter(
                    int(saved["id"]), details[0], details[1]
                )
                refresh(created["id"])
            except Exception as exc:
                QMessageBox.warning(dialog, "Saved Filter", str(exc))

        def toggle_share():
            saved = selected_filter()
            if not saved:
                return
            try:
                updated = self.bom_service.update_saved_bom_filter(
                    int(saved["id"]), is_shared=not bool(saved.get("is_shared"))
                )
                refresh(updated["id"])
            except Exception as exc:
                QMessageBox.warning(dialog, "Saved Filter", str(exc))

        def move_selected(direction):
            saved = selected_filter()
            if not saved:
                return
            try:
                self.bom_service.move_saved_bom_filter(int(saved["id"]), direction)
                refresh(saved["id"])
            except Exception as exc:
                QMessageBox.warning(dialog, "Saved Filter", str(exc))

        def delete_selected():
            saved = selected_filter()
            if not saved:
                return
            answer = QMessageBox.question(
                dialog, "Delete Saved Filter", f"Delete '{saved['name']}'?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
            try:
                self.bom_service.delete_saved_bom_filter(int(saved["id"]))
                if int(saved["id"]) == int(self._active_saved_filter_id or 0):
                    self._active_saved_filter_id = None
                    self._active_saved_filter_name = ""
                    self._update_advanced_filter_button_state()
                refresh()
            except Exception as exc:
                QMessageBox.warning(dialog, "Saved Filter", str(exc))

        table.itemSelectionChanged.connect(update_actions)
        table.itemDoubleClicked.connect(lambda _item: apply_selected())
        apply_btn.clicked.connect(apply_selected)
        save_btn.clicked.connect(save_current)
        update_btn.clicked.connect(update_selected)
        rename_btn.clicked.connect(rename_selected)
        duplicate_btn.clicked.connect(duplicate_selected)
        share_btn.clicked.connect(toggle_share)
        up_btn.clicked.connect(lambda: move_selected(-1))
        down_btn.clicked.connect(lambda: move_selected(1))
        delete_btn.clicked.connect(delete_selected)
        close_btn.clicked.connect(dialog.reject)
        refresh(self._active_saved_filter_id)
        dialog.exec_()

    def _collect_tree_column_values(self, column: int) -> list[str]:
        values = set()
        for tree in (
            getattr(self, "tree", None),
            getattr(self, "_search_tree", None),
            getattr(self, "_ebom_tree", None),
        ):
            if tree is None:
                continue
            try:
                for item in self._iter_tree_items(tree):
                    value = str(item.text(column) or "").strip()
                    if value:
                        values.add(value)
            except Exception:
                continue
        return sorted(values, key=lambda s: s.lower())

    def _current_tree_for_filtering(self) -> QTreeWidget:
        if getattr(self, "_bom_mode", "cad") == "ebom":
            return getattr(self, "_ebom_tree", self.tree)
        if getattr(self, "_in_search_mode", False):
            return getattr(self, "_search_tree", self.tree)
        return self.tree

    def _file_badge_kind(self, item: QTreeWidgetItem, doc_key: str) -> str:
        payload = item.data(BOM_COL_FILES, BOM_TREE_FILES_ROLE) or {}
        value = payload.get(doc_key)
        if isinstance(value, (tuple, list)) and value:
            return str(value[0] or "na").lower()
        return "na"

    def _bom_tree_item_matches_advanced_filter(self, item: QTreeWidgetItem, filters: dict) -> bool:
        if self._is_folder_tree_item(item):
            return False
        text = str(filters.get("text") or "").strip()
        if text:
            haystack = " ".join([
                str(item.text(col) or "") for col in range(BOM_COL_NAME, BOM_COL_STATUS + 1)
            ] + [
                str(item.data(0, BOM_TREE_INWORK_ROLE) or ""),
                ", ".join(item.data(0, BOM_TREE_CATEGORY_ROLE) or []),
                str(item.toolTip(BOM_COL_NAME) or ""),
                str(item.toolTip(BOM_COL_FILES) or ""),
            ])
            whole_word = str(filters.get("text_match_mode") or "normal") == "whole_word"
            if not matches_bom_filter_text(haystack, text, whole_word=whole_word):
                return False

        locked_txt = str(item.data(0, BOM_TREE_INWORK_ROLE) or "")
        locked_l = locked_txt.lower()
        work_state = str(filters.get("work_state") or "Any")
        if work_state == "In Work" and "in work" not in locked_l:
            return False
        if work_state == "Checked In" and "in work" in locked_l:
            return False

        owner = str(filters.get("work_owner") or "All").strip()
        if owner and owner != "All" and owner.lower() not in locked_l:
            return False

        status = str(filters.get("status") or "All").strip()
        if status and status != "All" and str(item.text(BOM_COL_STATUS) or "").strip().lower() != status.lower():
            return False

        part_type = str(filters.get("type") or "All").strip()
        if part_type and part_type != "All" and str(item.text(BOM_COL_TYPE) or "").strip().lower() != part_type.lower():
            return False

        revision = str(filters.get("revision") or "").strip().lower()
        if revision and revision not in str(item.text(BOM_COL_REV) or "").strip().lower():
            return False

        selected_categories = [
            str(value).strip()
            for value in (filters.get("categories") or [])
            if str(value).strip()
        ]
        # Compatibility with a filter created before category multi-selection.
        legacy_category = str(filters.get("category") or "").strip()
        if not selected_categories and legacy_category not in ("", "All"):
            selected_categories = [legacy_category]
        item_categories = [str(value).strip() for value in (item.data(0, BOM_TREE_CATEGORY_ROLE) or [])]
        if selected_categories:
            selected_keys = {value.casefold() for value in selected_categories}
            matches_uncategorized = "uncategorized" in selected_keys and not item_categories
            matches_category = any(value.casefold() in selected_keys for value in item_categories)
            if not (matches_uncategorized or matches_category):
                return False

        structure = str(filters.get("structure") or "Any")
        is_assembly = bool(item.data(0, BOM_TREE_IS_ASSEMBLY_ROLE)) or item.childCount() > 0
        if structure == "Assemblies only" and not is_assembly:
            return False
        if structure == "Leaf parts only" and is_assembly:
            return False

        doc_map = {
            "OK": "ok",
            "Outdated": "outdated",
            "Missing": "missing",
            "Not attached": "na",
        }
        for doc_key in ("pdf", "step"):
            desired = str(filters.get(doc_key) or "Any")
            if desired != "Any" and self._file_badge_kind(item, doc_key) != doc_map.get(desired, desired.lower()):
                return False

        integrity = str(filters.get("integrity") or "Any")
        integrity_state = str((item.data(BOM_COL_INTEGRITY, BOM_TREE_INTEGRITY_ROLE) or {}).get("state") or "ok").lower()
        if integrity == "Healthy only" and integrity_state != "ok":
            return False
        if integrity == "Has integrity issues" and integrity_state != "warn":
            return False

        issues = str(filters.get("issues") or "Any")
        issue_summary = item.data(0, BOM_TREE_ISSUE_ROLE) or {}
        active_count = int(issue_summary.get("active_count") or 0)
        total_count = int(issue_summary.get("total_count") or 0)
        if issues == "Active issues" and active_count <= 0:
            return False
        if issues == "Any linked issue" and total_count <= 0:
            return False
        if issues == "No linked issues" and total_count > 0:
            return False

        return True

    def show_advanced_filter_dialog(self):
        existing = getattr(self, "_advanced_filter_dialog", None)
        if existing is not None and existing.isVisible():
            existing.raise_()
            existing.activateWindow()
            return

        dialog = QDialog(self, Qt.Tool)
        dialog.setWindowModality(Qt.NonModal)
        dialog.setModal(False)
        dialog.setAttribute(Qt.WA_DeleteOnClose, True)
        self._advanced_filter_dialog = dialog

        def forget_dialog(*_):
            if getattr(self, "_advanced_filter_dialog", None) is dialog:
                self._advanced_filter_dialog = None

        dialog.destroyed.connect(forget_dialog)
        dialog.setWindowTitle("Advanced BOM Filter")
        dialog.resize(660, 550)
        layout = QVBoxLayout(dialog)
        layout.setSpacing(10)

        intro = QLabel(
            "Filter the visible BOM by text, lifecycle state, owner, document health, issues, and integrity. "
            "Separate text values with commas to match any value."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color:#475569;")
        layout.addWidget(intro)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        current = dict(getattr(self, "_bom_advanced_filters", {}) or self._default_bom_advanced_filters())

        text_input = QLineEdit(str(current.get("text") or ""))
        text_input.setPlaceholderText("Multiple values: MA00, MA01")
        text_input.setToolTip("Separate values with commas. An item is shown when it matches any value.")
        text_match_combo = QComboBox()
        text_match_combo.addItem("Normal filter", "normal")
        text_match_combo.addItem("Match whole word", "whole_word")
        current_match_mode = str(current.get("text_match_mode") or "normal")
        current_match_index = text_match_combo.findData(current_match_mode)
        text_match_combo.setCurrentIndex(max(0, current_match_index))
        text_match_combo.setToolTip(
            "Normal filter matches text inside a larger value. Match whole word requires word boundaries."
        )
        grid.addWidget(QLabel("Contains"), 0, 0)
        grid.addWidget(text_input, 0, 1, 1, 2)
        grid.addWidget(text_match_combo, 0, 3)

        work_combo = QComboBox()
        work_combo.addItems(["Any", "In Work", "Checked In"])
        work_combo.setCurrentText(str(current.get("work_state") or "Any"))
        grid.addWidget(QLabel("Work state"), 1, 0)
        grid.addWidget(work_combo, 1, 1)

        owner_combo = QComboBox()
        owner_combo.addItem("All")
        users = self.project_service.get_users_for_project(self.session.project_id) or []
        for u in users:
            label = str(u.get("username") or "").strip()
            if not label:
                continue
            owner_combo.addItem(label)
        if owner_combo.findText(str(current.get("work_owner") or "All")) >= 0:
            owner_combo.setCurrentText(str(current.get("work_owner") or "All"))
        grid.addWidget(QLabel("Owner"), 1, 2)
        grid.addWidget(owner_combo, 1, 3)

        status_combo = QComboBox()
        status_combo.addItem("All")
        status_combo.addItems(self._collect_tree_column_values(BOM_COL_STATUS))
        if status_combo.findText(str(current.get("status") or "All")) >= 0:
            status_combo.setCurrentText(str(current.get("status") or "All"))
        grid.addWidget(QLabel("Status"), 2, 0)
        grid.addWidget(status_combo, 2, 1)

        type_combo = QComboBox()
        type_combo.addItem("All")
        type_combo.addItems(self._collect_tree_column_values(BOM_COL_TYPE))
        if type_combo.findText(str(current.get("type") or "All")) >= 0:
            type_combo.setCurrentText(str(current.get("type") or "All"))
        grid.addWidget(QLabel("Type"), 2, 2)
        grid.addWidget(type_combo, 2, 3)

        revision_input = QLineEdit(str(current.get("revision") or ""))
        revision_input.setPlaceholderText("Revision contains...")
        grid.addWidget(QLabel("Revision"), 3, 0)
        grid.addWidget(revision_input, 3, 1)

        structure_combo = QComboBox()
        structure_combo.addItems(["Any", "Assemblies only", "Leaf parts only"])
        structure_combo.setCurrentText(str(current.get("structure") or "Any"))
        grid.addWidget(QLabel("Structure"), 3, 2)
        grid.addWidget(structure_combo, 3, 3)

        category_list = QListWidget()
        category_list.setMaximumHeight(92)
        category_list.setToolTip("Select one or more categories. Items matching any selection are shown.")

        category_names = ["Uncategorized"]
        try:
            category_names.extend(row["name"] for row in self.bom_service.list_categories())
        except Exception:
            pass
        current_categories = {
            str(value).casefold() for value in (current.get("categories") or [])
        }
        legacy_category = str(current.get("category") or "").strip()
        if not current_categories and legacy_category not in ("", "All"):
            current_categories.add(legacy_category.casefold())
        for category_name in category_names:
            item = QListWidgetItem(str(category_name))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(
                Qt.Checked if str(category_name).casefold() in current_categories else Qt.Unchecked
            )
            category_list.addItem(item)
        grid.addWidget(QLabel("Category"), 4, 0)
        grid.addWidget(category_list, 4, 1, 1, 3)

        pdf_combo = QComboBox()
        pdf_combo.addItems(["Any", "OK", "Outdated", "Missing", "Not attached"])
        pdf_combo.setCurrentText(str(current.get("pdf") or "Any"))
        grid.addWidget(QLabel("PDF"), 5, 0)
        grid.addWidget(pdf_combo, 5, 1)

        step_combo = QComboBox()
        step_combo.addItems(["Any", "OK", "Outdated", "Missing", "Not attached"])
        step_combo.setCurrentText(str(current.get("step") or "Any"))
        grid.addWidget(QLabel("STEP"), 5, 2)
        grid.addWidget(step_combo, 5, 3)

        integrity_combo = QComboBox()
        integrity_combo.addItems(["Any", "Healthy only", "Has integrity issues"])
        integrity_combo.setCurrentText(str(current.get("integrity") or "Any"))
        grid.addWidget(QLabel("Integrity"), 6, 0)
        grid.addWidget(integrity_combo, 6, 1)

        issue_combo = QComboBox()
        issue_combo.addItems(["Any", "Active issues", "Any linked issue", "No linked issues"])
        issue_combo.setCurrentText(str(current.get("issues") or "Any"))
        grid.addWidget(QLabel("Issues"), 6, 2)
        grid.addWidget(issue_combo, 6, 3)

        layout.addLayout(grid)

        show_parent_check = QCheckBox("Show parent branches for matching child items")
        show_parent_check.setChecked(bool(current.get("show_parent_matches", True)))
        layout.addWidget(show_parent_check)

        expand_check = QCheckBox("Expand matching branches after applying")
        expand_check.setChecked(bool(current.get("expand_matches", True)))
        layout.addWidget(expand_check)

        remove_duplicates_check = QCheckBox("Remove duplicate items (keep first occurrence)")
        remove_duplicates_check.setChecked(bool(current.get("remove_duplicates", False)))
        remove_duplicates_check.setToolTip(
            "Show a flat result list with one row per BOM item ID, keeping its first occurrence."
        )
        layout.addWidget(remove_duplicates_check)

        def update_tree_option_state(remove_duplicates: bool):
            show_parent_check.setEnabled(not remove_duplicates)
            expand_check.setEnabled(not remove_duplicates)

        remove_duplicates_check.toggled.connect(update_tree_option_state)
        update_tree_option_state(remove_duplicates_check.isChecked())

        buttons = QDialogButtonBox()
        apply_btn = buttons.addButton("Apply", QDialogButtonBox.ApplyRole)
        save_btn = buttons.addButton("Save As...", QDialogButtonBox.ActionRole)
        clear_btn = buttons.addButton("Clear Filter", QDialogButtonBox.ResetRole)
        close_btn = buttons.addButton(QDialogButtonBox.Close)
        layout.addWidget(buttons)

        def collect_filters():
            return {
                "text": text_input.text().strip(),
                "text_match_mode": text_match_combo.currentData(),
                "work_state": work_combo.currentText(),
                "work_owner": owner_combo.currentText(),
                "status": status_combo.currentText(),
                "type": type_combo.currentText(),
                "revision": revision_input.text().strip(),
                "categories": [
                    category_list.item(row).text()
                    for row in range(category_list.count())
                    if category_list.item(row).checkState() == Qt.Checked
                ],
                "structure": structure_combo.currentText(),
                "pdf": pdf_combo.currentText(),
                "step": step_combo.currentText(),
                "integrity": integrity_combo.currentText(),
                "issues": issue_combo.currentText(),
                "remove_duplicates": remove_duplicates_check.isChecked(),
                "show_parent_matches": show_parent_check.isChecked(),
                "expand_matches": expand_check.isChecked(),
            }

        def on_apply():
            filters = collect_filters()
            self._active_saved_filter_id = None
            self._active_saved_filter_name = ""
            self.apply_bom_tree_filter(filters)

        def on_save_as():
            saved = self._save_bom_filter_definition(collect_filters())
            if not saved:
                return
            self._active_saved_filter_id = int(saved["id"])
            self._active_saved_filter_name = str(saved.get("name") or "")
            self.apply_bom_tree_filter(saved.get("definition") or {})
            self._active_saved_filter_id = int(saved["id"])
            self._active_saved_filter_name = str(saved.get("name") or "")
            self._update_advanced_filter_button_state()

        def on_clear():
            self.clear_bom_tree_filter()
            dialog.close()

        apply_btn.clicked.connect(on_apply)
        save_btn.clicked.connect(on_save_as)
        clear_btn.clicked.connect(on_clear)
        close_btn.clicked.connect(dialog.close)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def clear_bom_tree_filter(self):
        was_flat_filter = bool(getattr(self, "_advanced_filter_flat_mode", False))
        self._advanced_filter_flat_mode = False
        self._bom_advanced_filters = self._default_bom_advanced_filters()
        self._active_saved_filter_id = None
        self._active_saved_filter_name = ""
        for tree in (
            getattr(self, "tree", None),
            getattr(self, "_search_tree", None),
            getattr(self, "_ebom_tree", None),
        ):
            if tree is None:
                continue
            try:
                for item in self._iter_tree_items(tree):
                    item.setHidden(False)
            except Exception:
                pass
        if getattr(self, "_bom_mode", "cad") == "ebom":
            self._refresh_ebom_filters()
        elif was_flat_filter:
            try:
                if self.search_input.text().strip():
                    self._perform_search_now()
                else:
                    self._exit_search_mode()
            except Exception:
                self._exit_search_mode()
        self._refresh_bom_row_numbers()
        self._update_advanced_filter_button_state(visible_count=None)

    def _update_advanced_filter_button_state(self, visible_count: int | None = None):
        active = not self._is_default_bom_advanced_filter()
        try:
            self.clear_filter_btn.setEnabled(active)
        except Exception:
            pass
        try:
            if active:
                suffix = f" ({visible_count} shown)" if visible_count is not None else ""
                saved_name = str(getattr(self, "_active_saved_filter_name", "") or "").strip()
                label = saved_name if saved_name else "Active"
                if len(label) > 24:
                    label = label[:21] + "..."
                self.advanced_filter_btn.setText(f"Advanced Filter: {label}{suffix}")
            else:
                self.advanced_filter_btn.setText("Advanced Filter")
        except Exception:
            pass
        try:
            saved_name = str(getattr(self, "_active_saved_filter_name", "") or "").strip()
            self.saved_filters_btn.setToolTip(
                f"Applied saved filter: {saved_name}" if saved_name else "Apply or manage saved BOM filters"
            )
        except Exception:
            pass

    def _apply_bom_tree_filter_to_tree(self, tree: QTreeWidget, filters: dict) -> int:
        visible_count = 0
        show_parents = bool(filters.get("show_parent_matches", True))

        def recurse(item):
            nonlocal visible_count
            show_self = self._bom_tree_item_matches_advanced_filter(item, filters)
            child_match_visible = False
            for i in range(item.childCount()):
                child = item.child(i)
                child_show = recurse(child)
                child_match_visible = child_match_visible or child_show
            show = show_self or (show_parents and child_match_visible)
            item.setHidden(not show)
            if show_self:
                visible_count += 1
                if filters.get("expand_matches", True):
                    try:
                        item.setExpanded(True)
                    except Exception:
                        pass
            return show

        try:
            tree.setUpdatesEnabled(False)
            for i in range(tree.topLevelItemCount()):
                recurse(tree.topLevelItem(i))
        finally:
            try:
                tree.setUpdatesEnabled(True)
            except Exception:
                pass
        return visible_count

    def _clone_tree_item_shallow(self, item: QTreeWidgetItem) -> QTreeWidgetItem:
        clone = QTreeWidgetItem([str(item.text(col) or "") for col in range(BOM_TREE_COLUMN_COUNT)])
        clone.setTextAlignment(BOM_COL_ROW, Qt.AlignCenter)
        for col in range(BOM_TREE_COLUMN_COUNT):
            try:
                clone.setToolTip(col, item.toolTip(col))
            except Exception:
                pass
        for column, role in (
            (0, Qt.UserRole),
            (0, BOM_TREE_INWORK_ROLE),
            (0, BOM_TREE_IS_ASSEMBLY_ROLE),
            (0, BOM_TREE_ISSUE_ROLE),
            (0, BOM_TREE_CATEGORY_ROLE),
            (0, BOM_TREE_BINDING_UPDATE_ROLE),
            (0, BOM_TREE_POLICY_ROLE),
            (0, BOM_TREE_OCCURRENCE_ROLE),
            (0, BOM_TREE_PROMOTION_ROLE),
            (BOM_COL_FILES, BOM_TREE_FILES_ROLE),
            (BOM_COL_INTEGRITY, BOM_TREE_INTEGRITY_ROLE),
        ):
            try:
                clone.setData(column, role, item.data(column, role))
            except Exception:
                pass
        try:
            clone.setIcon(BOM_COL_NAME, item.icon(BOM_COL_NAME))
        except Exception:
            pass
        return clone

    def _source_tree_for_flat_bom_filter(self) -> QTreeWidget:
        query = ""
        try:
            query = self.search_input.text().strip()
        except Exception:
            query = ""
        if query and getattr(self, "_in_search_mode", False) and not getattr(self, "_advanced_filter_flat_mode", False):
            return getattr(self, "_search_tree", self.tree)
        return self.tree

    def _iter_tree_items_visual_order(self, tree_widget: QTreeWidget):
        stack = []
        try:
            for index in range(tree_widget.topLevelItemCount() - 1, -1, -1):
                stack.append(tree_widget.topLevelItem(index))
        except Exception:
            return
        while stack:
            item = stack.pop()
            yield item
            try:
                for index in range(item.childCount() - 1, -1, -1):
                    stack.append(item.child(index))
            except Exception:
                continue

    def _apply_bom_tree_filter_flat(self, filters: dict) -> int:
        source_tree = self._source_tree_for_flat_bom_filter()
        remove_duplicates = bool(filters.get("remove_duplicates", False))
        source_items = (
            self._iter_tree_items_visual_order(source_tree)
            if remove_duplicates
            else self._iter_tree_items(source_tree)
        )
        matches = [
            item for item in source_items
            if self._bom_tree_item_matches_advanced_filter(item, filters)
        ]
        if remove_duplicates:
            matches = deduplicate_bom_items_by_id(
                matches,
                lambda item: item.data(0, Qt.UserRole),
            )
        # The source may be _search_tree, which _enter_search_mode() clears. Build
        # detached rows first so deleted Qt item wrappers are never accessed.
        match_clones = [self._clone_tree_item_shallow(item) for item in matches]
        self._advanced_filter_flat_mode = True
        self._enter_search_mode()
        try:
            self._search_tree.setUpdatesEnabled(False)
            for clone in match_clones:
                clone.setHidden(False)
                self._search_tree.addTopLevelItem(clone)
        finally:
            try:
                self._search_tree.setUpdatesEnabled(True)
            except Exception:
                pass
        self._sync_search_tree_row_numbers()
        return len(match_clones)

    def apply_bom_tree_filter(self, filters: dict | None = None):
        self._bom_advanced_filters = self._normalize_bom_filter_definition(
            filters or self._bom_advanced_filters or self._default_bom_advanced_filters()
        )
        if self._is_default_bom_advanced_filter():
            self.clear_bom_tree_filter()
            return
        if getattr(self, "_bom_mode", "cad") == "ebom":
            total_visible = self._refresh_ebom_filters()
            self._update_advanced_filter_button_state(total_visible)
            return
        # Let an incremental basic-search build finish before filtering its rows.
        # _search_build_step() reapplies the current advanced filter on completion.
        try:
            if self.search_input.text().strip() and self._search_build_timer.isActive():
                self._update_advanced_filter_button_state(visible_count=None)
                return
        except Exception:
            pass
        # Advanced predicates must inspect the full BOM. Materialize only when the
        # user explicitly filters; normal startup and browsing remain level-lazy.
        if not getattr(self, "_in_search_mode", False):
            self._materialize_all_lazy_branches()
        if (
            bool(self._bom_advanced_filters.get("remove_duplicates", False))
            or not bool(self._bom_advanced_filters.get("show_parent_matches", True))
        ):
            total_visible = self._apply_bom_tree_filter_flat(self._bom_advanced_filters)
            self._update_advanced_filter_button_state(total_visible)
            return
        if getattr(self, "_advanced_filter_flat_mode", False):
            self._advanced_filter_flat_mode = False
            try:
                if self.search_input.text().strip():
                    self._perform_search_now()
                    return
                self._exit_search_mode()
            except Exception:
                self._exit_search_mode()
        total_visible = 0
        for tree in (getattr(self, "tree", None), getattr(self, "_search_tree", None)):
            if tree is None:
                continue
            try:
                total_visible += self._apply_bom_tree_filter_to_tree(tree, self._bom_advanced_filters)
            except Exception:
                pass
        self._refresh_bom_row_numbers()
        self._update_advanced_filter_button_state(total_visible)

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
        released = False
        if enabled and getattr(self, "current_part_id", None):
            try:
                details = self.bom_service.get_part_details(int(self.current_part_id)) or {}
                state = str(details.get("revision_state") or details.get("lifecycle_state") or "")
                released = state.strip().lower() == "released"
            except Exception:
                released = False
        for w in (
            getattr(self, "add_attachment_btn", None),
            getattr(self, "add_version_btn", None),
            getattr(self, "set_active_btn", None),
            getattr(self, "remove_attachment_btn", None),
            getattr(self, "delete_version_btn", None),
        ):
            if w is not None:
                w.setEnabled(bool(enabled) and can_release and not released)
        for w in (
            getattr(self, "create_baseline_btn", None),
            getattr(self, "release_version_btn", None),
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
            if hasattr(self, "file_related_issues_table"):
                self.file_related_issues_table.setRowCount(0)
            if hasattr(self, "pdf_viewer"):
                self.pdf_viewer.close_preview()
                self.pdf_viewer.setVisible(False)
            if hasattr(self, "preview_file_btn"):
                self.preview_file_btn.blockSignals(True)
                self.preview_file_btn.setChecked(False)
                self.preview_file_btn.setText("Preview")
                self.preview_file_btn.blockSignals(False)
            self._set_files_tab_enabled(False)
            return

        self._set_files_tab_enabled(True)
        details = {}
        try:
            managed_files = self.managed_file_service.list_current_files(
                int(self.current_part_id)
            )
        except Exception as exc:
            managed_files = []
            if hasattr(self, "pdf_viewer"):
                self.pdf_viewer.show_error(f"Unable to load managed files:\n{exc}")

        try:
            details = self.bom_service.get_part_details(int(self.current_part_id)) or {}
            context = self.managed_file_service.revision_repo.get_current_context(
                int(self.current_part_id)
            )
            lock = self.managed_file_service.part_file_service.lock_repo.get_by_part(
                int(self.current_part_id)
            )
            values = {
                "item": " - ".join(
                    value for value in (
                        str(details.get("aes_number") or "").strip(),
                        str(details.get("name") or "").strip(),
                    ) if value
                ) or str(self.current_part_id),
                "version": str(context.get("version_label") or "-"),
                "state": str(context.get("state") or "-"),
                "lock": self.managed_file_service._username(lock.user_id) if lock else "Not checked out",
            }
            for key, label in getattr(self, "files_summary_labels", {}).items():
                label.setText(values.get(key, "-"))
        except Exception:
            pass

        self.files_table.setRowCount(len(managed_files))
        first_pdf_row = None
        for i, entry in enumerate(managed_files):
            role_item = QTableWidgetItem(str(entry.get("role_label") or ""))
            role_item.setData(Qt.UserRole, entry.get("file_id"))
            role_item.setData(Qt.UserRole + 1, entry)
            self.files_table.setItem(i, 0, role_item)
            values = [
                entry.get("filename"),
                entry.get("file_revision"),
                entry.get("creo_iteration"),
                entry.get("bound_to"),
                entry.get("source"),
                entry.get("state"),
                entry.get("health"),
                entry.get("created_by_label"),
                entry.get("updated"),
            ]
            for column, value in enumerate(values, start=1):
                self.files_table.setItem(i, column, QTableWidgetItem(str(value or "")))
            if first_pdf_row is None and str(entry.get("file_type") or "").upper() == "PDF":
                first_pdf_row = i

        # Clear versions view until an attachment is selected
        self.versions_table.setRowCount(0)
        if hasattr(self, "file_related_issues_table"):
            self.file_related_issues_table.setRowCount(0)
        if first_pdf_row is not None:
            self.files_table.selectRow(first_pdf_row)
            self.on_attachment_selected()
        elif hasattr(self, "pdf_viewer"):
            self._collapse_managed_preview()

    def _selected_attachment_id(self):
        items = getattr(self, "files_table", None).selectedItems() if hasattr(self, "files_table") else []
        if not items:
            return None
        file_id = self.files_table.item(self.files_table.currentRow(), 0).data(Qt.UserRole)
        return file_id

    def _selected_managed_file(self):
        if not hasattr(self, "files_table") or self.files_table.currentRow() < 0:
            return None
        item = self.files_table.item(self.files_table.currentRow(), 0)
        return item.data(Qt.UserRole + 1) if item else None

    def _selected_file_type(self):
        entry = self._selected_managed_file() or {}
        return str(entry.get("file_type") or "").strip().upper()

    def _selected_history_row(self):
        if not hasattr(self, "versions_table") or self.versions_table.currentRow() < 0:
            return None
        item = self.versions_table.item(self.versions_table.currentRow(), 0)
        return item.data(Qt.UserRole + 1) if item else None

    def _selected_version_id(self):
        items = getattr(self, "versions_table", None).selectedItems() if hasattr(self, "versions_table") else []
        if not items:
            return None
        return self.versions_table.item(self.versions_table.currentRow(), 0).data(Qt.UserRole)

    def on_attachment_selected(self):
        selected = self._selected_managed_file()
        file_id = self._selected_attachment_id()
        if not selected:
            self.versions_table.setRowCount(0)
            if hasattr(self, "file_related_issues_table"):
                self.file_related_issues_table.setRowCount(0)
            if hasattr(self, "pdf_viewer"):
                self.pdf_viewer.close_preview()
            self._collapse_managed_preview()
            return
        history = self.managed_file_service.list_file_history(
            int(self.current_part_id),
            str(selected.get("role") or "document"),
            file_id=file_id,
        )
        self.versions_table.setRowCount(len(history))
        for i, row in enumerate(history):
            version_item = QTableWidgetItem(str(row.get("bound_to") or ""))
            version_item.setData(Qt.UserRole, row.get("version_id"))
            version_item.setData(Qt.UserRole + 1, row)
            self.versions_table.setItem(i, 0, version_item)
            values = [
                row.get("role_label"), row.get("filename"), row.get("file_revision"), row.get("creo_iteration"),
                row.get("source"), row.get("state"), row.get("health"),
                row.get("created_by_label"), row.get("created_at"), row.get("note"),
            ]
            for column, value in enumerate(values, start=1):
                self.versions_table.setItem(i, column, QTableWidgetItem(str(value or "")))

        # Auto-preview the active version if it is a PDF
        self._try_pdf_preview_for_active(file_id)
        if file_id:
            self._load_related_issues_for_file(file_id, self._selected_version_id())
        elif hasattr(self, "file_related_issues_table"):
            self.file_related_issues_table.setRowCount(0)
        self._update_managed_file_actions()

    def _update_managed_file_actions(self):
        entry = self._selected_managed_file() or {}
        is_document = bool(entry.get("file_id"))
        can_manage = bool(self.perm.can("release_files") and is_document)
        for button in (
            getattr(self, "add_version_btn", None),
            getattr(self, "set_active_btn", None),
            getattr(self, "remove_attachment_btn", None),
            getattr(self, "link_file_issue_btn", None),
            getattr(self, "release_version_btn", None),
            getattr(self, "delete_version_btn", None),
        ):
            if button:
                button.setEnabled(can_manage)
        for button in (
            getattr(self, "open_active_btn", None),
            getattr(self, "open_folder_btn", None),
            getattr(self, "open_version_btn", None),
        ):
            if button:
                button.setEnabled(bool(entry))
        if hasattr(self, "preview_file_btn"):
            is_pdf = str(entry.get("file_type") or "").upper() == "PDF"
            self.preview_file_btn.setEnabled(is_pdf)
            if not is_pdf:
                self._collapse_managed_preview()

    def _load_related_issues_for_file(self, file_id, version_id=None):
        if not hasattr(self, "file_related_issues_table"):
            return
        try:
            issues = self.traceability_service.issues_for_engineering_file(
                int(file_id),
                int(version_id) if version_id else None,
            )
        except Exception:
            issues = []
        self.file_related_issues_table.setRowCount(len(issues))
        for row, issue in enumerate(issues):
            values = [
                issue.get("issue_number"),
                issue.get("title"),
                issue.get("status"),
                issue.get("file_role"),
                issue.get("linked_at"),
            ]
            for col, value in enumerate(values):
                cell = QTableWidgetItem(str(value or ""))
                if col == 0 and issue.get("id") is not None:
                    cell.setData(Qt.UserRole, int(issue["id"]))
                self.file_related_issues_table.setItem(row, col, cell)

    # ── PDF preview helpers ─────────────────────────────────────────
    def _collapse_managed_preview(self):
        if hasattr(self, "preview_file_btn"):
            self.preview_file_btn.blockSignals(True)
            self.preview_file_btn.setChecked(False)
            self.preview_file_btn.setText("Preview")
            self.preview_file_btn.blockSignals(False)
        if hasattr(self, "pdf_viewer"):
            self.pdf_viewer.close_preview()
            self.pdf_viewer.setVisible(False)

    def _toggle_managed_preview(self, checked):
        if not hasattr(self, "pdf_viewer"):
            return
        if not checked:
            self._collapse_managed_preview()
            return
        entry = self._selected_managed_file() or {}
        if str(entry.get("file_type") or "").upper() != "PDF":
            self._collapse_managed_preview()
            return
        self.preview_file_btn.setText("Hide Preview")
        self.pdf_viewer.setVisible(True)
        history = self._selected_history_row() or {}
        if str(history.get("filename") or "").lower().endswith(".pdf"):
            self._on_version_selection_changed()
        else:
            self._try_pdf_preview_for_active(self._selected_attachment_id())

    def _try_pdf_preview_for_active(self, file_id):
        """Load the selected managed PDF into the preview."""
        if not hasattr(self, "pdf_viewer"):
            return
        if not getattr(self, "preview_file_btn", None) or not self.preview_file_btn.isChecked():
            return
        try:
            entry = self._selected_managed_file() or {}
            if str(entry.get("file_type") or "").upper() != "PDF":
                self.pdf_viewer.close_preview()
                return
            path = str(entry.get("path") or "")
            resolved = self._resolve_file_path(path)
            if resolved:
                self.pdf_viewer.load_pdf(resolved)
            else:
                self.pdf_viewer.show_error("The selected PDF attachment has no active version.")
        except Exception as exc:
            self.pdf_viewer.show_error(f"Unable to preview the active PDF:\n{exc}")

    def _on_version_selection_changed(self):
        """When a specific version row is selected, preview it if it is a PDF."""
        file_id = self._selected_attachment_id()
        version_id = self._selected_version_id()
        if file_id:
            self._load_related_issues_for_file(file_id, version_id)
        if not hasattr(self, "pdf_viewer"):
            return
        self._update_managed_file_actions()
        if not getattr(self, "preview_file_btn", None) or not self.preview_file_btn.isChecked():
            return
        try:
            history = self._selected_history_row() or {}
            if not history:
                return
            fname = str(history.get("filename") or "").lower()
            if not fname.endswith(".pdf"):
                self._collapse_managed_preview()
                return
            path = str(history.get("path") or "")
            resolved = self._resolve_file_path(path)
            if resolved:
                self.pdf_viewer.load_pdf(resolved)
            else:
                self.pdf_viewer.show_error("Unable to resolve the selected PDF version path.")
        except Exception as exc:
            self.pdf_viewer.show_error(f"Unable to preview the selected PDF version:\n{exc}")

    def release_selected_version(self):
        if not self.perm.can("release_files"):
            return QMessageBox.warning(self, "Permission", "You do not have permission to release files.")

        file_id = self._selected_attachment_id()
        version_id = self._selected_version_id()
        if not file_id or not version_id:
            return QMessageBox.warning(self, "Select", "Select an attachment and a version.")

        try:
            self.part_file_service.release_version(version_id)
            self.refresh_files_tab()
            self.on_attachment_selected()
            if getattr(self, "current_part_id", None):
                self._refresh_part_in_tree(int(self.current_part_id))
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to approve document version:\n{e}")

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

        revision, ok = QInputDialog.getText(
            self,
            "Create New Revision",
            "New revision code (examples: B, A1, A010):",
        )
        if not ok:
            return
        revision = (revision or "").strip().upper()
        note, ok = QInputDialog.getMultiLineText(
            self,
            "Create New Revision",
            f"Reason for creating revision {revision} from {current or 'the released revision'}:",
        )
        if not ok:
            return

        try:
            created = self.bom_service.create_revision(self.current_part_id, revision, note=note)
            self._refresh_lock_family_rows(int(self.current_part_id))
            self.display_details(self.current_part_id)
            QMessageBox.information(
                self,
                "New Revision",
                f"Created {created.get('version_label') or revision + '.1'} from the released configuration.",
            )
        except Exception as e:
            QMessageBox.critical(self, "New Revision", f"Failed to create revision:\n{e}")

    def release_part_revision(self):
        if not getattr(self, "current_part_id", None):
            return
        if not self.perm.can("set_revision"):
            return QMessageBox.warning(self, "Permission", "You do not have permission to release revisions.")
        details = self.bom_service.get_part_details(int(self.current_part_id)) or {}
        version = str(details.get("current_version") or details.get("revision") or "")
        note, ok = QInputDialog.getMultiLineText(
            self,
            "Release Revision",
            f"Release note for {version}:",
        )
        if not ok:
            return
        answer = QMessageBox.question(
            self,
            "Release Revision",
            f"Release {version}? The revision and its exact child configuration will become immutable.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            self.bom_service.release_part(int(self.current_part_id), note=note)
            self._refresh_lock_family_rows(int(self.current_part_id))
            self.display_details(int(self.current_part_id))
            QMessageBox.information(self, "Release Revision", f"{version} is now released.")
        except Exception as exc:
            QMessageBox.critical(self, "Release Revision", str(exc))

    def add_attachment(self):
        if not self.perm.can("release_files"):
            return QMessageBox.warning(self, "Permission", "You do not have permission to manage files.")
        if not getattr(self, "current_part_id", None):
            return

        file_type, ok = QInputDialog.getItem(
            self, "Document Type", "Select document type:",
            ["PDF", "STEP", "DWG", "XLSX", "OTHER"], 0, False
        )
        if not ok:
            return

        display_name, ok = QInputDialog.getText(self, "Display Name", "Enter attachment name:")
        if not ok or not display_name.strip():
            return

        file_revision, ok = QInputDialog.getText(
            self, "File Revision", "File revision (optional, e.g. A010 or A020):"
        )
        if not ok:
            return

        note, _ = QInputDialog.getText(self, "Version Note", "Version note (optional):")

        source_path, _ = QFileDialog.getOpenFileName(self, "Select file")
        if not source_path:
            return
        if self._is_native_creo_path(source_path):
            return QMessageBox.warning(
                self, "Native Creo Content",
                "Native PRT, ASM, and DRW files are managed through checkout and commit."
            )

        try:
            self.part_file_service.create_attachment(
                part_id=self.current_part_id,
                file_type=file_type,
                display_name=display_name.strip(),
                description="",
                source_path=source_path,
                note=note or "",
                revision_override=str(file_revision or "").strip().upper(),
                file_role=self.managed_file_service.role_for_type(file_type),
            )
            self.refresh_files_tab()
            self._refresh_part_in_tree(int(self.current_part_id))
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to add attachment:\n{e}")

    def link_selected_file_to_issue(self):
        file_id = self._selected_attachment_id()
        if not file_id:
            return QMessageBox.warning(self, "Select", "Select a vaulted file first.")
        try:
            issues = self.issue_service.list_issues({"include_archived": False})
        except Exception:
            issues = []
        if not issues:
            return QMessageBox.information(self, "Issues", "No issues are available to link.")

        labels = [
            f"{issue['issue_number']} - {issue['title']} [{issue['status']}]"
            for issue in issues
        ]
        selected, ok = QInputDialog.getItem(
            self,
            "Link File to Issue",
            "Select issue:",
            labels,
            0,
            False,
        )
        if not ok or not selected:
            return
        issue = issues[labels.index(selected)]

        file_type = self._selected_file_type()
        default_role = {
            "PDF": "exported_pdf",
            "STEP": "exported_step",
            "STP": "exported_step",
        }.get(file_type, "other")
        roles = [
            "exported_pdf",
            "exported_step",
            "inspection_report",
            "screenshot",
            "supporting_doc",
            "other",
        ]
        role, ok = QInputDialog.getItem(
            self,
            "File Role",
            "Traceability role:",
            roles,
            max(0, roles.index(default_role) if default_role in roles else 0),
            False,
        )
        if not ok:
            return
        note, _ = QInputDialog.getText(self, "Link Note", "Note (optional):")
        version_id = self._selected_version_id()
        try:
            self.traceability_service.link_issue_to_engineering_file(
                int(issue["id"]),
                int(file_id),
                int(version_id) if version_id else None,
                role,
                note or "",
            )
            self._load_related_issues_for_file(file_id, version_id)
            self._refresh_linked_issue_traceability(int(issue["id"]))
            QMessageBox.information(self, "Linked", "Engineering file linked to issue.")
        except Exception as e:
            QMessageBox.critical(self, "Link File", str(e))

    def _refresh_linked_issue_traceability(self, issue_id: int):
        try:
            issue_page = getattr(self.window(), "issue_page", None)
            if not issue_page:
                return
            if getattr(issue_page, "current_issue_id", None) == int(issue_id):
                if hasattr(issue_page, "_load_engineering_files"):
                    issue_page._load_engineering_files()
                if hasattr(issue_page, "_load_history"):
                    issue_page._load_history()
                if hasattr(issue_page, "_load_commits"):
                    issue_page._load_commits()
            elif hasattr(issue_page, "refresh"):
                # Keep the issue list counts/current cache fresh without rebuilding the whole app.
                issue_page.refresh()
        except Exception:
            pass

    def add_attachment_version(self):
        if not self.perm.can("release_files"):
            return QMessageBox.warning(self, "Permission", "You do not have permission to manage files.")
        file_id = self._selected_attachment_id()
        if not file_id:
            QMessageBox.warning(self, "Select", "Select an attachment first.")
            return
        revision, ok = QInputDialog.getText(self, "Version Revision", "Revision (optional, e.g. A010):")
        if not ok:
            return
        revision = (revision or "").strip().upper()
        note, ok = QInputDialog.getText(self, "Version Note", "Version note (optional):")
        if not ok:
            return
        source_path, _ = QFileDialog.getOpenFileName(self, "Select new version file")
        if not source_path:
            return
        try:
            self.part_file_service.add_new_version_with_revision(
                file_id,
                source_path,
                note=note or "",
                revision=revision,
            )
            self.refresh_files_tab()
            # keep versions visible
            self.on_attachment_selected()
            if getattr(self, "current_part_id", None):
                self._refresh_part_in_tree(int(self.current_part_id))
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to add version:\n{e}")

    def set_active_version(self):
        if not self.perm.can("release_files"):
            return QMessageBox.warning(self, "Permission", "You do not have permission to manage files.")
        file_id = self._selected_attachment_id()
        version_id = self._selected_version_id()
        if not file_id or not version_id:
            QMessageBox.warning(self, "Select", "Select a document and a historical version.")
            return
        try:
            self.part_file_service.set_active_version(file_id, version_id)
            self.refresh_files_tab()
            self.on_attachment_selected()
            if getattr(self, "current_part_id", None):
                self._refresh_part_in_tree(int(self.current_part_id))
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to select the working document version:\n{e}")

    def open_active_attachment(self):
        entry = self._selected_managed_file() or {}
        if not entry:
            QMessageBox.warning(self, "Select", "Select managed content.")
            return
        self._open_file(str(entry.get("path") or ""), "Managed Content")

    def open_active_attachment_folder(self):
        entry = self._selected_managed_file() or {}
        if not entry:
            QMessageBox.warning(self, "Select", "Select managed content.")
            return
        path = str(entry.get("path") or "")
        resolved = self._resolve_file_path(path)
        if not resolved or not safe_exists(resolved):
            QMessageBox.warning(self, "Missing File", f"Attachment file not found:\n{resolved}")
            return
        folder = os.path.dirname(os.path.abspath(os.path.normpath(resolved)))
        if not folder or not safe_exists(folder):
            QMessageBox.warning(self, "Missing Folder", f"Managed file folder not found:\n{folder}")
            return
        try:
            safe_startfile(folder)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to open managed file folder:\n{e}")

    def _infer_file_type_from_path(self, path: str) -> str:
        ext = os.path.splitext(path or "")[1].lower()
        if ext == ".pdf":
            return "PDF"
        if ext in (".step", ".stp"):
            return "STEP"
        if ext in (".dwg", ".dxf"):
            return "DWG"
        if ext in (".xlsx", ".xls"):
            return "XLSX"
        return "OTHER"

    @staticmethod
    def _is_native_creo_path(path: str) -> bool:
        return bool(re.search(r"\.(?:prt|asm|drw)(?:\.\d+)?$", str(path or ""), re.IGNORECASE))

    def _on_files_dropped(self, paths):
        if not getattr(self, "current_part_id", None):
            QMessageBox.warning(self, "Select", "Select a part first.")
            return
        revision, ok = QInputDialog.getText(self, "Version Revision", "Revision for dropped files (optional, e.g. A010):")
        if not ok:
            return
        revision = (revision or "").strip().upper()
        note, ok = QInputDialog.getText(self, "Version Note", "Version note for dropped files (optional):")
        if not ok:
            return

        added = 0
        failed = []
        # cache current attachments for quick type lookup; refresh when we create a new attachment
        attachments = self.part_file_service.list_attachments(self.current_part_id)

        for p in paths:
            try:
                if not p or not safe_exists(p):
                    raise ValueError("File not found")
                if self._is_native_creo_path(p):
                    raise ValueError("Native Creo content must be added through checkout and commit")

                file_type = self._infer_file_type_from_path(p)
                if file_type == "OTHER":
                    file_type, ok = QInputDialog.getItem(
                        self,
                        "File Type",
                        f"Select type for:\n{os.path.basename(p)}",
                        ["PDF", "STEP", "DWG", "XLSX", "OTHER"],
                        4,
                        False,
                    )
                    if not ok:
                        continue

                # If an attachment of this type already exists for the part, add as a new version
                existing = None
                try:
                    for f in (attachments or []):
                        expected_role = self.managed_file_service.role_for_type(file_type)
                        if (
                            str((f.file_type or "")).upper() == str(file_type or "").upper()
                            and str(getattr(f, "file_role", "") or "").lower() == expected_role
                        ):
                            existing = f
                            break
                except Exception:
                    existing = None

                if existing:
                    # add new version to existing attachment
                    self.part_file_service.add_new_version_with_revision(
                        existing.id,
                        p,
                        note=note or "",
                        revision=revision,
                    )
                else:
                    display_name = os.path.splitext(os.path.basename(p))[0]
                    self.part_file_service.create_attachment(
                        part_id=self.current_part_id,
                        file_type=file_type,
                        display_name=display_name,
                        description="",
                        source_path=p,
                        note=note or "",
                        revision_override=revision,
                        file_role=self.managed_file_service.role_for_type(file_type),
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
            self._refresh_part_in_tree(int(self.current_part_id))
        if failed:
            QMessageBox.warning(self, "Some files failed", "\n".join(failed[:15]))

    def open_selected_version(self):
        history = self._selected_history_row() or {}
        if not history:
            QMessageBox.warning(self, "Select", "Select historical content.")
            return
        self._open_file(str(history.get("path") or ""), "Historical Content")

    def delete_selected_version(self):
        if not self.perm.can("release_files"):
            return QMessageBox.warning(self, "Permission", "You do not have permission to manage files.")
        file_id = self._selected_attachment_id()
        version_id = self._selected_version_id()
        if not file_id or not version_id:
            QMessageBox.warning(self, "Select", "Select an attachment and a version.")
            return
        confirm = QMessageBox.question(
            self, "Obsolete Version",
            "Mark the selected document version obsolete? The managed content will be preserved.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        try:
            self.part_file_service.delete_version(file_id, version_id)
            self.refresh_files_tab()
            self.on_attachment_selected()
            if getattr(self, "current_part_id", None):
                self._refresh_part_in_tree(int(self.current_part_id))
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to obsolete version:\n{e}")

    def remove_attachment(self):
        if not self.perm.can("release_files"):
            return QMessageBox.warning(self, "Permission", "You do not have permission to manage files.")
        file_id = self._selected_attachment_id()
        if not file_id:
            QMessageBox.warning(self, "Select", "Select an attachment.")
            return
        confirm = QMessageBox.question(
            self, "Obsolete Document",
            "Mark this document and its versions obsolete? Managed content and released references will be preserved.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        try:
            self.part_file_service.delete_attachment(file_id)
            self.refresh_files_tab()
            if getattr(self, "current_part_id", None):
                self._refresh_part_in_tree(int(self.current_part_id))
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
                    safe_startfile(out_dir)
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
        if not safe_exists(resolved):
            QMessageBox.warning(self, "Missing File", f"{label} file not found:\n{resolved}")
            return
        try:
            safe_startfile(resolved)  # Windows
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
        path = ""
        try:
            for attachment in self.part_file_service.list_attachments(int(part_id)):
                if str(attachment.file_type or "").upper() == "PDF":
                    path = self.part_file_service.resolve_active_path(attachment.id)
                    if path:
                        break
        except Exception:
            path = ""
        if not path:
            details = self.bom_service.get_part_details(part_id) or {}
            path = details.get("pdf_path", "")
        self._open_file(path, "PDF")

    def preview_part_pdf(self, part_id: int):
        """Select a BOM item, open its Files tab, and preview its active PDF."""
        self.display_details(int(part_id))
        try:
            self.tabs.setCurrentWidget(self.files_tab)
        except Exception:
            pass
        if not self.pdf_viewer.is_loaded():
            details = self.bom_service.get_part_details(int(part_id)) or {}
            path = self._resolve_file_path(details.get("pdf_path", ""))
            if path:
                self.pdf_viewer.load_pdf(path)
            else:
                self.pdf_viewer.show_error("No PDF attachment is available for this BOM item.")

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

    def compare_part_structure(self, part_id=None, whole_bom: bool = False):
        if isinstance(part_id, bool):
            part_id = None
        if not whole_bom and not part_id:
            part_id = getattr(self, "current_part_id", None)
        if not whole_bom and not part_id:
            return QMessageBox.warning(self, "Compare", "Select a BOM item first.")
        if not getattr(self.session, "project_id", None):
            return QMessageBox.warning(self, "Compare", "No active project selected.")
        try:
            dlg = WindchillCompareSetupDialog(
                self,
                self.bom_service,
                self.project_service,
                int(self.session.project_id),
                int(part_id) if part_id else None,
                whole_bom=whole_bom,
            )
            dlg.exec_()
        except Exception as e:
            QMessageBox.critical(self, "Compare", f"Failed to open structure compare:\n{e}")

    def compare_assembly_iterations(self, part_id=None):
        """Open the immutable occurrence comparison for one assembly."""
        if isinstance(part_id, bool):
            part_id = None
        part_id = part_id or getattr(self, "current_part_id", None)
        if not part_id:
            return QMessageBox.warning(
                self, "Compare Assembly Iterations", "Select an assembly first."
            )
        try:
            details = self.bom_service.get_part_details(int(part_id)) or {}
            if str(details.get("type") or "").strip().lower() not in {"asm", "assembly"}:
                return QMessageBox.warning(
                    self,
                    "Compare Assembly Iterations",
                    "Iteration comparison is available only for assemblies.",
                )
            iterations = self.bom_service.list_part_iterations(int(part_id)) or []
            if len(iterations) < 2:
                return QMessageBox.information(
                    self,
                    "Compare Assembly Iterations",
                    "This assembly needs at least two checked-in iterations before they can be compared.",
                )
            dialog = AssemblyIterationCompareDialog(
                self,
                self.bom_service,
                int(part_id),
                cad_file_opener=self._show_iteration_cad_files,
            )
            dialog.exec_()
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Compare Assembly Iterations",
                f"Failed to compare assembly iterations:\n{exc}",
            )

    def create_assembly_configuration(self, part_id=None):
        if isinstance(part_id, bool):
            part_id = None
        part_id = part_id or getattr(self, "current_part_id", None)
        if not part_id:
            return QMessageBox.warning(
                self, "Create Configuration", "Select an assembly first."
            )
        project_id = getattr(self.session, "project_id", None)
        if not project_id:
            return QMessageBox.warning(
                self, "Create Configuration", "No active project is selected."
            )
        try:
            details = self.bom_service.get_part_details(int(part_id)) or {}
            if str(details.get("type") or "").strip().lower() not in {"asm", "assembly"}:
                return QMessageBox.warning(
                    self, "Create Configuration", "A configuration must start from an assembly."
                )
            if not self.bom_service.list_part_iterations(int(part_id)):
                return QMessageBox.information(
                    self,
                    "Create Configuration",
                    "This assembly has no checked-in iteration to freeze.",
                )
            dialog = CreateAssemblyConfigurationDialog(
                self,
                self.assembly_configuration_service,
                self.bom_service,
                int(project_id),
                int(part_id),
            )
            dialog.exec_()
        except Exception as exc:
            QMessageBox.critical(
                self, "Create Configuration", f"Failed to create configuration:\n{exc}"
            )

    def manage_assembly_configurations(self):
        project_id = getattr(self.session, "project_id", None)
        if not project_id:
            return QMessageBox.warning(
                self, "Manage Configurations", "No active project is selected."
            )
        try:
            dialog = ManageAssemblyConfigurationsDialog(
                self, self.assembly_configuration_service, int(project_id)
            )
            dialog.exec_()
        except Exception as exc:
            QMessageBox.critical(
                self, "Manage Configurations", f"Failed to open configurations:\n{exc}"
            )


    def add_child(self, parent_item=None):
        """Select a parent, then select a child directly from the BOM/search view."""
        if not self.perm.can("manage_parts"):
            return QMessageBox.warning(self, "Permission", "You do not have permission to add child relations.")
        if isinstance(parent_item, bool) or not isinstance(parent_item, QTreeWidgetItem):
            parent_item = None
        if parent_item is not None and (
            self._is_folder_tree_item(parent_item)
            or str(parent_item.text(BOM_COL_TYPE) or "").strip().lower() not in {"asm", "assembly"}
        ):
            return QMessageBox.warning(self, "Add Child", "Only an assembly can contain child items.")

        if parent_item is None:
            state = {"phase": "select_parent", "selections": []}
            message = "Select the parent assembly in the BOM. You can use Search to locate it. Press Esc to cancel."
        else:
            state = {
                "phase": "select_child",
                "target_parent_id": int(parent_item.data(0, Qt.UserRole)),
                "target_parent_name": str(parent_item.text(BOM_COL_NAME) or ""),
                "selections": [],
            }
            message = (
                f"Parent selected: {state['target_parent_name']}. Select a child in the BOM; "
                "use Search if needed. Press Esc to cancel."
            )
        self._start_relation_selection_mode(state, message)

    def add_selected_to_parent(self, selected_items) -> None:
        """Keep selected child occurrences and wait for a target parent in the BOM."""
        if not self.perm.can("manage_parts"):
            return QMessageBox.warning(self, "Permission", "You do not have permission to change BOM relations.")
        selections = []
        for item in selected_items or []:
            payload = self._relation_selection_for_item(item)
            if payload is not None:
                selections.append(payload)
        if not selections:
            return QMessageBox.warning(self, "Add to Parent", "Select one or more BOM items first.")
        state = {"phase": "select_target_parent", "selections": selections}
        count = len(selections)
        self._start_relation_selection_mode(
            state,
            f"{count} item{'s' if count != 1 else ''} selected. Select the target parent assembly in the BOM; "
            "use Search if needed. Press Esc to cancel.",
        )

    def _start_relation_selection_mode(self, state: dict, message: str) -> None:
        self._exit_relation_selection_mode(clear_status=False)
        self._relation_selection_state = dict(state or {})
        for tree in (self.tree, self._search_tree):
            try:
                tree.itemClicked.connect(self._on_relation_selection_clicked)
            except Exception:
                pass
        self.setCursor(Qt.PointingHandCursor)
        self.window().statusBar().showMessage(message)

    def _exit_relation_selection_mode(self, clear_status: bool = True) -> None:
        for tree in (getattr(self, "tree", None), getattr(self, "_search_tree", None)):
            if tree is None:
                continue
            try:
                tree.itemClicked.disconnect(self._on_relation_selection_clicked)
            except Exception:
                pass
        self._relation_selection_state = None
        self.setCursor(Qt.ArrowCursor)
        if clear_status:
            try:
                self.window().statusBar().showMessage("Relation operation cancelled.")
            except Exception:
                pass

    def _relation_selection_for_item(self, item: QTreeWidgetItem) -> dict | None:
        if item is None or self._is_folder_tree_item(item) or self._is_lazy_placeholder(item):
            return None
        try:
            child_id = int(item.data(0, Qt.UserRole))
        except (TypeError, ValueError):
            return None
        payload = {
            "child_id": child_id,
            "child_name": str(item.text(BOM_COL_NAME) or item.text(0) or child_id),
            "source_parent_id": None,
            "source_options": [],
        }
        if item.treeWidget() is self.tree:
            payload["source_parent_id"] = self._direct_relation_parent_for_item(item)
            return payload

        try:
            sources = self.bom_service.relation_sources_for_part(child_id) or []
        except Exception:
            sources = []
        if len(sources) == 1:
            payload["source_parent_id"] = int(sources[0]["parent_id"])
        elif len(sources) > 1:
            payload["source_options"] = list(sources)
        return payload

    def _direct_relation_parent_for_item(self, item: QTreeWidgetItem) -> int | None:
        if item is None or item.treeWidget() is not self.tree:
            return None
        parent = item.parent()
        while parent is not None and self._is_folder_tree_item(parent):
            parent = parent.parent()
        if parent is None or parent.data(0, Qt.UserRole) is None:
            return None
        try:
            return int(parent.data(0, Qt.UserRole))
        except (TypeError, ValueError):
            return None

    def _on_relation_selection_clicked(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        state = getattr(self, "_relation_selection_state", None)
        if not state:
            return
        phase = str(state.get("phase") or "")
        if phase in {"select_parent", "select_target_parent"}:
            if self._is_folder_tree_item(item):
                self.window().statusBar().showMessage("Select an assembly, not an organizational folder.")
                return
            if str(item.text(BOM_COL_TYPE) or "").strip().lower() not in {"asm", "assembly"}:
                self.window().statusBar().showMessage("The target parent must be an assembly.")
                return
            target_parent_id = int(item.data(0, Qt.UserRole))
            target_parent_name = str(item.text(BOM_COL_NAME) or "")
            if phase == "select_parent":
                state["phase"] = "select_child"
                state["target_parent_id"] = target_parent_id
                state["target_parent_name"] = target_parent_name
                self.window().statusBar().showMessage(
                    f"Parent selected: {target_parent_name}. Select a child in the BOM; use Search if needed. "
                    "Press Esc to cancel."
                )
                return
            self._finish_relation_selection(target_parent_id, target_parent_name, state.get("selections") or [])
            return

        if phase == "select_child":
            selection = self._relation_selection_for_item(item)
            if selection is None:
                return
            self._finish_relation_selection(
                int(state["target_parent_id"]),
                str(state.get("target_parent_name") or ""),
                [selection],
            )

    def _ask_relation_action(self, count: int, target_name: str) -> str | None:
        box = QMessageBox(self)
        box.setWindowTitle("Add to Parent")
        box.setIcon(QMessageBox.Question)
        box.setText(
            f"Add {count} selected item{'s' if count != 1 else ''} to {target_name}?"
        )
        box.setInformativeText("Copy keeps the current occurrence. Move transfers it to the selected parent.")
        copy_button = box.addButton("Copy", QMessageBox.AcceptRole)
        move_button = box.addButton("Move", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Cancel)
        box.exec_()
        clicked = box.clickedButton()
        if clicked is copy_button:
            return "copy"
        if clicked is move_button:
            return "move"
        return None

    def _resolve_relation_sources(self, selections: list[dict], action: str) -> list[dict] | None:
        resolved = []
        for selection in selections:
            row = dict(selection)
            options = list(row.pop("source_options", []) or [])
            row.pop("child_name", None)
            if options:
                labels = []
                by_label = {}
                for option in options:
                    aes = str(option.get("parent_aes_number") or "").strip()
                    name = str(option.get("parent_name") or "").strip()
                    quantity = int(option.get("quantity") or 1)
                    label = f"{aes} - {name} (Qty {quantity})" if aes else f"{name} (Qty {quantity})"
                    labels.append(label)
                    by_label[label] = option
                selected_label, ok = QInputDialog.getItem(
                    self,
                    "Select Direct Occurrence",
                    f"Choose the source occurrence to {action}:",
                    labels,
                    0,
                    False,
                )
                if not ok:
                    return None
                row["source_parent_id"] = int(by_label[selected_label]["parent_id"])
            resolved.append(row)
        return resolved

    def _finish_relation_selection(
        self,
        target_parent_id: int,
        target_parent_name: str,
        selections: list[dict],
    ) -> None:
        action = self._ask_relation_action(len(selections), target_parent_name)
        if action is None:
            self._exit_relation_selection_mode()
            return
        resolved = self._resolve_relation_sources(selections, action)
        if resolved is None:
            return
        try:
            result = self.bom_service.apply_child_relation_operation(
                int(target_parent_id), resolved, action
            )
        except Exception as exc:
            QMessageBox.critical(self, "BOM Structure", str(exc))
            return

        changed = len(result.get("child_ids") or [])
        skipped = len(result.get("skipped_child_ids") or [])
        verb = "moved" if action == "move" else "copied"
        result["message"] = f"{changed} item{'s' if changed != 1 else ''} {verb}."
        if skipped:
            result["message"] += f" {skipped} already under the selected parent were skipped."
        self._exit_relation_selection_mode(clear_status=False)
        self._apply_child_relation_result(result)

    def _start_interactive_add_child(self, parent_item=None):
        """Start interactive add-child mode (select parent → child → confirm)."""
        if not self.perm.can("manage_parts"):
            return QMessageBox.warning(self, "Permission", "You do not have permission to add child relations.")
        if isinstance(parent_item, bool):
            parent_item = None
        if parent_item is not None and not isinstance(parent_item, QTreeWidgetItem):
            parent_item = None
        if getattr(self, "add_child_mode", False):
            self._exit_add_child_mode()

        selection_tree = parent_item.treeWidget() if parent_item is not None else self._current_tree_for_filtering()
        if selection_tree is None:
            selection_tree = self.tree
        if parent_item is not None:
            if self._is_folder_tree_item(parent_item):
                return QMessageBox.warning(self, "Add Child", "An organizational folder cannot be a BOM parent.")
            part_type = str(parent_item.text(BOM_COL_TYPE) or "").strip().lower()
            if part_type not in {"asm", "assembly"}:
                return QMessageBox.warning(self, "Add Child", "Only an assembly can contain child items.")

        self.parent_item = parent_item
        self.child_item = None
        self.add_child_mode = True
        self._add_child_selection_tree = selection_tree

        # 🔹 Keep your existing design — just give a temporary focus cue
        pal = selection_tree.palette()
        self._original_base_color = pal.color(QPalette.Base)
        pal.setColor(QPalette.Base, QColor(230, 245, 255))  # very light blue background
        selection_tree.setPalette(pal)

        # Cursor + status hint
        self.setCursor(Qt.PointingHandCursor)
        self.window().statusBar().showMessage("🔹 Select the parent part in the tree (Press Esc to cancel)...")

        if parent_item is not None:
            self.window().statusBar().showMessage(
                f"Parent selected: {parent_item.text(BOM_COL_NAME)} | Now select the child part (Press Esc to cancel)..."
            )

        # Connect handlers
        selection_tree.itemClicked.connect(self._on_tree_click_for_add_child)


    def _on_tree_click_for_add_child(self, item):
        """Handle parent → child selection sequence."""
        if not self.add_child_mode:
            return

        # Step 1: select parent
        if self.parent_item is None:
            part_type = item.text(BOM_COL_TYPE).strip().lower()
            if part_type not in {"asm", "assembly"}:
                self.window().statusBar().showMessage("⚠️ Selected part is not an ASM. Please select a valid assembly.")
                return  # Don’t continue

            self.parent_item = item
            self.window().statusBar().showMessage(f"Parent selected: {item.text(BOM_COL_NAME)} | Now select the child part...")
            return

        # Step 2: select child
        if self.child_item is None:
            if item == self.parent_item:
                self.window().statusBar().showMessage("⚠️ Parent and child cannot be the same part. Select a different child or press Esc to cancel.")
                return

            self.child_item = item
            parent_name = self.parent_item.text(BOM_COL_NAME)
            child_name = self.child_item.text(BOM_COL_NAME)

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
                    self._add_child_relation_to_trees(int(parent_id), int(child_id), self.child_item)
                    self.window().statusBar().showMessage("Child added successfully.")
                    try:
                        self.display_details(int(parent_id))
                    except Exception:
                        pass
                except Exception as e:
                    self.window().statusBar().showMessage(f"Failed to add child: {str(e)}")
            else:
                self.window().statusBar().showMessage("Add-child operation cancelled.")

            # Exit selection mode
            self._exit_add_child_mode()


    def _exit_add_child_mode(self):
        """Clean up state, visuals, and connections."""
        selection_tree = getattr(self, "_add_child_selection_tree", None)
        if selection_tree is not None:
            try:
                selection_tree.itemClicked.disconnect(self._on_tree_click_for_add_child)
            except Exception:
                pass
            try:
                palette = selection_tree.palette()
                palette.setColor(QPalette.Base, self._original_base_color)
                selection_tree.setPalette(palette)
            except Exception:
                pass

        self.setCursor(Qt.ArrowCursor)
        #self.window().statusBar().clearMessage()
        self.parent_item = None
        self.child_item = None
        self.add_child_mode = False
        self._add_child_selection_tree = None


    def keyPressEvent(self, event: QKeyEvent):
        """Handle ESC to cancel add-child mode."""
        if getattr(self, "_relation_selection_state", None) and event.key() == Qt.Key_Escape:
            self._exit_relation_selection_mode()
        elif getattr(self, "add_child_mode", False) and event.key() == Qt.Key_Escape:
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
        if getattr(self, "_bom_mode", "cad") == "ebom":
            visible = self._refresh_ebom_filters()
            if not self._is_default_bom_advanced_filter():
                self._update_advanced_filter_button_state(visible)
            self._tree_stack.setCurrentIndex(3)
            return
        if not query:
            self._exit_search_mode()
            if not self._is_default_bom_advanced_filter():
                self.apply_bom_tree_filter(self._bom_advanced_filters)
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
        if getattr(self, "_bom_mode", "cad") == "ebom":
            return
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

        # A new basic search replaces any previous advanced flat result list.
        self._advanced_filter_flat_mode = False
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
                    self._sync_search_tree_row_numbers()
                    self._search_tree.setUpdatesEnabled(True)
                except Exception:
                    pass
                try:
                    # Search results are flat; no need to expand deeply.
                    self._search_tree.expandToDepth(1)
                except Exception:
                    pass
                try:
                    if not self._is_default_bom_advanced_filter():
                        self.apply_bom_tree_filter(self._bom_advanced_filters)
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

    def _invalidate_doc_indicator(self, part_id: int | None = None) -> None:
        try:
            if part_id is None:
                self._doc_indicator_cache.clear()
            else:
                self._doc_indicator_cache.pop(int(part_id), None)
        except Exception:
            self._doc_indicator_cache = {}

    def _refresh_diagnostic_for_part(self, part_id: int) -> set:
        self._invalidate_doc_indicator(int(part_id))
        rows = []
        try:
            if self.working_dir and os.path.isdir(self.working_dir):
                rows = self.diag_service.sync_bom_part_files(self.working_dir, int(part_id)) or []
        except Exception:
            rows = []
        return self._replace_missing_rows_for_part(int(part_id), rows)

    def _make_tree_item(self, info: dict) -> QTreeWidgetItem:
        item = QTreeWidgetItem([""] * BOM_TREE_COLUMN_COUNT)
        self._apply_tree_item_data(item, info or {})
        return item

    @staticmethod
    def _is_folder_tree_item(item: QTreeWidgetItem | None) -> bool:
        return bool(item is not None and item.data(0, BOM_TREE_FOLDER_ROLE))

    def _make_folder_tree_item(self, folder: dict) -> QTreeWidgetItem:
        item = QTreeWidgetItem(["", str(folder.get("name") or "Folder"), "", "", "", "", "", ""])
        item.setData(0, Qt.UserRole, None)
        item.setData(0, BOM_TREE_FOLDER_ROLE, int(folder["id"]))
        item.setData(0, BOM_TREE_PATH_ROLE, str(folder.get("_tree_path") or ""))
        item.setData(0, BOM_TREE_IS_ASSEMBLY_ROLE, True)
        item.setData(0, BOM_TREE_CHILDREN_LOADED_ROLE, True)
        item.setIcon(BOM_COL_NAME, _bom_type_icon("folder"))
        item.setToolTip(BOM_COL_NAME, "Organizational folder")
        folder_id = int(folder["id"])
        row_no = (getattr(self, "_bom_folder_path_rows", {}) or {}).get(str(folder.get("_tree_path") or ""), "")
        if not row_no:
            row_no = (getattr(self, "_bom_folder_row_numbers", {}) or {}).get(folder_id, "")
        item.setText(BOM_COL_ROW, str(row_no or ""))
        item.setTextAlignment(BOM_COL_ROW, Qt.AlignCenter)
        return item

    @staticmethod
    def _is_lazy_placeholder(item: QTreeWidgetItem | None) -> bool:
        return bool(item is not None and item.data(0, BOM_TREE_PLACEHOLDER_ROLE))

    def _ensure_lazy_placeholder(self, item: QTreeWidgetItem) -> None:
        if not item.data(0, BOM_TREE_IS_ASSEMBLY_ROLE):
            return
        if item.data(0, BOM_TREE_CHILDREN_LOADED_ROLE):
            return
        if item.childCount() and self._is_lazy_placeholder(item.child(0)):
            return
        placeholder = QTreeWidgetItem(["", "Loading...", "", "", "", "", "", ""])
        placeholder.setData(0, BOM_TREE_PLACEHOLDER_ROLE, True)
        placeholder.setDisabled(True)
        item.addChild(placeholder)

    @staticmethod
    def _container_count(container) -> int:
        return container.topLevelItemCount() if isinstance(container, QTreeWidget) else container.childCount()

    @staticmethod
    def _container_item(container, index: int) -> QTreeWidgetItem:
        return container.topLevelItem(index) if isinstance(container, QTreeWidget) else container.child(index)

    @staticmethod
    def _container_take(container, index: int) -> QTreeWidgetItem:
        return container.takeTopLevelItem(index) if isinstance(container, QTreeWidget) else container.takeChild(index)

    @staticmethod
    def _container_add(container, item: QTreeWidgetItem) -> None:
        if isinstance(container, QTreeWidget):
            container.addTopLevelItem(item)
        else:
            container.addChild(item)

    def _take_direct_bom_item(self, container, part_id: int) -> QTreeWidgetItem | None:
        for index in range(self._container_count(container)):
            item = self._container_item(container, index)
            if self._is_folder_tree_item(item):
                continue
            try:
                if int(item.data(0, Qt.UserRole)) == int(part_id):
                    return self._container_take(container, index)
            except Exception:
                continue
        return None

    def _extract_folder_bom_items(self, folder_item: QTreeWidgetItem) -> list[QTreeWidgetItem]:
        extracted = []
        while folder_item.childCount():
            child = folder_item.takeChild(0)
            if self._is_folder_tree_item(child):
                extracted.extend(self._extract_folder_bom_items(child))
            else:
                extracted.append(child)
        return extracted

    def _clear_folder_nodes_from_container(self, container) -> None:
        index = self._container_count(container) - 1
        promoted = []
        while index >= 0:
            item = self._container_item(container, index)
            if self._is_folder_tree_item(item):
                folder_item = self._container_take(container, index)
                promoted.extend(self._extract_folder_bom_items(folder_item))
            index -= 1
        for item in promoted:
            self._container_add(container, item)

    def _folder_context_containers(self, parent_bom_id):
        if parent_bom_id is None:
            return [self.tree]
        return self._find_tree_items(int(parent_bom_id), self.tree)

    def _render_folder_context(self, parent_bom_id, folders: list[dict], containers=None) -> None:
        context_folders = [
            folder for folder in folders
            if folder.get("effective_parent_bom_id") == parent_bom_id
        ]
        if not context_folders:
            return
        children_by_folder = defaultdict(list)
        roots = []
        for folder in context_folders:
            parent_folder_id = folder.get("parent_folder_id")
            if parent_folder_id is None:
                roots.append(folder)
            else:
                children_by_folder[int(parent_folder_id)].append(folder)
        roots.sort(key=lambda row: (int(row.get("sort_order") or 0), int(row["id"])))
        for values in children_by_folder.values():
            values.sort(key=lambda row: (int(row.get("sort_order") or 0), int(row["id"])))

        def add_folder(folder: dict, display_parent, base_container) -> None:
            display_path = ""
            if not isinstance(display_parent, QTreeWidget):
                display_path = str(display_parent.data(0, BOM_TREE_PATH_ROLE) or "")
            folder_path = f"{display_path}/f{int(folder['id'])}" if display_path else f"f{int(folder['id'])}"
            folder_item = self._make_folder_tree_item(dict(folder, _tree_path=folder_path))
            self._container_add(display_parent, folder_item)
            assigned_ids = {int(value) for value in (folder.get("item_ids") or [])}
            ordered_assigned_ids = []
            for index in range(self._container_count(base_container)):
                candidate = self._container_item(base_container, index)
                if self._is_folder_tree_item(candidate):
                    continue
                try:
                    candidate_id = int(candidate.data(0, Qt.UserRole))
                except (TypeError, ValueError):
                    continue
                if candidate_id in assigned_ids:
                    ordered_assigned_ids.append(candidate_id)
            for part_id in ordered_assigned_ids:
                bom_item = self._take_direct_bom_item(base_container, int(part_id))
                if bom_item is not None:
                    folder_item.addChild(bom_item)
            for child_folder in children_by_folder.get(int(folder["id"]), []):
                add_folder(child_folder, folder_item, base_container)
            folder_item.setExpanded(False)

        base_containers = containers if containers is not None else self._folder_context_containers(parent_bom_id)
        for base_container in base_containers:
            for folder in roots:
                add_folder(folder, base_container, base_container)

    def _apply_organizational_folders(self) -> None:
        try:
            folders = self.bom_service.list_bom_folders() or []
        except Exception:
            folders = []
        self._bom_folders_cache = list(folders)
        contexts = {folder.get("effective_parent_bom_id") for folder in folders}
        for context in sorted(contexts, key=lambda value: (-1 if value is None else int(value))):
            self._render_folder_context(context, folders)

    def _refresh_folder_context(self, parent_bom_id) -> None:
        containers = self._folder_context_containers(parent_bom_id)
        for container in containers:
            self._clear_folder_nodes_from_container(container)
        try:
            folders = self.bom_service.list_bom_folders() or []
        except Exception:
            folders = []
        self._bom_folders_cache = list(folders)
        render_containers = containers
        if getattr(self, "_lazy_tree_active", False) and parent_bom_id is not None:
            render_containers = [
                container for container in containers
                if container.data(0, BOM_TREE_CHILDREN_LOADED_ROLE)
            ]
        if render_containers:
            self._render_folder_context(parent_bom_id, folders, containers=render_containers)
        self._renumber_full_bom_tree_rows()
        self._sync_search_tree_row_numbers()

    def _apply_tree_item_data(self, item: QTreeWidgetItem, info: dict) -> None:
        part_id = info.get("id")
        existing_path = str(item.data(0, BOM_TREE_PATH_ROLE) or "")
        existing_loaded = item.data(0, BOM_TREE_CHILDREN_LOADED_ROLE)
        existing_is_assembly = bool(item.data(0, BOM_TREE_IS_ASSEMBLY_ROLE))
        issues = self._issues_for_part(part_id)
        locked_txt = ""
        if info.get("locked"):
            who = str(info.get("locked_by_username") or "").strip()
            locked_txt = f"In Work ({who})" if who else "In Work"

        row_text = str(info.get("_tree_row_text") or info.get("_tree_row_number") or "").strip()
        if not row_text:
            row_text = item.text(BOM_COL_ROW)
        item.setText(BOM_COL_ROW, row_text)
        item.setTextAlignment(BOM_COL_ROW, Qt.AlignCenter)
        item.setText(BOM_COL_NAME, str(info.get("name", "") or ""))
        item.setData(0, BOM_TREE_INWORK_ROLE, locked_txt)
        has_lazy_metadata = "_has_children" in info
        is_asm = (
            bool(info.get("_has_children"))
            if has_lazy_metadata
            else existing_is_assembly or bool((info.get("children") or [])) or item.childCount() > 0
        )
        item.setData(0, BOM_TREE_IS_ASSEMBLY_ROLE, is_asm)
        item.setData(0, BOM_TREE_PATH_ROLE, str(info.get("_tree_path") or existing_path))
        loaded_state = not bool(info.get("_has_children")) if has_lazy_metadata else existing_loaded
        if loaded_state is None:
            loaded_state = True
        item.setData(0, BOM_TREE_CHILDREN_LOADED_ROLE, bool(loaded_state))
        issue_summary = self._issue_summary_cache.get(int(part_id), {}) if part_id is not None else {}
        item.setData(0, BOM_TREE_ISSUE_ROLE, issue_summary)
        category_names = info.get("category_names", info.get("categories", []))
        if isinstance(category_names, str):
            category_names = [category_names] if category_names.strip() else []
        item.setData(0, BOM_TREE_CATEGORY_ROLE, list(category_names or []))
        binding_update_count = int(info.get("binding_update_count") or 0)
        item.setData(0, BOM_TREE_BINDING_UPDATE_ROLE, binding_update_count)
        item.setData(0, BOM_TREE_POLICY_ROLE, {
            "classification": str(info.get("classification") or "PHYSICAL"),
            "default_ebom_behavior": str(
                info.get("default_ebom_behavior") or "NORMAL"
            ),
            "ebom_behavior": str(info.get("ebom_behavior") or "INHERIT"),
            "resolved_ebom_behavior": str(
                info.get("resolved_ebom_behavior")
                or info.get("default_ebom_behavior")
                or "NORMAL"
            ),
            "represented_part_id": info.get("represented_part_id"),
            "cad_control_mode": str(info.get("cad_control_mode") or "CONTROLLED"),
        })
        item.setData(0, BOM_TREE_OCCURRENCE_ROLE, {
            "usage_id": info.get("usage_id"),
            "parent_id": info.get("relation_parent_id"),
            "quantity": info.get("quantity"),
        })
        item.setData(
            0, BOM_TREE_PROMOTION_ROLE, list(info.get("promoted_through") or [])
        )
        item.setText(BOM_COL_FILES, "")
        item.setText(BOM_COL_AES, str(info.get("aes_number", "") or ""))
        item.setText(BOM_COL_TYPE, str(info.get("type", "") or ""))
        item.setText(BOM_COL_REV, str(info.get("current_version") or info.get("revision", "") or ""))
        item.setText(BOM_COL_STATUS, str(info.get("status", "") or ""))
        item.setText(BOM_COL_INTEGRITY, "")
        item.setData(0, Qt.UserRole, part_id)

        try:
            if info.get("_defer_indicators"):
                empty_summary = {
                    "pdf": {"state": "absent", "tooltip": "PDF: checking..."},
                    "step": {"state": "absent", "tooltip": "STEP: checking..."},
                }
                item.setData(BOM_COL_FILES, BOM_TREE_FILES_ROLE, _file_badges_payload(part_id, issues, empty_summary))
                item.setData(BOM_COL_INTEGRITY, BOM_TREE_INTEGRITY_ROLE, _integrity_payload(part_id, self.missing_files, self.missing_ids))
                item.setIcon(BOM_COL_NAME, _bom_type_icon(info.get("type")))
                self._indicator_refresh_queue.append((item, part_id))
                if not self._indicator_refresh_timer.isActive():
                    self._indicator_refresh_timer.start()
                return
            summary = self._indicator_summary_for_part(part_id, issues)
            item.setData(BOM_COL_FILES, BOM_TREE_FILES_ROLE, _file_badges_payload(part_id, issues, summary))
            item.setData(BOM_COL_INTEGRITY, BOM_TREE_INTEGRITY_ROLE, _integrity_payload(part_id, self.missing_files, self.missing_ids))
            item.setIcon(BOM_COL_NAME, _bom_type_icon(info.get("type")))
            tips0 = []
            if locked_txt:
                tips0.append(locked_txt)
            direct_active = int(issue_summary.get("direct_active_count", issue_summary.get("active_count") or 0) or 0)
            inherited_active = int(issue_summary.get("inherited_active_count") or 0)
            if direct_active and inherited_active:
                tips0.append(f"{direct_active} direct active issue(s); {inherited_active} inherited active issue(s) from children")
            elif direct_active:
                tips0.append(f"{direct_active} direct active issue(s) linked to this part")
            elif inherited_active:
                tips0.append(f"{inherited_active} inherited active issue(s) from child parts")
            elif int(issue_summary.get("total_count") or 0):
                tips0.append("All issues linked to this part are resolved")
            if binding_update_count:
                tips0.append(
                    f"{binding_update_count} direct child version update(s) available"
                )
            classification = str(info.get("classification") or "PHYSICAL").upper()
            occurrence_behavior = str(info.get("ebom_behavior") or "INHERIT").upper()
            resolved_behavior = str(
                info.get("resolved_ebom_behavior")
                or info.get("default_ebom_behavior")
                or "NORMAL"
            ).upper()
            tips0.append(
                f"EBOM policy: {classification}; occurrence {occurrence_behavior}; "
                f"effective {resolved_behavior}"
            )
            if str(info.get("cad_control_mode") or "CONTROLLED").upper() == "SUPPLIER_PACKAGE":
                tips0.append(
                    "Supplier-managed CAD package: internal owned CAD dependencies are not BOM items and are excluded from individual integrity checks."
                )
            if info.get("represented_part_id"):
                tips0.append(
                    f"CAD-only representation of physical BOM item #{info.get('represented_part_id')}. "
                    "It shares that item's AES number and has no drawing, PDF, or STEP delivery output."
                )
            elif resolved_behavior == "EXCLUDE":
                tips0.append(
                    "Not for delivery: this CAD occurrence and its descendants do not appear in Released EBOM."
                )
            elif resolved_behavior == "FLATTEN":
                tips0.append(
                    "CAD grouping only: this occurrence does not appear in Released EBOM; its children are promoted."
                )
            promoted_through = list(info.get("promoted_through") or [])
            if promoted_through:
                labels = " > ".join(
                    str(
                        value.get("aes_number")
                        or value.get("name")
                        or value.get("bom_id")
                    )
                    for value in promoted_through
                )
                tips0.append(
                    f"Promoted through flattened CAD structure: {labels}"
                )
            if not info.get("represented_part_id"):
                tips0.append(str(summary["pdf"].get("tooltip", "PDF: unknown")))
                tips0.append(str(summary["step"].get("tooltip", "STEP: unknown")))
            item.setToolTip(BOM_COL_NAME, "\n".join(tips0))
            item.setToolTip(BOM_COL_FILES, "\n".join([
                str(summary["pdf"].get("tooltip", "PDF: unknown")),
                str(summary["step"].get("tooltip", "STEP: unknown")),
            ]))
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

    def _renumber_tree_rows(self, tree_widget: QTreeWidget | None, update_full_map: bool = False) -> None:
        if tree_widget is None:
            return
        row_map = defaultdict(list)
        row_no = 0

        def recurse(item: QTreeWidgetItem, ancestors_visible: bool = True):
            nonlocal row_no, row_map
            visible = True if update_full_map else (ancestors_visible and not item.isHidden())
            if visible:
                row_no += 1
                item.setText(BOM_COL_ROW, str(row_no))
                if update_full_map:
                    try:
                        row_map[int(item.data(0, Qt.UserRole))].append(row_no)
                    except Exception:
                        pass
            else:
                item.setText(BOM_COL_ROW, "")
            item.setTextAlignment(BOM_COL_ROW, Qt.AlignCenter)
            for idx in range(item.childCount()):
                recurse(item.child(idx), visible)

        try:
            for idx in range(tree_widget.topLevelItemCount()):
                recurse(tree_widget.topLevelItem(idx), True)
        except Exception:
            pass
        if update_full_map:
            self._bom_row_numbers = {pid: rows for pid, rows in row_map.items()}

    def _refresh_bom_row_numbers(self) -> None:
        self._renumber_full_bom_tree_rows()
        self._sync_search_tree_row_numbers()
        self._renumber_tree_rows(getattr(self, "_ebom_tree", None))

    def _renumber_full_bom_tree_rows(self) -> None:
        if getattr(self, "_lazy_tree_active", False):
            try:
                numbering = self.bom_service.get_bom_lazy_numbering(int(self.session.project_id))
                self._bom_row_numbers = dict(numbering.get("row_numbers") or {})
                self._bom_folder_row_numbers = dict(numbering.get("folder_rows") or {})
                self._bom_folder_path_rows = dict(numbering.get("folder_path_rows") or {})
                self._bom_folders_cache = list(numbering.get("folders") or [])
                path_rows = numbering.get("path_rows") or {}
                for item in self._iter_tree_items(self.tree):
                    if self._is_lazy_placeholder(item):
                        continue
                    folder_id = item.data(0, BOM_TREE_FOLDER_ROLE)
                    if folder_id:
                        path = str(item.data(0, BOM_TREE_PATH_ROLE) or "")
                        row_no = self._bom_folder_path_rows.get(path, self._bom_folder_row_numbers.get(int(folder_id), ""))
                    else:
                        path = str(item.data(0, BOM_TREE_PATH_ROLE) or "")
                        row_no = path_rows.get(path, "")
                        if not row_no:
                            try:
                                rows = self._bom_row_numbers.get(int(item.data(0, Qt.UserRole)), [])
                                row_no = rows[0] if rows else ""
                            except Exception:
                                row_no = ""
                    item.setText(BOM_COL_ROW, str(row_no or ""))
                    item.setTextAlignment(BOM_COL_ROW, Qt.AlignCenter)
                return
            except Exception:
                pass
        self._renumber_tree_rows(getattr(self, "tree", None), update_full_map=True)

    def _full_bom_row_text_for_part(self, part_id) -> str:
        try:
            rows = (getattr(self, "_bom_row_numbers", {}) or {}).get(int(part_id), [])
        except Exception:
            rows = []
        if not rows:
            return ""
        if len(rows) <= 3:
            return ", ".join(str(r) for r in rows)
        return ", ".join(str(r) for r in rows[:3]) + "+"

    def _sync_search_tree_row_numbers(self) -> None:
        search_tree = getattr(self, "_search_tree", None)
        if search_tree is None:
            return
        try:
            for item in self._iter_tree_items(search_tree):
                item.setText(BOM_COL_ROW, self._full_bom_row_text_for_part(item.data(0, Qt.UserRole)))
                item.setTextAlignment(BOM_COL_ROW, Qt.AlignCenter)
        except Exception:
            pass

    def _on_tree_item_entered(self, item: QTreeWidgetItem, column: int) -> None:
        """Show explicit tooltip when hovering a tree item (works around platform quirks)."""
        try:
            if item is None:
                return
            if column in (BOM_COL_FILES, BOM_COL_STATUS, BOM_COL_INTEGRITY):
                return
            tip = item.toolTip(column) or item.toolTip(BOM_COL_NAME) or ""
            if tip:
                tw = item.treeWidget()
                QToolTip.showText(QCursor.pos(), tip, tw or self.tree)
        except Exception:
            pass

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
                "name": item.text(BOM_COL_NAME),
                "aes_number": item.text(BOM_COL_AES),
                "type": item.text(BOM_COL_TYPE),
                "revision": item.text(BOM_COL_REV),
                "status": item.text(BOM_COL_STATUS),
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
        if getattr(self, "_lazy_tree_active", False):
            try:
                numbering = self.bom_service.get_bom_lazy_numbering(int(self.session.project_id))
                path = str(int(part_id))
                info["_tree_path"] = path
                info["_tree_row_number"] = (numbering.get("path_rows") or {}).get(path, "")
                info["_has_children"] = False
                info["_defer_indicators"] = True
                self._bom_row_numbers = dict(numbering.get("row_numbers") or {})
            except Exception:
                pass

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

        self._renumber_full_bom_tree_rows()
        self._sync_search_tree_row_numbers()
        self._select_part_item(int(part_id))
        return info

    def _child_exists_under_item(self, parent_item: QTreeWidgetItem, child_id: int) -> bool:
        for idx in range(parent_item.childCount()):
            child = parent_item.child(idx)
            if self._is_folder_tree_item(child) and self._child_exists_under_item(child, child_id):
                return True
            try:
                if int(child.data(0, Qt.UserRole)) == int(child_id):
                    return True
            except Exception:
                continue
        return False

    def _direct_child_items(self, parent_item: QTreeWidgetItem, child_id: int) -> list[QTreeWidgetItem]:
        matches = []
        for idx in range(parent_item.childCount()):
            child = parent_item.child(idx)
            if self._is_folder_tree_item(child):
                matches.extend(self._direct_child_items(child, child_id))
                continue
            try:
                if int(child.data(0, Qt.UserRole)) == int(child_id):
                    matches.append(child)
            except Exception:
                continue
        return matches

    def _add_child_relation_to_tree_widget(
        self,
        tree_widget: QTreeWidget,
        parent_id: int,
        child_node: dict,
    ) -> int:
        if tree_widget is None or not child_node:
            return 0
        added = 0
        child_id = int(child_node.get("id"))
        try:
            tree_widget.setUpdatesEnabled(False)
        except Exception:
            pass
        try:
            for parent_item in self._find_tree_items(int(parent_id), tree_widget):
                if (
                    tree_widget is self.tree
                    and getattr(self, "_lazy_tree_active", False)
                    and not parent_item.data(0, BOM_TREE_CHILDREN_LOADED_ROLE)
                ):
                    # The database is already updated. Let the first expansion fetch
                    # the complete direct level once instead of inserting a duplicate.
                    continue
                if self._child_exists_under_item(parent_item, child_id):
                    continue
                node = dict(child_node)
                if (
                    tree_widget is self.tree
                    and getattr(self, "_lazy_tree_active", False)
                    and parent_item.data(0, BOM_TREE_CHILDREN_LOADED_ROLE)
                ):
                    parent_path = str(parent_item.data(0, BOM_TREE_PATH_ROLE) or "")
                    child_path = f"{parent_path}/{child_id}" if parent_path else str(child_id)
                    try:
                        numbering = self.bom_service.get_bom_lazy_numbering(int(self.session.project_id))
                        node["_tree_path"] = child_path
                        node["_tree_row_number"] = (numbering.get("path_rows") or {}).get(child_path, "")
                        node["_has_children"] = child_id in (numbering.get("has_children") or set())
                    except Exception:
                        pass
                    node["_defer_indicators"] = True
                new_item = self._add_node_to_tree(tree_widget, node, parent_item)
                self._ensure_lazy_placeholder(new_item)
                parent_item.setExpanded(True)
                added += 1
        finally:
            try:
                tree_widget.setUpdatesEnabled(True)
                tree_widget.viewport().update()
            except Exception:
                pass
        return added

    def _add_child_relation_to_trees(self, parent_id: int, child_id: int, source_item: QTreeWidgetItem | None = None) -> None:
        if source_item is not None:
            child_node = self._node_from_tree_item(source_item)
        else:
            child_node = self.bom_service.get_part_details(int(child_id)) or {}
            child_node = dict(child_node)
            child_node["children"] = []
        if not child_node:
            return

        self._add_child_relation_to_tree_widget(self.tree, int(parent_id), child_node)
        try:
            if getattr(self, "_in_search_mode", False):
                if self._part_matches_current_search(child_node):
                    self._add_child_relation_to_tree_widget(self._search_tree, int(parent_id), child_node)
        except Exception:
            pass

        try:
            self._refresh_part_in_tree(int(parent_id))
            self._refresh_part_in_tree(int(child_id))
        except Exception:
            pass
        self._renumber_full_bom_tree_rows()
        self._sync_search_tree_row_numbers()

    def _remove_child_relation_from_tree_widget(self, tree_widget: QTreeWidget, parent_id: int, child_id: int) -> int:
        if tree_widget is None:
            return 0
        removed = 0
        try:
            tree_widget.setUpdatesEnabled(False)
        except Exception:
            pass
        try:
            for parent_item in self._find_tree_items(int(parent_id), tree_widget):
                for child_item in list(self._direct_child_items(parent_item, int(child_id))):
                    try:
                        visual_parent = child_item.parent()
                        if visual_parent is not None:
                            visual_parent.removeChild(child_item)
                        else:
                            index = tree_widget.indexOfTopLevelItem(child_item)
                            if index >= 0:
                                tree_widget.takeTopLevelItem(index)
                        removed += 1
                    except Exception:
                        pass
                if (
                    tree_widget is self.tree
                    and getattr(self, "_lazy_tree_active", False)
                    and parent_item.data(0, BOM_TREE_CHILDREN_LOADED_ROLE)
                ):
                    has_engineering_child = False
                    for index in range(parent_item.childCount()):
                        candidate = parent_item.child(index)
                        if self._is_lazy_placeholder(candidate):
                            continue
                        if self._is_folder_tree_item(candidate):
                            if candidate.childCount() > 0:
                                has_engineering_child = True
                                break
                            continue
                        has_engineering_child = True
                        break
                    parent_item.setData(0, BOM_TREE_IS_ASSEMBLY_ROLE, has_engineering_child)
                    if not has_engineering_child:
                        parent_item.setData(0, BOM_TREE_CHILDREN_LOADED_ROLE, True)
        finally:
            try:
                tree_widget.setUpdatesEnabled(True)
                tree_widget.viewport().update()
            except Exception:
                pass
        return removed

    def _remove_child_relation_from_trees(self, parent_id: int, child_id: int) -> None:
        self._remove_child_relation_from_tree_widget(self.tree, int(parent_id), int(child_id))
        self._remove_child_relation_from_tree_widget(self._search_tree, int(parent_id), int(child_id))
        try:
            self._refresh_part_in_tree(int(parent_id))
            self._refresh_part_in_tree(int(child_id))
        except Exception:
            pass
        self._renumber_full_bom_tree_rows()
        self._sync_search_tree_row_numbers()

    def remove_selected_children(
        self,
        parent_id: int,
        parent_name: str,
        selected_items,
    ) -> None:
        child_ids = []
        child_names = []
        for item in selected_items or []:
            try:
                child_id = int(item.data(0, Qt.UserRole))
            except (TypeError, ValueError):
                continue
            if child_id in child_ids:
                continue
            child_ids.append(child_id)
            child_names.append(str(item.text(BOM_COL_NAME) or child_id))
        if not child_ids:
            return

        count = len(child_ids)
        preview = "\n".join(f"- {name}" for name in child_names[:10])
        if count > 10:
            preview += f"\n- ... and {count - 10} more"
        reply = QMessageBox.question(
            self,
            "Remove Child Relations",
            f"Remove {count} selected child{'ren' if count != 1 else ''} from {parent_name}?\n\n"
            f"{preview}\n\n"
            "This does not delete any BOM item. If an item has no other parent, it will be moved to the top level.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        pending_paths = set(getattr(self, "_pending_relation_expanded_paths", set()) or set())
        pending_part_ids = set(getattr(self, "_pending_relation_expanded_part_ids", set()) or set())

        def capture(item: QTreeWidgetItem, selected_subtree: bool = False) -> None:
            if item is None or self._is_lazy_placeholder(item):
                return
            try:
                item_id = int(item.data(0, Qt.UserRole))
            except (TypeError, ValueError):
                item_id = None
            in_selected_subtree = selected_subtree or (item_id in child_ids if item_id is not None else False)
            if item.isExpanded():
                path = str(item.data(0, BOM_TREE_PATH_ROLE) or "")
                if path:
                    pending_paths.add(path)
                if in_selected_subtree and item_id is not None:
                    pending_part_ids.add(item_id)
            for index in range(item.childCount()):
                capture(item.child(index), in_selected_subtree)

        for parent_item in self._find_tree_items(int(parent_id), self.tree):
            for index in range(parent_item.childCount()):
                capture(parent_item.child(index))
        self._pending_relation_expanded_paths = pending_paths
        self._pending_relation_expanded_part_ids = pending_part_ids

        try:
            result = self.bom_service.remove_children_from_parent(int(parent_id), child_ids)
        except Exception as exc:
            QMessageBox.critical(self, "Remove Child", f"Failed to remove selected children:\n{exc}")
            return

        self._cancel_lazy_requests_for_parts({int(parent_id)})
        try:
            self._bom_folders_cache = list(self.bom_service.list_bom_folders() or [])
        except Exception:
            pass
        try:
            numbering = self.bom_service.get_bom_lazy_numbering(int(self.session.project_id))
            has_children = set(numbering.get("has_children") or set())
            path_rows = dict(numbering.get("path_rows") or {})
            self._bom_row_numbers = dict(numbering.get("row_numbers") or {})
            self._bom_folder_row_numbers = dict(numbering.get("folder_rows") or {})
            self._bom_folder_path_rows = dict(numbering.get("folder_path_rows") or {})
        except Exception:
            has_children = {int(parent_id)}
            path_rows = {}

        for parent_item in self._find_tree_items(int(parent_id), self.tree):
            parent_item.setData(0, BOM_TREE_IS_ASSEMBLY_ROLE, int(parent_id) in has_children)
            parent_item.setData(0, BOM_TREE_CHILDREN_LOADED_ROLE, False)
            parent_item.setData(0, BOM_TREE_REPLACE_CHILDREN_ROLE, True)
            self._load_lazy_children_for_item(parent_item, asynchronous=True, expand_after=False)

        self._renumber_full_bom_tree_rows()
        for child_id in result.get("moved_to_root_ids") or []:
            root_path = str(int(child_id))
            if any(
                str(existing.data(0, BOM_TREE_PATH_ROLE) or "") == root_path
                for existing in self._find_tree_items(int(child_id), self.tree)
            ):
                continue
            info = self.bom_service.get_part_details(int(child_id)) or {}
            if not info:
                continue
            info = dict(info)
            info["children"] = []
            info["_tree_path"] = root_path
            info["_tree_row_number"] = path_rows.get(root_path, "")
            info["_has_children"] = int(child_id) in has_children
            info["_defer_indicators"] = True
            root_item = self._make_tree_item(info)
            target_row = int(info.get("_tree_row_number") or 0)
            insert_at = self.tree.topLevelItemCount()
            for index in range(self.tree.topLevelItemCount()):
                try:
                    existing_row = int(self.tree.topLevelItem(index).text(BOM_COL_ROW) or 0)
                except (TypeError, ValueError):
                    existing_row = 0
                if existing_row and target_row and existing_row > target_row:
                    insert_at = index
                    break
            self.tree.insertTopLevelItem(insert_at, root_item)
            self._ensure_lazy_placeholder(root_item)
            if int(child_id) in self._pending_relation_expanded_part_ids:
                self._pending_relation_expanded_part_ids.discard(int(child_id))
                root_item.setExpanded(True)

        self._renumber_full_bom_tree_rows()
        self._sync_search_tree_row_numbers()
        try:
            self.tree.viewport().update()
            self.display_details(int(parent_id))
        except Exception:
            pass

        moved_items = result.get("moved_to_root_items") or []
        if moved_items:
            moved_names = "\n".join(
                f"- {row.get('aes_number') + ' - ' if row.get('aes_number') else ''}{row.get('name') or row.get('id')}"
                for row in moved_items
            )
            QMessageBox.warning(
                self,
                "Moved to Top Level",
                "The following items had no other parent and were moved to the top level. "
                "They were not deleted:\n\n" + moved_names,
            )
        else:
            QMessageBox.information(
                self,
                "Children Removed",
                f"{count} child relation{'s' if count != 1 else ''} removed. The items still exist elsewhere in the BOM.",
            )

    def _apply_child_relation_result(self, result: dict) -> None:
        """Refresh changed relation contexts without altering expansion state."""
        if not result:
            return
        parent_ids = {int(result.get("target_parent_id"))}
        parent_ids.update(int(value) for value in (result.get("source_parent_ids") or []))
        changed_child_ids = {int(value) for value in (result.get("child_ids") or [])}

        pending_paths = set(getattr(self, "_pending_relation_expanded_paths", set()) or set())
        pending_part_ids = set(getattr(self, "_pending_relation_expanded_part_ids", set()) or set())

        def capture_expanded(item: QTreeWidgetItem, moving_subtree: bool = False) -> None:
            if item is None or self._is_lazy_placeholder(item):
                return
            try:
                item_id = int(item.data(0, Qt.UserRole))
            except (TypeError, ValueError):
                item_id = None
            in_moving_subtree = moving_subtree or (item_id in changed_child_ids if item_id is not None else False)
            if item.isExpanded():
                path = str(item.data(0, BOM_TREE_PATH_ROLE) or "")
                if path:
                    pending_paths.add(path)
                if in_moving_subtree and item_id is not None:
                    pending_part_ids.add(item_id)
            for child_index in range(item.childCount()):
                capture_expanded(item.child(child_index), in_moving_subtree)

        for parent_id in parent_ids:
            for parent_item in self._find_tree_items(parent_id, self.tree):
                for child_index in range(parent_item.childCount()):
                    capture_expanded(parent_item.child(child_index))
        if result.get("had_root_sources"):
            for child_id in changed_child_ids:
                for root_item in self._find_tree_items(child_id, self.tree):
                    if str(root_item.data(0, BOM_TREE_PATH_ROLE) or "") == str(child_id):
                        capture_expanded(root_item, True)

        self._pending_relation_expanded_paths = pending_paths
        self._pending_relation_expanded_part_ids = pending_part_ids
        self._cancel_lazy_requests_for_parts(parent_ids)

        if result.get("had_root_sources"):
            for child_id in (result.get("child_ids") or []):
                for item in list(self._find_tree_items(int(child_id), self.tree)):
                    if str(item.data(0, BOM_TREE_PATH_ROLE) or "") != str(int(child_id)):
                        continue
                    visual_parent = item.parent()
                    if visual_parent is not None:
                        visual_parent.removeChild(item)
                    else:
                        index = self.tree.indexOfTopLevelItem(item)
                        if index >= 0:
                            self.tree.takeTopLevelItem(index)

        try:
            self._bom_folders_cache = list(self.bom_service.list_bom_folders() or [])
        except Exception:
            pass
        try:
            numbering = self.bom_service.get_bom_lazy_numbering(int(self.session.project_id))
            has_children = set(numbering.get("has_children") or set())
        except Exception:
            has_children = set(parent_ids)

        for parent_id in parent_ids:
            for item in self._find_tree_items(int(parent_id), self.tree):
                if self._is_folder_tree_item(item):
                    continue
                item.setData(0, BOM_TREE_IS_ASSEMBLY_ROLE, parent_id in has_children)
                item.setData(0, BOM_TREE_CHILDREN_LOADED_ROLE, False)
                item.setData(0, BOM_TREE_REPLACE_CHILDREN_ROLE, True)
                if parent_id in has_children and item.childCount() == 0:
                    self._ensure_lazy_placeholder(item)
                self._load_lazy_children_for_item(
                    item, asynchronous=True, expand_after=False
                )

        self._renumber_full_bom_tree_rows()
        self._sync_search_tree_row_numbers()
        try:
            self.tree.viewport().update()
        except Exception:
            pass

        message = str(result.get("message") or "BOM structure updated.")
        try:
            self.window().statusBar().showMessage(message)
        except Exception:
            pass

    def _restore_pending_relation_expansions(self, parent_item: QTreeWidgetItem) -> None:
        pending_paths = getattr(self, "_pending_relation_expanded_paths", set()) or set()
        pending_part_ids = getattr(self, "_pending_relation_expanded_part_ids", set()) or set()

        def restore(item: QTreeWidgetItem) -> None:
            if item is None or self._is_lazy_placeholder(item):
                return
            path = str(item.data(0, BOM_TREE_PATH_ROLE) or "")
            try:
                part_id = int(item.data(0, Qt.UserRole))
            except (TypeError, ValueError):
                part_id = None
            should_expand = path in pending_paths or (part_id is not None and part_id in pending_part_ids)
            if should_expand:
                pending_paths.discard(path)
                if part_id is not None:
                    pending_part_ids.discard(part_id)
                item.setExpanded(True)
            if self._is_folder_tree_item(item):
                for index in range(item.childCount()):
                    restore(item.child(index))

        for index in range(parent_item.childCount()):
            restore(parent_item.child(index))

    def _selected_sibling_reorder_context(self):
        selected = [item for item in self.tree.selectedItems() if item is not None]
        if not selected:
            item = self.tree.currentItem()
            selected = [item] if item is not None else []
        if not selected:
            raise ValueError("Select one or more BOM items to reorder.")

        visual_parent = selected[0].parent()
        if visual_parent is None:
            raise ValueError("Top-level BOM items cannot be reordered here because they are not child associations.")
        for item in selected:
            if item.parent() is not visual_parent:
                raise ValueError("Reorder works only for items under the same parent assembly.")
        engineering_parent = visual_parent
        while engineering_parent is not None and engineering_parent.data(0, Qt.UserRole) is None:
            engineering_parent = engineering_parent.parent()
        if engineering_parent is None or engineering_parent.data(0, Qt.UserRole) is None:
            raise ValueError("The engineering parent for this visual group was not found.")
        parent_id = int(engineering_parent.data(0, Qt.UserRole))
        selected_ids = [int(item.data(0, Qt.UserRole)) for item in selected]
        current_order = [int(value) for value in self.bom_service.ordered_child_ids(parent_id)]
        selected_set = set(selected_ids)
        selected_in_order = [cid for cid in current_order if cid in selected_set]
        if not selected_in_order:
            raise ValueError("No valid selected children to reorder.")
        return parent_id, current_order, selected_in_order

    def _apply_reordered_children(self, parent_id: int, ordered_child_ids: list[int]) -> None:
        self.bom_service.reorder_children(int(parent_id), [int(x) for x in ordered_child_ids])
        self._reorder_children_in_tree_widget(self.tree, int(parent_id), ordered_child_ids)
        self._reorder_children_in_tree_widget(self._search_tree, int(parent_id), ordered_child_ids)
        self._renumber_full_bom_tree_rows()
        self._sync_search_tree_row_numbers()
        try:
            self._refresh_part_in_tree(int(parent_id))
        except Exception:
            pass

    def _reorder_children_in_tree_widget(self, tree_widget: QTreeWidget, parent_id: int, ordered_child_ids: list[int]) -> None:
        if tree_widget is None:
            return
        order = [int(x) for x in ordered_child_ids]
        order_positions = {child_id: index for index, child_id in enumerate(order)}
        try:
            tree_widget.setUpdatesEnabled(False)
        except Exception:
            pass
        try:
            for parent_item in self._find_tree_items(int(parent_id), tree_widget):
                def sort_visual_container(container: QTreeWidgetItem) -> None:
                    children = [container.takeChild(0) for _ in range(container.childCount())]
                    sortable_positions = []
                    sortable_items = []
                    for index, child in enumerate(children):
                        try:
                            child_id = int(child.data(0, Qt.UserRole))
                        except (TypeError, ValueError):
                            child_id = None
                        if child_id in order_positions:
                            sortable_positions.append(index)
                            sortable_items.append(child)
                    sortable_items.sort(
                        key=lambda child: order_positions[int(child.data(0, Qt.UserRole))]
                    )
                    for index, child in zip(sortable_positions, sortable_items):
                        children[index] = child
                    for child in children:
                        container.addChild(child)
                    for child in children:
                        if self._is_folder_tree_item(child):
                            sort_visual_container(child)

                sort_visual_container(parent_item)
        finally:
            try:
                tree_widget.setUpdatesEnabled(True)
                tree_widget.viewport().update()
            except Exception:
                pass

    def _reorder_selected_siblings(self, mode: str) -> None:
        if not self.perm.can("manage_parts"):
            return QMessageBox.warning(self, "Permission", "You do not have permission to reorder BOM items.")
        try:
            parent_id, current_order, selected_in_order = self._selected_sibling_reorder_context()
            selected_set = set(selected_in_order)
            remaining = [cid for cid in current_order if cid not in selected_set]

            if mode == "top":
                new_order = selected_in_order + remaining
            elif mode == "bottom":
                new_order = remaining + selected_in_order
            elif mode == "up":
                new_order = list(current_order)
                selected_positions = [i for i, cid in enumerate(new_order) if cid in selected_set]
                if selected_positions and selected_positions[0] > 0:
                    before_idx = selected_positions[0] - 1
                    before = new_order.pop(before_idx)
                    insert_at = selected_positions[-1]
                    new_order.insert(insert_at, before)
            elif mode == "down":
                new_order = list(current_order)
                selected_positions = [i for i, cid in enumerate(new_order) if cid in selected_set]
                if selected_positions and selected_positions[-1] < len(new_order) - 1:
                    after_idx = selected_positions[-1] + 1
                    after = new_order.pop(after_idx)
                    insert_at = selected_positions[0]
                    new_order.insert(insert_at, after)
            elif mode == "position":
                pos, ok = QInputDialog.getInt(
                    self,
                    "Move To Position",
                    f"New position for selected item(s), 1 to {len(current_order)}:",
                    1,
                    1,
                    max(1, len(current_order)),
                    1,
                )
                if not ok:
                    return
                insert_at = max(0, min(int(pos) - 1, len(remaining)))
                new_order = remaining[:insert_at] + selected_in_order + remaining[insert_at:]
            else:
                return

            if new_order == current_order:
                return
            self._apply_reordered_children(parent_id, new_order)
            self.window().statusBar().showMessage("BOM child order updated.")
        except Exception as e:
            QMessageBox.warning(self, "Reorder", str(e))

    def _handle_tree_drag_reorder(self, selected_ids: list, target_id: int, target_parent_id: int, where: str) -> None:
        if not self.perm.can("manage_parts"):
            return QMessageBox.warning(self, "Permission", "You do not have permission to reorder BOM items.")
        try:
            if int(target_parent_id) < 0:
                raise ValueError("Top-level BOM items cannot be reordered here.")
            selected_set = {int(x) for x in selected_ids or []}
            if int(target_id) in selected_set:
                return
            current_order = [
                int(value) for value in self.bom_service.ordered_child_ids(int(target_parent_id))
            ]
            if int(target_id) not in current_order or not selected_set.issubset(set(current_order)):
                raise ValueError("Drag reorder works only between siblings under the same parent assembly.")
            selected_in_order = [cid for cid in current_order if cid in selected_set]
            remaining = [cid for cid in current_order if cid not in selected_set]
            target_index = remaining.index(int(target_id))
            if where == "below":
                target_index += 1
            new_order = remaining[:target_index] + selected_in_order + remaining[target_index:]
            if new_order == current_order:
                return
            self._apply_reordered_children(int(target_parent_id), new_order)
            self.window().statusBar().showMessage("BOM child order updated.")
        except Exception as e:
            QMessageBox.warning(self, "Reorder", str(e))

    def _refresh_part_in_tree(self, part_id: int) -> dict:
        self._refresh_diagnostic_for_part(int(part_id))
        info = self.bom_service.get_part_details(int(part_id)) or {}
        if not info:
            return {}
        for item in self._find_tree_items(int(part_id)):
            self._apply_tree_item_data(item, info)
        try:
            if getattr(self, "_in_search_mode", False) and self._part_matches_current_search(info):
                if not self._find_tree_items(int(part_id), self._search_tree):
                    self._add_node_to_tree(self._search_tree, dict(info, children=[]))
        except Exception:
            pass
        try:
            if not self._is_default_bom_advanced_filter():
                self.apply_bom_tree_filter(self._bom_advanced_filters)
        except Exception:
            pass
        for tree in (getattr(self, "tree", None), getattr(self, "_search_tree", None)):
            try:
                tree.viewport().update()
            except Exception:
                pass
        return info

    def refresh_parts_after_merge(self, part_ids) -> None:
        """Refresh only BOM rows affected by a successful merge."""
        refreshed = set()
        requested = list(part_ids or [])
        try:
            requested.extend(self.bom_service.direct_parent_ids(requested))
        except Exception:
            pass
        for part_id in requested:
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

    def refresh_issue_indicators(self, affected_part_ids=None) -> None:
        """Repaint only BOM nodes whose propagated issue summary changed."""
        try:
            previous = dict(self._issue_summary_cache or {})
            current = self.issue_service.part_summary()
        except Exception:
            return

        candidate_ids = set(previous) | set(current)
        candidate_ids.update(
            int(part_id) for part_id in (affected_part_ids or []) if part_id is not None
        )
        changed_ids = {
            int(part_id)
            for part_id in candidate_ids
            if previous.get(int(part_id), {}) != current.get(int(part_id), {})
        }
        self._issue_summary_cache = current

        for part_id in changed_ids:
            try:
                info = self.bom_service.get_part_details(int(part_id)) or {}
                if not info:
                    continue
                for item in self._find_tree_items(int(part_id)):
                    self._apply_tree_item_data(item, info)
            except Exception:
                continue

        try:
            score = max(
                0,
                self.issue_service.health_score()
                - len(getattr(self, "missing_ids", set()) or set()),
            )
            color = "#2e7d32" if score >= 85 else ("#a16207" if score >= 65 else "#b91c1c")
            self.bom_health_label.setText(f"Health: {score}/100")
            self.bom_health_label.setStyleSheet(
                f"font-size:11px;font-weight:700;color:{color};background:transparent;border:none;"
            )
        except Exception:
            pass

        try:
            current_part_id = getattr(self, "current_part_id", None)
            if current_part_id is not None and int(current_part_id) in changed_ids:
                self.refresh_issues_tab()
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
        self._renumber_full_bom_tree_rows()
        self._sync_search_tree_row_numbers()

    def add_part(self):
        if not self.perm.can("manage_parts"):
            return QMessageBox.warning(self, "Permission", "You do not have permission to add parts.")
        dialog = PartDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            part_data = dialog.get_data()
            if not part_data["name"]:
                QMessageBox.warning(self, "Validation Error", "Name is required.")
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
            if not updated_data["name"]:
                QMessageBox.warning(self, "Validation Error", "Name is required.")
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
        """Refresh the selected part row in every visible BOM tree representation."""
        try:
            pid = part_id or getattr(self, "current_part_id", None)
            if not pid:
                current_tree = self._current_tree_for_filtering()
                item = current_tree.currentItem() if current_tree else None
                pid = item.data(0, Qt.UserRole) if item else None
            if not pid:
                return
            self._refresh_part_in_tree(int(pid))
        except Exception:
            pass

    def _refresh_loaded_part_branch(self, part_id: int) -> None:
        self._refresh_part_in_tree(int(part_id))
        if not getattr(self, "_lazy_tree_active", False):
            return
        for item in list(self._find_tree_items(int(part_id), self.tree)):
            if not item.data(0, BOM_TREE_CHILDREN_LOADED_ROLE):
                continue
            parent_path = str(item.data(0, BOM_TREE_PATH_ROLE) or "")
            expanded = bool(item.isExpanded())
            nodes = self.bom_service.get_bom_lazy_children(
                int(self.session.project_id), int(part_id), parent_path
            ) or []
            item.setData(0, BOM_TREE_REPLACE_CHILDREN_ROLE, True)
            self._apply_lazy_children_result(item, int(part_id), nodes, expand_after=False)
            item.setExpanded(expanded and bool(nodes))

    def _refresh_lock_family_rows(self, part_id: int, refresh_loaded_branches: bool = False):
        try:
            part_ids = self.bom_service.part_ids_sharing_base_file(int(part_id)) or [int(part_id)]
        except Exception:
            part_ids = [int(part_id)]
        for pid in part_ids:
            if refresh_loaded_branches:
                self._refresh_loaded_part_branch(int(pid))
            else:
                self._refresh_current_tree_item_lock_state(int(pid))
            self._invalidate_doc_indicator(int(pid))
        if refresh_loaded_branches:
            self._renumber_full_bom_tree_rows()
            self._sync_search_tree_row_numbers()
        return part_ids

    def checkin_part(self, part_id=None):
        if isinstance(part_id, bool):
            part_id = None
        part_id = part_id or getattr(self, "current_part_id", None)
        if not part_id:
            QMessageBox.warning(self, "Check In", "Select a checked-out BOM item.")
            return
        try:
            analysis = self.bom_service.analyze_checkout(int(part_id))
        except (ValueError, PermissionError) as exc:
            QMessageBox.warning(self, "Check In", str(exc))
            return
        except Exception as exc:
            QMessageBox.critical(self, "Check In", f"Could not analyze the checkout:\n{exc}")
            return

        dialog = CheckoutReviewDialog(analysis, self)
        if dialog.exec_() != QDialog.Accepted:
            return
        if dialog.action == CheckoutReviewDialog.ACTION_COMMIT:
            main_window = self.window()
            commit_page = getattr(main_window, "commit_page", None)
            if commit_page is None or not hasattr(commit_page, "prepare_checkin_from_bom"):
                QMessageBox.warning(self, "Check In", "The Commit page is not available.")
                return
            commit_page.prepare_checkin_from_bom(analysis)
            if hasattr(main_window, "switch_page"):
                main_window.switch_page(1)
            return
        if dialog.action != CheckoutReviewDialog.ACTION_CHECKIN:
            return

        try:
            result = self.bom_service.checkin_non_cad_changes(
                int(part_id), dialog.comment()
            )
            context = (result or {}).get("context") or {}
            QMessageBox.information(
                self,
                "Check In",
                f"Checkout completed as {context.get('version_label') or analysis.get('next_version')}.\n"
                "Unchanged native CAD and drawing content were inherited.",
            )
            for affected_id in (result or {}).get("affected_part_ids") or [int(part_id)]:
                self._refresh_loaded_part_branch(int(affected_id))
                self._invalidate_doc_indicator(int(affected_id))
            self._renumber_full_bom_tree_rows()
            self._sync_search_tree_row_numbers()
            self.display_details(int(part_id))
        except (ValueError, PermissionError) as exc:
            QMessageBox.warning(self, "Check In", str(exc))
        except Exception as exc:
            QMessageBox.critical(self, "Check In", f"Check-in failed:\n{exc}")

    def undo_checkout(self, part_id=None):
        if not part_id:
            if not self.current_part_id:
                QMessageBox.warning(self, "No Selection", "Please select a checked-out item.")
                return
            part_id = self.current_part_id
        answer = QMessageBox.question(
            self,
            "Undo Checkout",
            "Discard the working BOM attributes and structure and release this checkout?\n\n"
            "This does not create an iteration and is not a check-in.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            self.bom_service.undo_checkout(int(part_id))
            QMessageBox.information(self, "Undo Checkout", "Checkout undone. No iteration was created.")
            self._refresh_lock_family_rows(int(part_id), refresh_loaded_branches=True)
            try:
                self.display_details(int(part_id))
            except Exception:
                pass
        except ValueError as e:
            QMessageBox.warning(self, "Undo Checkout", str(e))
        except PermissionError as e:
            QMessageBox.warning(self, "Permission", str(e))
        except Exception as e:
            QMessageBox.critical(self, "Undo Checkout", f"Failed to undo checkout: {str(e)}")

    def checkout_part(self, part_id=None):
        if not part_id:
            if not self.current_part_id:
                QMessageBox.warning(self, "No Selection", "Please select a part to check out.")
                return
            part_id = self.current_part_id

        details = self.bom_service.get_part_details(int(part_id)) or {}
        state = str(
            details.get("revision_state") or details.get("lifecycle_state") or ""
        ).strip().lower()
        released_revision_code = None
        if state == "released":
            current_version = str(details.get("current_version") or details.get("revision") or "")
            try:
                suggested_revision = self.bom_service.suggest_next_revision(int(part_id))
            except Exception:
                suggested_revision = ""
            released_revision_code, ok = QInputDialog.getText(
                self,
                "Check Out Released Item",
                f"{current_version} is Released and will remain immutable.\n"
                "The next completed commit (check-in) will create revision:",
                QLineEdit.Normal,
                suggested_revision,
            )
            if not ok:
                return
            released_revision_code = str(released_revision_code or "").strip()
            if not released_revision_code:
                QMessageBox.warning(self, "Check Out", "Enter the revision to create on commit.")
                return

        as_user_id = None
        # Master/Admin can check out as a project-assigned user.
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

        confirmation = (
            f"Check out Released {details.get('current_version') or details.get('revision')} for work?\n\n"
            f"The Released iteration will not change. The next completed commit will create "
            f"{released_revision_code}.1."
            if released_revision_code else
            f"Are you sure you want to check out part {part_id}? This part will be blocked for work."
        )
        reply = QMessageBox.question(
            self,
            "Confirm Check Out",
            confirmation,
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            try:
                self.bom_service.checkout_part(
                    part_id,
                    as_user_id=as_user_id,
                    released_revision_code=released_revision_code,
                )
                QMessageBox.information(self, "Success", "Part checked out successfully.")
                self._refresh_lock_family_rows(int(part_id))
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
            dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
            if getattr(dt, "tzinfo", None) is not None:
                dt = dt.replace(tzinfo=None)
            return dt
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

    def _commit_effective_dt(self, commit):
        try:
            raw = getattr(commit, "merged_at", None) or getattr(commit, "committed_at", None)
            return self._parse_dt(str(raw)) if raw else None
        except Exception:
            return None

    def _latest_change_commit_for_part(self, part_id: int):
        """Latest real CAD/DRW change for this part across the project family."""
        try:
            proj = self.project_service.get_project_by_id(self.session.project_id) or {}
            root_id = proj.get("root_project_id")
            commits = (
                self.diag_service.repo.get_all_commits_for_root(int(root_id))
                if root_id else self.diag_service.repo.get_all_commits(self.session.project_id)
            )
        except Exception:
            commits = []

        latest = None
        latest_key = (datetime.min, -1)
        good_statuses = {"pending", "validated", "approved"}
        for c in commits or []:
            try:
                if int(getattr(c, "part_id", 0) or 0) != int(part_id):
                    continue
                if str(getattr(c, "status", "") or "").lower() not in good_statuses:
                    continue
                dt = self._commit_effective_dt(c) or datetime.min
                key = (dt, int(getattr(c, "id", 0) or 0))
                if key > latest_key:
                    latest = c
                    latest_key = key
            except Exception:
                continue
        return latest

    def _commit_ack_token(self, commit) -> str:
        if not commit:
            return ""
        dt = str(getattr(commit, "merged_at", None) or getattr(commit, "committed_at", None) or "")
        cid = str(getattr(commit, "commit_id", "") or "")
        row_id = str(getattr(commit, "id", "") or "")
        return f"commit:{row_id}:{cid}:{dt}"

    def _ack_covers_commit(self, ack: dict | None, commit) -> bool:
        if not ack or not commit:
            return False
        value = str(ack.get("acknowledged_against") or "").strip()
        if not value:
            return False
        if value == self._commit_ack_token(commit):
            return True

        # Backward compatibility: older acknowledgements stored a timestamp.
        ack_dt = self._parse_dt(value)
        commit_dt = self._commit_effective_dt(commit)
        if ack_dt and commit_dt and ack_dt >= commit_dt:
            return True
        return False

    def _doc_ack_target(self, part_id: int, doc_type: str) -> str:
        latest_commit = self._latest_change_commit_for_part(int(part_id))
        if latest_commit:
            return self._commit_ack_token(latest_commit)
        part_mod = self._get_part_modified_dt(int(part_id))
        if part_mod:
            return part_mod.isoformat(sep=" ")
        return datetime.now().isoformat(sep=" ")

    def _legacy_doc_path(self, part_id: int, doc_type: str) -> str:
        try:
            details = self.bom_service.get_part_details(int(part_id)) or {}
        except Exception:
            details = {}
        key = "pdf_path" if str(doc_type).upper() == "PDF" else "step_path"
        return str(details.get(key) or "").strip()

    def _doc_indicator_state(self, part_id: int, doc_type: str, attachments: list | None = None, latest_commit=None) -> dict:
        doc_type = str(doc_type).upper()
        label = doc_type
        latest_commit = latest_commit if latest_commit is not None else self._latest_change_commit_for_part(int(part_id))
        commit_dt = self._commit_effective_dt(latest_commit) if latest_commit else None

        if attachments is None:
            try:
                attachments = self.part_file_service.list_attachments(int(part_id))
            except Exception:
                attachments = []

        candidates = []
        for att in attachments or []:
            try:
                if str(getattr(att, "file_type", "") or "").upper() != doc_type:
                    continue
                ver = self.part_file_service.get_active_version(att.id)
                created_dt = self._parse_dt(getattr(ver, "created_at", None) or "") if ver else None
                sort_dt = created_dt or self._parse_dt(getattr(att, "created_at", None) or "") or datetime.min
                candidates.append((sort_dt, att, ver))
            except Exception:
                continue

        if candidates:
            candidates.sort(key=lambda row: (row[0], int(getattr(row[1], "id", 0) or 0)), reverse=True)
            _sort_dt, att, ver = candidates[0]
            if not ver:
                return {"state": "bad", "tooltip": f"{label}: attachment has no active version"}

            created_dt = self._parse_dt(getattr(ver, "created_at", None) or "")
            path = self.part_file_service.resolve_version_path(ver)
            if not path or not safe_exists(path):
                return {"state": "bad", "tooltip": f"{label}: file missing on disk"}

            if doc_type == "STEP" and latest_commit:
                latest_step_path = str(getattr(latest_commit, "step_file_path", "") or "").strip()
                latest_step_name = os.path.basename(latest_step_path)
                version_name = str(getattr(ver, "original_filename", "") or "").strip()
                if latest_step_name and version_name and latest_step_name == version_name:
                    return {"state": "ok", "tooltip": f"{label}: current from compared commit STEP"}

            if latest_commit and (not created_dt or (commit_dt and commit_dt > created_dt)):
                try:
                    ack = self.part_doc_ack_service.get_ack(int(part_id), doc_type)
                except Exception:
                    ack = None
                if self._ack_covers_commit(ack, latest_commit):
                    return {"state": "ack", "tooltip": f"{label}: safe by user acknowledgement"}
                return {"state": "bad", "tooltip": f"{label}: newer commit needs review"}

            return {"state": "ok", "tooltip": f"{label}: current"}

        legacy_path = self._legacy_doc_path(int(part_id), doc_type)
        if legacy_path:
            resolved = self._resolve_file_path(legacy_path)
            if not resolved or not safe_exists(resolved):
                return {"state": "bad", "tooltip": f"{label}: file missing on disk"}
            if latest_commit:
                try:
                    ack = self.part_doc_ack_service.get_ack(int(part_id), doc_type)
                except Exception:
                    ack = None
                if self._ack_covers_commit(ack, latest_commit):
                    return {"state": "ack", "tooltip": f"{label}: legacy file safe by user acknowledgement"}
                return {"state": "bad", "tooltip": f"{label}: legacy file needs review after newer commit"}
            return {"state": "ok", "tooltip": f"{label}: legacy file exists"}

        return {"state": "absent", "tooltip": f"{label}: no attachment"}

    def _indicator_summary_for_part(self, part_id: int | None, issues: set | None = None) -> dict:
        if part_id is None:
            return {
                "pdf": {"state": "absent", "tooltip": "PDF: no attachment"},
                "step": {"state": "absent", "tooltip": "STEP: no attachment"},
            }
        try:
            pid = int(part_id)
        except Exception:
            pid = None
        if pid is None:
            return {
                "pdf": {"state": "absent", "tooltip": "PDF: no attachment"},
                "step": {"state": "absent", "tooltip": "STEP: no attachment"},
            }

        cached = self._doc_indicator_cache.get(pid)
        if cached is None:
            try:
                attachments = self.part_file_service.list_attachments(pid)
            except Exception:
                attachments = []
            latest_commit = self._latest_change_commit_for_part(pid)
            cached = {
                "pdf": self._doc_indicator_state(pid, "PDF", attachments, latest_commit),
                "step": self._doc_indicator_state(pid, "STEP", attachments, latest_commit),
            }
            self._doc_indicator_cache[pid] = cached

        summary = {
            "pdf": dict(cached.get("pdf") or {}),
            "step": dict(cached.get("step") or {}),
        }

        issues = issues or set()
        pdf_state = str(summary["pdf"].get("state") or "").lower()
        step_state = str(summary["step"].get("state") or "").lower()
        if ("missing_pdf" in issues or "outdated_pdf" in issues) and pdf_state not in ("ok", "ack"):
            summary["pdf"] = {"state": "bad", "tooltip": "PDF: file missing or outdated on disk"}
        if ("missing_step" in issues or "outdated_step" in issues) and step_state not in ("ok", "ack"):
            summary["step"] = {"state": "bad", "tooltip": "STEP: file missing or outdated on disk"}
        return summary

    def _pick_indicator_icon(self, issues: set, part_id: int | None = None) -> QIcon:
        summary = self._indicator_summary_for_part(part_id, issues)
        return self._make_indicator_icon(
            summary["pdf"].get("state", "absent"),
            summary["step"].get("state", "absent"),
        )

    def load_tree(self):
        # Show spinner immediately, then fetch tree data in a background thread.
        self._cancel_lazy_level_requests()
        self._tree_load_seq += 1
        seq = self._tree_load_seq

        pid = getattr(self.session, "project_id", None)
        if not pid:
            try:
                self.tree.clear()
            except Exception:
                pass
            self._set_tree_loading(False)
            self._mark_initial_tree_ready()
            return
        try:
            self._issue_summary_cache = self.issue_service.part_summary()
            score = max(0, self.issue_service.health_score() - len(getattr(self, "missing_ids", set()) or set()))
            color = "#2e7d32" if score >= 85 else ("#a16207" if score >= 65 else "#b91c1c")
            self.bom_health_label.setText(f"Health: {score}/100")
            self.bom_health_label.setStyleSheet(
                f"font-size:11px;font-weight:700;color:{color};background:transparent;border:none;"
            )
        except Exception:
            self._issue_summary_cache = {}

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
        self._mark_initial_tree_ready()

    def _mark_initial_tree_ready(self) -> None:
        """Record the first terminal tree state and emit its sticky readiness signal."""
        if self._initial_tree_ready_emitted:
            return
        self._initial_tree_ready_emitted = True
        self.initial_tree_ready.emit()

    @property
    def initial_tree_is_ready(self) -> bool:
        return bool(self._initial_tree_ready_emitted)

    def _load_tree_from_data(self, tree_data: dict, missing_map: dict) -> None:
        # Use incremental builder to keep UI responsive.
        self._begin_tree_build(int(getattr(self, "_tree_load_seq", 0)), tree_data or {}, missing_map or {})

    def _begin_tree_build(self, seq: int, tree_data: dict, missing_map: dict) -> None:
        self._tree_build_seq = int(seq)
        self._tree_build_missing_map = missing_map or {}
        self._last_tree_node_count = 0
        self._invalidate_doc_indicator()
        self._indicator_refresh_timer.stop()
        self._indicator_refresh_queue.clear()
        self._lazy_tree_active = "roots" in (tree_data or {})
        self._lazy_tree_materialized = False
        self._pending_relation_expanded_paths = set()
        self._pending_relation_expanded_part_ids = set()
        self._bom_row_numbers = dict((tree_data or {}).get("row_numbers") or {})
        self._bom_folder_row_numbers = dict((tree_data or {}).get("folder_rows") or {})
        self._bom_folder_path_rows = dict((tree_data or {}).get("folder_path_rows") or {})
        self._bom_folders_cache = list((tree_data or {}).get("folders") or [])

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
            roots = (tree_data or {}).get("roots") if self._lazy_tree_active else (tree_data or {}).values()
            for info in (roots or []):
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

        start = time.perf_counter()
        processed = 0
        try:
            while self._tree_build_queue and processed < 50 and (time.perf_counter() - start) < 0.01:
                parent_item, info = self._tree_build_queue.popleft()
                item = self._make_tree_item(info)

                if parent_item is None:
                    self.tree.addTopLevelItem(item)
                else:
                    parent_item.addChild(item)

                if self._lazy_tree_active:
                    self._ensure_lazy_placeholder(item)

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
                try:
                    if self._lazy_tree_active:
                        self._render_folder_context(None, self._bom_folders_cache)
                    else:
                        self._apply_organizational_folders()
                        self._renumber_full_bom_tree_rows()
                    self._sync_search_tree_row_numbers()
                except Exception:
                    pass

                # Start every BOM level collapsed; users expand only the branches they need.
                try:
                    self.tree.collapseAll()
                except Exception:
                    pass

                self._set_tree_loading(False)
                try:
                    if not self._is_default_bom_advanced_filter():
                        self.apply_bom_tree_filter(self._bom_advanced_filters)
                except Exception:
                    pass

                # Fire once when the first current tree load reaches a terminal state.
                try:
                    self._mark_initial_tree_ready()
                except Exception:
                    pass
        except RuntimeError:
            try:
                self._tree_build_timer.stop()
            except Exception:
                pass
            try:
                self.tree.setUpdatesEnabled(True)
                self._set_tree_loading(False)
            except Exception:
                pass
            try:
                self._mark_initial_tree_ready()
            except Exception:
                pass
            return

    def _load_tree_impl(self, project_id: int) -> None:
        # Legacy internal method name kept for compatibility; now sync-calls the builder.
        if not project_id:
            return
        tree_data = self.bom_service.get_bom_lazy_tree(project_id) or {}
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

    def _on_bom_item_expanded(self, item: QTreeWidgetItem) -> None:
        if not getattr(self, "_lazy_tree_active", False):
            return
        if (
            item is None
            or self._is_folder_tree_item(item)
            or self._is_lazy_placeholder(item)
            or item.data(0, BOM_TREE_CHILDREN_LOADED_ROLE)
        ):
            return

        # Keep the branch visually closed while its direct level is fetched. This
        # prevents the empty-row flash and lets the branch indicator host the spinner.
        previous_signal_state = self.tree.blockSignals(True)
        try:
            item.setExpanded(False)
        finally:
            self.tree.blockSignals(previous_signal_state)
        self._load_lazy_children_for_item(item, asynchronous=True, expand_after=True)

    def _load_lazy_children_for_item(
        self,
        item: QTreeWidgetItem,
        asynchronous: bool = True,
        expand_after: bool = False,
    ) -> None:
        if item is None or self._is_folder_tree_item(item) or self._is_lazy_placeholder(item):
            return
        if item.data(0, BOM_TREE_CHILDREN_LOADED_ROLE):
            return
        if item.data(0, BOM_TREE_LOADING_ROLE):
            return
        try:
            part_id = int(item.data(0, Qt.UserRole))
        except (TypeError, ValueError):
            return

        parent_path = str(item.data(0, BOM_TREE_PATH_ROLE) or "")
        if asynchronous:
            self._start_lazy_children_request(item, part_id, parent_path, expand_after)
            return

        try:
            nodes = self.bom_service.get_bom_lazy_children(
                int(self.session.project_id), part_id,
                parent_path,
            ) or []
            self._apply_lazy_children_result(item, part_id, nodes, expand_after=expand_after)
        except Exception as exc:
            item.setData(0, BOM_TREE_CHILDREN_LOADED_ROLE, False)
            self._ensure_lazy_placeholder(item)
            self.show_alert(f"Could not load this BOM level: {exc}", "error")

    def _start_lazy_children_request(
        self,
        item: QTreeWidgetItem,
        part_id: int,
        parent_path: str,
        expand_after: bool,
    ) -> None:
        self._lazy_level_request_seq += 1
        request_id = int(self._lazy_level_request_seq)
        worker = _LazyChildrenWorker(
            request_id,
            self.bom_service,
            int(self.session.project_id),
            int(part_id),
            parent_path,
        )
        thread = QThread(self)
        worker.moveToThread(thread)
        self._lazy_level_requests[request_id] = {
            "worker": worker,
            "thread": thread,
            "item": item,
            "part_id": int(part_id),
            "parent_path": str(parent_path),
            "expand_after": bool(expand_after),
        }
        self.tree.setItemLoading(item, True)

        thread.started.connect(worker.run)
        worker.finished.connect(self._on_lazy_children_loaded)
        worker.failed.connect(self._on_lazy_children_failed)
        worker.done.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _lazy_request_item_is_current(self, request: dict) -> bool:
        item = request.get("item")
        try:
            return (
                item is not None
                and item.treeWidget() is self.tree
                and int(item.data(0, Qt.UserRole)) == int(request.get("part_id"))
                and str(item.data(0, BOM_TREE_PATH_ROLE) or "") == str(request.get("parent_path") or "")
            )
        except Exception:
            return False

    def _on_lazy_children_loaded(self, request_id: int, nodes: object) -> None:
        request = self._lazy_level_requests.pop(int(request_id), None)
        if not request:
            return
        item = request.get("item")
        if not self._lazy_request_item_is_current(request):
            try:
                self.tree.setItemLoading(item, False)
            except Exception:
                pass
            return
        expand_after = bool(request.get("expand_after"))
        loaded_nodes = list(nodes or [])
        try:
            self._apply_lazy_children_result(
                item,
                int(request["part_id"]),
                loaded_nodes,
                expand_after=False,
            )
        except Exception as exc:
            item.setData(0, BOM_TREE_CHILDREN_LOADED_ROLE, False)
            self._ensure_lazy_placeholder(item)
            self.show_alert(f"Could not display this BOM level: {exc}", "error")
            loaded_nodes = []
        finally:
            try:
                self.tree.setItemLoading(item, False)
            except Exception:
                pass
        if expand_after and item.treeWidget() is self.tree and loaded_nodes:
            item.setExpanded(True)

    def _on_lazy_children_failed(self, request_id: int, error: str) -> None:
        request = self._lazy_level_requests.pop(int(request_id), None)
        if not request:
            return
        item = request.get("item")
        try:
            self.tree.setItemLoading(item, False)
        except Exception:
            pass
        if not self._lazy_request_item_is_current(request):
            return
        item.setData(0, BOM_TREE_CHILDREN_LOADED_ROLE, False)
        self._ensure_lazy_placeholder(item)
        self.show_alert(f"Could not load this BOM level: {error}", "error")

    def _apply_lazy_children_result(
        self,
        item: QTreeWidgetItem,
        part_id: int,
        nodes: list,
        expand_after: bool = False,
    ) -> None:
        self.tree.setUpdatesEnabled(False)
        try:
            item.setData(0, BOM_TREE_CHILDREN_LOADED_ROLE, True)
            if item.data(0, BOM_TREE_REPLACE_CHILDREN_ROLE):
                while item.childCount():
                    item.takeChild(0)
                item.setData(0, BOM_TREE_REPLACE_CHILDREN_ROLE, False)
            for index in range(item.childCount() - 1, -1, -1):
                if self._is_lazy_placeholder(item.child(index)):
                    item.takeChild(index)
            for node in nodes:
                try:
                    child_id = int(node.get("id"))
                except (TypeError, ValueError):
                    child_id = None
                if child_id is not None and self._child_exists_under_item(item, child_id):
                    continue
                child = self._make_tree_item(node)
                item.addChild(child)
                self._ensure_lazy_placeholder(child)
            self._render_folder_context(part_id, self._bom_folders_cache, containers=[item])
            item.setData(0, BOM_TREE_IS_ASSEMBLY_ROLE, bool(nodes))
            self._restore_pending_relation_expansions(item)
        finally:
            try:
                self.tree.setUpdatesEnabled(True)
                self.tree.viewport().update()
            except Exception:
                pass
        if expand_after and item.treeWidget() is self.tree and bool(nodes):
            item.setExpanded(True)

    def _materialize_all_lazy_branches(self) -> None:
        if not getattr(self, "_lazy_tree_active", False) or getattr(self, "_lazy_tree_materialized", False):
            return
        self._cancel_lazy_level_requests()

        def visit(item: QTreeWidgetItem):
            if self._is_folder_tree_item(item):
                for index in range(item.childCount()):
                    visit(item.child(index))
                return
            if self._is_lazy_placeholder(item):
                return
            self._load_lazy_children_for_item(item, asynchronous=False, expand_after=False)
            for index in range(item.childCount()):
                visit(item.child(index))

        self.tree.setUpdatesEnabled(False)
        try:
            for index in range(self.tree.topLevelItemCount()):
                visit(self.tree.topLevelItem(index))
            self._lazy_tree_materialized = True
        finally:
            self.tree.setUpdatesEnabled(True)
            self.tree.viewport().update()

    def on_tree_item_clicked(self, item, column):
        folder_id = item.data(0, BOM_TREE_FOLDER_ROLE) if item is not None else None
        if folder_id:
            self._show_folder_selection(item, int(folder_id))
            return
        item_id = item.data(0, Qt.UserRole)
        self.display_details(item_id)

    def _set_engineering_item_actions_enabled(self, enabled: bool) -> None:
        can_manage = self.perm.can("manage_parts")
        self.edit_part_btn.setEnabled(bool(enabled and can_manage))
        self.delete_part_btn.setEnabled(bool(enabled and can_manage))
        self.add_child_btn.setEnabled(bool(enabled and can_manage))
        self.checkout_part_btn.setEnabled(bool(enabled))
        self.undo_checkout_btn.setEnabled(bool(enabled))
        self.set_revision_btn.setEnabled(bool(enabled and self.perm.can("set_revision")))
        self.release_revision_btn.setEnabled(bool(enabled and self.perm.can("set_revision")))
        try:
            details = getattr(self, "_current_part_details", {}) or {}
            self.compare_iterations_btn.setEnabled(
                bool(enabled) and str(details.get("type") or "").lower() in {"asm", "assembly"}
            )
            self.create_configuration_btn.setEnabled(
                bool(enabled) and str(details.get("type") or "").lower() in {"asm", "assembly"}
            )
            self.update_child_versions_btn.setEnabled(
                bool(enabled) and str(details.get("type") or "").lower() in {"asm", "assembly"}
            )
        except Exception:
            pass
        self._sync_action_ribbon_menus()

    def _show_folder_selection(self, item: QTreeWidgetItem, folder_id: int) -> None:
        self.clear_details()
        self.current_folder_id = int(folder_id)
        self.details_summary_card.hide()
        self.associated_files_card.hide()
        self.details_alert_frame.hide()
        self._set_engineering_item_actions_enabled(False)
        try:
            self.tabs.setCurrentIndex(0)
        except Exception:
            pass

    def _indicator_refresh_step(self) -> None:
        start = time.perf_counter()
        processed = 0
        while self._indicator_refresh_queue and processed < 8 and (time.perf_counter() - start) < 0.008:
            item, part_id = self._indicator_refresh_queue.popleft()
            try:
                if item.treeWidget() is None:
                    continue
                issues = self._issues_for_part(part_id)
                summary = self._indicator_summary_for_part(part_id, issues)
                item.setData(BOM_COL_FILES, BOM_TREE_FILES_ROLE, _file_badges_payload(part_id, issues, summary))
                item.setData(BOM_COL_INTEGRITY, BOM_TREE_INTEGRITY_ROLE, _integrity_payload(part_id, self.missing_files, self.missing_ids))
                item.setToolTip(BOM_COL_FILES, "\n".join([
                    str(summary["pdf"].get("tooltip", "PDF: unknown")),
                    str(summary["step"].get("tooltip", "STEP: unknown")),
                ]))
                name_tips = []
                locked_text = str(item.data(0, BOM_TREE_INWORK_ROLE) or "")
                if locked_text:
                    name_tips.append(locked_text)
                issue_summary = item.data(0, BOM_TREE_ISSUE_ROLE) or {}
                active_count = int(issue_summary.get("active_count") or 0)
                if active_count:
                    name_tips.append(f"{active_count} active issue(s) linked to this part")
                policy = item.data(0, BOM_TREE_POLICY_ROLE) or {}
                classification = str(
                    policy.get("classification") or "PHYSICAL"
                ).upper()
                occurrence_behavior = str(
                    policy.get("ebom_behavior") or "INHERIT"
                ).upper()
                resolved_behavior = str(
                    policy.get("resolved_ebom_behavior") or "NORMAL"
                ).upper()
                name_tips.append(
                    f"EBOM policy: {classification}; occurrence {occurrence_behavior}; "
                    f"effective {resolved_behavior}"
                )
                if resolved_behavior == "EXCLUDE":
                    name_tips.append(
                        "Not for delivery: excluded with all descendants from Released EBOM."
                    )
                elif resolved_behavior == "FLATTEN":
                    name_tips.append(
                        "CAD grouping only: hidden from Released EBOM; children are promoted."
                    )
                name_tips.extend([
                    str(summary["pdf"].get("tooltip", "PDF: unknown")),
                    str(summary["step"].get("tooltip", "STEP: unknown")),
                ])
                item.setToolTip(BOM_COL_NAME, "\n".join(name_tips))
            except Exception:
                pass
            processed += 1
        if not self._indicator_refresh_queue:
            self._indicator_refresh_timer.stop()
        try:
            self.tree.viewport().update()
            self._search_tree.viewport().update()
        except Exception:
            pass

    def _clear_folder_selection(self) -> None:
        self.current_folder_id = None
        self.details_summary_card.show()
        self.associated_files_card.show()
        self._set_engineering_item_actions_enabled(False)
        self.clear_details()

    def _create_structure_relation_tree(self) -> QTreeWidget:
        tree = QTreeWidget()
        tree.setColumnCount(10)
        tree.setHeaderLabels([
            "Name", "AES Number", "Relation", "Qty", "Type", "Current",
            "Bound", "Latest", "Binding", "Lifecycle",
        ])
        tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tree.setSelectionBehavior(QAbstractItemView.SelectRows)
        tree.setSelectionMode(QAbstractItemView.SingleSelection)
        tree.setAlternatingRowColors(True)
        tree.setUniformRowHeights(True)
        tree.setIconSize(QSize(12, 12))
        tree.setIndentation(14)
        tree.setRootIsDecorated(True)
        header = tree.header()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for column in range(1, 10):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        return tree

    def _add_structure_relation_node(
        self,
        tree: QTreeWidget,
        node: dict,
        parent: QTreeWidgetItem | None = None,
    ) -> QTreeWidgetItem | None:
        if not node:
            return None
        quantity = node.get("quantity")
        item = QTreeWidgetItem([
            str(node.get("name") or ""),
            str(node.get("aes_number") or ""),
            str(node.get("relation") or ""),
            "" if quantity is None else str(quantity),
            str(node.get("type") or ""),
            str(node.get("current_version") or node.get("revision") or ""),
            str(node.get("bound_version") or ""),
            str(node.get("latest_version") or ""),
            str(node.get("binding_status") or ""),
            str(node.get("lifecycle_state") or node.get("status") or ""),
        ])
        item.setData(0, Qt.UserRole, node.get("id"))
        item.setData(0, Qt.UserRole + 1, node.get("usage_id"))
        item.setData(0, Qt.UserRole + 2, node.get("relation_parent_id"))
        item.setData(0, STRUCTURE_CURRENT_ITERATION_ROLE, node.get("current_iteration_id"))
        item.setData(0, STRUCTURE_BOUND_ITERATION_ROLE, node.get("bound_iteration_id"))
        item.setData(0, STRUCTURE_LATEST_ITERATION_ROLE, node.get("latest_iteration_id"))
        item.setIcon(0, _bom_type_icon(node.get("type")))
        if node.get("cycle"):
            item.setToolTip(0, "Circular structure reference")
            item.setForeground(2, QBrush(QColor("#b91c1c")))
        if node.get("promotion_path"):
            item.setToolTip(
                0,
                "Effective EBOM parent after flattening through: "
                + str(node["promotion_path"]),
            )
        if parent is None:
            tree.addTopLevelItem(item)
        else:
            parent.addChild(item)
        for child in node.get("children") or []:
            self._add_structure_relation_node(tree, child, item)
        return item

    def _populate_structure_views(self, context: dict) -> None:
        uses = (context or {}).get("uses")
        where_used = (context or {}).get("where_used")
        effective_where_used = (context or {}).get("effective_where_used")
        for tree, root in (
            (self.uses_tree, uses),
            (self.where_used_tree, where_used),
            (self.effective_where_used_tree, effective_where_used),
        ):
            tree.setUpdatesEnabled(False)
            try:
                tree.clear()
                root_item = self._add_structure_relation_node(tree, root)
                if root_item is not None:
                    root_item.setExpanded(True)
            finally:
                tree.setUpdatesEnabled(True)

        uses_count = int((context or {}).get("uses_count") or 0)
        where_used_count = int((context or {}).get("where_used_count") or 0)
        effective_count = int(
            (context or {}).get("effective_where_used_count") or 0
        )
        effective_error = str(
            (context or {}).get("effective_where_used_error") or ""
        )
        self.structure_summary_label.setText(
            f"Structure relations: {uses_count} uses  |  {where_used_count} direct CAD parent(s)"
            f"  |  {effective_count} effective EBOM parent occurrence(s)"
        )
        self.structure_views.setTabText(0, f"Uses ({uses_count})")
        self.structure_views.setTabText(1, f"CAD Where Used ({where_used_count})")
        self.structure_views.setTabText(
            2, f"Effective EBOM Where Used ({effective_count})"
        )
        self.effective_where_used_tree.setToolTip(
            effective_error or "First visible EBOM parent after flattening."
        )
        try:
            details = getattr(self, "_current_part_details", {}) or {}
            self._update_lifecycle_action_states(details)
        except Exception:
            self.compare_iterations_btn.setEnabled(False)
            self.update_child_versions_btn.setEnabled(False)

    def update_child_versions(self):
        if not getattr(self, "current_part_id", None):
            return
        try:
            rows = self.bom_service.get_child_version_status(int(self.current_part_id)) or []
        except Exception as exc:
            QMessageBox.warning(self, "Child Versions", str(exc))
            return
        outdated = [row for row in rows if not row.get("is_latest")]
        if not outdated:
            QMessageBox.information(self, "Child Versions", "All direct children already use their latest iterations.")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Update Child Versions")
        dialog.resize(720, 420)
        layout = QVBoxLayout(dialog)
        info = QLabel(
            "Select direct children to adopt in the checked-out assembly. "
            "The new bindings become permanent only when the assembly is committed."
        )
        info.setWordWrap(True)
        layout.addWidget(info)
        table = QTableWidget(len(rows), 5)
        table.setHorizontalHeaderLabels(["Child", "Bound", "Latest", "Status", "Source"])
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for column in range(1, 5):
            table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeToContents)
        for row_index, row in enumerate(rows):
            child_label = str(row.get("aes_number") or row.get("part_number") or "").strip()
            name = str(row.get("name") or "").strip()
            if child_label and name:
                child_label = f"{child_label} - {name}"
            else:
                child_label = child_label or name
            values = [
                child_label,
                str(row.get("bound_version") or ""),
                str(row.get("latest_version") or ""),
                "Current" if row.get("is_latest") else "Update available",
                str(row.get("binding_source") or ""),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.UserRole, int(row["child_bom_id"]))
                table.setItem(row_index, column, item)
                if not row.get("is_latest"):
                    item.setSelected(True)
        layout.addWidget(table)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Update Selected")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec_() != QDialog.Accepted:
            return
        selected_ids = []
        for index in table.selectionModel().selectedRows():
            item = table.item(index.row(), 0)
            if item is not None:
                selected_ids.append(int(item.data(Qt.UserRole)))
        if not selected_ids:
            QMessageBox.warning(self, "Child Versions", "Select at least one child.")
            return
        try:
            changed = self.bom_service.update_children_to_latest(
                int(self.current_part_id), selected_ids
            )
            self._refresh_part_in_tree(int(self.current_part_id))
            self.display_details(int(self.current_part_id))
            QMessageBox.information(
                self, "Child Versions", f"Updated {len(changed)} child binding(s) in the working assembly."
            )
        except Exception as exc:
            QMessageBox.warning(self, "Child Versions", str(exc))

    def _show_latest_bom_cad_files(self, part_id: int) -> None:
        try:
            details = self.bom_service.get_part_details(int(part_id)) or {}
            iteration_id = details.get("current_iteration_id")
            if not iteration_id:
                raise ValueError("This item has no current iteration.")
            self._show_iteration_cad_files(
                int(iteration_id), "Latest", str(details.get("current_version") or "")
            )
        except Exception as exc:
            QMessageBox.warning(self, "CAD Files", str(exc))

    def _show_iteration_cad_files(
        self, iteration_id: int, relation_label: str, displayed_version: str = ""
    ) -> None:
        files = self.bom_service.get_iteration_cad_files(int(iteration_id)) or {}
        if not files:
            QMessageBox.warning(self, "CAD Files", "The selected iteration was not found.")
            return
        version = str(files.get("version_label") or displayed_version or "").strip()
        dialog = QDialog(self)
        dialog.setWindowTitle(f"{relation_label} CAD Files - {version}".strip(" -"))
        dialog.resize(560, 210)
        layout = QVBoxLayout(dialog)
        heading = QLabel(f"{relation_label} iteration: {version or iteration_id}")
        heading.setStyleSheet("font-weight:700;")
        layout.addWidget(heading)
        table = QTableWidget(2, 2)
        table.setHorizontalHeaderLabels(["File Role", "Captured Creo File"])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        rows = (
            ("Native CAD", str(files.get("filename") or "Not captured")),
            ("Drawing", str(files.get("drawing") or "Not captured")),
        )
        for row_index, values in enumerate(rows):
            for column, value in enumerate(values):
                table.setItem(row_index, column, QTableWidgetItem(value))
        layout.addWidget(table)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec_()

    def _show_structure_context_menu(self, position) -> None:
        tree = self.sender()
        if not isinstance(tree, QTreeWidget):
            return
        item = tree.itemAt(position)
        if item is None:
            return
        menu = QMenu(tree)
        entries = (
            ("Current", STRUCTURE_CURRENT_ITERATION_ROLE, item.text(5)),
            ("Bound", STRUCTURE_BOUND_ITERATION_ROLE, item.text(6)),
            ("Latest", STRUCTURE_LATEST_ITERATION_ROLE, item.text(7)),
        )
        for label, role, version in entries:
            iteration_id = item.data(0, role)
            if iteration_id is None:
                continue
            action = menu.addAction(
                f"View {label} CAD Files ({version})" if str(version or "").strip()
                else f"View {label} CAD Files"
            )
            action.triggered.connect(
                lambda _=False, iid=int(iteration_id), relation=label, ver=str(version or ""):
                self._show_iteration_cad_files(iid, relation, ver)
            )
        part_id = item.data(0, Qt.UserRole)
        is_assembly = str(item.text(4) or "").strip().lower() in {"asm", "assembly"}
        if part_id is not None and is_assembly:
            if menu.actions():
                menu.addSeparator()
            compare_action = menu.addAction("Compare Assembly Iterations...")
            compare_action.triggered.connect(
                lambda _=False, pid=int(part_id): self.compare_assembly_iterations(pid)
            )
        if menu.actions():
            menu.exec_(tree.viewport().mapToGlobal(position))

    def _open_structure_tree_item(self, item: QTreeWidgetItem, _column: int) -> None:
        part_id = item.data(0, Qt.UserRole) if item is not None else None
        if part_id is None:
            return
        self._select_part_item(int(part_id))
        self.display_details(int(part_id))

    def _on_tree_item_double_clicked(self, item, column):
        item_id = item.data(0, Qt.UserRole)
        if not item_id:
            return
        if int(column) == BOM_COL_FILES:
            tree = item.treeWidget() or self.tree
            payload = item.data(BOM_COL_FILES, BOM_TREE_FILES_ROLE) or {}
            row_rect = tree.visualItemRect(item)
            column_rect = QRect(
                tree.header().sectionViewportPosition(BOM_COL_FILES),
                row_rect.top(),
                tree.header().sectionSize(BOM_COL_FILES),
                row_rect.height(),
            )
            pdf_rect, step_rect = _files_delegate_pill_rects(column_rect, payload)
            cursor_pos = tree.viewport().mapFromGlobal(QCursor.pos())
            if step_rect and step_rect.contains(cursor_pos):
                self.open_part_step(int(item_id))
            else:
                self.preview_part_pdf(int(item_id))
            return
        self.issue_requested.emit(int(item_id))

    def display_details(self, item_id):
        if item_id is None:
            return
        self.current_folder_id = None
        self.details_summary_card.show()
        self.associated_files_card.show()
        self._set_engineering_item_actions_enabled(True)
        self.current_part_id = item_id
        details = self.bom_service.get_part_details(item_id) or {}
        try:
            structure_context = self.bom_service.get_structure_context(int(item_id)) or {}
        except Exception:
            structure_context = {}
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

        self._update_details_summary(details)

        self._populate_structure_views(structure_context)

        # History panel (genius panel)
        self._history_rows = list(history)
        self.history_panel.set_data(history, analytics)

        self.notes_view.setText(details.get("notes", ""))
        self.refresh_issues_tab()

    def clear_details(self):
        self.current_part_id = None
        self._update_details_summary({})
        self.uses_tree.clear()
        self.where_used_tree.clear()
        self.effective_where_used_tree.clear()
        self.structure_summary_label.setText("Select a BOM item")
        self.structure_views.setTabText(0, "Uses")
        self.structure_views.setTabText(1, "CAD Where Used")
        self.structure_views.setTabText(2, "Effective EBOM Where Used")
        self.notes_view.clear()
        try:
            self.part_issues_table.setRowCount(0)
        except Exception:
            pass
        try:
            self.history_panel.clear()
            self._history_rows = []
        except Exception:
            pass
        self.refresh_files_tab()

    @staticmethod
    def _details_card_label(text: str, object_name: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName(object_name)
        return label

    @staticmethod
    def _details_value(details: dict, keys) -> str:
        for key in keys:
            value = details.get(key)
            if value is not None and str(value).strip():
                return str(value)
        return ""

    def _update_details_summary(self, details: dict) -> None:
        self._current_part_details = dict(details or {})
        has_details = bool(self._current_part_details)
        self.view_full_details_btn.setEnabled(has_details)
        self.edit_categories_btn.setEnabled(has_details and bool(getattr(self, "current_part_id", None)))

        name = self._details_value(self._current_part_details, ("name", "aes_number", "part_number"))
        self.details_name_label.setText(name or "Select a BOM item")

        identity_parts = []
        aes_number = self._details_value(self._current_part_details, ("aes_number",))
        part_number = self._details_value(self._current_part_details, ("part_number",))
        part_type = self._details_value(self._current_part_details, ("type",))
        current_version = self._details_value(self._current_part_details, ("current_version", "revision"))
        if aes_number:
            identity_parts.append(f"AES {aes_number}")
        if part_number:
            identity_parts.append(f"Part {part_number}")
        if part_type:
            identity_parts.append(part_type.upper())
        if current_version:
            identity_parts.append(f"Version {current_version}")
        classification = str(
            self._current_part_details.get("classification") or "PHYSICAL"
        ).upper()
        default_behavior = str(
            self._current_part_details.get("default_ebom_behavior") or "NORMAL"
        ).upper()
        if classification != "PHYSICAL":
            identity_parts.append(classification.replace("_", " "))
        if default_behavior == "EXCLUDE":
            identity_parts.append("Default: NOT FOR DELIVERY")
        elif default_behavior == "FLATTEN":
            identity_parts.append("Default: FLATTEN IN EBOM")
        if self._current_part_details.get("represented_part_id"):
            target_label = self._details_value(
                self._current_part_details,
                ("represented_part_name", "represented_part_aes"),
            )
            identity_parts.append(
                f"CAD REP OF {target_label or ('#' + str(self._current_part_details['represented_part_id']))}"
            )
        if str(
            self._current_part_details.get("cad_control_mode") or "CONTROLLED"
        ).upper() == "SUPPLIER_PACKAGE":
            dependency_count = int(
                self._current_part_details.get("cad_dependency_count") or 0
            )
            identity_parts.append(f"SUPPLIER PACKAGE · {dependency_count} CAD FILES")
        self.details_identity_label.setText(
            "  |  ".join(identity_parts)
            if identity_parts else "Select a row in the BOM structure to view its summary."
        )

        cad_file = self._details_value(
            self._current_part_details, ("filename", "base_file_name")
        )
        drawing_file = self._details_value(
            self._current_part_details, ("drawing", "base_drw_name", "drawing_number")
        )
        self.associated_cad_file_label.setText(cad_file or "Not linked")
        self.associated_drawing_file_label.setText(drawing_file or "Not linked")

        for _field_key, (field_label, value_label, detail_keys) in self._details_summary_fields.items():
            value = self._details_value(self._current_part_details, detail_keys)
            field_label.show()
            value_label.show()
            value_label.setText(value)
        self._update_lifecycle_action_states(self._current_part_details)

    def _update_lifecycle_action_states(self, details: dict) -> None:
        has_item = bool(details and getattr(self, "current_part_id", None))
        locked = bool(details.get("locked")) if has_item else False
        state = str(
            details.get("revision_state") or details.get("lifecycle_state") or ""
        ).strip().lower()
        released = state == "released"
        obsolete = state == "obsolete"
        pending_revision = str(details.get("pending_revision_code") or "").strip()
        editable_checkout = not released or bool(pending_revision)
        is_assembly = str(details.get("type") or "").strip().lower() in {"asm", "assembly"}
        can_manage = self.perm.can("manage_parts")
        can_revision = self.perm.can("set_revision")
        read_only_ebom = getattr(self, "_bom_mode", "cad") == "ebom"
        self.checkout_part_btn.setEnabled(
            has_item and not locked and not obsolete and not read_only_ebom
        )
        self.undo_checkout_btn.setEnabled(has_item and locked and not read_only_ebom)
        self.edit_part_btn.setEnabled(
            has_item and locked and can_manage and editable_checkout and not read_only_ebom
        )
        self.delete_part_btn.setEnabled(
            has_item and can_manage and editable_checkout and not read_only_ebom
        )
        self.add_child_btn.setEnabled(
            has_item and locked and can_manage and is_assembly
            and editable_checkout and not read_only_ebom
        )
        self.compare_iterations_btn.setEnabled(has_item and is_assembly)
        self.create_configuration_btn.setEnabled(
            has_item and is_assembly and not read_only_ebom
        )
        self.update_child_versions_btn.setEnabled(
            has_item and locked and is_assembly and editable_checkout and not read_only_ebom
        )
        self.set_revision_btn.setEnabled(
            has_item and not locked and released and not pending_revision
            and can_revision and not read_only_ebom
        )
        self.release_revision_btn.setEnabled(
            has_item and not locked and not released and can_revision and not read_only_ebom
        )
        self._sync_action_ribbon_menus()

    def _edit_current_part_categories(self) -> None:
        part_id = getattr(self, "current_part_id", None)
        if not part_id:
            return

        try:
            available_categories = self.bom_service.list_categories()
            assigned_categories = set(self.bom_service.categories_for_part(int(part_id)))
        except Exception as exc:
            QMessageBox.critical(self, "Categories", f"Could not load categories:\n{exc}")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Assign Categories")
        dialog.resize(440, 430)
        layout = QVBoxLayout(dialog)
        layout.setSpacing(8)
        layout.addWidget(QLabel("Assign this BOM item to one or more project categories."))

        category_list = QListWidget()
        category_list.setAlternatingRowColors(True)

        def add_category_item(name: str, checked: bool = False, category_id=None) -> None:
            clean_name = " ".join(str(name or "").split())
            if not clean_name:
                return
            for row in range(category_list.count()):
                if category_list.item(row).text().casefold() == clean_name.casefold():
                    if checked:
                        category_list.item(row).setCheckState(Qt.Checked)
                    if category_id is not None:
                        category_list.item(row).setData(Qt.UserRole, int(category_id))
                    return
            item = QListWidgetItem(clean_name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
            item.setData(Qt.UserRole, int(category_id) if category_id is not None else None)
            category_list.addItem(item)

        for category in available_categories:
            name = str(category.get("name") or "")
            add_category_item(name, name in assigned_categories, category.get("id"))
        layout.addWidget(category_list, 1)

        def confirm_assigned_category_deletion(category_name: str, parts: list) -> bool:
            confirmation = QDialog(dialog)
            confirmation.setWindowTitle("Delete Assigned Category")
            confirmation.resize(620, 430)
            confirmation_layout = QVBoxLayout(confirmation)
            warning = QLabel(
                f"Category '{category_name}' is assigned to {len(parts)} BOM item(s).\n"
                "Deleting it will remove the category from every item listed below."
            )
            warning.setWordWrap(True)
            warning.setStyleSheet(
                "color:#991b1b;background:#fee2e2;border:1px solid #f87171;"
                "border-radius:5px;padding:8px;font-weight:700;"
            )
            confirmation_layout.addWidget(warning)

            parts_table = QTableWidget()
            parts_table.setColumnCount(4)
            parts_table.setHorizontalHeaderLabels(["AES Number", "Name", "Part Number", "Type"])
            parts_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
            for column in (0, 2, 3):
                parts_table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeToContents)
            parts_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            parts_table.setSelectionBehavior(QAbstractItemView.SelectRows)
            parts_table.setRowCount(len(parts))
            for row, part in enumerate(parts):
                values = (
                    part.get("aes_number") or "",
                    part.get("name") or "",
                    part.get("part_number") or "",
                    part.get("type") or "",
                )
                for column, value in enumerate(values):
                    parts_table.setItem(row, column, QTableWidgetItem(str(value)))
            confirmation_layout.addWidget(parts_table, 1)

            confirmation_buttons = QDialogButtonBox()
            delete_button = confirmation_buttons.addButton(
                "Delete Category", QDialogButtonBox.DestructiveRole
            )
            cancel_button = confirmation_buttons.addButton(QDialogButtonBox.Cancel)
            delete_button.setObjectName("danger")
            delete_button.clicked.connect(confirmation.accept)
            cancel_button.clicked.connect(confirmation.reject)
            confirmation_layout.addWidget(confirmation_buttons)
            return confirmation.exec_() == QDialog.Accepted

        def delete_category_item(item: QListWidgetItem) -> None:
            if item is None:
                return
            category_name = item.text()
            category_id = item.data(Qt.UserRole)
            if category_id is None:
                category_list.takeItem(category_list.row(item))
                return
            try:
                usage = self.bom_service.category_usage(int(category_id))
                parts = list(usage.get("parts") or [])
            except Exception as exc:
                QMessageBox.critical(dialog, "Delete Category", f"Could not inspect category usage:\n{exc}")
                return

            if parts:
                confirmed = confirm_assigned_category_deletion(category_name, parts)
            else:
                confirmed = QMessageBox.question(
                    dialog,
                    "Delete Category",
                    f"Delete the empty project category '{category_name}'?",
                    QMessageBox.Yes | QMessageBox.Cancel,
                    QMessageBox.Cancel,
                ) == QMessageBox.Yes
            if not confirmed:
                return

            try:
                result = self.bom_service.delete_category(int(category_id))
                affected_parts = list(result.get("parts") or [])
                category_list.takeItem(category_list.row(item))
                deleted_key = category_name.casefold()
                for part in affected_parts:
                    affected_id = int(part["id"])
                    for tree_item in self._find_tree_items(affected_id):
                        remaining = [
                            name for name in (tree_item.data(0, BOM_TREE_CATEGORY_ROLE) or [])
                            if str(name).casefold() != deleted_key
                        ]
                        tree_item.setData(0, BOM_TREE_CATEGORY_ROLE, remaining)

                if any(int(part["id"]) == int(part_id) for part in affected_parts):
                    assigned = self.bom_service.categories_for_part(int(part_id))
                    self._current_part_details["categories"] = ", ".join(assigned)
                    self._update_details_summary(self._current_part_details)

                active_categories = list(self._bom_advanced_filters.get("categories") or [])
                filtered_categories = [
                    name for name in active_categories if str(name).casefold() != deleted_key
                ]
                if filtered_categories != active_categories:
                    self._bom_advanced_filters["categories"] = filtered_categories
                    self.apply_bom_tree_filter(self._bom_advanced_filters)
            except Exception as exc:
                QMessageBox.critical(dialog, "Delete Category", f"Could not delete category:\n{exc}")

        def show_category_context_menu(position) -> None:
            item = category_list.itemAt(position)
            if item is None:
                return
            menu = QMenu(category_list)
            delete_action = menu.addAction("Delete Category")
            delete_action.triggered.connect(lambda: delete_category_item(item))
            menu.exec_(category_list.viewport().mapToGlobal(position))

        category_list.setContextMenuPolicy(Qt.CustomContextMenu)
        category_list.customContextMenuRequested.connect(show_category_context_menu)

        new_category_row = QHBoxLayout()
        new_category_input = QLineEdit()
        new_category_input.setPlaceholderText("New project category")
        add_category_btn = QPushButton("Add Category")

        def add_new_category() -> None:
            name = new_category_input.text()
            if not name.strip():
                return
            add_category_item(name, checked=True)
            new_category_input.clear()

        add_category_btn.clicked.connect(add_new_category)
        new_category_input.returnPressed.connect(add_new_category)
        new_category_row.addWidget(new_category_input, 1)
        new_category_row.addWidget(add_category_btn)
        layout.addLayout(new_category_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        layout.addWidget(buttons)

        def save_categories() -> None:
            add_new_category()
            names = [
                category_list.item(row).text()
                for row in range(category_list.count())
                if category_list.item(row).checkState() == Qt.Checked
            ]
            try:
                assigned = self.bom_service.set_part_categories(int(part_id), names)
                self._current_part_details["categories"] = ", ".join(assigned)
                self._update_details_summary(self._current_part_details)
                for item in self._find_tree_items(int(part_id)):
                    item.setData(0, BOM_TREE_CATEGORY_ROLE, list(assigned))
                if not self._is_default_bom_advanced_filter():
                    self.apply_bom_tree_filter(self._bom_advanced_filters)
                dialog.accept()
            except Exception as exc:
                QMessageBox.critical(dialog, "Categories", f"Could not save categories:\n{exc}")

        buttons.accepted.connect(save_categories)
        buttons.rejected.connect(dialog.reject)
        dialog.exec_()

    def _open_full_bom_details(self) -> None:
        details = dict(getattr(self, "_current_part_details", {}) or {})
        if not details:
            return

        title = self._details_value(details, ("name", "aes_number", "part_number")) or "BOM Item"
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Full Details - {title}")
        dlg.resize(820, 560)

        layout = QVBoxLayout(dlg)
        heading = QLabel(title)
        heading.setStyleSheet("font-size:13px;font-weight:700;color:#172033;")
        layout.addWidget(heading)

        table = QTableWidget()
        table.setColumnCount(2)
        table.setHorizontalHeaderLabels(["Property", "Value"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setWordWrap(True)
        table.setRowCount(len(details))
        for row, (key, value) in enumerate(details.items()):
            table.setItem(row, 0, QTableWidgetItem(str(key)))
            table.setItem(row, 1, QTableWidgetItem(str(value if value is not None else "")))
        table.resizeRowsToContents()
        layout.addWidget(table, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dlg.reject)
        buttons.accepted.connect(dlg.accept)
        layout.addWidget(buttons)
        dlg.exec_()

    def refresh_issues_tab(self):
        if not getattr(self, "current_part_id", None):
            self.part_issues_table.setRowCount(0)
            return
        try:
            issues = self.issue_service.issues_for_part(int(self.current_part_id))
        except Exception:
            issues = []
        self.part_issues_table.setRowCount(len(issues))
        for row, issue in enumerate(issues):
            values = [issue["issue_number"], issue["title"], issue["status"], issue["priority"]]
            for col, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                cell.setData(Qt.UserRole, int(issue["id"]))
                if col == 3:
                    cell.setForeground(QBrush(QColor("#b91c1c" if issue["priority"] == "Critical" else "#374151")))
                self.part_issues_table.setItem(row, col, cell)

    def _open_current_part_issues(self):
        if getattr(self, "current_part_id", None):
            self.issue_requested.emit(int(self.current_part_id))

    def _create_issue_for_current_part(self):
        if getattr(self, "current_part_id", None):
            self.create_issue_requested.emit(int(self.current_part_id))

    # ═══════════════════════════════════════════════════════════════════════
    #  History Panel Callbacks
    # ═══════════════════════════════════════════════════════════════════════

    def _open_history_details_dialog(self, ev_data: dict):
        """Open a rich details dialog for a history event.
        Called by HistoryPanel.open_details_requested signal with the full event dict."""
        if not ev_data:
            return

        ev_type = str(ev_data.get("event", "") or "")
        commit_id = str(ev_data.get("commit_id", "") or "")
        if ev_type.upper() == "COMMIT" and commit_id:
            try:
                self._open_commit_history_details_dialog(ev_data, commit_id)
                return
            except Exception as exc:
                QMessageBox.warning(self, "Commit Details", f"Could not load full commit details:\n{exc}")

        ts = str(ev_data.get("timestamp", "") or "")
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
        object_version = str(ev_data.get("object_version", "") or "")
        if object_version:
            info_parts.append(f"Revision / iteration: {object_version}")
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
        shown_keys = {"timestamp", "event", "object_version", "user", "project", "version", "details", "commit_id", "step_diff_status", "step_diff_summary", "step_error", "step_file_path", "step_prev_file_path"}
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

    def _open_commit_history_details_dialog(self, ev_data: dict, commit_id: str):
        project_id = getattr(self.session, "project_id", None)
        details = CommitService().get_commit_group_details(
            str(commit_id),
            int(project_id) if project_id is not None else None,
        )
        files = details.get("files") or []
        if not files:
            raise ValueError(f"No commit rows found for {commit_id}.")

        first = files[0]
        status = details.get("status") or first.get("status") or ""
        style = _style_for("COMMIT")
        if str(status).lower() == "approved":
            style = {"icon": "✅", "label": "Approved", "color": "#16a34a", "bg": "#dcfce7"}
        elif str(status).lower() == "validated":
            style = {"icon": "🔵", "label": "Validated", "color": "#2563eb", "bg": "#dbeafe"}
        elif str(status).lower() == "pending":
            style = {"icon": "🟡", "label": "Pending", "color": "#ca8a04", "bg": "#fef9c3"}

        dlg = QDialog(self)
        dlg.setWindowTitle(f"{style['icon']}  Commit Details — {details.get('title') or commit_id}")
        dlg.setMinimumSize(900, 680)
        screen = QApplication.primaryScreen().availableGeometry()
        dlg.resize(min(1040, int(screen.width() * 0.88)), min(820, int(screen.height() * 0.88)))
        root = QVBoxLayout(dlg)
        root.setContentsMargins(10, 10, 10, 10)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(4, 4, 12, 4)
        layout.setSpacing(12)

        header = QFrame()
        header.setStyleSheet(f"""
            QFrame {{
                background: {style['bg']};
                border: 1px solid {style['color']}40;
                border-left: 5px solid {style['color']};
                border-radius: 8px;
            }}
        """)
        h_lay = QVBoxLayout(header)
        h_lay.setSpacing(5)
        badge = QLabel(f"  {style['icon']}  {style['label'].upper()}  ")
        badge.setStyleSheet(f"""
            background: {style['color']}; color: #ffffff;
            border-radius: 10px; padding: 3px 12px;
            font-size: 12px; font-weight: bold;
        """)
        badge.setFixedWidth(badge.sizeHint().width() + 20)
        h_lay.addWidget(badge)
        h_lay.addWidget(QLabel(f"<b style='font-size:13px'>{details.get('title') or first.get('filename') or commit_id}</b>"))
        meta = []
        if details.get("author"):
            meta.append(f"👤 {details.get('author')}")
        if details.get("checker"):
            meta.append(f"🔍 {details.get('checker')}")
        if details.get("committed_at"):
            meta.append(f"🗓 {str(details.get('committed_at'))[:19]} ({_relative_time(str(details.get('committed_at')))})")
        meta.append(f"🏷 {commit_id[:16]}")
        meta_lbl = QLabel("    ".join(meta))
        meta_lbl.setWordWrap(True)
        meta_lbl.setStyleSheet("color:#4b5563;font-size:11px;border:none;background:transparent;")
        h_lay.addWidget(meta_lbl)
        layout.addWidget(header)

        message = details.get("message") or ev_data.get("details") or ""
        if message:
            msg = QTextEdit()
            msg.setReadOnly(True)
            msg.setMinimumHeight(140)
            msg.setMaximumHeight(280)
            msg.setPlainText(str(message))
            layout.addWidget(msg)

        display_details = dict(details)
        display_details["object_version"] = ev_data.get("object_version", "")
        self._add_commit_key_value_table(layout, display_details, first)
        self._add_commit_files_tree(layout, files)
        self._add_commit_issues_table(layout, details.get("issues") or [])
        self._add_commit_doc_table(layout, details.get("engineering_files") or [], "Vaulted Engineering Outputs")
        self._add_commit_doc_table(layout, details.get("validation_docs") or [], "Validation Documents")

        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        buttons = QHBoxLayout()
        copy_btn = QPushButton("📋  Copy to Clipboard")
        copy_btn.setObjectName("neutral")
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(json.dumps(details, indent=2, default=str)))
        buttons.addWidget(copy_btn)
        buttons.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setObjectName("neutral")
        close_btn.clicked.connect(dlg.accept)
        buttons.addWidget(close_btn)
        root.addLayout(buttons)
        dlg.exec_()

    def _add_commit_key_value_table(self, layout, details: dict, first: dict):
        table = QTableWidget()
        table.setColumnCount(2)
        table.setHorizontalHeaderLabels(["Field", "Value"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.setMinimumHeight(190)
        table.setMaximumHeight(330)
        rows = [
            ("Status", details.get("status")),
            ("Commit ID", details.get("commit_id")),
            ("Project", details.get("project_name")),
            ("Project Version", details.get("project_version_label")),
            ("Revision / Iteration", details.get("object_version")),
            ("Author", details.get("author")),
            ("Designer", details.get("designer")),
            ("Checker", details.get("checker")),
            ("Committed At", details.get("committed_at")),
            ("Merged By", details.get("merged_by")),
            ("Merged At", details.get("merged_at")),
            ("Merge ID", details.get("merge_id")),
            ("Merge Message", details.get("merge_message")),
            ("Approved Version", details.get("approved_version")),
            ("PR Path", details.get("pr_path")),
            ("Signature", details.get("signature")),
            ("Selected BOM Part", first.get("part_name") or first.get("part_id")),
        ]
        rows = [(k, v) for k, v in rows if v not in (None, "")]
        table.setRowCount(len(rows))
        for r, (key, value) in enumerate(rows):
            key_item = QTableWidgetItem(str(key))
            key_item.setFont(QFont("Segoe UI", 10, QFont.Bold))
            table.setItem(r, 0, key_item)
            val_item = QTableWidgetItem(str(value))
            val_item.setToolTip(str(value))
            table.setItem(r, 1, val_item)
        layout.addWidget(table)

    def _add_commit_files_tree(self, layout, files: list):
        layout.addWidget(QLabel(f"<b>Files in this commit ({len(files)})</b>"))
        tree = QTreeWidget()
        tree.setColumnCount(2)
        tree.setHeaderLabels(["File / Field", "Value"])
        tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        tree.header().setSectionResizeMode(1, QHeaderView.Stretch)
        tree.setAlternatingRowColors(True)
        tree.setMinimumHeight(260)
        for item in files:
            filename = str(item.get("filename") or "")
            top = QTreeWidgetItem([filename, f"Status: {item.get('status') or ''} | Approved version: {item.get('approved_version') or ''}"])
            font = top.font(0)
            font.setBold(True)
            top.setFont(0, font)
            tree.addTopLevelItem(top)
            for label, value in (
                ("Row ID", item.get("id")),
                ("Commit ID", item.get("commit_id")),
                ("Status", item.get("status")),
                ("Type", item.get("type")),
                ("Part", item.get("part_name") or item.get("part_id")),
                ("Part ID", item.get("part_id")),
                ("AES", item.get("aes_number")),
                ("Revision", item.get("part_revision")),
                ("Source Path", item.get("file_path")),
                ("PR Path", item.get("pr_path")),
                ("Approved Version", item.get("approved_version")),
                ("Merged At", item.get("merged_at")),
                ("STEP Status", item.get("step_diff_status")),
                ("STEP File", item.get("step_file_path")),
                ("STEP Diff", item.get("step_diff_path")),
            ):
                if value not in (None, ""):
                    child = QTreeWidgetItem([str(label), str(value)])
                    child.setToolTip(1, str(value))
                    top.addChild(child)
            top.setExpanded(True)
        layout.addWidget(tree)

    def _add_commit_issues_table(self, layout, issues: list):
        if not issues:
            return
        layout.addWidget(QLabel(f"<b>Linked Issues ({len(issues)})</b>"))
        table = QTableWidget()
        table.setColumnCount(7)
        table.setHorizontalHeaderLabels(["Issue", "Title", "Status", "Priority", "Relation", "Validation", "Resolution"])
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setMinimumHeight(170)
        table.setMaximumHeight(300)
        table.setRowCount(len(issues))
        for r, issue in enumerate(issues):
            values = [
                issue.get("issue_number"), issue.get("title"), issue.get("status"),
                issue.get("priority"), issue.get("relation_type"),
                issue.get("validation_status"), issue.get("resolution_comment"),
            ]
            for c, value in enumerate(values):
                table.setItem(r, c, QTableWidgetItem(str(value or "")))
        layout.addWidget(table)

    def _add_commit_doc_table(self, layout, docs: list, title: str):
        if not docs:
            return
        layout.addWidget(QLabel(f"<b>{title} ({len(docs)})</b>"))
        table = QTableWidget()
        table.setColumnCount(8)
        table.setHorizontalHeaderLabels(["Role", "Type", "Filename", "Part", "Version", "Revision", "Exists", "Path"])
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(7, QHeaderView.Stretch)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setMinimumHeight(180)
        table.setMaximumHeight(320)
        table.setRowCount(len(docs))
        for r, doc in enumerate(docs):
            path = doc.get("source_path") or doc.get("stored_path") or ""
            exists = bool(path and safe_exists(path))
            values = [
                doc.get("doc_role") or doc.get("file_role"),
                doc.get("file_type"),
                doc.get("original_filename") or doc.get("filename") or doc.get("display_name"),
                doc.get("part_name") or doc.get("part_id"),
                doc.get("version_no") or doc.get("resolved_version_id"),
                doc.get("revision"),
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
        open_btn.clicked.connect(lambda _c=False, t=table: self._open_selected_commit_doc(t))
        row.addWidget(open_btn)
        layout.addLayout(row)

    def _open_selected_commit_doc(self, table):
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
                w.writerow(["timestamp", "project", "version", "object_version", "event", "user", "details",
                            "commit_id", "step_diff_status", "step_diff_summary"])
                for ev in rows:
                    w.writerow(
                        [
                            ev.get("timestamp", ""),
                            ev.get("project", ""),
                            ev.get("version", ""),
                            ev.get("object_version", ""),
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
    def _doc_export_text(self, item: QTreeWidgetItem, doc_key: str) -> str:
        payload = item.data(BOM_COL_FILES, BOM_TREE_FILES_ROLE) or {}
        value = payload.get(doc_key)
        if isinstance(value, (tuple, list)) and value:
            state = str(value[0] or "na").lower()
            labels = {
                "ok": "OK",
                "outdated": "Outdated",
                "missing": "Missing",
                "na": "Not attached",
                "bad": "Missing",
                "ack": "OK",
            }
            return labels.get(state, state.title() if state else "Unknown")
        return "Unknown"

    def _integrity_export_text(self, item: QTreeWidgetItem) -> str:
        payload = item.data(BOM_COL_INTEGRITY, BOM_TREE_INTEGRITY_ROLE) or {}
        state = str(payload.get("state") or "ok").lower()
        return "Has issues" if state == "warn" else "Healthy"

    def _issue_export_text(self, item: QTreeWidgetItem) -> str:
        summary = item.data(0, BOM_TREE_ISSUE_ROLE) or {}
        active = int(summary.get("active_count") or 0)
        total = int(summary.get("total_count") or 0)
        if active:
            return f"{active} active / {total} linked"
        if total:
            return f"0 active / {total} linked"
        return "No linked issues"

    def _active_filter_summary(self) -> list[tuple[str, str]]:
        filters = dict(getattr(self, "_bom_advanced_filters", {}) or self._default_bom_advanced_filters())
        defaults = self._default_bom_advanced_filters()
        labels = {
            "text": "Contains",
            "text_match_mode": "Text match mode",
            "work_state": "Work state",
            "work_owner": "Owner",
            "status": "Status",
            "type": "Type",
            "revision": "Revision",
            "categories": "Categories",
            "structure": "Structure",
            "pdf": "PDF",
            "step": "STEP",
            "integrity": "Integrity",
            "issues": "Issues",
            "remove_duplicates": "Duplicate items",
            "show_parent_matches": "Show parent branches",
            "expand_matches": "Expand matches",
        }
        rows = []
        for key, label in labels.items():
            value = filters.get(key, defaults.get(key, ""))
            if key == "text_match_mode" and not str(filters.get("text") or "").strip():
                continue
            if value != defaults.get(key, ""):
                if isinstance(value, (list, tuple, set)):
                    rows.append((label, ", ".join(str(item) for item in value)))
                elif key == "text_match_mode":
                    rows.append((label, "Match whole word" if value == "whole_word" else "Normal filter"))
                elif key == "remove_duplicates":
                    rows.append((label, "First occurrence only"))
                else:
                    rows.append((label, str(value)))
        search_query = ""
        try:
            search_query = self.search_input.text().strip()
        except Exception:
            pass
        if search_query:
            rows.append(("Search mode", "Current search results"))
        return rows or [("Filter", "None - all visible BOM rows")]

    def _collect_visible_bom_export_rows(self) -> list[dict]:
        tree = self._current_tree_for_filtering()
        rows: list[dict] = []

        def recurse(item: QTreeWidgetItem, level: int, ancestor_visible: bool = True):
            visible = ancestor_visible and not item.isHidden()
            if not visible:
                return
            part_id = item.data(0, Qt.UserRole)
            issue_text = self._issue_export_text(item)
            integrity_payload = item.data(BOM_COL_INTEGRITY, BOM_TREE_INTEGRITY_ROLE) or {}
            details = []
            for tip in (item.toolTip(BOM_COL_NAME), item.toolTip(BOM_COL_FILES), integrity_payload.get("tooltip")):
                tip = str(tip or "").strip()
                if tip and tip not in details:
                    details.append(tip)
            rows.append({
                "level": level,
                "name": item.text(BOM_COL_NAME),
                "aes_number": item.text(BOM_COL_AES),
                "type": item.text(BOM_COL_TYPE),
                "revision": item.text(BOM_COL_REV),
                "categories": ", ".join(item.data(0, BOM_TREE_CATEGORY_ROLE) or []),
                "status": item.text(BOM_COL_STATUS),
                "work_state": item.data(0, BOM_TREE_INWORK_ROLE) or "Checked In",
                "pdf": self._doc_export_text(item, "pdf"),
                "step": self._doc_export_text(item, "step"),
                "integrity": self._integrity_export_text(item),
                "issues": issue_text,
                "part_id": "" if part_id is None else str(part_id),
                "details": "\n".join(details),
            })
            for child_index in range(item.childCount()):
                recurse(item.child(child_index), level + 1, visible)

        for top_index in range(tree.topLevelItemCount()):
            recurse(tree.topLevelItem(top_index), 0)
        return rows

    def _export_visible_bom_csv(self, file_path: str, rows: list[dict]) -> None:
        fieldnames = [
            "level", "name", "aes_number", "type", "revision", "categories", "status",
            "work_state", "pdf", "step", "integrity", "issues", "part_id", "details",
        ]
        with open(file_path, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    def _export_visible_bom_xlsx(self, file_path: str, rows: list[dict]) -> None:
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Alignment, Font, PatternFill
            from openpyxl.utils import get_column_letter
        except ImportError as exc:
            raise RuntimeError(
                "Excel export requires openpyxl. Install dependencies from requirements.txt, then try again."
            ) from exc

        headers = [
            ("level", "Level"),
            ("name", "Name"),
            ("aes_number", "AES Number"),
            ("type", "Type"),
            ("revision", "Revision"),
            ("categories", "Categories"),
            ("status", "Status"),
            ("work_state", "Work State"),
            ("pdf", "PDF"),
            ("step", "STEP"),
            ("integrity", "Integrity"),
            ("issues", "Issues"),
            ("part_id", "Part ID"),
            ("details", "Details"),
        ]
        wb = Workbook()
        ws = wb.active
        ws.title = "Filtered BOM"

        title = "BOM Export"
        if not self._is_default_bom_advanced_filter() or getattr(self, "_in_search_mode", False):
            title = "Filtered BOM Export"
        ws["A1"] = title
        ws["A1"].font = Font(bold=True, size=14, color="111827")
        ws["A2"] = f"Exported: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        ws["A3"] = f"Rows: {len(rows)}"
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(headers))
        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(headers))
        ws.merge_cells(start_row=3, start_column=1, end_row=3, end_column=len(headers))

        header_row = 5
        for col, (_, label) in enumerate(headers, start=1):
            cell = ws.cell(row=header_row, column=col, value=label)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="1F2937")
            cell.alignment = Alignment(horizontal="center", vertical="center")

        for row_index, row in enumerate(rows, start=header_row + 1):
            for col, (key, _) in enumerate(headers, start=1):
                value = row.get(key, "")
                cell = ws.cell(row=row_index, column=col, value=value)
                cell.alignment = Alignment(vertical="top", wrap_text=(key == "details"))
                if key == "name":
                    cell.alignment = Alignment(indent=min(int(row.get("level") or 0), 10), vertical="top")
                    if int(row.get("level") or 0) == 0:
                        cell.font = Font(bold=True)
            try:
                ws.row_dimensions[row_index].outlineLevel = min(int(row.get("level") or 0), 7)
            except Exception:
                pass

        ws.freeze_panes = "A6"
        ws.auto_filter.ref = f"A{header_row}:{get_column_letter(len(headers))}{max(header_row, header_row + len(rows))}"
        widths = {
            "A": 8, "B": 32, "C": 16, "D": 12, "E": 12, "F": 28, "G": 14,
            "H": 18, "I": 14, "J": 14, "K": 16, "L": 20, "M": 10, "N": 54,
        }
        for column, width in widths.items():
            ws.column_dimensions[column].width = width

        summary = wb.create_sheet("Export Filter")
        summary["A1"] = "Filter"
        summary["B1"] = "Value"
        summary["A1"].font = Font(bold=True, color="FFFFFF")
        summary["B1"].font = Font(bold=True, color="FFFFFF")
        summary["A1"].fill = PatternFill("solid", fgColor="1F2937")
        summary["B1"].fill = PatternFill("solid", fgColor="1F2937")
        for idx, (label, value) in enumerate(self._active_filter_summary(), start=2):
            summary.cell(row=idx, column=1, value=label)
            summary.cell(row=idx, column=2, value=value)
        summary.column_dimensions["A"].width = 24
        summary.column_dimensions["B"].width = 48
        wb.save(file_path)

    def export_bom(self):
        if getattr(self, "_bom_mode", "cad") == "ebom":
            selected = self._ebom_tree.currentItem()
            if selected is None and self._ebom_tree.topLevelItemCount():
                selected = self._ebom_tree.topLevelItem(0)
            if selected is None:
                QMessageBox.warning(
                    self, "Export Released EBOM", "There is no resolved EBOM to export."
                )
                return
            while selected.parent() is not None:
                selected = selected.parent()
            root_bom_id = selected.data(0, Qt.UserRole)
            file_path, _selected_filter = QFileDialog.getSaveFileName(
                self,
                "Export Released EBOM",
                "released_ebom.csv",
                "CSV Files (*.csv)",
            )
            if not file_path:
                return
            if not file_path.lower().endswith(".csv"):
                file_path += ".csv"
            try:
                result = self.bom_service.export_released_ebom(
                    int(root_bom_id), file_path
                )
                QMessageBox.information(
                    self,
                    "Export Released EBOM",
                    f"Exported {result['row_count']} effective EBOM row(s) to {file_path}.",
                )
            except Exception as exc:
                QMessageBox.critical(self, "Export Released EBOM", str(exc))
            return
        default_name = "bom_filtered.xlsx" if not self._is_default_bom_advanced_filter() else "bom.xlsx"
        file_path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export BOM",
            default_name,
            "Excel Workbook (*.xlsx);;CSV Files (*.csv);;All Files (*)",
        )
        if not file_path:
            return
        try:
            rows = self._collect_visible_bom_export_rows()
            if not rows:
                QMessageBox.warning(self, "Export BOM", "There are no visible BOM rows to export.")
                return

            lower = file_path.lower()
            wants_csv = "csv" in selected_filter.lower() or lower.endswith(".csv")
            if wants_csv:
                if not lower.endswith(".csv"):
                    file_path += ".csv"
                self._export_visible_bom_csv(file_path, rows)
            else:
                if not lower.endswith(".xlsx"):
                    file_path += ".xlsx"
                self._export_visible_bom_xlsx(file_path, rows)
            QMessageBox.information(self, "Success", f"BOM exported to {file_path}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to export BOM: {str(e)}")

    def _make_indicator_icon(self, pdf_ok, step_ok, step_present: bool = True) -> QIcon:
        """Create a clearer 2-part badge icon: left=PDF, right=STEP.

        Each side is a small rounded badge labeled with 'PDF' / 'STEP'.
        Colors: ok=green, ack=blue, bad=red, absent=gray. Keeps cache for speed.
        """
        def _state(value):
            if isinstance(value, bool):
                return "ok" if value else "bad"
            value = str(value or "").lower()
            return value if value in {"ok", "ack", "bad", "absent"} else "absent"

        pdf_state = _state(pdf_ok)
        step_state = _state(step_ok)
        cache_key = (pdf_state, step_state, bool(step_present))
        try:
            icon = getattr(self, "_indicator_icon_cache", {}).get(cache_key)
            if icon:
                return icon
        except Exception:
            self._indicator_icon_cache = {}

        # Badge dimensions (wide enough for short label)
        h = 12
        badge_w = 24
        gap = 2
        total_w = badge_w * 2 + gap if step_present else badge_w
        pm = QPixmap(total_w, h)
        pm.fill(Qt.transparent)

        painter = QPainter(pm)
        painter.setRenderHint(QPainter.Antialiasing, True)

        colors = {
            "ok": QColor(34, 197, 94),    # green
            "ack": QColor(14, 165, 233),  # blue
            "bad": QColor(239, 68, 68),   # red
            "absent": QColor(156, 163, 175),
        }

        def draw_badge(x: int, label: str, state: str):
            bg = colors.get(state, colors["absent"])
            painter.setPen(Qt.NoPen)
            painter.setBrush(bg)
            rect = QRect(x, 0, badge_w, h)
            painter.drawRoundedRect(rect, 3, 3)

            # Text color: white for colored states, dark for absent
            if state == "absent":
                text_color = QColor(55, 65, 81)
            else:
                text_color = QColor(255, 255, 255)

            f = painter.font()
            f.setPointSize(6)
            f.setBold(True)
            painter.setFont(f)
            painter.setPen(text_color)
            painter.drawText(rect, Qt.AlignCenter, label)

            # ACK state: small inner ring to indicate acknowledged
            if state == "ack":
                painter.setPen(QColor(255, 255, 255, 200))
                painter.setBrush(Qt.NoBrush)
                painter.drawRoundedRect(rect.adjusted(3, 3, -3, -3), 4, 4)

        # Draw PDF badge (left)
        draw_badge(0, "PDF", pdf_state)

        # Draw STEP badge (right) if present
        if step_present:
            draw_badge(badge_w + gap, "STEP", step_state)

        painter.end()
        icon = QIcon(pm)
        try:
            self._indicator_icon_cache[cache_key] = icon
        except Exception:
            self._indicator_icon_cache = {cache_key: icon}
        return icon

    def _refresh_current_tree_item_indicator(self):
        """Refresh the selected row's PDF/STEP indicator in all visible trees."""
        try:
            part_id = getattr(self, "current_part_id", None)
            if not part_id:
                current_tree = self._current_tree_for_filtering()
                item = current_tree.currentItem() if current_tree else None
                part_id = item.data(0, Qt.UserRole) if item else None
            if not part_id:
                return

            self._refresh_part_in_tree(int(part_id))
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

        file_type = self._selected_file_type()

        menu = QMenu(self)
        if file_type in ("PDF", "STEP"):
            act = QAction(f"Acknowledge {file_type} as safe", self)

            def _do():
                ack_target = self._doc_ack_target(int(self.current_part_id), file_type)
                self.part_doc_ack_service.mark_up_to_date(
                    int(self.current_part_id), file_type, ack_target
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
        edit_revision_act = QAction("Edit Revision", self)
        edit_note_act = QAction("Edit Note", self)

        def _edit_revision():
            current = str(getattr(ver, "revision", "") or "").strip()
            revision, ok = QInputDialog.getText(
                self,
                "Edit Version Revision",
                "Revision (blank allowed, e.g. A010):",
                text=current,
            )
            if not ok:
                return
            try:
                self.part_file_service.update_version_revision(int(version_id), revision)
                self.on_attachment_selected()
            except Exception as exc:
                QMessageBox.critical(self, "Edit Revision", f"Failed to update revision:\n{exc}")

        def _edit_note():
            current = str(getattr(ver, "note", "") or "")
            note, ok = QInputDialog.getText(
                self,
                "Edit Version Note",
                "Note:",
                text=current,
            )
            if not ok:
                return
            try:
                self.part_file_service.update_version_note(int(version_id), note)
                self.on_attachment_selected()
            except Exception as exc:
                QMessageBox.critical(self, "Edit Note", f"Failed to update note:\n{exc}")

        edit_revision_act.triggered.connect(_edit_revision)
        edit_note_act.triggered.connect(_edit_note)
        can_edit = self.perm.can("release_files")
        edit_revision_act.setEnabled(can_edit)
        edit_note_act.setEnabled(can_edit)
        menu.addAction(edit_revision_act)
        menu.addAction(edit_note_act)
        menu.addSeparator()

        if file_type in ("PDF", "STEP"):
            act = QAction(f"Acknowledge {file_type} as safe", self)

            def _do():
                ack_target = self._doc_ack_target(int(self.current_part_id), file_type)
                self.part_doc_ack_service.mark_up_to_date(
                    int(self.current_part_id), file_type, ack_target
                )
                # fast refresh (avoid full tree rebuild)
                self._refresh_current_tree_item_indicator()

            act.triggered.connect(_do)
            menu.addAction(act)

        menu.exec_(self.versions_table.viewport().mapToGlobal(position))

