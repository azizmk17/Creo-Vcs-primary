"""Vertical item history and isolated Nexus branch workflows."""
import json
from pathlib import Path
import re
import tempfile

from PyQt5.QtCore import Qt, QThread, QTimer, QPointF, QUrl, pyqtSignal
from PyQt5.QtGui import QColor, QPainterPath, QPen, QPolygonF, QDesktopServices
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTreeWidget, QTreeWidgetItem, QHeaderView, QStyledItemDelegate, QStyle,
    QComboBox, QDialog, QFormLayout, QLineEdit, QPlainTextEdit, QDialogButtonBox,
    QMessageBox, QCheckBox, QFileDialog, QTableWidget, QTableWidgetItem,
    QAbstractItemView, QDoubleSpinBox, QSplitter, QInputDialog,
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


class _ReleaseDialog(QDialog):
    def __init__(self, sources, parent):
        super().__init__(parent)
        self.setWindowTitle("Freeze release package")
        self.resize(720, 500)
        layout, form = QVBoxLayout(self), QFormLayout()
        self.cad_revision = QLineEdit(sources["cad_revision"])
        self.cad_revision.setReadOnly(True)
        self.document_revision = QLineEdit()
        self.document_revision.setPlaceholderText("e.g. A010")
        form.addRow("CAD / project revision", self.cad_revision)
        form.addRow("Document / package revision", self.document_revision)
        layout.addLayout(form)
        label = QLabel("Select the exact current file versions to freeze. This creates a permanent "
                       "package; it does not grant validation or change the item's lifecycle.")
        label.setWordWrap(True)
        layout.addWidget(label)
        self.files = QTableWidget(0, 4)
        self.files.setHorizontalHeaderLabels(["Include", "Exact file", "Role", "Document revision"])
        self.files.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        for key, entry in sources["files"].items():
            row = self.files.rowCount()
            self.files.insertRow(row)
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            check.setCheckState(Qt.Checked)
            check.setData(Qt.UserRole, key)
            self.files.setItem(row, 0, check)
            for column, value in enumerate((entry["filename"], entry["role"], entry.get("file_revision") or ""), 1):
                cell = QTableWidgetItem(str(value))
                cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
                self.files.setItem(row, column, cell)
        layout.addWidget(self.files)
        layout.addWidget(QLabel("Release purpose / note"))
        self.note = QPlainTextEdit()
        self.note.setMaximumHeight(80)
        layout.addWidget(self.note)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Freeze package")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def keys(self):
        return [self.files.item(row, 0).data(Qt.UserRole) for row in range(self.files.rowCount())
                if self.files.item(row, 0).checkState() == Qt.Checked]

    def accept(self):
        if not self.keys() or not self.document_revision.text().strip() or not self.note.toPlainText().strip():
            QMessageBox.warning(self, "Release package", "Select files and enter the document revision and release note.")
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
        self.release_button = QPushButton("Freeze Release Package…")
        self.export_button = QPushButton("Export Selected Release…")
        self.files_button = QPushButton("View Branch Files…")
        for button in (self.create_button, self.release_button, self.export_button):
            actions.addWidget(button)
        layout.addLayout(actions)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        layout.addWidget(self.status)
        splitter = QSplitter(Qt.Vertical)
        self.graph = VerticalHistoryView()
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
        release = (last["cad_revision"] + " / " + last["document_revision"]) if last else "None"
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
                text += ["", "Frozen package: " + event["release_id"],
                         "CAD / project revision: " + str(payload.get("cad_revision") or ""),
                         "Document / package revision: " + str(payload.get("document_revision") or ""),
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
        def ready(sources):
            dialog = _ReleaseDialog(sources, self)
            if dialog.exec_() != QDialog.Accepted:
                return
            revision, note, keys = dialog.document_revision.text(), dialog.note.toPlainText(), dialog.keys()
            token = service._master_token(sources)
            self._run(lambda: service.freeze_release(item_id, revision, note,
                expected=token, selected_keys=keys, source_snapshot_id=sources.get("source_snapshot_id")),
                self._changed, "Freezing the exact release package…")
        self._run(lambda: service.release_sources(item_id, branch_id), ready, "Loading exact release file versions…")

    def _export(self):
        row = self.graph.currentItem()
        event = row.data(0, EVENT_ROLE) if row else {}
        if not event.get("release_id"):
            return
        payload = event.get("payload") or {}
        filename = re.sub(r"[^A-Za-z0-9._-]", "_", "Release_" +
                          str(payload.get("cad_revision") or "") + "_" +
                          str(payload.get("document_revision") or "") + ".zip")
        destination, _ = QFileDialog.getSaveFileName(self, "Export frozen release", filename, "Release package (*.zip)")
        if not destination:
            return
        if not destination.lower().endswith(".zip"):
            destination += ".zip"
        service, release_id = self._service, event["release_id"]
        self._run(lambda: service.export_release(release_id, destination),
                  lambda path: self.status.setText("Exported exact release package: " + path),
                  "Verifying and exporting the frozen release files…")

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
