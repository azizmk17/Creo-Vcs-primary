from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView, QTextEdit,
    QMessageBox
)
from PyQt5.QtGui import QFont
from PyQt5.QtCore import Qt

from core.services.diag_service import DiagService
from core.repositories.diag_repository import DiagRepository
from core.services.project_service import ProjectService
from core.session_manager import SessionManager
from core.services.ui_permission import UIPermissionHelper

import os

class DiagPage(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.service = DiagService()
        self.session = SessionManager()
        self.project_service = ProjectService()
        self.perm = UIPermissionHelper()
        # Audit logging (best-effort)
        try:
            from core.services.audit_service import AuditService
            self.audit_service = AuditService()
        except Exception:
            self.audit_service = None

        self.setWindowTitle("Commit Synchronization & Integrity")
        self.setMinimumSize(950, 600)

        self.working_dir = None
        self.commits_dir = None

        self.setup_ui()
        self.connect_events()
        #self.populate_demo_data()  # For preview before real scan

        if self.session.project_id:
            self.working_dir = self.get_working_dir()
            if self.working_dir:
                self.commits_dir = self.working_dir + "/commits"

        self._apply_action_permissions()

    def _apply_action_permissions(self):
        project_loaded = bool(self.session.project_id)
        can_admin_actions = project_loaded and self.perm.can("merge")

        try:
            self.btn_force_integrate.setEnabled(can_admin_actions)
            self.btn_delete_selected.setEnabled(can_admin_actions)
            if not project_loaded:
                tip = "Load a project to use this action."
            elif not can_admin_actions:
                tip = "Only Master/Admin can use this action."
            else:
                tip = None
            if tip:
                self.btn_force_integrate.setToolTip(tip)
                self.btn_delete_selected.setToolTip(tip)
        except Exception:
            pass


    def get_working_dir(self):
        if not self.session.project_id:
            return None
        project = self.project_service.get_project_by_id(self.session.project_id) or {}
        project_working_dir = project.get("working_directory")
        if project_working_dir:
            return project_working_dir
        

    def setup_ui(self):
        # (same design code you pasted — unchanged)
        # just remove demo population part and store tab references
        layout = QVBoxLayout(self)
        header = QLabel("Commit Synchronization & Integrity Dashboard")
        header.setFont(QFont("Segoe UI", 16, QFont.Bold))
        header.setAlignment(Qt.AlignCenter)
        layout.addWidget(header)

        # summary
        status_frame = QFrame()
        hbox = QHBoxLayout(status_frame)
        self.lbl_status_db = self.create_status_box("Database ↔ Commits Folder", "Waiting", "#7f8c8d")
        self.lbl_status_working = self.create_status_box("Working Directory", "Waiting", "#7f8c8d")
        self.lbl_status_orphan = self.create_status_box("Orphan Files", "Waiting", "#7f8c8d")
        for w in [self.lbl_status_db, self.lbl_status_working, self.lbl_status_orphan]:
            hbox.addWidget(w)
        layout.addWidget(status_frame)

        # tabs
        self.tabs = QTabWidget()
        self.tab_db = self.create_tab_table("Database vs Commits Folder")
        self.tab_working = self.create_tab_table("Working Directory Validation")
        self.tab_orphan = self.create_tab_table("Untracked / Orphan Files")
        self.tabs.addTab(self.tab_db, "Database Sync")
        self.tabs.addTab(self.tab_working, "Working Dir Check")
        self.tabs.addTab(self.tab_orphan, "Orphan Files")
        layout.addWidget(self.tabs)

        # Actions for unexpected parts
        action_row = QHBoxLayout()
        self.btn_force_integrate = QPushButton("Force integrate selected")
        self.btn_force_integrate.setObjectName("secondary")
        self.btn_force_integrate.setToolTip(
            "Mark selected unexpected parts as legal in working directory (without creating a commit)."
        )
        action_row.addWidget(self.btn_force_integrate)

        self.btn_delete_selected = QPushButton("Delete selected")
        self.btn_delete_selected.setObjectName("danger")
        self.btn_delete_selected.setToolTip(
            "Delete selected unexpected parts from the working directory. This cannot be undone."
        )
        action_row.addWidget(self.btn_delete_selected)

        action_row.addStretch(1)
        layout.addLayout(action_row)

        # console
        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setObjectName("console")
        layout.addWidget(self.console)

        # buttons
        btns = QHBoxLayout()
        self.btn_refresh = QPushButton("Refresh Status")
        self.btn_scan = QPushButton("Run Full Scan")
        self.btn_close = QPushButton("Close")
        self.btn_refresh.setObjectName("neutral")
        self.btn_scan.setObjectName("primary")
        self.btn_close.setObjectName("neutral")
        for b in [self.btn_refresh, self.btn_scan, self.btn_close]:
            btns.addWidget(b)
        layout.addLayout(btns)

        self.btn_close.clicked.connect(self.close)

    def connect_events(self):
        self.btn_refresh.clicked.connect(self.refresh_status)
        self.btn_scan.clicked.connect(self.run_full_scan)
        self.btn_force_integrate.clicked.connect(self.force_integrate_selected)
        self.btn_delete_selected.clicked.connect(self.delete_selected_unexpected)

    def create_status_box(self, title, value, color):
        frame = QFrame()
        vbox = QVBoxLayout(frame)
        lbl_title = QLabel(title)
        lbl_value = QLabel(value)
        lbl_value.setFont(QFont("Segoe UI", 14, QFont.Bold))
        lbl_value.setStyleSheet(f"color:{color}")
        lbl_title.setAlignment(Qt.AlignCenter)
        lbl_value.setAlignment(Qt.AlignCenter)
        vbox.addWidget(lbl_title)
        vbox.addWidget(lbl_value)
        frame.setMinimumWidth(200)
        return frame

    def create_tab_table(self, title):
        frame = QFrame()
        vbox = QVBoxLayout(frame)
        table = QTableWidget(0, 3)
        table.setHorizontalHeaderLabels(["Item", "Status", "Details"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        # enable row selection for force-integrate
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setSelectionMode(QTableWidget.ExtendedSelection)
        vbox.addWidget(table)
        frame.table = table
        return frame

    def run_full_scan(self):
        self.console.append(">> Running full synchronization analysis...\n")
        self.refresh_status()

    def refresh_status(self):
        if not self.session.project_id or not self.working_dir or not self.commits_dir:
            self.console.append(">> No project loaded. Select a project to run diagnostics.\n")
            self._apply_action_permissions()
            return

        db_result = self.service.check_db_vs_commits(self.commits_dir)
        working_result = self.service.check_working_directory(self.working_dir)
        orphan_result = self.service.check_orphan_files(self.working_dir)

        # Update tables
        self.populate_table(self.tab_db.table, db_result["rows"])
        self.populate_table(self.tab_working.table, working_result)
        self.populate_table(self.tab_orphan.table, orphan_result)

        # Update summary
        def update_status(label_frame, color):
            value_label = label_frame.findChildren(QLabel)[1]
            value_label.setStyleSheet(f"color: {color};")

        self.lbl_status_db.findChildren(QLabel)[1].setText(f"{db_result['summary']['synced']} synced")
        update_status(self.lbl_status_db, "#E67E22")
        
        # Only count rows that are truly outdated (not force integrated)
        outdated_count = sum(
            1 for row in working_result
            if isinstance(row, (list, tuple))
            and (
                (row[1] == "❌ Unexpected")
                or (row[1] == "❌ Outdated")
                or (row[1] == "outdated_file")
            )
        )
        if outdated_count == 0:
            self.lbl_status_working.findChildren(QLabel)[1].setText("Up to date")
            update_status(self.lbl_status_working, "#37C02B")
        else:
            self.lbl_status_working.findChildren(QLabel)[1].setText(f"{outdated_count} Outdated")
            update_status(self.lbl_status_working, "#C0392B")
        
        self.lbl_status_orphan.findChildren(QLabel)[1].setText(f"{len(orphan_result)} found")
        update_status(self.lbl_status_orphan, "#F39C12")

        self.console.append(">> Scan complete.\n")
        self._apply_action_permissions()

    def force_integrate_selected(self):
        """Take selected rows from Working Dir Check where status is Unexpected and allowlist them."""
        if not self.perm.can("merge"):
            QMessageBox.warning(self, "Permission denied", "Only Master/Admin can force integrate parts.")
            return

        table = self.tab_working.table
        rows = {idx.row() for idx in table.selectionModel().selectedRows()}
        if not rows:
            QMessageBox.information(self, "No selection", "Select one or more unexpected parts in the Working Dir Check tab.")
            return

        to_integrate = []
        for r in sorted(rows):
            item = table.item(r, 0)
            status = table.item(r, 1)
            if not item:
                continue
            if status and "Unexpected" not in (status.text() or ""):
                continue
            to_integrate.append(item.text().strip())

        if not to_integrate:
            QMessageBox.information(self, "Nothing to integrate", "Selected rows are not marked as Unexpected.")
            return

        confirm = QMessageBox.question(
            self,
            "Force integrate",
            "This will mark the selected files as legal in the working directory (without committing).\n\nContinue?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        ok, failed = 0, []
        for fn in to_integrate:
            try:
                self.service.force_integrate_part(fn, self.working_dir)
                ok += 1
                self.console.append(f">> Integrated: {fn}")
            except Exception as e:
                failed.append((fn, str(e)))

        if failed:
            msg = "\n".join([f"- {fn}: {err}" for fn, err in failed])
            QMessageBox.warning(self, "Some failed", f"Integrated {ok} file(s).\n\nFailed:\n{msg}")
        else:
            QMessageBox.information(self, "Done", f"Integrated {ok} file(s).")

        # rescan to hide integrated items
        self.refresh_status()

    def delete_selected_unexpected(self):
        """Delete selected 'Unexpected' working-dir items from disk and save an audit log entry."""
        if not self.perm.can("merge"):
            QMessageBox.warning(self, "Permission denied", "Only Master/Admin can delete unexpected parts.")
            return

        table = self.tab_working.table
        rows = {idx.row() for idx in table.selectionModel().selectedRows()}
        if not rows:
            QMessageBox.information(self, "No selection", "Select one or more unexpected parts in the Working Dir Check tab.")
            return

        to_delete: list[str] = []
        for r in sorted(rows):
            item = table.item(r, 0)
            status = table.item(r, 1)
            if not item:
                continue
            if status and "Unexpected" not in (status.text() or ""):
                continue
            to_delete.append(item.text().strip())

        if not to_delete:
            QMessageBox.information(self, "Nothing to delete", "Selected rows are not marked as Unexpected.")
            return

        preview = "\n".join([f"- {x}" for x in to_delete[:15]])
        if len(to_delete) > 15:
            preview += f"\n... and {len(to_delete) - 15} more"

        confirm = QMessageBox.question(
            self,
            "Delete files",
            "This will permanently delete the selected files from the working directory.\n\n"
            "Files:\n"
            f"{preview}\n\n"
            "Continue?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        ok = 0
        failed: list[tuple[str, str]] = []
        deleted_paths: list[str] = []
        try:
            ok, failed, deleted_paths = self.service.delete_unexpected_files(to_delete, self.working_dir)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to delete files:\n\n{str(e)}")
            return

        for full_path in deleted_paths:
            fn = os.path.basename(full_path)
            self.console.append(f">> Deleted: {fn}")

            # Best-effort audit log
            try:
                if self.audit_service and self.audit_service.supported():
                    part_id = None
                    try:
                        part_id = self.service.resolve_part_id_from_filename(fn, self.working_dir)
                    except Exception:
                        part_id = None

                    if part_id:
                        self.audit_service.log(
                            part_id=int(part_id),
                            event_type="WORKING_DIR_DELETE",
                            entity_type="FILE",
                            entity_id=str(fn),
                            message="Deleted unexpected file from diagnostic tab",
                            payload={"filename": fn, "path": full_path},
                        )
            except Exception:
                pass

        if failed:
            msg = "\n".join([f"- {fn}: {err}" for fn, err in failed])
            QMessageBox.warning(self, "Some failed", f"Deleted {ok} file(s).\n\nFailed:\n{msg}")
        else:
            QMessageBox.information(self, "Done", f"Deleted {ok} file(s).")

        self.refresh_status()

    def populate_table(self, table, rows):
        table.setRowCount(0)
        for r, row in enumerate(rows):
            table.insertRow(r)
            for c, val in enumerate(row):
                table.setItem(r, c, QTableWidgetItem(str(val)))



    # ------------------------
    # Demo Data for Preview
    # ------------------------
    def populate_demo_data(self):
        """Populate demo data for preview mode before real scan runs."""
        # --- Console initialization ---
        self.console.append(">> Initialized integrity dialog preview.")
        self.console.append(">> Ready to perform database and folder comparison.\n")

        # --- Access the three main tables ---
        tabs = self.findChild(QTabWidget)
        db_table = tabs.widget(0).table
        working_table = tabs.widget(1).table
        orphan_table = tabs.widget(2).table

        # ============================================================
        # 1️⃣ Database vs Commits Folder
        # ============================================================
        db_table.setRowCount(3)

        db_entries = [
            ("commit_001", "✅ Synced", "Folder matches database"),
            ("commit_002", "⚠️ Missing", "Exists in DB but not in filesystem"),
            ("commit_003", "✅ Synced", "OK"),
        ]

        for row, (item, status, detail) in enumerate(db_entries):
            db_table.setItem(row, 0, QTableWidgetItem(item))
            db_table.setItem(row, 1, QTableWidgetItem(status))
            db_table.setItem(row, 2, QTableWidgetItem(detail))

        # ============================================================
        # 2️⃣ Working Directory Validation
        # ============================================================
        working_table.setRowCount(2)

        working_entries = [
            ("part_AX45.prt", "✅ Up-to-date", "Matches approved commit"),
            ("part_19B.prt", "❌ Outdated", "Modified after approval"),
        ]

        for row, (item, status, detail) in enumerate(working_entries):
            working_table.setItem(row, 0, QTableWidgetItem(item))
            working_table.setItem(row, 1, QTableWidgetItem(status))
            working_table.setItem(row, 2, QTableWidgetItem(detail))

        # ============================================================
        # 3️⃣ Orphan / Untracked Files
        # ============================================================
        orphan_table.setRowCount(1)

        orphan_entries = [
            ("temp_file123.tmp", "🗑 Orphan", "Not linked to any commit"),
        ]

        for row, (item, status, detail) in enumerate(orphan_entries):
            orphan_table.setItem(row, 0, QTableWidgetItem(item))
            orphan_table.setItem(row, 1, QTableWidgetItem(status))
            orphan_table.setItem(row, 2, QTableWidgetItem(detail))

        # ============================================================
        # 🔵 Header Status Summary Updates
        # ============================================================
        def update_status(label_frame, text, color):
            value_label = label_frame.findChildren(QLabel)[1]
            value_label.setText(text)
            value_label.setStyleSheet(f"color: {color};")

        update_status(self.lbl_status_db, "⚠️ Partial", "#E67E22")
        update_status(self.lbl_status_working, "❌ Outdated", "#C0392B")
        update_status(self.lbl_status_orphan, "1 Found", "#F39C12")

        # --- Log ---
        self.console.append(">> Demo data loaded successfully.\n")

