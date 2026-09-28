"""Background native CAD inspection and explicit review/apply."""

import threading

from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QPlainTextEdit, QProgressBar, QMessageBox, QStyle,
)

from core.services.cad_structure_sync_service import CadStructureSyncService


class _ScanTask(QThread):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, operation, parent):
        super().__init__(parent)
        self.operation = operation

    def run(self):
        try:
            self.completed.emit(self.operation())
        except Exception as exc:
            self.failed.emit(str(exc))


class CadStructureScanDialog(QDialog):
    def __init__(self, db_name, project_id, root_id, actor_id, *, can_manage, can_merge, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Validate CAD Structure")
        self.resize(960, 620)
        self.service = CadStructureSyncService(db_name)
        self.project_id, self.root_id, self.actor_id = project_id, root_id, actor_id
        self.can_manage, self.can_merge = can_manage, can_merge
        self.task = None
        self.result = None
        self.cancel = threading.Event()
        self.applied = False
        self.changed = False
        self.applying = False
        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        self.scan_button = QPushButton("Scan CAD")
        self.scan_button.setIcon(self.style().standardIcon(QStyle.SP_BrowserReload))
        self.scan_button.clicked.connect(self.scan)
        toolbar.addWidget(self.scan_button)
        self.force = QCheckBox("Force fresh scan")
        toolbar.addWidget(self.force)
        toolbar.addStretch()
        self.status = QLabel("Ready")
        toolbar.addWidget(self.status)
        layout.addLayout(toolbar)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Change", "CAD Document", "Related Model", "Before", "After"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 100)
        self.table.setColumnWidth(1, 250)
        self.table.setColumnWidth(2, 250)
        layout.addWidget(self.table, 3)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        layout.addWidget(self.details, 1)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.apply_button = QPushButton("Apply Reviewed CAD Changes")
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self.apply)
        buttons.addWidget(self.apply_button)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.reject)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

    def _start(self, operation, callback):
        self.cancel.clear()
        self.scan_button.setEnabled(False)
        self.apply_button.setEnabled(False)
        self.force.setEnabled(False)
        self.close_button.setText("Close" if self.applying else "Cancel")
        self.close_button.setEnabled(not self.applying)
        self.progress.show()
        self.task = _ScanTask(operation, self)
        self.task.completed.connect(callback)
        self.task.failed.connect(self._failed)
        self.task.finished.connect(self._finished)
        self.task.start()

    def _finished(self):
        self.progress.hide()
        self.scan_button.setEnabled(True)
        self.force.setEnabled(True)
        self.close_button.setText("Close")
        self.close_button.setEnabled(True)
        self.applying = False
        self.apply_button.setEnabled(bool(self.result and not self.applied and self.can_manage
                                          and not self.result["plan"]["problems"] and not self.result["readonly"]))
        self.task.deleteLater()
        self.task = None

    def _failed(self, message):
        self.result = None
        self.status.setText("Not applied")
        self.details.setPlainText(message)

    def scan(self):
        self.result = None
        self.applied = False
        self.table.setRowCount(0)
        self.details.clear()
        self.status.setText("Scanning controlled project CAD...")
        force = self.force.isChecked()
        self._start(lambda: self.service.scan(self.project_id, self.root_id, self.actor_id,
                                               cancel=self.cancel, force=force), self._scanned)

    def _scanned(self, result):
        self.result = result
        plan = result["plan"]
        self.table.setRowCount(len(plan["changes"]))
        for index, change in enumerate(plan["changes"]):
            for column, key in enumerate(("action", "parent", "child", "before", "after")):
                item = QTableWidgetItem(str(change[key]))
                item.setToolTip(str(change[key]))
                self.table.setItem(index, column, item)
        self.status.setText("%d changes / %d conflicts" % (len(plan["changes"]), len(plan["problems"])))
        details = []
        if plan["problems"]:
            details.append("Conflicts:\n" + "\n".join(plan["problems"]))
        if plan.get("warnings"):
            details.append("Warnings:\n" + "\n".join(plan["warnings"]))
        self.details.setPlainText("\n\n".join(details) or
                                  "Scan complete. CAD structure only; EBOM remains unchanged.")

    def apply(self):
        if not self.result:
            return
        if QMessageBox.question(self, "Apply CAD Structure", "Apply this reviewed CAD structure?\n"
                                "New CAD Documents may be registered. EBOM and Item associations are not rebuilt.",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        scan_id = self.result["id"]
        self.applying = True
        self.status.setText("Validating and applying...")
        self._start(lambda: self.service.apply(scan_id, self.actor_id, can_manage=self.can_manage,
                                                can_merge=self.can_merge), self._applied)

    def _applied(self, _plan):
        self.applied = True
        self.changed = True
        self.status.setText("CAD structure applied")
        self.details.setPlainText("CAD structure synchronized. Use Compare CAD to Item Structure before an EBOM build.")

    def reject(self):
        if self.task is not None:
            if not self.applying:
                self.cancel.set()
            self.status.setText("Waiting for the current operation to finish...")
            return
        super().reject()

    def closeEvent(self, event):
        if self.task is not None:
            if not self.applying:
                self.cancel.set()
            event.ignore()
        else:
            event.accept()
