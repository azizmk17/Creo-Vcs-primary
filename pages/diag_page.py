from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView, QTextEdit,
    QMessageBox, QInputDialog, QLineEdit, QCheckBox
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
        self.setFont(QFont("Segoe UI", 8))
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
        self._checked_by_table = {}

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
            self.btn_assign_supplier.setEnabled(can_admin_actions)
            self.btn_unassign_supplier.setEnabled(can_admin_actions)
            if not project_loaded:
                tip = "Load a project to use this action."
            elif not can_admin_actions:
                tip = "Only Master/Admin can use this action."
            else:
                tip = None
            if tip:
                self.btn_force_integrate.setToolTip(tip)
                self.btn_delete_selected.setToolTip(tip)
                self.btn_assign_supplier.setToolTip(tip)
                self.btn_unassign_supplier.setToolTip(tip)
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
        self.setObjectName("diagnosticWorkspace")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 7, 8, 8)
        layout.setSpacing(6)
        header_frame = QFrame()
        header_frame.setObjectName("diagnosticHeader")
        header_layout = QVBoxLayout(header_frame)
        header_layout.setContentsMargins(11, 7, 11, 7)
        header_layout.setSpacing(1)
        header = QLabel("Commit Synchronization & Integrity")
        header.setObjectName("diagnosticTitle")
        subtitle = QLabel("Controlled-file reconciliation, workspace integrity, and supplier package scope")
        subtitle.setObjectName("diagnosticSubtitle")
        header_layout.addWidget(header)
        header_layout.addWidget(subtitle)
        layout.addWidget(header_frame)

        # summary
        status_frame = QFrame()
        status_frame.setObjectName("diagnosticStatusStrip")
        hbox = QHBoxLayout(status_frame)
        hbox.setContentsMargins(5, 5, 5, 5)
        hbox.setSpacing(5)
        self.lbl_status_db = self.create_status_box("Database ↔ Commits Folder", "Waiting", "#7f8c8d")
        self.lbl_status_working = self.create_status_box("Working Directory", "Waiting", "#7f8c8d")
        self.lbl_status_orphan = self.create_status_box("Orphan Files", "Waiting", "#7f8c8d")
        for w in [self.lbl_status_db, self.lbl_status_working, self.lbl_status_orphan]:
            hbox.addWidget(w)
        layout.addWidget(status_frame)

        filter_frame = QFrame()
        filter_frame.setObjectName("diagnosticFilterStrip")
        filter_row = QHBoxLayout(filter_frame)
        filter_row.setContentsMargins(6, 4, 6, 4)
        filter_row.setSpacing(6)
        filter_row.addWidget(QLabel("FIND"))
        self.table_search_input = QLineEdit()
        self.table_search_input.setPlaceholderText(
            "Search the current diagnostic tab..."
        )
        self.table_search_input.setClearButtonEnabled(True)
        filter_row.addWidget(self.table_search_input, 1)
        self.show_only_selected_input = QCheckBox("Show only selected")
        filter_row.addWidget(self.show_only_selected_input)
        self.checked_count_label = QLabel("0 selected")
        self.checked_count_label.setStyleSheet("color: #6b7280; font-weight: 600;")
        filter_row.addWidget(self.checked_count_label)
        layout.addWidget(filter_frame)

        # tabs
        self.tabs = QTabWidget()
        self.tabs.setObjectName("diagnosticTabs")
        self.tabs.setDocumentMode(True)
        self.tab_db = self.create_tab_table("Database vs Commits Folder")
        self.tab_working = self.create_tab_table("Working Directory Validation")
        self.tab_orphan = self.create_tab_table("Untracked / Orphan Files")
        self.tab_supplier = self.create_tab_table("Supplier-owned CAD Dependencies")
        self.tabs.addTab(self.tab_db, "Database Sync")
        self.tabs.addTab(self.tab_working, "Working Dir Check")
        self.tabs.addTab(self.tab_orphan, "Orphan Files")
        self.tabs.addTab(self.tab_supplier, "Supplier Packages")
        layout.addWidget(self.tabs, 3)

        # Actions for unexpected parts
        action_frame = QFrame()
        action_frame.setObjectName("diagnosticActionStrip")
        action_row = QHBoxLayout(action_frame)
        action_row.setContentsMargins(5, 3, 5, 3)
        action_row.setSpacing(4)
        action_row.addWidget(QLabel("ACTIONS"))
        self.btn_assign_supplier = QPushButton("Assign selected to supplier package")
        self.btn_assign_supplier.setObjectName("primary")
        self.btn_assign_supplier.setToolTip(
            "Assign selected orphan or unexpected Creo files to a black-box supplier assembly without creating BOM items."
        )
        action_row.addWidget(self.btn_assign_supplier)

        self.btn_unassign_supplier = QPushButton("Unassign selected package files")
        self.btn_unassign_supplier.setObjectName("secondary")
        self.btn_unassign_supplier.setToolTip(
            "Remove selected ownership records so the files become orphan/unexpected again."
        )
        action_row.addWidget(self.btn_unassign_supplier)

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
        layout.addWidget(action_frame)

        # console
        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setObjectName("console")
        self.console.setMinimumHeight(95)
        layout.addWidget(self.console, 1)

        # buttons
        btns = QHBoxLayout()
        self.btn_refresh = QPushButton("Refresh Status")
        self.btn_scan = QPushButton("Run Full Scan")
        self.btn_close = QPushButton("Close")
        self.btn_refresh.setObjectName("neutral")
        self.btn_scan.setObjectName("primary")
        self.btn_close.setObjectName("neutral")
        btns.addStretch(1)
        for b in [self.btn_refresh, self.btn_scan, self.btn_close]:
            btns.addWidget(b)
        layout.addLayout(btns)

        self.btn_close.clicked.connect(self.close)
        self.setStyleSheet("""
            QDialog#diagnosticWorkspace { background: #e1e5e9; }
            QFrame#diagnosticHeader {
                background: #ffffff;
                border: 1px solid #aeb9c5;
                border-left: 4px solid #2f75a4;
            }
            QLabel#diagnosticTitle {
                color: #172c3f;
                font-size: 14px;
                font-weight: 700;
            }
            QLabel#diagnosticSubtitle {
                color: #586b7d;
                font-size: 10px;
            }
            QFrame#diagnosticStatusStrip,
            QFrame#diagnosticFilterStrip,
            QFrame#diagnosticActionStrip {
                background: #eef1f4;
                border: 1px solid #aeb9c5;
            }
            QFrame#diagnosticStatusBox {
                background: #ffffff;
                border: 1px solid #c2cbd4;
                border-left: 3px solid #6689a5;
            }
            QFrame#diagnosticFilterStrip QLabel,
            QFrame#diagnosticActionStrip QLabel {
                color: #53687c;
                font-size: 9px;
                font-weight: 700;
            }
            QFrame#diagnosticFilterStrip QLineEdit,
            QFrame#diagnosticActionStrip QPushButton {
                min-height: 23px;
                border-radius: 0;
                padding: 1px 7px;
            }
            QTabWidget#diagnosticTabs::pane {
                background: #ffffff;
                border: 1px solid #aeb9c5;
            }
            QTabWidget#diagnosticTabs QTabBar::tab {
                background: #dfe4e9;
                border: 1px solid #aeb9c5;
                border-radius: 0;
                padding: 5px 10px;
                color: #344a5f;
                font-weight: 600;
            }
            QTabWidget#diagnosticTabs QTabBar::tab:selected {
                background: #ffffff;
                color: #173f5e;
                border-top: 2px solid #2f75a4;
                border-bottom-color: #ffffff;
            }
            QTableWidget[diagnosticTable="true"] {
                background: #ffffff;
                alternate-background-color: #f6f8f9;
                border: 0;
                gridline-color: #d9dfe6;
                selection-background-color: #dbe9f4;
                selection-color: #172c3f;
            }
            QTableWidget[diagnosticTable="true"] QHeaderView::section {
                background: #e5e9ed;
                color: #30475b;
                border: 0;
                border-right: 1px solid #c4cdd6;
                border-bottom: 1px solid #aeb9c5;
                padding: 4px 6px;
                font-weight: 600;
            }
            QTextEdit#console {
                background: #202b35;
                color: #d8e2ea;
                border: 1px solid #111a22;
                border-radius: 0;
                font-family: "Cascadia Mono", Consolas, monospace;
                font-size: 9px;
                padding: 5px;
            }
        """)

    def connect_events(self):
        self.btn_refresh.clicked.connect(self.refresh_status)
        self.btn_scan.clicked.connect(self.run_full_scan)
        self.btn_force_integrate.clicked.connect(self.force_integrate_selected)
        self.btn_delete_selected.clicked.connect(self.delete_selected_unexpected)
        self.btn_assign_supplier.clicked.connect(self.assign_selected_to_supplier_package)
        self.btn_unassign_supplier.clicked.connect(self.unassign_selected_supplier_files)
        self.table_search_input.textChanged.connect(self._apply_current_table_filter)
        self.show_only_selected_input.toggled.connect(self._apply_current_table_filter)
        self.tabs.currentChanged.connect(self._apply_current_table_filter)

    def create_status_box(self, title, value, color):
        frame = QFrame()
        frame.setObjectName("diagnosticStatusBox")
        vbox = QVBoxLayout(frame)
        vbox.setContentsMargins(8, 5, 8, 5)
        vbox.setSpacing(1)
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
        vbox.setContentsMargins(0, 0, 0, 0)
        table = QTableWidget(0, 3)
        table.setProperty("diagnosticTable", True)
        table.setHorizontalHeaderLabels(["Item", "Status", "Details"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(24)
        # enable row selection for force-integrate
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setSelectionMode(QTableWidget.ExtendedSelection)
        table.itemChanged.connect(
            lambda item, current_table=table: self._on_table_item_changed(
                current_table, item
            )
        )
        vbox.addWidget(table)
        frame.table = table
        return frame

    @staticmethod
    def _row_key(item: QTableWidgetItem) -> str:
        return str(item.data(Qt.UserRole + 1) or "") if item else ""

    def _checked_keys(self, table: QTableWidget) -> set[str]:
        return self._checked_by_table.setdefault(table, set())

    def _on_table_item_changed(self, table: QTableWidget, item: QTableWidgetItem) -> None:
        if item is None or item.column() != 0:
            return
        key = self._row_key(item)
        if not key:
            return
        checked = self._checked_keys(table)
        if item.checkState() == Qt.Checked:
            checked.add(key)
        else:
            checked.discard(key)
        self._apply_table_filter(table)

    def _apply_current_table_filter(self, *_args) -> None:
        try:
            current = self.tabs.currentWidget()
            table = getattr(current, "table", None)
            if table is not None:
                self._apply_table_filter(table)
        except Exception:
            pass

    def _apply_table_filter(self, table: QTableWidget) -> None:
        query = str(self.table_search_input.text() or "").strip().casefold()
        selected_only = self.show_only_selected_input.isChecked()
        for row in range(table.rowCount()):
            first = table.item(row, 0)
            checked = bool(first and first.checkState() == Qt.Checked)
            searchable = " ".join(
                str(table.item(row, column).text() or "")
                for column in range(table.columnCount())
                if table.item(row, column) is not None
            ).casefold()
            table.setRowHidden(
                row,
                bool((query and query not in searchable) or (selected_only and not checked)),
            )
        if table is getattr(self.tabs.currentWidget(), "table", None):
            self.checked_count_label.setText(
                f"{len(self._checked_keys(table))} selected"
            )

    def _checked_rows(self, table: QTableWidget) -> list[int]:
        return [
            row for row in range(table.rowCount())
            if table.item(row, 0) is not None
            and table.item(row, 0).checkState() == Qt.Checked
        ]

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
        supplier_dependencies = self.service.list_cad_dependencies()

        # Update tables
        self.populate_table(self.tab_db.table, db_result["rows"])
        self.populate_table(self.tab_working.table, working_result)
        self.populate_table(self.tab_orphan.table, orphan_result)
        self._populate_supplier_dependencies(supplier_dependencies)

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

    def _populate_supplier_dependencies(self, dependencies) -> None:
        table = self.tab_supplier.table
        rows = list(dependencies or [])
        previous = set(self._checked_keys(table))
        incoming = set()
        table.blockSignals(True)
        table.setRowCount(len(rows))
        for row_index, dependency in enumerate(rows):
            filename = str(
                dependency.get("original_filename")
                or dependency.get("base_file_name")
                or ""
            )
            owner = " — ".join(
                value for value in (
                    str(
                        dependency.get("owner_item_number")
                        or dependency.get("owner_aes_number")
                        or ""
                    ).strip(),
                    str(dependency.get("owner_name") or "").strip(),
                ) if value
            )
            name_item = QTableWidgetItem(filename)
            name_item.setData(Qt.UserRole, int(dependency["id"]))
            key = f"dependency:{int(dependency['id'])}"
            incoming.add(key)
            name_item.setData(Qt.UserRole + 1, key)
            name_item.setFlags(name_item.flags() | Qt.ItemIsUserCheckable)
            name_item.setCheckState(Qt.Checked if key in previous else Qt.Unchecked)
            table.setItem(row_index, 0, name_item)
            table.setItem(row_index, 1, QTableWidgetItem("Owned dependency"))
            table.setItem(row_index, 2, QTableWidgetItem(owner))
        self._checked_by_table[table] = previous.intersection(incoming)
        table.blockSignals(False)
        self._apply_table_filter(table)

    def assign_selected_to_supplier_package(self):
        if not self.perm.can("merge"):
            QMessageBox.warning(self, "Permission denied", "Only Master/Admin can assign package files.")
            return
        current = self.tabs.currentWidget()
        if current not in (self.tab_orphan, self.tab_working):
            QMessageBox.information(
                self,
                "Select files",
                "Select files in Orphan Files or Working Dir Check first.",
            )
            return
        table = current.table
        selected_rows = self._checked_rows(table)
        filenames = [
            str(table.item(row, 0).text() or "").strip()
            for row in selected_rows if table.item(row, 0)
        ]
        if not filenames:
            QMessageBox.information(self, "No selection", "Check one or more Creo files.")
            return
        packages = self.service.list_supplier_packages()
        if not packages:
            QMessageBox.information(
                self,
                "No supplier package",
                "Edit the owning assembly and set CAD Control to SUPPLIER PACKAGE first.",
            )
            return
        labels = [
            f"{row.get('part_number') or 'No Number'} — {row.get('name') or row.get('id')} "
            f"({int(row.get('dependency_count') or 0)} files)"
            for row in packages
        ]
        selected_label, ok = QInputDialog.getItem(
            self,
            "Assign CAD dependencies",
            "Supplier-managed assembly:",
            labels,
            0,
            False,
        )
        if not ok:
            return
        package = packages[labels.index(selected_label)]
        try:
            count = self.service.assign_cad_dependencies(int(package["id"]), filenames)
        except Exception as exc:
            QMessageBox.critical(self, "Assignment failed", str(exc))
            return
        QMessageBox.information(
            self,
            "Dependencies assigned",
            f"Assigned {count} CAD file(s) to "
            f"{package.get('part_number') or package.get('name')}.\n"
            "They are no longer checked as individual BOM files.",
        )
        self.refresh_status()
        self.tabs.setCurrentWidget(self.tab_supplier)

    def unassign_selected_supplier_files(self):
        if not self.perm.can("merge"):
            QMessageBox.warning(self, "Permission denied", "Only Master/Admin can unassign package files.")
            return
        table = self.tab_supplier.table
        dependency_ids = []
        for row in self._checked_rows(table):
            item = table.item(row, 0)
            if item and item.data(Qt.UserRole) is not None:
                dependency_ids.append(int(item.data(Qt.UserRole)))
        if not dependency_ids:
            QMessageBox.information(
                self, "No selection", "Check package files in the Supplier Packages tab."
            )
            return
        confirm = QMessageBox.question(
            self,
            "Unassign dependencies",
            f"Unassign {len(dependency_ids)} selected CAD file(s)? They may appear as orphan files again.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        try:
            count = self.service.remove_cad_dependencies(dependency_ids)
        except Exception as exc:
            QMessageBox.critical(self, "Unassign failed", str(exc))
            return
        QMessageBox.information(self, "Dependencies unassigned", f"Unassigned {count} file(s).")
        self.refresh_status()

    def force_integrate_selected(self):
        """Take selected rows from Working Dir Check where status is Unexpected and allowlist them."""
        if not self.perm.can("merge"):
            QMessageBox.warning(self, "Permission denied", "Only Master/Admin can force integrate parts.")
            return

        table = self.tab_working.table
        rows = self._checked_rows(table)
        if not rows:
            QMessageBox.information(self, "No selection", "Check one or more unexpected parts in the Working Dir Check tab.")
            return

        to_integrate = []
        for r in rows:
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
        rows = self._checked_rows(table)
        if not rows:
            QMessageBox.information(self, "No selection", "Check one or more unexpected parts in the Working Dir Check tab.")
            return

        to_delete: list[str] = []
        for r in rows:
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
        previous = set(self._checked_keys(table))
        incoming = set()
        table.blockSignals(True)
        table.setRowCount(0)
        for r, row in enumerate(rows):
            table.insertRow(r)
            for c, val in enumerate(row):
                cell = QTableWidgetItem(str(val))
                if c == 0:
                    key = f"item:{str(val).strip().casefold()}"
                    incoming.add(key)
                    cell.setData(Qt.UserRole + 1, key)
                    cell.setFlags(cell.flags() | Qt.ItemIsUserCheckable)
                    cell.setCheckState(Qt.Checked if key in previous else Qt.Unchecked)
                table.setItem(r, c, cell)
        self._checked_by_table[table] = previous.intersection(incoming)
        table.blockSignals(False)
        self._apply_table_filter(table)



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

