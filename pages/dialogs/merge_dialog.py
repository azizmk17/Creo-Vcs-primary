from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QTreeWidget, QTreeWidgetItem,
    QPushButton, QHBoxLayout, QMessageBox, QTextEdit
)
from PyQt5.QtCore import Qt

class MergeDialog(QDialog):
    def __init__(self, merge_service,merge_repo, parent=None):
        super().__init__(parent)
        self.merge_service = merge_service
        self.merge_repo = merge_repo
        self.setWindowTitle("Merge to Master")
        self.resize(650, 500)

        layout = QVBoxLayout(self)

        # Info label
        layout.addWidget(QLabel("Select parts or designers to merge:"))

        # Tree widget: Designers → Parts
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Designer", "Part", "Status"])
        self.tree.setColumnWidth(0, 200)
        layout.addWidget(self.tree)

        # Merge message
        layout.addWidget(QLabel("Merge Message:"))
        self.message_edit = QTextEdit()
        self.message_edit.setPlaceholderText("Enter merge message...")
        self.message_edit.setFixedHeight(80)
        layout.addWidget(self.message_edit)

        # Buttons
        btn_layout = QHBoxLayout()
        self.btn_merge_selected = QPushButton("Merge Selected")
        self.btn_cancel = QPushButton("Cancel")
        btn_layout.addWidget(self.btn_merge_selected)
        btn_layout.addWidget(self.btn_cancel)
        layout.addLayout(btn_layout)

        self.btn_merge_selected.clicked.connect(self.merge_selected)
        self.btn_cancel.clicked.connect(self.reject)

        self.load_pending_commits()

    def load_pending_commits(self):
        """Load all pending commits grouped by designer."""
        commits = self.merge_repo.get_pending_commits_grouped()
        self.tree.clear()

        for designer, parts in commits.items():
            designer_item = QTreeWidgetItem([designer, "", ""])
            designer_item.setCheckState(0, 0)  # unchecked
            self.tree.addTopLevelItem(designer_item)

            for part in parts:
                # store part_id inside QTreeWidgetItem for later
                part_item = QTreeWidgetItem(["", part["filename"], part["status"]])
                part_item.setCheckState(0, 0)
                part_item.setData(0, Qt.UserRole, part["id"])  # store commit ID
                designer_item.addChild(part_item)

    def merge_selected(self):
        """Update selected commits to Approved with a message."""
        selected_ids = []

        for i in range(self.tree.topLevelItemCount()):
            designer_item = self.tree.topLevelItem(i)

            if designer_item.checkState(0):  # merge all parts of designer
                for j in range(designer_item.childCount()):
                    part_item = designer_item.child(j)
                    selected_ids.append(part_item.data(0, Qt.UserRole))
            else:
                for j in range(designer_item.childCount()):
                    part_item = designer_item.child(j)
                    if part_item.checkState(0):
                        selected_ids.append(part_item.data(0, Qt.UserRole))

        if not selected_ids:
            QMessageBox.warning(self, "No Selection", "Please select parts or designers to merge.")
            return

        message = self.message_edit.toPlainText().strip()
        if not message:
            QMessageBox.warning(self, "No Message", "Please enter a merge message.")
            return

        # ✅ use IDs directly
        try:
            affected_part_ids = self.merge_service.excute_merge(selected_ids, message)
        except Exception as exc:
            QMessageBox.warning(self, "Merge Blocked", str(exc))
            return
        if affected_part_ids:
            parent = self.parent()
            if hasattr(parent, "_refresh_bom_rows_for_parts"):
                parent._refresh_bom_rows_for_parts(affected_part_ids)
            parent.refresh()
            QMessageBox.information(self, "Merge Complete", f"Merged {selected_ids} commits to Master.")
            self.accept()

