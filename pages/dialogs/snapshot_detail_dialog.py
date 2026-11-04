import os, json
from PyQt5.QtWidgets import (
    QAbstractItemView, QDialog, QFrame, QHeaderView, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTreeWidget, QTreeWidgetItem, QLineEdit, QFileDialog,
    QMessageBox, QWidget
)

from pages.dialogs.professional_style import (
    apply_professional_dialog_style,
    make_dialog_header,
    make_section_title,
)


class SnapshotDetailDialog(QDialog):
    def __init__(self, snapshot_id, data, working_dir=None):
        super().__init__()
        self.data = dict(data or {})
        data = self.data
        self.setWindowTitle(f"Snapshot #{snapshot_id} Details")
        self.resize(900, 600)
        self.setMinimumSize(720, 480)
        self.working_dir = working_dir
        apply_professional_dialog_style(self)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(
            make_dialog_header(
                str(data.get("snapshot_name") or f"Snapshot #{snapshot_id}"),
                f"Snapshot #{snapshot_id}  |  Created by {data.get('created_by') or '-'}  |  "
                f"{data.get('created_at') or '-'}",
                kicker="CONFIGURATION BASELINE",
            )
        )

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(7)
        root_layout.addWidget(body, 1)

        # === Search bar ===
        search_bar = QFrame()
        search_bar.setObjectName("professionalCommandBar")
        search_layout = QHBoxLayout()
        search_layout.setContentsMargins(8, 6, 8, 6)
        search_bar.setLayout(search_layout)
        search_layout.addWidget(QLabel("Find"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Filter by file name or path")
        self.search_input.textChanged.connect(self.filter_tree)
        search_layout.addWidget(self.search_input, 1)
        layout.addWidget(search_bar)

        # === File Tree ===
        layout.addWidget(make_section_title("SNAPSHOT CONTENT"))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["File / Object", "Size / Priority", "Modified / State", "Path / Checksum"])
        self.tree.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(3, QHeaderView.Stretch)
        layout.addWidget(self.tree, 1)

        self.load_snapshot_content()

        # === Buttons ===
        footer = QFrame()
        footer.setObjectName("professionalFooter")
        btn_layout = QHBoxLayout(footer)
        btn_layout.setContentsMargins(8, 6, 8, 6)
        btn_layout.setSpacing(6)
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
        layout.addWidget(footer)

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
        backup = snapshot_data.get("backup") or {}
        if backup:
            self.tree.addTopLevelItem(QTreeWidgetItem([
                "Backup", str(backup.get("file_count", 0)) + " files",
                str(backup.get("status") or ""), str(backup.get("path") or ""),
            ]))
            removed = backup.get("removed") or []
            cleanup = QTreeWidgetItem(["Cleanup", str(len(removed)) + " files removed",
                                       str(backup.get("cleanup") or ""), ""])
            self.tree.addTopLevelItem(cleanup)
            for name in removed:
                cleanup.addChild(QTreeWidgetItem([str(name), "", "Archived", os.path.join(str(backup.get("path") or ""), name)]))
            item_updates = backup.get("updated_item_files") or []
            if item_updates:
                updates = QTreeWidgetItem(["Current Item file references updated", str(len(item_updates)), "Recorded", ""])
                self.tree.addTopLevelItem(updates)
                for entry in item_updates:
                    updates.addChild(QTreeWidgetItem(["Item " + str(entry.get("item_id")), str(entry.get("field") or ""),
                                                      str(entry.get("from") or ""), str(entry.get("to") or "")]))
            cad_updates = backup.get("updated_cad_files") or []
            if cad_updates:
                updates = QTreeWidgetItem(["Current CAD file references updated", str(len(cad_updates)), "Recorded", ""])
                self.tree.addTopLevelItem(updates)
                for entry in cad_updates:
                    updates.addChild(QTreeWidgetItem(["CAD " + str(entry.get("cad_document_id")), "",
                                                      str(entry.get("from") or ""), str(entry.get("to") or "")]))
            kept = backup.get("skipped") or []
            if kept:
                kept_node = QTreeWidgetItem(["Root Creo files kept", str(len(kept)), "Current, required or unavailable for removal", ""])
                self.tree.addTopLevelItem(kept_node)
                for name in kept:
                    kept_node.addChild(QTreeWidgetItem([str(name), "", "Kept in project", ""]))
            history = backup.get("removed_history") or []
            history_node = QTreeWidgetItem(["Commit / pull-request Creo files", str(len(history)) + " files removed", "Archived", ""])
            self.tree.addTopLevelItem(history_node)
            archived_details = {item.get("relative_path"): item for item in snapshot_data.get("archived_commit_files") or []}
            for path in history:
                commits = (archived_details.get(path) or {}).get("commits") or []
                parts = sorted({int(part) for commit in commits for part in commit.get("part_ids") or []})
                context = "Item IDs: " + ", ".join(str(part) for part in parts) if parts else "Archived"
                history_node.addChild(QTreeWidgetItem([str(path), "", context, os.path.join(str(backup.get("path") or ""), "history", path)]))
            removed_directories = backup.get("removed_directories") or []
            if removed_directories:
                folders = QTreeWidgetItem(["Empty directories removed", str(len(removed_directories)), "Cleanup", ""])
                self.tree.addTopLevelItem(folders)
                for path in removed_directories:
                    folders.addChild(QTreeWidgetItem([str(path), "", "Removed after emptying", ""]))
            skipped = backup.get("skipped_history") or []
            if skipped:
                skipped_node = QTreeWidgetItem(["Historical files kept", str(len(skipped)),
                                                "In use, changed, or still needed", ""])
                self.tree.addTopLevelItem(skipped_node)
                for path in skipped:
                    skipped_node.addChild(QTreeWidgetItem([str(path), "", "Kept in project", ""]))
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
