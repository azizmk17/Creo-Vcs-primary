"""Vertical item history and isolated Nexus branch workflows."""
import json
from pathlib import Path
import re
import tempfile

from PyQt5.QtCore import Qt, QThread, QTimer, QPointF, QRectF, QUrl, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF, QDesktopServices
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTreeWidget, QTreeWidgetItem, QHeaderView, QStyledItemDelegate, QStyle,
    QComboBox, QDialog, QFormLayout, QLineEdit, QPlainTextEdit, QDialogButtonBox,
    QMessageBox, QCheckBox, QFileDialog, QTableWidget, QTableWidgetItem,
    QAbstractItemView, QDoubleSpinBox, QSplitter, QInputDialog,
    QGraphicsObject, QGraphicsScene, QGraphicsView,
)

from config import DB_NAME
from core.session_manager import SessionManager
from core.services.nexus_history_service import NexusHistoryService, ITEM_FIELDS
from core.services.nexus_history_service import local_timestamp


EVENT_ROLE = Qt.UserRole + 130
GRAPH_ROLE = Qt.UserRole + 131


def _local_timestamp_text(value, timezone_name=None):
    parsed = local_timestamp(value, naive_is_utc=str(timezone_name or "").upper() == "UTC")
    return parsed.strftime("%Y-%m-%d %H:%M:%S") if parsed else str(value or "")


class _Task(QThread):
    result = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, operation, parent):
        super().__init__(parent)
        self.operation = operation

    def run(self):
        try:
            self.result.emit(self.operation())
        except Exception as exc:
            self.error.emit(str(exc))


class _GraphDelegate(QStyledItemDelegate):
    """Releases use a short snapshot tick; only BRANCH_CREATED forks a lane."""
    def paint(self, painter, option, index):
        if index.column() != 0:
            return super().paint(painter, option, index)
        super().paint(painter, option, index)
        data = index.data(GRAPH_ROLE) or {}
        painter.save()
        painter.setRenderHint(painter.Antialiasing)
        painter.setClipRect(option.rect)
        rect, cy = option.rect, option.rect.center().y()
        top, bottom, master = rect.top(), rect.bottom() + 1, rect.left() + 17
        fg = option.palette.highlightedText().color() if option.state & QStyle.State_Selected else option.palette.text().color()
        surface = option.palette.highlight().color() if option.state & QStyle.State_Selected else option.palette.base().color()
        painter.setPen(QPen(fg, 1.5))
        painter.drawLine(QPointF(master, top), QPointF(master, bottom))
        branch_color = QColor("#417cc2") if surface.lightness() > 110 else QColor("#9fc7fa")
        paused_color = option.palette.text().color()
        for lane in data.get("lanes", []):
            x = rect.left() + lane["x"]
            pen = QPen(paused_color if lane["paused"] else branch_color, 1.8)
            if lane["paused"]:
                pen.setStyle(Qt.DashLine)
            painter.setPen(pen)
            path = QPainterPath()
            if lane["create"]:
                path.moveTo(master, cy)
                path.cubicTo(x, cy, x, cy - 5, x, top)
            elif lane["merge"]:
                path.moveTo(x, bottom)
                path.cubicTo(x, cy, master, cy + 5, master, cy)
            else:
                path.moveTo(x, top)
                path.lineTo(x, bottom)
            painter.drawPath(path)
        x = rect.left() + data.get("node_x", 17)
        painter.setPen(QPen(branch_color if data.get("branch") else fg, 2))
        painter.setBrush(surface)
        if data.get("release"):
            painter.setPen(QPen(fg, 1.5))
            painter.drawLine(QPointF(x, cy), QPointF(x + 18, cy))
            x += 18
            painter.setBrush(QColor("#b46b30") if surface.lightness() > 110 else QColor("#efb079"))
            painter.drawPolygon(QPolygonF([
                QPointF(x, cy-6), QPointF(x+6, cy),
                QPointF(x, cy+6), QPointF(x-6, cy),
            ]))
        elif data.get("commit_state") == "validated":
            painter.setPen(QPen(QColor("#16733a"), 1.8))
            painter.setBrush(QColor("#198849"))
            painter.drawEllipse(QPointF(x, cy), 6, 6)
            painter.setPen(QPen(QColor("white"), 1.5))
            painter.drawLine(QPointF(x-3, cy), QPointF(x-1, cy+2))
            painter.drawLine(QPointF(x-1, cy+2), QPointF(x+3, cy-3))
        elif data.get("commit_state") == "unvalidated":
            painter.setPen(QPen(QColor("#b65a00"), 2))
            painter.setBrush(surface)
            painter.drawEllipse(QPointF(x, cy), 6, 6)
            painter.drawLine(QPointF(x, cy-3), QPointF(x, cy+1))
            painter.drawPoint(QPointF(x, cy+3))
        else:
            painter.drawEllipse(QPointF(x, cy), 5, 5)
        painter.restore()


class VerticalHistoryView(QTreeWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(5)
        self.setHeaderLabels(["Graph", "Date", "Action / note", "By", "CAD / document revision"])
        self.setRootIsDecorated(False)
        self.setAlternatingRowColors(True)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setItemDelegateForColumn(0, _GraphDelegate(self))
        self.header().setSectionResizeMode(0, QHeaderView.Fixed)
        self.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.header().setSectionResizeMode(2, QHeaderView.Stretch)
        self.header().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.header().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.setMinimumHeight(200)

    def set_events(self, events, branches):
        self.clear()
        bounds = {}
        visible_branches = {e.get("branch_id") for e in events if e.get("branch_id")}
        for lane, branch in enumerate(b for b in branches if b["id"] in visible_branches):
            indices = [i for i, e in enumerate(events) if e.get("branch_id") == branch["id"]]
            created = next((i for i in indices if events[i]["event_type"] == "BRANCH_CREATED"), None)
            merged = next((i for i in indices if events[i]["event_type"] == "BRANCH_MERGED"), None)
            bounds[branch["id"]] = {
                "x": 62 + lane * 24, "bottom": created if created is not None else len(events)-1,
                "top": merged if merged is not None else 0, "created": created, "merged": merged,
                "paused": branch["state"] == "Paused", "last": min(indices),
            }
        self.setColumnWidth(0, 86 + len(bounds) * 24)
        for row_index, event in enumerate(events):
            payload = event.get("payload") or {}
            title = event.get("title") or event["event_type"]
            text = title + ("\n" + event["note"] if event.get("note") else "")
            revisions = []
            if payload.get("cad_revision"):
                revisions.append("CAD " + str(payload["cad_revision"]))
            if payload.get("document_revision"):
                revisions.append("DOC " + str(payload["document_revision"]))
            row = QTreeWidgetItem([
                "", _local_timestamp_text(event.get("created_at"), event.get("timestamp_timezone")),
                text, str(event.get("actor") or event.get("actor_id") or ""), " / ".join(revisions),
            ])
            row.setData(0, EVENT_ROLE, event)
            branch = bounds.get(event.get("branch_id"))
            graph = {"release": event["event_type"] == "RELEASE_FROZEN",
                     "branch": bool(branch), "node_x": 17, "lanes": []}
            if str(event["event_type"]).upper() in {"COMMIT", "BRANCH_COMMIT"}:
                status = str(payload.get("validation_status") or "").strip().casefold()
                if status in {"validated", "approved", "released"}:
                    graph["commit_state"] = "validated"
                    row.setToolTip(0, "Validated commit")
                elif status in {"pending", "integrated", "under development", "rejected", "withdrawn"}:
                    graph["commit_state"] = "unvalidated"
                    row.setToolTip(0, "Not validated · " + status.capitalize())
                else:
                    graph["commit_state"] = "unknown"
                    row.setToolTip(0, "Commit validation status unknown")
            for lane in bounds.values():
                if lane["top"] <= row_index <= lane["bottom"]:
                    graph["lanes"].append({
                        "x": lane["x"], "create": row_index == lane["created"],
                        "merge": row_index == lane["merged"],
                        "paused": lane["paused"] and row_index <= lane["last"],
                    })
            if branch and event["event_type"] not in {"BRANCH_CREATED", "BRANCH_MERGED"}:
                graph["node_x"] = branch["x"]
            row.setData(0, GRAPH_ROLE, graph)
            row.setToolTip(2, text)
            self.addTopLevelItem(row)
        if self.topLevelItemCount():
            self.setCurrentItem(self.topLevelItem(0))


class _ProjectTimelineNode(QGraphicsObject):
    activated = pyqtSignal(object)

    def __init__(self, event, parent=None):
        super().__init__(parent)
        self.event = event
        self._selected = False
        self._rect = QRectF(0, 0, 164, 78)
        self.setAcceptHoverEvents(True)
        self.setAcceptedMouseButtons(Qt.LeftButton)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(self._tooltip())

    def boundingRect(self):
        return self._rect

    def _tooltip(self):
        return "\n".join(value for value in (
            str(self.event.get("title") or ""),
            _local_timestamp_text(self.event.get("created_at"), self.event.get("timestamp_timezone")),
            str(self.event.get("note") or ""),
        ) if value)

    def _colors(self):
        category = self.event.get("category")
        if category == "release":
            return QColor("#fff1dc"), QColor("#a95a13")
        if category == "master_checkin":
            return QColor("#edf1f8"), QColor("#53657d")
        event_type = str(self.event.get("event_type") or "")
        if event_type == "BRANCH_MERGED":
            return QColor("#e7f2ed"), QColor("#367253")
        if event_type == "BRANCH_PAUSED":
            return QColor("#fff6df"), QColor("#9b7417")
        return QColor("#eaf0ff"), QColor("#4359ad")

    def _heading(self):
        category = self.event.get("category")
        if category == "release":
            return f"RELEASE · R{self.event.get('package_revision', 1)}"
        if category == "master_checkin":
            return "MASTER CHECK-IN"
        labels = {
            "BRANCH_CREATED": "BRANCH CREATED",
            "BRANCH_CHECKIN": "BRANCH CHECK-IN",
            "BRANCH_PAUSED": "BRANCH PAUSED",
            "BRANCH_RESUMED": "BRANCH RESUMED",
            "BRANCH_MERGED": "MERGED TO MASTER",
        }
        return labels.get(str(self.event.get("event_type") or ""), "BRANCH ACTIVITY")

    def _label(self):
        if self.event.get("category") == "release":
            return str(self.event.get("package_name") or "Release package")
        if self.event.get("category") == "master_checkin":
            return str(self.event.get("member_count") or 0) + " item(s)"
        items = self.event.get("items") or []
        return (str(items[0].get("name")) if len(items) == 1
                else f"{len(items)} affected item(s)" if items
                else str(self.event.get("note") or "Branch activity"))

    def paint(self, painter, option, widget=None):
        fill, border = self._colors()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor("#173c62") if self._selected else border,
                            2.4 if self._selected else 1.4))
        painter.setBrush(fill)
        painter.drawRoundedRect(self._rect, 7, 7)
        painter.setPen(QPen(border, 1.4))
        if self.event.get("category") == "release":
            center = QPointF(11, 12)
            painter.setBrush(border)
            painter.drawPolygon(QPolygonF([center + QPointF(0, -5), center + QPointF(5, 0),
                                           center + QPointF(0, 5), center + QPointF(-5, 0)]))
        else:
            painter.setBrush(fill)
            painter.drawEllipse(QPointF(11, 12), 4.5, 4.5)
        painter.setPen(border)
        painter.drawText(QRectF(20, 4, 138, 18), Qt.AlignLeft | Qt.AlignVCenter,
                         self._heading())
        painter.setPen(QColor("#263548"))
        painter.drawText(QRectF(8, 23, 148, 31), Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap,
                         self._label()[:58])
        painter.setPen(QColor("#5d6876"))
        painter.drawText(QRectF(8, 56, 148, 17), Qt.AlignLeft | Qt.AlignVCenter,
                         _local_timestamp_text(self.event.get("created_at"),
                                               self.event.get("timestamp_timezone")))

    def set_selected(self, selected):
        selected = bool(selected)
        if selected != self._selected:
            self._selected = selected
            self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.activated.emit(self.event)
            event.accept()
            return
        super().mousePressEvent(event)


class _ProjectTimelineGraph(QWidget):
    event_selected = pyqtSignal(object)
    zoom_changed = pyqtSignal(int)
    CARD_WIDTH = 164
    COLUMN_GAP = 40
    LEFT = 220

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.scene = QGraphicsScene(self)
        self.view = QGraphicsView(self.scene, self)
        self.view.setRenderHint(QPainter.Antialiasing)
        self.view.setDragMode(QGraphicsView.ScrollHandDrag)
        self.view.setBackgroundBrush(QColor("#f7f9fc"))
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.view.setMinimumHeight(330)
        layout.addWidget(self.view)
        self._nodes = {}
        self._selected_id = None
        self._data = {"events": [], "branches": []}
        self._filter = "All project activity"
        self._zoom = 1.0

    def set_timeline(self, data, filter_name="All project activity"):
        self._data, self._filter = data, filter_name
        self._draw()

    def _draw(self):
        self.scene.clear()
        self._nodes.clear()
        self._selected_id = None
        all_events = self._data.get("events", [])
        if self._filter == "Release packages":
            events = [event for event in all_events if event.get("category") == "release"]
        elif self._filter == "Nexus branches":
            events = [event for event in all_events if event.get("category") == "branch"]
        elif self._filter == "Master check-ins":
            events = [event for event in all_events if event.get("category") == "master_checkin"]
        else:
            events = list(all_events)

        branches = list(self._data.get("branches", []))
        present = {str(event.get("branch_id") or "") for event in events if event.get("branch_id")}
        branches = [branch for branch in branches if str(branch["id"]) in present]
        if not events:
            text = self.scene.addText("No project events match this view.")
            text.setPos(30, 35)
            self.scene.setSceneRect(0, 0, 720, 140)
            return

        master_y = 135
        branch_top = 275
        row_step = 120
        branch_rows = {str(branch["id"]): branch_top + index * row_step
                       for index, branch in enumerate(branches)}
        max_y = branch_top + max(0, len(branches) - 1) * row_step + 100
        spacing = self.CARD_WIDTH + self.COLUMN_GAP
        width = self.LEFT + len(events) * spacing + 100
        self.scene.setSceneRect(0, 0, max(width, 900), max(max_y, 390))
        self.scene.addText("Earlier  ←                                      Time flows left to right                                      →  Later")
        self.scene.addLine(self.LEFT - 24, master_y, width - 35, master_y,
                           QPen(QColor("#355577"), 3))
        master_label = self.scene.addText("MASTER")
        master_label.setDefaultTextColor(QColor("#203c59"))
        master_label.setPos(20, master_y - 14)

        event_x = {}
        for index, event in enumerate(events):
            center_x = self.LEFT + index * spacing + self.CARD_WIDTH / 2
            event_x[str(event.get("id"))] = center_x
            lane_id = str(event.get("lane_id") or "master")
            y = branch_rows.get(lane_id, master_y)
            event_type = str(event.get("event_type") or "")
            if event_type == "BRANCH_CREATED":
                color = QColor("#4b63af")
                path = QPainterPath(QPointF(center_x, master_y))
                path.lineTo(center_x, y)
                self.scene.addPath(path, QPen(color, 2.2))
            elif event_type == "BRANCH_MERGED":
                color = QColor("#477558")
                path = QPainterPath(QPointF(center_x, y))
                path.lineTo(center_x, master_y)
                self.scene.addPath(path, QPen(color, 2.2))

        for branch in branches:
            branch_id = str(branch["id"])
            y = branch_rows[branch_id]
            branch_events = [event for event in events if str(event.get("branch_id") or "") == branch_id]
            positions = [event_x[str(event.get("id"))] for event in branch_events
                         if str(event.get("id")) in event_x]
            if not positions:
                continue
            start = min(positions)
            created = next((event_x[str(event["id"])] for event in branch_events
                            if event.get("event_type") == "BRANCH_CREATED" and str(event["id"]) in event_x), start)
            merged = next((event_x[str(event["id"])] for event in branch_events
                           if event.get("event_type") == "BRANCH_MERGED" and str(event["id"]) in event_x), None)
            end = merged if merged is not None else max(positions)
            color = {"Active": QColor("#4b63af"), "Paused": QColor("#af8422"),
                     "Merged": QColor("#7d8996"), "Abandoned": QColor("#8e6870")}.get(
                         branch.get("state"), QColor("#4b63af"))
            pen = QPen(color, 3)
            if branch.get("state") == "Paused":
                pen.setStyle(Qt.DashLine)
            self.scene.addLine(created, y, max(created, end), y, pen)
            label = self.scene.addText(str(branch.get("name") or "Nexus branch"))
            label.setDefaultTextColor(color)
            label.setPos(20, y - 24)
            state_label = self.scene.addText(f"{branch.get('state')} · {branch.get('item_count', 0)} items")
            state_label.setDefaultTextColor(QColor("#5c6670"))
            state_label.setPos(20, y + 1)

        # Month separators and labels help users orient across long histories.
        prior_month = None
        for index, event in enumerate(events):
            center_x = self.LEFT + index * spacing + self.CARD_WIDTH / 2
            parsed = local_timestamp(event.get("created_at"),
                naive_is_utc=event.get("timestamp_timezone") == "UTC")
            month = parsed.strftime("%b %Y") if parsed else "Unknown date"
            if month != prior_month:
                self.scene.addLine(center_x - self.CARD_WIDTH / 2 - 8, 42,
                                   center_x - self.CARD_WIDTH / 2 - 8, max_y - 10,
                                   QPen(QColor("#d4dce6"), 1, Qt.DashLine))
                marker = self.scene.addText(month)
                marker.setDefaultTextColor(QColor("#56687d"))
                marker.setPos(center_x - self.CARD_WIDTH / 2, 35)
                prior_month = month

        for event in events:
            lane_id = str(event.get("lane_id") or "master")
            y = branch_rows.get(lane_id, master_y)
            node = _ProjectTimelineNode(event)
            node.setPos(event_x[str(event.get("id"))] - self.CARD_WIDTH / 2, y - 39)
            node.activated.connect(self._select_node)
            self.scene.addItem(node)
            self._nodes[str(event.get("id"))] = node
        self.scene.update()

    def _select_node(self, event):
        for node in self._nodes.values():
            node.set_selected(node.event.get("id") == event.get("id"))
        self.event_selected.emit(event)

    def zoom_in(self):
        self._set_zoom(min(2.5, self._zoom * 1.25))

    def zoom_out(self):
        self._set_zoom(max(0.5, self._zoom / 1.25))

    def reset_zoom(self):
        self._set_zoom(1.0)

    def _set_zoom(self, value):
        value = float(value)
        if abs(value - self._zoom) < 0.001:
            return
        self.view.scale(value / self._zoom, value / self._zoom)
        self._zoom = value
        self.zoom_changed.emit(round(self._zoom * 100))


class _ProjectTimelineDialog(QDialog):
    def __init__(self, service, data, parent=None):
        super().__init__(parent)
        self.service, self.data = service, data
        self.setWindowTitle("Project timeline and release packages · " +
                            str(data.get("project", {}).get("name") or "Project"))
        self.resize(1440, 900)
        self.setMinimumSize(960, 650)
        self._detail_worker = None
        self._export_worker = None
        self._selected_event = None
        self._branches = {str(branch["id"]): branch for branch in data.get("branches", [])}
        layout = QVBoxLayout(self)
        heading = QLabel(f"{data.get('project', {}).get('name') or 'Project'}  ·  Project revision "
                         f"{data.get('project', {}).get('revision') or 'A'}")
        heading.setStyleSheet("font-size: 16px; font-weight: 700; color: #263d62;")
        layout.addWidget(heading)
        summary = QLabel(f"{data.get('release_count', 0)} package revision(s)  ·  "
                         f"{len(data.get('branches', []))} Nexus branch(es)  ·  "
                         f"{len(data.get('events', []))} timeline event(s).  "
                         "Time runs left to right; use the horizontal scrollbar to move through history. "
                         "Commits are omitted. Select a card to inspect its contents or branch activity.")
        summary.setWordWrap(True)
        layout.addWidget(summary)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Show"))
        self.filter_combo = QComboBox()
        for value in ("All project activity", "Release packages", "Nexus branches", "Master check-ins"):
            self.filter_combo.addItem(value)
        controls.addWidget(self.filter_combo)
        self.zoom_out_button = QPushButton("−")
        self.zoom_out_button.setToolTip("Zoom out of the timeline graph")
        self.zoom_in_button = QPushButton("+")
        self.zoom_in_button.setToolTip("Zoom in to the timeline graph")
        self.zoom_label = QLabel("100%")
        self.zoom_reset_button = QPushButton("Reset zoom")
        controls.addWidget(QLabel("Zoom"))
        controls.addWidget(self.zoom_out_button)
        controls.addWidget(self.zoom_label)
        controls.addWidget(self.zoom_in_button)
        controls.addWidget(self.zoom_reset_button)
        self.export_button = QPushButton("Export Selected Package Revision…")
        self.export_button.setEnabled(False)
        controls.addWidget(self.export_button)
        controls.addStretch(1)
        controls.addWidget(QLabel("◆ Release   ● Master check-in   Blue lane: active   "
                                  "Dashed amber: paused   Gray: merged"))
        layout.addLayout(controls)
        splitter = QSplitter(Qt.Vertical)
        self.graph = _ProjectTimelineGraph()
        splitter.addWidget(self.graph)
        details_panel = QWidget()
        details_layout = QVBoxLayout(details_panel)
        details_layout.setContentsMargins(0, 0, 0, 0)
        self.detail_summary = QLabel("Select a release, Master check-in, or branch event above.")
        self.detail_summary.setWordWrap(True)
        details_layout.addWidget(self.detail_summary)
        self.contents = QTreeWidget()
        self.contents.setColumnCount(4)
        self.contents.setHeaderLabels(["Package contents / affected items", "File role", "Revision", "Size"])
        self.contents.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.contents.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.contents.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.contents.header().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        details_layout.addWidget(self.contents, 1)
        splitter.addWidget(details_panel)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
        self.graph.event_selected.connect(self._select_event)
        self.filter_combo.currentTextChanged.connect(self._filter_changed)
        self.export_button.clicked.connect(self._export_selected)
        self.zoom_in_button.clicked.connect(self.graph.zoom_in)
        self.zoom_out_button.clicked.connect(self.graph.zoom_out)
        self.zoom_reset_button.clicked.connect(self.graph.reset_zoom)
        self.graph.zoom_changed.connect(lambda value: self.zoom_label.setText(f"{value}%"))
        self.graph.set_timeline(data)

    def _filter_changed(self, value):
        self._selected_event = None
        self.export_button.setEnabled(False)
        self.contents.clear()
        self.detail_summary.setText("Select a timeline card to inspect its contents or activity.")
        self.graph.set_timeline(self.data, value)

    def _select_event(self, event):
        self._selected_event = event
        is_release = event.get("category") == "release"
        self.export_button.setEnabled(is_release and self._export_worker is None)
        self.contents.clear()
        if is_release:
            self.detail_summary.setText("Loading frozen package contents…")
            release_id = str(event.get("release_id") or "")
            if self._detail_worker is None:
                self._load_release_details(release_id)
            return

        parsed = _local_timestamp_text(event.get("created_at"), event.get("timestamp_timezone"))
        branch = self._branches.get(str(event.get("branch_id") or ""))
        lines = [str(event.get("title") or ""), parsed]
        if event.get("actor"):
            lines.append("By " + str(event["actor"]))
        if branch:
            lines.append("Branch state: " + str(branch.get("state") or "Unknown"))
        if event.get("status"):
            lines.append("Master work state: " + str(event["status"]))
        if event.get("note"):
            lines.extend(["", str(event["note"])])
        if event.get("notes"):
            lines.extend(["", *[str(note) for note in event["notes"]]])
        self.detail_summary.setText("\n".join(lines))
        for item in event.get("items", []):
            row = QTreeWidgetItem([str(item.get("name") or "Item " + str(item.get("id"))), "BOM item", "", ""])
            self.contents.addTopLevelItem(row)
        if branch:
            payload = event.get("payload") or {}
            for key in ("source_commit_id", "commit_id", "validation_status", "native_state"):
                value = payload.get(key)
                if value not in (None, ""):
                    self.contents.addTopLevelItem(QTreeWidgetItem([
                        str(key.replace("_", " ").title()), "Branch activity", str(value), ""]))
        if not self.contents.topLevelItemCount():
            self.contents.addTopLevelItem(QTreeWidgetItem(["No additional item details are recorded.", "", "", ""]))

    def _load_release_details(self, release_id):
        worker = _Task(lambda: self.service.project_release_details(release_id), self)
        self._detail_worker = worker
        worker.result.connect(lambda release: self._show_release(release, release_id))
        worker.error.connect(lambda message: self.detail_summary.setText(
            "Could not open release contents: " + message
            if self._selected_event and str(self._selected_event.get("release_id") or "") == release_id
            else self.detail_summary.text()))
        def finished():
            self._detail_worker = None
            worker.deleteLater()
            selected = self._selected_event or {}
            selected_id = str(selected.get("release_id") or "")
            if selected.get("category") == "release" and selected_id and selected_id != release_id:
                self.detail_summary.setText("Loading frozen package contents…")
                self._load_release_details(selected_id)
        worker.finished.connect(finished)
        worker.start()

    def _show_release(self, release, release_id):
        if not self._selected_event or str(self._selected_event.get("release_id") or "") != release_id:
            return
        self.detail_summary.setText(
            f"{release.get('package_name')} · R{release.get('package_revision')}  |  "
            f"{len(release.get('members', []))} items  |  "
            f"{_local_timestamp_text(release.get('created_at'), 'UTC')}  |  By {release.get('actor') or 'Unknown'}\n"
            f"{release.get('note') or ''}"
        )
        for member in release.get("members", []):
            item_label = " · ".join(value for value in (member.get("name"), member.get("part_number"),
                                                         member.get("drawing_number")) if value)
            row = QTreeWidgetItem([item_label or ("Item " + str(member["id"])), "BOM item",
                f"CAD {member.get('cad_revision') or '—'} / DOC {member.get('document_revision') or '—'}", ""])
            self.contents.addTopLevelItem(row)
            for file_info in member.get("files", []):
                child = QTreeWidgetItem([file_info.get("filename", ""), file_info.get("role", "File"),
                    file_info.get("file_revision") or "—",
                    (f"{file_info.get('size_bytes', 0):,} bytes" if file_info.get("size_bytes") else "")])
                row.addChild(child)
        shared = release.get("shared_files", [])
        if shared:
            row = QTreeWidgetItem(["Shared CAD dependencies", "Shared files", "", ""])
            self.contents.addTopLevelItem(row)
            for file_info in shared:
                row.addChild(QTreeWidgetItem([file_info.get("filename", ""), file_info.get("role", "File"),
                    file_info.get("file_revision") or "—",
                    (f"{file_info.get('size_bytes', 0):,} bytes" if file_info.get("size_bytes") else "")]))
        self.contents.expandAll()
        self.status.setText("The contents shown are the exact files frozen for this package revision.")

    def _export_selected(self):
        event = self._selected_event or {}
        if event.get("category") != "release" or self._export_worker is not None:
            return
        filename = re.sub(r"[^A-Za-z0-9._-]", "_", str(event.get("package_name") or "Release") +
                          "_R" + str(event.get("package_revision") or 1) + ".zip")
        destination, _ = QFileDialog.getSaveFileName(self, "Export package revision", filename,
                                                     "Release package (*.zip)")
        if not destination:
            return
        if not destination.lower().endswith(".zip"):
            destination += ".zip"
        release_id = str(event["release_id"])
        worker = _Task(lambda: self.service.export_release(release_id, destination), self)
        self._export_worker = worker
        worker.result.connect(lambda path: self.status.setText("Exported exact package revision: " + path))
        worker.error.connect(lambda message: self.status.setText("Could not export package: " + message))
        def finished():
            self._export_worker = None
            worker.deleteLater()
            self.export_button.setEnabled(bool(self._selected_event and
                                               self._selected_event.get("category") == "release"))
        worker.finished.connect(finished)
        self.export_button.setEnabled(False)
        self.status.setText("Verifying and exporting the selected package revision…")
        worker.start()

    def _wait_workers(self):
        for worker in (self._detail_worker, self._export_worker):
            if worker is not None and worker.isRunning():
                worker.wait()

    def reject(self):
        self._wait_workers()
        super().reject()

    def closeEvent(self, event):
        self._wait_workers()
        super().closeEvent(event)


class _BranchCheckinDialog(QDialog):
    def __init__(self, branch, current_item_id, parent):
        super().__init__(parent)
        self.setWindowTitle("Check in Nexus branch · " + branch["name"])
        self.resize(650, 650)
        self.manifest = branch["manifest"]
        self.edits, self.quantity_edits = {}, {}
        layout = QVBoxLayout(self)
        info = QLabel("Save CAD changes in the branch workspace before checking in. "
                      "These parameters and quantities belong to this branch only.")
        info.setWordWrap(True)
        layout.addWidget(info)
        self.item_combo = QComboBox()
        for item_id, item in self.manifest["items"].items():
            self.item_combo.addItem(str(item["attributes"].get("name") or item_id), item_id)
        layout.addWidget(self.item_combo)
        form, self.fields = QFormLayout(), {}
        for field in ITEM_FIELDS:
            edit = QLineEdit()
            self.fields[field] = edit
            form.addRow(field.replace("_", " ").title(), edit)
        layout.addLayout(form)
        self.quantities = QTableWidget(0, 2)
        self.quantities.setHorizontalHeaderLabels(["Existing child usage", "Branch quantity"])
        self.quantities.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.quantities.setMaximumHeight(150)
        layout.addWidget(self.quantities)
        layout.addWidget(QLabel("Check-in note"))
        self.note = QPlainTextEdit()
        self.note.setMaximumHeight(90)
        layout.addWidget(self.note)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Check in branch")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.active_item = None
        self.item_combo.currentIndexChanged.connect(self._select_item)
        index = self.item_combo.findData(str(current_item_id))
        self.item_combo.setCurrentIndex(max(0, index))
        self._select_item()

    def _save_fields(self):
        if self.active_item is None:
            return
        original = self.manifest["items"][self.active_item]
        changed = {field: edit.text() for field, edit in self.fields.items()
                   if edit.text() != str(original["attributes"].get(field) or "")}
        self.edits[self.active_item] = changed
        quantities = {}
        for row in range(self.quantities.rowCount()):
            usage_id = self.quantities.item(row, 0).data(Qt.UserRole)
            value = self.quantities.cellWidget(row, 1).value()
            if value != float(original["structure"][usage_id].get("quantity") or 1):
                quantities[usage_id] = value
        self.quantity_edits[self.active_item] = quantities

    def _select_item(self):
        self._save_fields()
        self.active_item = self.item_combo.currentData()
        item = self.manifest["items"][self.active_item]
        for field, edit in self.fields.items():
            edit.setText(str(self.edits.get(self.active_item, {}).get(field, item["attributes"].get(field)) or ""))
        self.quantities.setRowCount(len(item["structure"]))
        for row, (usage_id, usage) in enumerate(item["structure"].items()):
            label = QTableWidgetItem("Child " + str(usage["child_id"]) + " · usage " + usage_id)
            label.setData(Qt.UserRole, usage_id)
            label.setFlags(label.flags() & ~Qt.ItemIsEditable)
            self.quantities.setItem(row, 0, label)
            spin = QDoubleSpinBox()
            spin.setRange(1, 1e9)
            spin.setDecimals(0)
            spin.setValue(self.quantity_edits.get(self.active_item, {}).get(usage_id, usage.get("quantity") or 1))
            if str(usage.get("source") or "").upper() == "CAD":
                spin.setEnabled(False)
                spin.setToolTip("CAD-driven quantities follow the CAD structure workflow.")
            self.quantities.setCellWidget(row, 1, spin)

    def accept(self):
        if not self.note.toPlainText().strip():
            QMessageBox.warning(self, "Branch check-in", "Enter a check-in note.")
            return
        self._save_fields()
        super().accept()


class _ReleaseSelectionDialog(QDialog):
    def __init__(self, options, current_item_id, parent):
        super().__init__(parent)
        self.setWindowTitle("Create or extend a release package")
        self.resize(780, 610)
        self.options = options
        self.current_item_id = int(current_item_id)
        self.available_items = {int(value["id"]): value for value in options.get("items", [])}
        layout, form = QVBoxLayout(self), QFormLayout()
        self.package_combo = QComboBox()
        self.package_combo.addItem("Create a new release package", None)
        for package in options.get("packages", []):
            self.package_combo.addItem(
                f"{package['name']} · R{package['revision']} · {len(package['member_ids'])} items",
                package,
            )
        form.addRow("Release package", self.package_combo)
        self.package_name = QLineEdit()
        self.package_name.setPlaceholderText("e.g. 3D Printing · Housing Set")
        form.addRow("Package name", self.package_name)
        layout.addLayout(form)
        label = QLabel("Choose one or more BOM items. Each keeps its own document revision. "
                       "Appending creates a new frozen package revision and retains all existing "
                       "members with their original files.")
        label.setWordWrap(True)
        layout.addWidget(label)
        search_row = QHBoxLayout()
        self.item_search = QLineEdit()
        self.item_search.setPlaceholderText("Search BOM item by name, part number, or drawing number…")
        self.item_results = QComboBox()
        self.item_results.setMinimumWidth(240)
        self.add_item_button = QPushButton("Add item")
        search_row.addWidget(self.item_search, 1)
        search_row.addWidget(self.item_results, 1)
        search_row.addWidget(self.add_item_button)
        layout.addLayout(search_row)
        self.items_hint = QLabel()
        layout.addWidget(self.items_hint)
        self.items = QTableWidget(0, 3)
        self.items.setHorizontalHeaderLabels(["Selected BOM item", "Document revision", ""])
        self.items.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.items.setColumnWidth(1, 150)
        self.items.setColumnWidth(2, 70)
        layout.addWidget(self.items, 1)
        layout.addWidget(QLabel("Release purpose / note (shared by the package revision)"))
        self.note = QPlainTextEdit()
        self.note.setMaximumHeight(80)
        layout.addWidget(self.note)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Choose release files…")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.package_combo.currentIndexChanged.connect(self._package_changed)
        self.item_search.textChanged.connect(self._search_items)
        self.add_item_button.clicked.connect(self._add_selected_item)
        self._package_changed()
        self._search_items("")

    def _package_changed(self):
        package = self.package_combo.currentData()
        self.package_name.setText(str(package["name"]) if package else "")
        self.package_name.setReadOnly(bool(package))
        members = set(map(int, package["member_ids"])) if package else set()
        for row in reversed(range(self.items.rowCount())):
            item_id = int(self.items.item(row, 0).data(Qt.UserRole))
            if item_id in members:
                self.items.removeRow(row)
        if not package and self.current_item_id in self.available_items and not self._has_item(self.current_item_id):
            self._append_item(self.available_items[self.current_item_id])
        self._search_items(self.item_search.text())

    def _has_item(self, item_id):
        return any(int(self.items.item(row, 0).data(Qt.UserRole)) == int(item_id)
                   for row in range(self.items.rowCount()))

    def _search_items(self, text):
        package = self.package_combo.currentData()
        excluded = set(map(int, package["member_ids"])) if package else set()
        needle = str(text or "").strip().casefold()
        matches = [value for item_id, value in self.available_items.items()
                   if item_id not in excluded and not self._has_item(item_id)
                   and (not needle or needle in str(value["label"]).casefold()
                        or needle in str(item_id))]
        self.item_results.clear()
        for value in matches[:100]:
            self.item_results.addItem(str(value["label"]), int(value["id"]))
        if len(matches) > 100:
            self.items_hint.setText(f"Showing 100 of {len(matches)} matches. Refine the search.")
        elif matches:
            self.items_hint.setText(f"{len(matches)} matching item(s). Add each item you want in this package.")
        else:
            self.items_hint.setText("No matching BOM items are available to add.")
        self.add_item_button.setEnabled(bool(matches))

    def _append_item(self, value):
        row = self.items.rowCount()
        self.items.insertRow(row)
        item = QTableWidgetItem(str(value["label"]))
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        item.setData(Qt.UserRole, int(value["id"]))
        self.items.setItem(row, 0, item)
        revision = QTableWidgetItem("A010")
        revision.setToolTip("PDF / STEP / drawing revision for this item")
        self.items.setItem(row, 1, revision)
        remove = QPushButton("Remove")
        remove.clicked.connect(lambda _checked=False, item_id=int(value["id"]): self._remove_item(item_id))
        self.items.setCellWidget(row, 2, remove)

    def _remove_item(self, item_id):
        for row in reversed(range(self.items.rowCount())):
            if int(self.items.item(row, 0).data(Qt.UserRole)) == int(item_id):
                self.items.removeRow(row)
        self._search_items(self.item_search.text())

    def _add_selected_item(self):
        item_id = self.item_results.currentData()
        value = self.available_items.get(int(item_id)) if item_id is not None else None
        if value and not self._has_item(value["id"]):
            self._append_item(value)
            self._search_items(self.item_search.text())

    def selection(self):
        ids, revisions = [], {}
        for row in range(self.items.rowCount()):
            item_id = int(self.items.item(row, 0).data(Qt.UserRole))
            ids.append(item_id)
            revisions[str(item_id)] = self.items.item(row, 1).text().strip()
        return ids, revisions

    def package(self):
        return self.package_combo.currentData()

    def accept(self):
        ids, revisions = self.selection()
        if (not ids or any(not revisions.get(str(item_id)) for item_id in ids)
                or not self.note.toPlainText().strip()
                or (not self.package() and not self.package_name.text().strip())):
            QMessageBox.warning(self, "Release package",
                "Select at least one new BOM item, enter its document revision, and provide a package name and note.")
            return
        super().accept()


class _ReleaseFilesDialog(QDialog):
    def __init__(self, sources, package, parent):
        super().__init__(parent)
        self.setWindowTitle("Review exact release files")
        self.resize(760, 520)
        layout = QVBoxLayout(self)
        if package:
            inherited = QLabel(
                f"Package R{package['revision']} is being extended. Its {len(package['member_ids'])} existing "
                "items and their exact files/revisions will be carried forward unchanged."
            )
        else:
            inherited = QLabel("Select the exact file versions to include in the new package revision.")
        inherited.setWordWrap(True)
        layout.addWidget(inherited)
        self.files = QTableWidget(0, 5)
        self.files.setHorizontalHeaderLabels(["BOM item", "Include", "Exact file", "Role", "File revision"])
        self.files.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.files.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        item_labels = {}
        for item_key, item in sources.get("items", {}).items():
            attrs = item.get("attributes") or {}
            item_labels[str(item_key)] = " · ".join(str(attrs.get(name) or "").strip()
                for name in ("name", "part_number", "drawing_number")
                if str(attrs.get(name) or "").strip()) or ("BOM item " + str(item_key))
        for key, entry in sources["files"].items():
            row = self.files.rowCount()
            self.files.insertRow(row)
            associations = [str(value) for value in entry.get("item_ids", []) if str(value) in item_labels]
            owner_text = ", ".join(item_labels[value] for value in associations)
            if not owner_text:
                owner_text = "Shared CAD dependency"
            owner = QTableWidgetItem(owner_text)
            owner.setFlags(owner.flags() & ~Qt.ItemIsEditable)
            self.files.setItem(row, 0, owner)
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            check.setCheckState(Qt.Checked)
            check.setData(Qt.UserRole, key)
            self.files.setItem(row, 1, check)
            for column, value in enumerate((entry["filename"], entry["role"], entry.get("file_revision") or ""), 2):
                cell = QTableWidgetItem(str(value))
                cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
                self.files.setItem(row, column, cell)
        layout.addWidget(self.files, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Freeze release revision")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def keys(self):
        return [self.files.item(row, 1).data(Qt.UserRole) for row in range(self.files.rowCount())
                if self.files.item(row, 1).checkState() == Qt.Checked]

    def accept(self):
        if not self.keys():
            QMessageBox.warning(self, "Release package", "Select at least one exact file to freeze.")
            return
        super().accept()


class _MergeDialog(QDialog):
    def __init__(self, preview, parent):
        super().__init__(parent)
        self.setWindowTitle("Merge review · " + preview["branch_name"] + " → Master")
        self.resize(920, 560)
        self.preview, self.choices = preview, {}
        layout = QVBoxLayout(self)
        label = QLabel("This review compares the branch starting snapshot, current master, and branch head. "
                       "A conflict means both master and branch changed the same file or item field after "
                       "the branch started. Choose ‘Use branch’ to overwrite the current master value, or "
                       "‘Keep master’ to leave it unchanged; either choice lets you continue the merge. "
                       "File rows show SHA-256 identifiers so you can see which exact file versions differ. "
                       "Selected PDF/STEP files are published to Master and Managed Files. Validation "
                       "is retained for unchanged validated branch work; later unchecked changes "
                       "remain under development. Existing document versions stay in history.")
        label.setWordWrap(True)
        layout.addWidget(label)
        table = QTableWidget(len(preview["changes"]), 5)
        table.setHorizontalHeaderLabels(["Change", "Branch start", "Current master", "Branch head", "Resolution"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for row, change in enumerate(preview["changes"]):
            for column, key in enumerate(("label", "base", "master", "branch")):
                value = change.get(key)
                if change["kind"] == "file" and key != "label":
                    text = ("Missing" if not value else "File · SHA-256 " + str(value)[:16])
                    full_value = str(value or "Missing")
                else:
                    text = (json.dumps(value, ensure_ascii=False) if isinstance(value, dict)
                            else "—" if value is None or value == "" else str(value))
                    full_value = text
                if key == "label" and change.get("conflict"):
                    text = "CONFLICT · " + text
                cell = QTableWidgetItem(text[:80])
                explanation = ("Both branch and master changed this value since the branch starting snapshot."
                               if change.get("conflict") else "Branch change compared with the branch starting snapshot.")
                cell.setToolTip(explanation + "\nValue: " + full_value)
                cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
                table.setItem(row, column, cell)
            combo = QComboBox()
            if change["conflict"]:
                combo.addItem("Resolve conflict…", "")
            combo.addItem("Use branch (overwrite master)", "branch")
            combo.addItem("Keep master", "master")
            table.setCellWidget(row, 4, combo)
            self.choices[change["id"]] = combo
        layout.addWidget(table)
        layout.addWidget(QLabel("Merge note"))
        self.note = QPlainTextEdit()
        self.note.setMaximumHeight(80)
        layout.addWidget(self.note)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Merge to master…")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def decisions(self):
        return {key: combo.currentData() for key, combo in self.choices.items()}

    def accept(self):
        if not self.note.toPlainText().strip() or any(not v for v in self.decisions().values()):
            QMessageBox.warning(self, "Merge review", "Enter a merge note and resolve every conflict.")
            return
        validated = self.preview.get("native_state") == "Validated" and all(
            value == "branch" for value in self.decisions().values()
        )
        prompt = ("Publish the validated branch work and its PDF/STEP files to Master, preserving validation?"
                  if validated else "Publish the selected branch work and deliverables to Master? "
                  "Any work not covered by an unchanged validated commit will be marked Under development.")
        confirm = QMessageBox.question if validated else QMessageBox.warning
        if confirm(self, "Merge validated work" if validated else "Merge work to Master", prompt,
                   QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        super().accept()


class _BranchFilesDialog(QDialog):
    """Read-only access to files at a selected branch snapshot."""
    def __init__(self, data, service, parent=None):
        super().__init__(parent)
        self.data, self.service, self._worker = data, service, None
        self.setWindowTitle("Files — " + data["branch_name"])
        self.resize(970, 430)
        layout = QVBoxLayout(self)
        label = QLabel("Branch: " + data["branch_name"]
                       + "\nFiles at the selected point in the timeline, including attached PDF and STEP documents.")
        label.setWordWrap(True)
        layout.addWidget(label)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Type", "Filename", "Document revision", "Source commit", "Stored in", "Availability"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setRowCount(len(data["files"]))
        for row, entry in enumerate(data["files"]):
            values = [entry.get("file_type"), entry["filename"], entry.get("file_revision"),
                      entry.get("source_commit_id") or "Branch base", entry["storage"],
                      "Available" if entry["available"] else "Missing"]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value or ""))
                if column == 0:
                    cell.setData(Qt.UserRole, entry)
                self.table.setItem(row, column, cell)
        layout.addWidget(self.table)
        self.status = QLabel("" if data["files"] else "No files are associated with this Item in this snapshot.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        self.open_button, self.save_button, self.close_button = (
            QPushButton("Open"), QPushButton("Save a Copy…"), QPushButton("Close")
        )
        for button in (self.open_button, self.save_button, self.close_button):
            actions.addWidget(button)
        layout.addLayout(actions)
        self.open_button.clicked.connect(lambda: self._export(True))
        self.save_button.clicked.connect(lambda: self._export(False))
        self.close_button.clicked.connect(self.reject)
        self.table.itemSelectionChanged.connect(self._buttons)
        self.table.itemDoubleClicked.connect(lambda *_: self._export(True))
        if self.table.rowCount():
            self.table.selectRow(0)
        self._buttons()
        if QApplication.instance():
            QApplication.instance().aboutToQuit.connect(self._shutdown)

    def _shutdown(self):
        if self._worker is not None:
            self._worker.wait()

    def _entry(self):
        cell = self.table.item(self.table.currentRow(), 0)
        return cell.data(Qt.UserRole) if cell else None

    def _buttons(self):
        entry = self._entry() or {}
        enabled = self._worker is None and bool(entry.get("available"))
        self.open_button.setEnabled(enabled)
        self.save_button.setEnabled(enabled)
        self.close_button.setEnabled(self._worker is None)

    def reject(self):
        if self._worker is None:
            super().reject()

    def _export(self, preview):
        entry = self._entry()
        if self._worker is not None or not entry or not entry["available"]:
            return
        if preview:
            filename = re.sub(r'[<>:"/\\|?*]', "_", entry["filename"]).strip(" .") or "file"
            cache_key = re.sub(r"[^0-9a-fA-F]", "", str(entry["sha256"])) or "file"
            destination = str(Path(tempfile.gettempdir()) / "Nexus-branch-previews"
                              / cache_key / filename)
        else:
            destination, _ = QFileDialog.getSaveFileName(self, "Save Branch File", entry["filename"])
            if not destination:
                return
        worker = _Task(lambda: self.service.export_branch_file(
            self.data["item_id"], self.data["branch_id"], entry["key"], destination,
            snapshot_id=self.data["snapshot_id"], expected_sha256=entry["sha256"],
        ), self)
        self._worker = worker
        results, errors = [], []
        worker.result.connect(results.append)
        worker.error.connect(errors.append)
        def finished():
            self._worker = None
            worker.deleteLater()
            self._buttons()
            if errors:
                self.status.setText(errors[0])
            elif results:
                self.status.setText("Saved copy: " + results[0])
                if preview and not QDesktopServices.openUrl(QUrl.fromLocalFile(results[0])):
                    self.status.setText("Saved copy. Choose an application to open: " + results[0])
        worker.finished.connect(finished)
        self.status.setText("Verifying and copying the selected file…")
        self._buttons()
        worker.start()


class NexusTimelinePanel(QWidget):
    changed = pyqtSignal(object)

    def __init__(self, parent=None, *, db_name=DB_NAME):
        super().__init__(parent)
        self.db_name = db_name
        self._item_id, self._service, self._data, self._legacy = None, None, {}, []
        self._worker, self._generation, self._pending = None, 0, False
        self._limit = 200
        self._wanted_branch = None
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(150)
        self._debounce.timeout.connect(self._load)
        layout = QVBoxLayout(self)
        self.summary = QLabel("Select an item to view its timeline.")
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.PlainText)
        layout.addWidget(self.summary)
        actions = QHBoxLayout()
        self.create_button = QPushButton("New Nexus Branch…")
        self.release_button = QPushButton("Release Package…")
        self.export_button = QPushButton("Export Package Revision…")
        self.project_timeline_button = QPushButton("Project Timeline & Releases…")
        self.release_button.setToolTip("Create a package from one or more BOM items, or append new items to an existing package.")
        self.export_button.setToolTip("Export the exact immutable package revision selected in the timeline.")
        self.project_timeline_button.setToolTip("View every project release package, Master change, and Nexus branch on one horizontal timeline.")
        self.files_button = QPushButton("View Branch Files…")
        for button in (self.create_button, self.release_button, self.export_button,
                       self.project_timeline_button):
            actions.addWidget(button)
        layout.addLayout(actions)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        layout.addWidget(self.status)
        splitter = QSplitter(Qt.Vertical)
        self.graph = VerticalHistoryView()
        legend = QLabel("●✓ Validated commit     ○! Not validated     ○ Other event / unknown     ◆ Release")
        legend.setToolTip("Green with a check: validated. Orange with an exclamation mark: not validated.")
        layout.addWidget(legend)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(180)
        splitter.addWidget(self.graph)
        splitter.addWidget(self.details)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)
        branch_row = QHBoxLayout()
        branch_row.addWidget(QLabel("Nexus Branch"))
        self.branch_combo = QComboBox()
        self.branch_combo.setMinimumWidth(150)
        branch_row.addWidget(self.branch_combo, 1)
        branch_row.addWidget(self.files_button)
        self.more_button = QPushButton("Load more history")
        branch_row.addWidget(self.more_button)
        layout.addLayout(branch_row)
        branch_actions = QHBoxLayout()
        self.workspace_button = QPushButton("Open Workspace")
        self.refresh_workspace_button = QPushButton("Refresh Workspace…")
        self.checkin_button = QPushButton("Check in / Parameters…")
        self.pause_button = QPushButton("Pause")
        self.merge_button = QPushButton("Merge Review…")
        self.cleanup_button = QPushButton("Retry Workspace Cleanup")
        for button in (self.workspace_button, self.refresh_workspace_button,
                       self.checkin_button, self.pause_button, self.merge_button, self.cleanup_button):
            branch_actions.addWidget(button)
        layout.addLayout(branch_actions)
        self.create_button.clicked.connect(self._create)
        self.release_button.clicked.connect(self._release)
        self.export_button.clicked.connect(self._export)
        self.project_timeline_button.clicked.connect(self._project_timeline)
        self.files_button.clicked.connect(self._files)
        self.workspace_button.clicked.connect(lambda: self._workspace(False))
        self.refresh_workspace_button.clicked.connect(lambda: self._workspace(True))
        self.checkin_button.clicked.connect(self._checkin)
        self.pause_button.clicked.connect(self._pause)
        self.merge_button.clicked.connect(self._merge)
        self.cleanup_button.clicked.connect(self._retry_cleanup)
        self.more_button.clicked.connect(self._more)
        self.branch_combo.currentIndexChanged.connect(self._render)
        self.graph.currentItemChanged.connect(self._selected)
        self._buttons()
        if QApplication.instance():
            QApplication.instance().aboutToQuit.connect(self._shutdown)

    def set_item(self, item_id, legacy_history=None):
        self._generation += 1
        self._item_id, self._legacy = int(item_id), list(legacy_history or [])
        self._limit, self._pending = 200, True
        session = SessionManager()
        self._service = NexusHistoryService(self.db_name, user_id=session.user_id, project_id=session.project_id)
        self._data = {}
        self.graph.clear()
        self.details.clear()
        self.summary.setText("Loading selected item history…")
        self._buttons()
        self._debounce.start()

    def clear(self):
        self._generation += 1
        self._debounce.stop()
        self._item_id, self._service, self._data, self._pending = None, None, {}, False
        self.graph.clear()
        self.details.clear()
        self.branch_combo.clear()
        self.summary.setText("Select an item to view its timeline.")
        self.status.clear()
        self._buttons()

    def refresh(self):
        """Reload this selected item's activity after a project event."""
        if self._item_id is not None and self._service is not None:
            self._pending = True
            self._debounce.start()

    def _run(self, operation, done, message="Loading…"):
        if self._worker is not None:
            return
        generation = self._generation
        original_item = self._item_id
        worker = _Task(operation, self)
        self._worker = worker
        result, errors = [], []
        worker.result.connect(result.append)
        worker.error.connect(errors.append)
        self.status.setText(message)
        self._buttons()
        def finished():
            self._worker = None
            worker.deleteLater()
            if generation == self._generation:
                if errors:
                    self.status.setText(errors[0])
                elif result:
                    self.status.clear()
                    done(result[0])
            elif result and done == self._changed:
                value = result[0] if isinstance(result[0], dict) else {"item_ids": [original_item]}
                self.changed.emit(value)
            self._buttons()
            if self._pending and self._item_id is not None:
                self._debounce.start()
        worker.finished.connect(finished)
        worker.start()

    def _load(self):
        if self._worker is not None or self._service is None:
            return
        service, item_id, history, limit = self._service, self._item_id, self._legacy, self._limit
        self._pending = False
        self._run(lambda: service.timeline(item_id, history, limit), self._loaded, "Loading item events…")

    def _loaded(self, data):
        selected = self._wanted_branch or self.branch_combo.currentData()
        if selected is None and data.get("active_branch"):
            selected = data["active_branch"].get("branch_id")
        self._wanted_branch = None
        self._data = data
        item = data["item"]
        last = data["releases"][0] if data["releases"] else None
        release = ((str(last.get("package_name") or "Release") + " · R" +
                    str(last.get("package_revision") or 1) + " · " +
                    str(last.get("member_count") or 1) + " items") if last else "None")
        active_branch = (data.get("active_branch") or {}).get("name") or "Master"
        self.summary.setText(
            f"{item.get('name') or item['id']}  |  CAD / project revision: {data['cad_revision']}\n"
            f"Item lifecycle: {item.get('status') or item.get('lifecycle_state') or 'Design'}  |  "
            f"Master work: {data['master_state']}  |  Active branch: {active_branch}  |  "
            f"Latest frozen package: {release}"
        )
        self.branch_combo.blockSignals(True)
        self.branch_combo.clear()
        self.branch_combo.addItem("Master and all branches", None)
        for branch in data["branches"]:
            self.branch_combo.addItem(branch["name"] + " · " + branch["state"], branch["id"])
        self.branch_combo.setCurrentIndex(max(0, self.branch_combo.findData(selected)))
        self.branch_combo.blockSignals(False)
        self._render()

    def _branch(self):
        selected = self.branch_combo.currentData()
        return next((b for b in self._data.get("branches", []) if b["id"] == selected), None)

    def _render(self):
        branch = self._branch()
        events = self._data.get("events", [])
        if branch:
            events = [e for e in events if not e.get("branch_id") or e["branch_id"] == branch["id"]]
        self.graph.set_events(events, self._data.get("branches", []))
        self._buttons()

    def _buttons(self):
        busy, branch = self._worker is not None, self._branch()
        self.create_button.setEnabled(not busy and self._data.get("can_branch", False))
        self.release_button.setEnabled(not busy and self._data.get("can_release", False))
        self.project_timeline_button.setEnabled(not busy and bool(self._data))
        event = self.graph.currentItem().data(0, EVENT_ROLE) if self.graph.currentItem() else {}
        self.export_button.setEnabled(not busy and bool(event.get("release_id")))
        self.files_button.setEnabled(not busy and bool(event.get("snapshot_id") or branch))
        self.files_button.setText("View Commit Files…" if event.get("snapshot_id") else "View Branch Files…")
        active = bool(branch and branch["state"] == "Active")
        own = bool(branch and self._service and (branch["owner_id"] == self._service.user_id or self._data.get("can_merge")))
        can_work = bool(self._data.get("can_branch"))
        for button in (self.workspace_button, self.refresh_workspace_button, self.checkin_button):
            button.setEnabled(not busy and active and can_work)
        self.pause_button.setEnabled(not busy and own and branch["state"] in {"Active", "Paused"})
        self.pause_button.setText("Resume" if branch and branch["state"] == "Paused" else "Pause")
        self.merge_button.setEnabled(not busy and active and self._data.get("can_merge", False))
        merged = bool(branch and branch["state"] == "Merged")
        for button in (self.workspace_button, self.refresh_workspace_button, self.checkin_button,
                       self.pause_button, self.merge_button):
            button.setVisible(not merged)
        self.cleanup_button.setVisible(bool(merged and branch.get("cleanup_pending") and self._data.get("can_merge")))
        self.cleanup_button.setEnabled(not busy and merged and self._data.get("can_merge", False))
        self.more_button.setEnabled(not busy and bool(self._data))

    def _selected(self, current, previous=None):
        event = current.data(0, EVENT_ROLE) if current else None
        if event:
            payload = event.get("payload") or {}
            text = [event["title"], _local_timestamp_text(event.get("created_at"), event.get("timestamp_timezone")),
                    str(event.get("actor") or event.get("actor_id") or ""), "", event.get("note") or ""]
            if event.get("release_id"):
                revisions = payload.get("item_revisions") or {}
                own_revision = revisions.get(str(event.get("bom_id")), {})
                text += ["", "Frozen package: " + event["release_id"],
                         "Package: " + str(payload.get("package_name") or event.get("title") or ""),
                         "Package revision: R" + str(payload.get("package_revision") or 1),
                         "CAD / project revision: " + str(own_revision.get("cad_revision") or
                             payload.get("cad_revision") or ""),
                         "Document revision: " + str(own_revision.get("document_revision") or
                             payload.get("document_revision") or ""),
                         "Package members: " + ", ".join(
                             str(member.get("name") or member.get("id")) +
                             (" (" + str(member.get("part_number")) + ")" if member.get("part_number") else "")
                             for member in payload.get("members", [])),
                         "Export this package to retrieve its exact files at any time."]
            elif event.get("snapshot_id"):
                text += ["", "Branch snapshot: " + event["snapshot_id"]]
            elif event.get("legacy"):
                text += ["", "Existing activity record. No new frozen release package is inferred from this event."]
            self.details.setPlainText("\n".join(text))
        else:
            self.details.clear()
        self._buttons()

    def _files(self):
        event = self.graph.currentItem().data(0, EVENT_ROLE) if self.graph.currentItem() else {}
        branch = self._branch() or {}
        snapshot_id = event.get("snapshot_id")
        branch_id = event.get("branch_id") if snapshot_id else branch.get("id")
        if not branch_id:
            return
        service, item_id = self._service, self._item_id
        self._run(lambda: service.list_branch_files(item_id, branch_id, snapshot_id=snapshot_id),
                  lambda data: _BranchFilesDialog(data, service, self).exec_(),
                  "Loading branch CAD, PDF, STEP and attached documents…")

    def _changed(self, result=None):
        if isinstance(result, dict) and result.get("branch_id"):
            self._wanted_branch = result["branch_id"]
        self.changed.emit(result if isinstance(result, dict) else {"item_ids": [self._item_id]})
        self._pending = True
        self._debounce.start()

    def _create(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("New Nexus Branch")
        layout, form = QVBoxLayout(dialog), QFormLayout()
        name, note = QLineEdit(), QPlainTextEdit()
        note.setMaximumHeight(80)
        children = QCheckBox("Include the selected assembly's child items and referenced files")
        children.setChecked(True)
        form.addRow("Branch name", name)
        form.addRow("Purpose", note)
        layout.addLayout(form)
        label = QLabel("The branch starts from current master at creation time. Release packages "
                       "remain independent snapshots. CAD edits use a separate working folder.")
        label.setWordWrap(True)
        layout.addWidget(label)
        layout.addWidget(children)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec_() != QDialog.Accepted:
            return
        service, item_id = self._service, self._item_id
        branch_name, branch_note, include = name.text(), note.toPlainText(), children.isChecked()
        self._run(lambda: {"branch_id": service.create_branch([item_id], branch_name, branch_note, include),
                           "item_ids": [item_id]}, self._changed,
                  "Capturing the branch base and exact file versions…")

    def _workspace(self, refresh):
        branch = self._branch()
        if not branch:
            return
        if refresh and QMessageBox.question(self, "Refresh branch workspace",
                "This preserves the current workspace in a saved folder and opens a clean copy of "
                "the latest checked-in branch snapshot. Continue?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        if QMessageBox.information(self, "Creo branch workspace",
                "Use this folder as Creo's working directory. Use a clean Creo session when the "
                "same CAD names are already loaded from master. Save CAD changes here before branch check-in.",
                QMessageBox.Ok | QMessageBox.Cancel) != QMessageBox.Ok:
            return
        service, branch_id = self._service, branch["id"]
        self._run(lambda: service.workspace(branch_id, refresh=refresh),
                  lambda path: QDesktopServices.openUrl(QUrl.fromLocalFile(path)),
                  "Preparing the isolated branch workspace…")

    def _checkin(self):
        branch = self._branch()
        if not branch:
            return
        service, item_id = self._service, self._item_id
        def ready(details):
            dialog = _BranchCheckinDialog(details, item_id, self)
            if dialog.exec_() != QDialog.Accepted:
                return
            branch_id, head = details["id"], details["head_snapshot_id"]
            note, attrs, quantities = dialog.note.toPlainText(), dialog.edits, dialog.quantity_edits
            checkout = service.branch_checkout_for_item(item_id, branch_id=branch_id)
            if checkout:
                self._run(lambda: service.checkin_branch_item(
                    item_id, branch_id, note, attributes=attrs, quantities=quantities
                ), self._changed, "Saving the branch checkout as a new snapshot…")
            else:
                self._run(lambda: service.check_in(
                    branch_id, head, note, attributes=attrs, quantities=quantities
                ), self._changed, "Freezing the branch check-in…")
        self._run(lambda: service.branch_details(branch["id"]), ready)

    def _pause(self):
        branch = self._branch()
        if not branch:
            return
        target = "Active" if branch["state"] == "Paused" else "Paused"
        if target == "Paused" and QMessageBox.question(self, "Pause branch",
                "Only the last checked-in snapshot is preserved in branch history. Local unchecked "
                "files remain in the workspace. Pause this branch?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        service, branch_id = self._service, branch["id"]
        self._run(lambda: service.set_branch_state(branch_id, target), self._changed, "Updating branch state…")

    def _release(self):
        service, item_id = self._service, self._item_id
        branch = self._branch()
        branch_id = branch["id"] if branch else None
        def options_ready(options):
            selection = _ReleaseSelectionDialog(options, item_id, self)
            if selection.exec_() != QDialog.Accepted:
                return
            selected_ids, revisions = selection.selection()
            package = selection.package()
            package_id = package["id"] if package else None
            package_name = selection.package_name.text().strip()
            note = selection.note.toPlainText().strip()
            self._run(lambda: service.release_sources(selected_ids, branch_id),
                lambda sources: files_ready(sources, selected_ids, revisions, package,
                                            package_id, package_name, note),
                "Loading exact file versions for the selected BOM items…")

        def files_ready(sources, selected_ids, revisions, package, package_id, package_name, note):
            dialog = _ReleaseFilesDialog(sources, package, self)
            if dialog.exec_() != QDialog.Accepted:
                return
            token = service._master_token(sources)
            def freeze():
                release_id = service.freeze_release_package(
                    selected_ids, package_name, note, revisions, expected=token,
                    selected_keys=dialog.keys(),
                    source_snapshot_id=sources.get("source_snapshot_id"),
                    existing_package_id=package_id,
                )
                member_ids = list(package["member_ids"]) if package else []
                return {"release_id": release_id, "item_ids": sorted(set(member_ids + selected_ids))}
            self._run(freeze, self._changed, "Freezing the exact multi-item release revision…")

        self._run(lambda: service.release_options(branch_id), options_ready,
                  "Loading project items and existing release packages…")

    def _export(self):
        row = self.graph.currentItem()
        event = row.data(0, EVENT_ROLE) if row else {}
        if not event.get("release_id"):
            return
        payload = event.get("payload") or {}
        filename = re.sub(r"[^A-Za-z0-9._-]", "_", str(payload.get("package_name") or "Release") +
                          "_R" + str(payload.get("package_revision") or 1) + ".zip")
        destination, _ = QFileDialog.getSaveFileName(self, "Export frozen release", filename, "Release package (*.zip)")
        if not destination:
            return
        if not destination.lower().endswith(".zip"):
            destination += ".zip"
        service, release_id = self._service, event["release_id"]
        self._run(lambda: service.export_release(release_id, destination),
                  lambda path: self.status.setText("Exported exact release package: " + path),
                  "Verifying and exporting the frozen release files…")

    def _project_timeline(self):
        if self._worker is not None or not self._service:
            return
        service = self._service
        self._run(lambda: service.project_timeline(),
            lambda data: _ProjectTimelineDialog(service, data, self).exec_(),
            "Loading project releases, Master history, and Nexus branches…")

    def _merge_finished(self, result):
        self._changed(result)
        if result.get("cleanup_pending"):
            self.status.setText("Merged to Master. Branch workspace cleanup is pending.")
            QMessageBox.warning(
                self, "Merge complete — workspace cleanup pending",
                str(result.get("cleanup_warning") or "Workspace cleanup is pending.")
                + "\n\nRemaining workspace:\n" + str(result.get("cleanup_path") or ""),
            )
            return
        archive_path = str(result.get("archive_path") or "")
        message = ("The branch is merged and closed. Its timeline and frozen files remain available. "
                   "Workspace cleanup is complete.")
        if result.get("native_state"):
            message += "\nMaster work status: " + str(result["native_state"])
        if archive_path:
            message += ("\n\nThe extra workspace copy is in this optional archive. You may delete it "
                        "when no longer needed:\n\n" + archive_path)
        QMessageBox.information(self, "Branch merged", message)

    def _retry_cleanup(self):
        branch = self._branch()
        if not branch or branch["state"] != "Merged":
            return
        service, branch_id = self._service, branch["id"]
        self._run(lambda: service.cleanup_merged_branch(branch_id), self._merge_finished,
                  "Retrying workspace cleanup for the completed merge…")

    def _merge(self):
        branch = self._branch()
        if not branch:
            return
        service, branch_id = self._service, branch["id"]
        def ready(preview):
            if not preview["changes"]:
                answer = QMessageBox.question(
                    self, "No differences from master",
                    "The branch head matches current master: no CAD file, item parameter, or BOM "
                    "structure changes need publishing. You can still close the branch as merged; "
                    "its history will remain available and its workspace will be archived. Close it?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
                )
                if answer != QMessageBox.Yes:
                    return
                note, accepted = QInputDialog.getText(self, "Close branch as merged", "Merge note:")
                if not accepted or not note.strip():
                    return
                self._run(lambda: service.merge(preview, {}, note.strip()), self._merge_finished,
                          "Closing branch and preserving its history…")
                return
            dialog = _MergeDialog(preview, self)
            if dialog.exec_() != QDialog.Accepted:
                return
            decisions, note = dialog.decisions(), dialog.note.toPlainText()
            self._run(lambda: service.merge(preview, decisions, note), self._merge_finished,
                      "Publishing the reviewed branch and deliverables to Master…")
        self._run(lambda: service.preview_merge(branch_id), ready, "Comparing base, master and branch…")

    def _more(self):
        self._limit += 200
        self._pending = True
        self._debounce.start()

    def _shutdown(self):
        self._debounce.stop()
        if self._worker is not None:
            self._worker.wait()
