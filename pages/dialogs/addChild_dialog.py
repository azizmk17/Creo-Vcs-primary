from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QPushButton, QTreeWidget, QTreeWidgetItem, QMessageBox
)
from PyQt5.QtCore import Qt

class AddChildDialog(QDialog):
    def __init__(self, main_window):
        super().__init__(main_window)
        self.main_window = main_window
        self.setWindowTitle("Add Child Relation")
        self.setModal(True)

        self.parent_item = None
        self.child_item = None

        layout = QVBoxLayout()
        self.info_label = QLabel("Select the PARENT part from the tree.")
        layout.addWidget(self.info_label)

        # Use the same tree structure as main window (read-only clone)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name", "AES", "Type", "Status"])
        self.tree.setSelectionMode(QTreeWidget.SingleSelection)
        layout.addWidget(self.tree)

        # Copy items from the main tree (no logic duplication)
        self.clone_tree(self.main_window.tree, self.tree)

        # Buttons
        self.confirm_btn = QPushButton("Confirm")
        self.confirm_btn.setEnabled(False)
        self.confirm_btn.clicked.connect(self.confirm_selection)
        layout.addWidget(self.confirm_btn)

        self.setLayout(layout)
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
            self.info_label.setText("Parent selected.\nNow select the child part.")
        elif self.child_item is None:
            if item == self.parent_item:
                QMessageBox.warning(self, "Invalid", "Parent and child cannot be the same part.")
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
            self.info_label.setText("Select the PARENT part again.")
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
