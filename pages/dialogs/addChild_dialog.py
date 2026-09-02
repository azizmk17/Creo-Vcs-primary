from PyQt5.QtWidgets import (
    QAbstractItemView, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton,
    QTreeWidget, QTreeWidgetItem, QMessageBox, QVBoxLayout, QWidget
)
from PyQt5.QtCore import Qt

from pages.dialogs.professional_style import (
    apply_professional_dialog_style,
    make_dialog_header,
    make_section_title,
    set_banner_style,
)


class AddChildDialog(QDialog):
    def __init__(self, main_window):
        super().__init__(main_window)
        self.main_window = main_window
        self.setWindowTitle("Insert Existing Item")
        self.setModal(True)
        self.resize(780, 580)
        self.setMinimumSize(660, 460)
        apply_professional_dialog_style(self)

        self.parent_item = None
        self.child_item = None

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(
            make_dialog_header(
                "Insert Existing Item",
                "Select a parent occurrence, then select the Item to insert below it.",
                kicker="ITEM STRUCTURE",
            )
        )

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(7)
        root_layout.addWidget(body, 1)

        self.info_label = QLabel("Select the parent Item occurrence from the structure.")
        set_banner_style(self.info_label, "neutral")
        layout.addWidget(self.info_label)

        # Use the same tree structure as main window (read-only clone)
        layout.addWidget(make_section_title("AVAILABLE ITEM STRUCTURE"))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Item", "Number", "Type", "Status"])
        self.tree.setSelectionMode(QTreeWidget.SingleSelection)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        layout.addWidget(self.tree, 1)

        # Copy items from the main tree (no logic duplication)
        self.clone_tree(self.main_window.tree, self.tree)

        # Command footer
        footer = QFrame()
        footer.setObjectName("professionalFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(8, 6, 8, 6)
        footer_layout.setSpacing(6)
        self.confirm_btn = QPushButton("Confirm")
        self.confirm_btn.setObjectName("primary")
        self.confirm_btn.setEnabled(False)
        self.confirm_btn.clicked.connect(self.confirm_selection)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        footer_layout.addStretch(1)
        footer_layout.addWidget(cancel_btn)
        footer_layout.addWidget(self.confirm_btn)
        layout.addWidget(footer)

        self.tree.itemClicked.connect(self.on_item_clicked)

    def clone_tree(self, source_tree, target_tree):
        """Clone the visible structure of the main tree into this dialog."""
        def clone_item(src_item):
            new_item = QTreeWidgetItem([src_item.text(i) for i in range(src_item.columnCount())])
            new_item.setData(0, Qt.UserRole, src_item.data(0, Qt.UserRole))
            for i in range(src_item.childCount()):
                new_item.addChild(clone_item(src_item.child(i)))
            return new_item

        for i in range(source_tree.topLevelItemCount()):
            target_tree.addTopLevelItem(clone_item(source_tree.topLevelItem(i)))

        target_tree.expandAll()

    def on_item_clicked(self, item):
        if self.parent_item is None:
            self.parent_item = item
            self.info_label.setText("Parent selected.\nNow select the child Item.")
        elif self.child_item is None:
            if item == self.parent_item:
                QMessageBox.warning(self, "Invalid Selection", "Parent and child cannot be the same Item.")
                return
            self.child_item = item
            self.info_label.setText(
                f"Parent: {self.parent_item.text(0)}\nChild: {self.child_item.text(0)}\nClick Confirm to continue."
            )
            self.confirm_btn.setEnabled(True)
        else:
            # Reset if user clicks a third time
            self.parent_item = item
            self.child_item = None
            self.info_label.setText("Select the parent Item again.")
            self.confirm_btn.setEnabled(False)

    def confirm_selection(self):
        parent_id = self.parent_item.data(0, Qt.UserRole)
        child_id = self.child_item.data(0, Qt.UserRole)

        try:
            self.main_window.bom_service.add_child_by_id(parent_id, child_id)
            QMessageBox.information(self, "Success", "Child added successfully.")
            self.main_window.load_tree()
            self.close()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to add child: {str(e)}")
