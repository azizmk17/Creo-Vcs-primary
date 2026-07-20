from PyQt5.QtWidgets import (
    QAbstractItemView, QDialog, QFrame, QHeaderView, QVBoxLayout,
    QTreeWidget, QTreeWidgetItem, QPushButton, QHBoxLayout, QMessageBox,
    QTextEdit, QWidget
)
from PyQt5.QtCore import Qt

from pages.dialogs.professional_style import (
    apply_professional_dialog_style,
    make_dialog_header,
    make_section_title,
)


class MergeDialog(QDialog):
    def __init__(self, merge_service,merge_repo, parent=None):
        super().__init__(parent)
        self.merge_service = merge_service
        self.merge_repo = merge_repo
        self.setWindowTitle("Merge to Master")
        self.resize(760, 560)
        self.setMinimumSize(640, 460)
        apply_professional_dialog_style(self)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(
            make_dialog_header(
                "Merge to Master",
                "Review pending designer commits and promote the selected controlled content.",
                kicker="MASTER INTEGRATION",
            )
        )

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(7)
        root_layout.addWidget(body, 1)

        layout.addWidget(make_section_title("PENDING COMMITS"))

        # Tree widget: Designers → Parts
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Designer", "Controlled File", "State"])
        self.tree.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        layout.addWidget(self.tree, 1)

        # Merge message
        layout.addWidget(make_section_title("INTEGRATION COMMENT"))
        self.message_edit = QTextEdit()
        self.message_edit.setPlaceholderText("Required: describe the integrated change set")
        self.message_edit.setFixedHeight(72)
        layout.addWidget(self.message_edit)

        # Command footer
        footer = QFrame()
        footer.setObjectName("professionalFooter")
        btn_layout = QHBoxLayout(footer)
        btn_layout.setContentsMargins(8, 6, 8, 6)
        btn_layout.setSpacing(6)
        self.btn_merge_selected = QPushButton("Merge Selected")
        self.btn_merge_selected.setObjectName("primary")
        self.btn_cancel = QPushButton("Cancel")
        btn_layout.addStretch(1)
        btn_layout.addWidget(self.btn_cancel)
        btn_layout.addWidget(self.btn_merge_selected)
        layout.addWidget(footer)

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
            merge_result = self.merge_service.excute_merge(selected_ids, message)
        except Exception as exc:
            QMessageBox.warning(self, "Merge Blocked", str(exc))
            return
        if isinstance(merge_result, dict):
            affected_part_ids = merge_result.get("affected_part_ids") or []
            affected_cad_document_ids = merge_result.get("affected_cad_document_ids") or []
        else:
            affected_part_ids = merge_result or []
            affected_cad_document_ids = []
        if affected_part_ids or affected_cad_document_ids:
            parent = self.parent()
            if hasattr(parent, "_refresh_pdm_rows_after_merge"):
                parent._refresh_pdm_rows_after_merge(
                    affected_part_ids,
                    affected_cad_document_ids,
                )
            elif hasattr(parent, "_refresh_bom_rows_for_parts"):
                parent._refresh_bom_rows_for_parts(affected_part_ids)
            parent.refresh()
            QMessageBox.information(self, "Merge Complete", f"Merged {selected_ids} commits to Master.")
            self.accept()

