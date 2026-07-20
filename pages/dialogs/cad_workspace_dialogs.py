from __future__ import annotations

import os

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from core.services.cad_workspace_service import CadWorkspaceService
from utils import safe_startfile


class WorkspaceManagerDialog(QDialog):
    def __init__(self, service: CadWorkspaceService, parent=None):
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("Manage CAD Workspaces")
        self.resize(820, 430)
        layout = QVBoxLayout(self)
        intro = QLabel(
            "CAD workspaces are local to this machine and may contain CAD from several "
            "projects. Active CAD checkouts prevent workspace deletion."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([
            "Workspace", "Active CAD", "Projects", "Machine", "Last used", "Location"
        ])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)
        for column, width in enumerate((190, 80, 70, 130, 150, 280)):
            self.table.setColumnWidth(column, width)
        layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        for text, callback in (
            ("New...", self._create),
            ("Rename...", self._rename),
            ("Open Folder", self._open),
            ("Delete...", self._delete),
        ):
            button = QPushButton(text)
            button.clicked.connect(callback)
            actions.addWidget(button)
        actions.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        actions.addWidget(close)
        layout.addLayout(actions)
        self.refresh()

    def refresh(self):
        rows = self.service.list_workspaces()
        self.table.setRowCount(len(rows))
        for row_index, workspace in enumerate(rows):
            values = (
                workspace.get("name") or "",
                str(workspace.get("checkout_count") or 0),
                str(workspace.get("project_count") or 0),
                workspace.get("machine_id") or "",
                workspace.get("last_used_at") or "",
                workspace.get("path") or "",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.UserRole, workspace.get("id"))
                self.table.setItem(row_index, column, item)
        if rows:
            self.table.selectRow(0)

    def _selected(self):
        row = self.table.currentRow()
        if row < 0 or self.table.item(row, 0) is None:
            return None
        return self.service.get_workspace(self.table.item(row, 0).data(Qt.UserRole))

    def _create(self):
        name, accepted = QInputDialog.getText(self, "New CAD Workspace", "Workspace name:")
        if not accepted:
            return
        try:
            self.service.create_workspace(name)
            self.refresh()
        except Exception as exc:
            QMessageBox.warning(self, "New CAD Workspace", str(exc))

    def _rename(self):
        workspace = self._selected()
        if not workspace:
            return
        name, accepted = QInputDialog.getText(
            self, "Rename CAD Workspace", "Workspace name:",
            QLineEdit.Normal, workspace.get("name") or "",
        )
        if not accepted:
            return
        try:
            self.service.rename_workspace(workspace["id"], name)
            self.refresh()
        except Exception as exc:
            QMessageBox.warning(self, "Rename CAD Workspace", str(exc))

    def _open(self):
        workspace = self._selected()
        if not workspace:
            return
        try:
            safe_startfile(workspace["path"])
        except Exception as exc:
            QMessageBox.warning(self, "Open CAD Workspace", str(exc))

    def _delete(self):
        workspace = self._selected()
        if not workspace:
            return
        active = self.service.pdm_service.repo.list_checked_out_cad_by_workspace(workspace["id"])
        if active:
            QMessageBox.warning(
                self, "Delete CAD Workspace",
                "This workspace owns active CAD checkouts. Check in or undo those CAD "
                "Documents before deleting it.",
            )
            return
        answer = QMessageBox.warning(
            self, "Delete CAD Workspace",
            f'Delete local workspace "{workspace["name"]}"?\n\n'
            "All files in this application-managed workspace will be permanently removed.",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            self.service.delete_workspace(workspace["id"], force=True)
            self.refresh()
        except Exception as exc:
            QMessageBox.warning(self, "Delete CAD Workspace", str(exc))


class WorkspaceSelectionDialog(QDialog):
    def __init__(self, service: CadWorkspaceService, parent=None, *, title="Select CAD Workspace"):
        super().__init__(parent)
        self.service = service
        self.setWindowTitle(title)
        self.resize(560, 190)
        layout = QVBoxLayout(self)
        message = QLabel(
            "Choose the local workspace where the controlled CAD iteration will be copied."
        )
        message.setWordWrap(True)
        layout.addWidget(message)
        form = QFormLayout()
        self.combo = QComboBox()
        form.addRow("Workspace:", self.combo)
        layout.addLayout(form)
        tools = QHBoxLayout()
        create = QPushButton("New Workspace...")
        create.clicked.connect(self._create)
        manage = QPushButton("Manage Workspaces...")
        manage.clicked.connect(self._manage)
        tools.addWidget(create)
        tools.addWidget(manage)
        tools.addStretch(1)
        layout.addLayout(tools)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self._accept_if_valid)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.refresh()

    def refresh(self, select_id=None):
        current = select_id or self.combo.currentData()
        self.combo.clear()
        rows = self.service.list_workspaces()
        for workspace in rows:
            suffix = (
                f" ({workspace.get('checkout_count', 0)} active CAD, "
                f"{workspace.get('project_count', 0)} projects)"
            )
            self.combo.addItem((workspace.get("name") or "Workspace") + suffix, workspace["id"])
        if current:
            index = self.combo.findData(current)
            if index >= 0:
                self.combo.setCurrentIndex(index)
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(bool(rows))

    def _create(self):
        name, accepted = QInputDialog.getText(self, "New CAD Workspace", "Workspace name:")
        if not accepted:
            return
        try:
            workspace = self.service.create_workspace(name)
            self.refresh(workspace["id"])
        except Exception as exc:
            QMessageBox.warning(self, "New CAD Workspace", str(exc))

    def _manage(self):
        WorkspaceManagerDialog(self.service, self).exec_()
        self.refresh()

    def _accept_if_valid(self):
        if self.combo.currentData():
            self.accept()

    def selected_workspace(self):
        value = self.combo.currentData()
        return self.service.get_workspace(value) if value else None

    @classmethod
    def choose(cls, service, parent=None, *, title="Select CAD Workspace"):
        dialog = cls(service, parent, title=title)
        if dialog.exec_() != QDialog.Accepted:
            return None
        return dialog.selected_workspace()


class WorkspaceStagingDialog(QDialog):
    def __init__(
        self,
        service: CadWorkspaceService,
        project_id: int,
        user_id: int,
        parent=None,
        *,
        checkout_callback=None,
    ):
        super().__init__(parent)
        self.service = service
        self.project_id = int(project_id)
        self.user_id = int(user_id)
        self.checkout_callback = checkout_callback
        self.rows = []
        self.setWindowTitle("Stage from CAD Workspace")
        self.resize(980, 570)
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Select modified CAD files from a named local workspace. Files from other "
            "projects or CAD Documents not owned by your checkout cannot be staged."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        workspace_row = QHBoxLayout()
        workspace_row.addWidget(QLabel("Workspace:"))
        self.combo = QComboBox()
        self.combo.currentIndexChanged.connect(self.refresh)
        workspace_row.addWidget(self.combo, 1)
        manage = QPushButton("Manage...")
        manage.clicked.connect(self._manage)
        workspace_row.addWidget(manage)
        layout.addLayout(workspace_row)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(6)
        self.tree.setHeaderLabels([
            "Stage", "CAD file", "Status", "Checkout baseline", "Project", "Local path"
        ])
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectRows)
        header = self.tree.header()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(False)
        for column, width in enumerate((55, 210, 165, 155, 80, 360)):
            self.tree.setColumnWidth(column, width)
        layout.addWidget(self.tree, 1)
        self.detail = QLabel("")
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)
        self.tree.currentItemChanged.connect(
            lambda current, _previous: self.detail.setText(
                str(current.data(0, Qt.UserRole + 1) or "") if current else ""
            )
        )
        actions = QHBoxLayout()
        checkout = QPushButton("Check Out Selected CAD")
        checkout.clicked.connect(self._checkout_selected)
        checkout.setEnabled(callable(checkout_callback))
        actions.addWidget(checkout)
        actions.addStretch(1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Stage Selected")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        actions.addWidget(self.buttons)
        layout.addLayout(actions)
        self._load_workspaces()

    def _load_workspaces(self, selected=None):
        current = selected or self.combo.currentData()
        self.combo.blockSignals(True)
        self.combo.clear()
        for workspace in self.service.list_workspaces():
            self.combo.addItem(workspace.get("name") or "Workspace", workspace["id"])
        if current:
            index = self.combo.findData(current)
            if index >= 0:
                self.combo.setCurrentIndex(index)
        self.combo.blockSignals(False)
        self.refresh()

    def _manage(self):
        WorkspaceManagerDialog(self.service, self).exec_()
        self._load_workspaces()

    def refresh(self):
        self.tree.clear()
        workspace_id = self.combo.currentData()
        if not workspace_id:
            self.rows = []
            return
        try:
            self.rows = self.service.scan_workspace(
                workspace_id, self.project_id, self.user_id
            )
        except Exception as exc:
            self.rows = []
            QMessageBox.warning(self, "CAD Workspace", str(exc))
            return
        style = QApplication.style()
        for row in self.rows:
            item = QTreeWidgetItem([
                "", row["filename"], row["status"].replace("_", " ").title(),
                row.get("baseline_file_name") or "-",
                str(row.get("project_id") or "-"), row["path"],
            ])
            item.setData(0, Qt.UserRole, row)
            item.setData(0, Qt.UserRole + 1, row.get("detail") or "")
            if row.get("selectable"):
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(0, Qt.Checked)
                item.setIcon(2, style.standardIcon(style.SP_DialogApplyButton))
            else:
                item.setCheckState(0, Qt.Unchecked)
                item.setFlags(item.flags() & ~Qt.ItemIsUserCheckable)
                icon = (
                    style.SP_MessageBoxInformation
                    if row.get("status") == "UNCHANGED"
                    else style.SP_MessageBoxWarning
                )
                item.setIcon(2, style.standardIcon(icon))
            self.tree.addTopLevelItem(item)

    def _checkout_selected(self):
        item = self.tree.currentItem()
        row = item.data(0, Qt.UserRole) if item else None
        if not row or row.get("status") != "NOT_CHECKED_OUT":
            QMessageBox.information(
                self, "CAD Checkout", "Select a CAD row marked Not Checked Out."
            )
            return
        try:
            self.checkout_callback(row, self.service.get_workspace(self.combo.currentData()))
            self.refresh()
        except Exception as exc:
            QMessageBox.warning(self, "CAD Checkout", str(exc))

    def selected_rows(self):
        selected = []
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            row = item.data(0, Qt.UserRole) or {}
            if row.get("selectable") and item.checkState(0) == Qt.Checked:
                selected.append(dict(row))
        return selected
