import os, json
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTreeWidget, QTreeWidgetItem, QLineEdit, QFileDialog, QMessageBox
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QIcon

class SnapshotDetailDialog(QDialog):
    def __init__(self, snapshot_id, data, working_dir=None):
        super().__init__()
        self.setWindowTitle(f"Snapshot #{snapshot_id} Details")
        self.resize(900, 600)
        self.data = data
        self.working_dir = working_dir

        layout = QVBoxLayout(self)

        # === Snapshot Summary ===
        summary = QLabel(f"""
            <h3>Snapshot: {data['snapshot_name']}</h3>
            <p><b>Created by:</b> {data['created_by']}<br>
            <b>Date:</b> {data['created_at']}</p>
        """)
        layout.addWidget(summary)

        # === Search bar ===
        search_layout = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search file...")
        self.search_input.textChanged.connect(self.filter_tree)
        search_layout.addWidget(self.search_input)
        layout.addLayout(search_layout)

        # === File Tree ===
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["File / Folder", "Size (KB)", "Path"])
        self.tree.setColumnWidth(0, 300)
        layout.addWidget(self.tree)

        self.load_snapshot_content()

        # === Buttons ===
        btn_layout = QHBoxLayout()
        btn_compare = QPushButton("Compare with Working Dir")
        btn_export = QPushButton("Export JSON")
        btn_copy = QPushButton("Copy All Paths")
        btn_close = QPushButton("Close")

        btn_compare.clicked.connect(self.compare_with_working_dir)
        btn_export.clicked.connect(self.export_snapshot)
        btn_copy.clicked.connect(self.copy_paths)
        btn_close.clicked.connect(self.close)

        btn_compare.setObjectName("primary")
        btn_export.setObjectName("neutral")
        btn_copy.setObjectName("neutral")
        btn_close.setObjectName("neutral")
        btn_layout.addWidget(btn_compare)
        btn_layout.addWidget(btn_export)
        btn_layout.addWidget(btn_copy)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_close)
        layout.addLayout(btn_layout)

    # === Load Snapshot Data into Tree ===
    def load_snapshot_content(self):
        import json
        from PyQt5.QtWidgets import QTreeWidgetItem

        self.tree.clear()

        # Parse snapshot data
        try:
            snapshot_data = json.loads(self.data["snapshot_data"])
        except Exception as e:
            print("Failed to load snapshot data:", e)
            snapshot_data = {}

        if not snapshot_data:
            self.tree.addTopLevelItem(QTreeWidgetItem(["(No snapshot data found)"]))
            return

        working_dir = snapshot_data.get("working_dir", "(unknown directory)")
        files = snapshot_data.get("files", [])
        issue_state = snapshot_data.get("issue_state") or {}
        issue_summary = issue_state.get("summary") or {}

        # Add working directory info
        dir_item = QTreeWidgetItem(["Working Directory:", working_dir])
        self.tree.addTopLevelItem(dir_item)
        self.tree.addTopLevelItem(QTreeWidgetItem([""]))  # spacer

        issues_item = QTreeWidgetItem([
            "Engineering Issues",
            f"Open: {int(issue_summary.get('open_count') or 0)}",
            f"Critical: {int(issue_summary.get('critical_count') or 0)}",
            f"Closed: {int(issue_summary.get('closed_count') or 0)}",
        ])
        self.tree.addTopLevelItem(issues_item)
        for issue in issue_state.get("issues", []):
            issues_item.addChild(QTreeWidgetItem([
                issue.get("issue_number", ""),
                issue.get("priority", ""),
                issue.get("status", ""),
                issue.get("title", ""),
            ]))
        self.tree.addTopLevelItem(QTreeWidgetItem([""]))

        # Add header
        header_item = QTreeWidgetItem(["Filename", "Size (KB)", "Modified", "Checksum"])
        self.tree.addTopLevelItem(header_item)

        # Populate rows
        for f in files:
            filename = f.get("filename", "(unknown)")
            size_kb = round(f.get("size", 0) / 1024, 2)
            modified = f.get("modified", "-")
            checksum = f.get("checksum", "-")
            item = QTreeWidgetItem([filename, str(size_kb), modified, checksum])
            self.tree.addTopLevelItem(item)

        # Expand view and resize columns
        self.tree.expandAll()
        for i in range(self.tree.columnCount()):
            self.tree.resizeColumnToContents(i)


    # === Filter/Search ===
    def filter_tree(self, text):
        text = text.lower().strip()
        for i in range(self.tree.topLevelItemCount()):
            self._filter_item(self.tree.topLevelItem(i), text)

    def _filter_item(self, item, text):
        visible = False
        for j in range(item.childCount()):
            child_visible = self._filter_item(item.child(j), text)
            visible = visible or child_visible
        if text in item.text(0).lower() or text in item.text(2).lower():
            visible = True
        item.setHidden(not visible)
        return visible

    # === Copy all file paths ===
    def copy_paths(self):
        from PyQt5.QtWidgets import QApplication
        paths = []
        def collect(item):
            if item.text(2):
                paths.append(item.text(2))
            for i in range(item.childCount()):
                collect(item.child(i))
        for i in range(self.tree.topLevelItemCount()):
            collect(self.tree.topLevelItem(i))
        QApplication.clipboard().setText("\n".join(paths))
        QMessageBox.information(self, "Copied", f"{len(paths)} paths copied to clipboard.")

    # === Export to JSON file ===
    def export_snapshot(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export Snapshot", "", "JSON Files (*.json)")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            json.dump(json.loads(self.data["snapshot_data"]), f, indent=4)
        QMessageBox.information(self, "Exported", f"Snapshot exported to:\n{path}")

    # === Compare with working directory ===
    def compare_with_working_dir(self):
        if not self.working_dir or not os.path.isdir(self.working_dir):
            QMessageBox.warning(self, "Missing Directory", "Working directory not found.")
            return

        missing_files = []
        snapshot_files = json.loads(self.data["snapshot_data"])
        for f in snapshot_files:
            p = f.get("path")
            if p and not os.path.exists(p):
                missing_files.append(p)

        if missing_files:
            msg = "\n".join(missing_files[:30]) + ("\n..." if len(missing_files) > 30 else "")
            QMessageBox.warning(self, "Missing Files",
                                f"{len(missing_files)} files from snapshot not found:\n\n{msg}")
        else:
              QMessageBox.information(self, "Perfect Sync", "All snapshot files exist in the working directory.")
