from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QPushButton, QLabel, QTableWidget,
    QTableWidgetItem, QMessageBox, QHeaderView, QHBoxLayout, QFileDialog, QComboBox, QSizePolicy, QTextEdit, QDialog, QProgressDialog,
    QFrame, QAbstractItemView
)
from PyQt5.QtCore import Qt, QTimer
from core.services.snapshot_service import SnapshotService
from core.session_manager import SessionManager
from core.services.project_service import ProjectService
from pages.dialogs.snapshot_detail_dialog import SnapshotDetailDialog
import json, os

from PyQt5.QtCore import QThread
from core.workers.snapshot_worker import SnapshotWorker

from pages.dialogs.progress_dialog import ProgressDialog

class SnapshotPage(QWidget):
    def __init__(self):
        super().__init__()
        self.service = SnapshotService()
        self.session = SessionManager()
        self.project_service = ProjectService()
        self.snapshots = []

        self.working_dir = None
        self.commits_dir = None

        if self.session.project_id:
            self.working_dir = self.get_working_dir()
            self.commits_dir = os.path.join(self.working_dir, "commits")
            self.check_working_dir_existance()

        self.init_ui()

    # -------------------- UI SETUP --------------------
    def init_ui(self):
        self.setObjectName("snapshotPage")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 7, 8, 8)
        layout.setSpacing(6)

        header = QFrame()
        header.setObjectName("snapshotHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(11, 8, 11, 8)
        header_layout.setSpacing(10)
        header_text = QVBoxLayout()
        header_text.setSpacing(1)
        self.lbl_title = QLabel("Project Snapshots")
        self.lbl_title.setObjectName("snapshotTitle")
        self.lbl_summary = QLabel("Status: No snapshots yet.")
        self.lbl_summary.setObjectName("snapshotSummary")
        self.lbl_summary.setWordWrap(True)
        header_text.addWidget(self.lbl_title)
        header_text.addWidget(self.lbl_summary)
        header_layout.addLayout(header_text, 1)

        self.btn_new = QPushButton("Create Snapshot")
        self.btn_new.setObjectName("primary")
        self.btn_new.setToolTip("Capture the current controlled project state")
        header_layout.addWidget(self.btn_new)
        layout.addWidget(header)

        action_strip = QFrame()
        action_strip.setObjectName("snapshotActionStrip")
        action_strip_layout = QVBoxLayout(action_strip)
        action_strip_layout.setContentsMargins(5, 3, 5, 3)
        action_strip_layout.setSpacing(3)
        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(4)
        action_caption = QLabel("SNAPSHOT")
        action_caption.setObjectName("snapshotActionCaption")
        btn_layout.addWidget(action_caption)
        self.btn_view = QPushButton("View Details")
        self.btn_view.setObjectName("neutral")
        self.btn_delete = QPushButton("Delete Snapshot")
        self.btn_delete.setObjectName("danger")
        self.btn_export = QPushButton("Export JSON")
        self.btn_export.setObjectName("neutral")

        self.btn_new.clicked.connect(self.create_snapshot)
        self.btn_view.clicked.connect(self.view_selected_snapshot)
        self.btn_delete.clicked.connect(self.delete_snapshot)
        self.btn_export.clicked.connect(self.export_snapshot)

        btn_layout.addWidget(self.btn_view)
        btn_layout.addWidget(self.btn_export)
        btn_layout.addWidget(self.btn_delete)
        btn_layout.addStretch(1)
        action_strip_layout.addLayout(btn_layout)

        self.combo_from = QComboBox()
        self.combo_to = QComboBox()
        self.btn_compare = QPushButton("Compare Snapshots")
        self.btn_compare.setObjectName("neutral")

        compare_layout = QHBoxLayout()
        compare_layout.setContentsMargins(0, 0, 0, 0)
        compare_layout.setSpacing(4)
        compare_caption = QLabel("COMPARE")
        compare_caption.setObjectName("snapshotActionCaption")
        compare_layout.addWidget(compare_caption)
        compare_layout.addWidget(QLabel("From"))
        compare_layout.addWidget(self.combo_from, 1)
        compare_layout.addWidget(QLabel("To"))
        compare_layout.addWidget(self.combo_to, 1)
        compare_layout.addWidget(self.btn_compare)
        compare_layout.addStretch(1)
        action_strip_layout.addLayout(compare_layout)

        self.btn_compare.clicked.connect(self.compare_snapshots)
        layout.addWidget(action_strip)

        self.table = QTableWidget()
        self.table.setObjectName("snapshotTable")
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["ID", "Snapshot Name", "Created By", "Created At"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.cellDoubleClicked.connect(
            lambda row, _column: self.show_snapshot_details(
                int(self.table.item(row, 0).text())
            ) if self.table.item(row, 0) else None
        )
        layout.addWidget(self.table, 1)

        self.setStyleSheet("""
            QWidget#snapshotPage { background: #e1e5e9; }
            QFrame#snapshotHeader {
                background: #ffffff;
                border: 1px solid #aeb9c5;
                border-left: 4px solid #2f75a4;
            }
            QLabel#snapshotTitle {
                color: #172c3f;
                font-size: 14px;
                font-weight: 700;
            }
            QLabel#snapshotSummary {
                color: #586b7d;
                font-size: 10px;
            }
            QFrame#snapshotActionStrip {
                background: #eef1f4;
                border: 1px solid #aeb9c5;
            }
            QLabel#snapshotActionCaption {
                color: #4d6276;
                font-size: 9px;
                font-weight: 700;
                padding: 0 4px;
            }
            QFrame#snapshotActionSeparator { color: #aeb9c5; }
            QFrame#snapshotActionStrip QPushButton,
            QFrame#snapshotActionStrip QComboBox {
                min-height: 23px;
                border-radius: 0;
                padding: 1px 7px;
            }
            QTableWidget#snapshotTable {
                background: #ffffff;
                alternate-background-color: #f6f8f9;
                border: 1px solid #aeb9c5;
                border-radius: 0;
                selection-background-color: #dbe9f4;
                selection-color: #172c3f;
            }
            QTableWidget#snapshotTable QHeaderView::section {
                background: #e5e9ed;
                color: #30475b;
                border: 0;
                border-right: 1px solid #c4cdd6;
                border-bottom: 1px solid #aeb9c5;
                padding: 4px 6px;
                font-weight: 600;
            }
        """)
        QTimer.singleShot(0, self.load_snapshots)

        # Initial state
        self._update_action_state()
        self.table.itemSelectionChanged.connect(self._update_action_state)

    # -------------------- DIRECTORY HANDLING --------------------
    def get_working_dir(self):
        project = self.project_service.get_project_by_id(self.session.project_id)
        return project["working_directory"] if project else None

    def check_working_dir_existance(self):
        if not self.working_dir or not os.path.isdir(self.working_dir):
            # Don't block app startup; show as non-fatal.
            QMessageBox.warning(self, "Working Directory Missing", f"Working directory does not exist:\n{self.working_dir}")

    # -------------------- SNAPSHOT CRUD --------------------
    def load_snapshots(self):
        if not self.session.project_id:
            self.snapshots = []
            self.table.setRowCount(0)
            self.combo_from.clear()
            self.combo_to.clear()
            self.lbl_summary.setText("<b>Status:</b> No project loaded.")
            self._update_action_state()
            return

        rows = self.service.repo.get_all(self.session.project_id)
        self.snapshots = rows
        self.table.setRowCount(0)
        self.combo_from.clear()
        self.combo_to.clear()

        for r in rows:
            row_idx = self.table.rowCount()
            self.table.insertRow(row_idx)
            self.table.setItem(row_idx, 0, QTableWidgetItem(str(r["id"])))
            self.table.setItem(row_idx, 1, QTableWidgetItem(r["snapshot_name"]))
            self.table.setItem(row_idx, 2, QTableWidgetItem(r["created_by"]))
            self.table.setItem(row_idx, 3, QTableWidgetItem(r["created_at"]))

            self.combo_from.addItem(r["snapshot_name"], r["id"])
            self.combo_to.addItem(r["snapshot_name"], r["id"])

        # Update summary
        if rows:
            last = rows[0]
            self.lbl_summary.setText(f"<b>Total:</b> {len(rows)} snapshots<br>"
                                     f"<b>Last created:</b> {last['snapshot_name']} ({last['created_at']})")
        else:
            self.lbl_summary.setText("<b>Status:</b> No snapshots yet.")

        self._update_action_state()

    def _update_action_state(self):
        has_selection = self.table.currentRow() >= 0 and self.table.rowCount() > 0
        self.btn_view.setEnabled(has_selection)
        self.btn_delete.setEnabled(has_selection)
        self.btn_export.setEnabled(has_selection)
        # compare requires at least 2 snapshots
        self.btn_compare.setEnabled(self.combo_from.count() >= 2 and self.combo_to.count() >= 2)

    def on_snapshot_finished(self, snapshot_id):
        self.progress_dialog.setValue(100)
        QMessageBox.information(self, "Snapshot Created", f"Snapshot #{snapshot_id} successfully saved.")
        self.progress_dialog.close()
        self.load_snapshots()

    def on_snapshot_error(self, message):
        QMessageBox.critical(self, "Snapshot Error", f"{message}")
        self.progress_dialog.close()

    def _on_snapshot_cancelled_ui(self, dialog, thread):
        dialog.stop_animation()
        dialog.reject()
        thread.quit()
        QMessageBox.warning(self, "⚠️ Cancelled", "Snapshot process was cancelled.")


    def create_snapshot(self):
        project_id = self.session.project_id
        username = self.session.username
        snapshot_name = f"Snapshot_{username}_{project_id}_{self._timestamp()}"

        # Create and show progress dialog
        self.progress_dialog = QProgressDialog("Creating snapshot...", "Cancel", 0, 100, self)
        self.progress_dialog.setWindowModality(Qt.WindowModal)
        self.progress_dialog.setMinimumDuration(0)
        self.progress_dialog.setAutoClose(False)
        self.progress_dialog.setAutoReset(False)
        self.progress_dialog.setValue(0)
        self.progress_dialog.resize(400, 120)
        self.progress_dialog.show()

        # Run worker thread
        self.worker = SnapshotWorker(self.service, project_id, snapshot_name, self.working_dir, username)
        self.worker.progress.connect(self.progress_dialog.setValue)
        self.worker.error.connect(self.on_snapshot_error)
        self.worker.finished.connect(self.on_snapshot_finished)

        # Allow user to cancel
        self.progress_dialog.canceled.connect(self.worker.stop)

        self.worker.start()


    def delete_snapshot(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Select Snapshot", "Please select a snapshot to delete.")
            return
        snapshot_id = self.table.item(row, 0).text()
        reply = QMessageBox.question(self, "Confirm Delete",
                                     f"Are you sure you want to delete snapshot #{snapshot_id}?",
                                     QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.service.repo.delete(snapshot_id)
            self.load_snapshots()

    # -------------------- VIEW / EXPORT --------------------
    def view_selected_snapshot(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Select Snapshot", "Please select a snapshot first.")
            return
        snapshot_id = int(self.table.item(row, 0).text())
        self.show_snapshot_details(snapshot_id=snapshot_id)


    def show_snapshot_details(self, snapshot_id):
        data = self.service.repo.get_by_id(snapshot_id)
        if not data:
            QMessageBox.warning(self, "Empty Snapshot", "No data found for this snapshot.")
            return
        dlg = SnapshotDetailDialog(snapshot_id, data)
        dlg.exec_()

    def export_snapshot(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Select Snapshot", "Please select a snapshot to export.")
            return
        snapshot_id = int(self.table.item(row, 0).text())
        snapshot = self.service.repo.get_by_id(snapshot_id)
        if not snapshot:
            QMessageBox.warning(self, "Error", "Snapshot not found.")
            return

        save_path, _ = QFileDialog.getSaveFileName(self, "Export Snapshot", f"{snapshot['snapshot_name']}.json", "JSON Files (*.json)")
        if save_path:
            with open(save_path, "w") as f:
                f.write(snapshot["snapshot_data"])
            QMessageBox.information(self, "Export Complete", f"Snapshot exported to:\n{save_path}")

    # -------------------- COMPARISON --------------------
    def compare_snapshots(self):
        id_from = self.combo_from.currentData()
        id_to = self.combo_to.currentData()
        if not id_from or not id_to or id_from == id_to:
            QMessageBox.warning(self, "Invalid Selection", "Please select two different snapshots to compare.")
            return

        diff = self.service.compare_snapshots(id_from, id_to)

        # Prepare readable text summary
        summary = (
            f"<h3>📊 Snapshot Comparison</h3>"
            f"<b>From:</b> #{id_from}<br>"
            f"<b>To:</b> #{id_to}<br><br>"
            f"🟩 <b>Added:</b> {len(diff['added'])}<br>"
            f"🟥 <b>Removed:</b> {len(diff['removed'])}<br>"
            f"🟨 <b>Modified:</b> {len(diff['modified'])}<br><hr>"
        )

        def format_section(title, items, color):
            if not items:
                return f"<b style='color:{color};'>{title}:</b> <i>None</i><br><br>"
            html = f"<b style='color:{color};'>{title} ({len(items)} files):</b><br>"
            for f in items:
                html += f"<span style='margin-left:20px;'>• {f}</span><br>"
            html += "<br>"
            return html

        text_html = summary
        text_html += format_section("🟩 Added Files", diff["added"], "green")
        text_html += format_section("🟥 Removed Files", diff["removed"], "red")
        text_html += format_section("🟨 Modified Files", diff["modified"], "orange")

        text_html += format_section("Issues Added", diff.get("issue_added", []), "#b91c1c")
        text_html += format_section("Issues Removed", diff.get("issue_removed", []), "#2e7d32")
        text_html += format_section("Issue State Changed", diff.get("issue_changed", []), "#a16207")

        # Create dialog
        dlg = QDialog(self)
        dlg.setWindowTitle("Snapshot Comparison Result")
        dlg.resize(800, 600)

        layout = QVBoxLayout(dlg)

        label_title = QLabel("<h2>Snapshot Comparison Result</h2>")
        layout.addWidget(label_title)

        text_area = QTextEdit()
        text_area.setReadOnly(True)
        text_area.setTextInteractionFlags(text_area.textInteractionFlags() | Qt.TextSelectableByMouse)
        text_area.setHtml(text_html)
        text_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(text_area)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(dlg.close)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_close)
        layout.addLayout(btn_layout)

        dlg.exec_()

    # -------------------- UTIL --------------------
    def _timestamp(self):
        from datetime import datetime
        return datetime.now().strftime("%Y%m%d_%H%M%S")
