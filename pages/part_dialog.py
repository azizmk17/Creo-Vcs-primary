from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QLineEdit, QComboBox, QHBoxLayout,
    QPushButton, QTextEdit, QDateTimeEdit, QSpinBox, QDialogButtonBox, QFileDialog,
    QDoubleSpinBox
)
from PyQt5.QtCore import QDateTime
import os


class PartDialog(QDialog):
    """Dialog for adding/editing parts (full feature set)"""

    def __init__(self, parent=None, part_data=None, filename=None):
        super().__init__(parent)
        self.setWindowTitle("Add Part" if part_data is None else "Edit Part")
        self.setModal(True)
        self.resize(520, 640)

        self.part_data = part_data or {}
        self.filename = filename
        self.init_ui()
        self.load_data()
        

    def init_ui(self):
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.part_aes_input = QLineEdit()
        self.part_aes_input.setPlaceholderText("e.g. AES-0001")
        self.part_name_input = QLineEdit()
        self.part_name_input.setPlaceholderText("Part display name")
        self.part_type_input = QComboBox()
        self.part_type_input.addItems(["prt", "asm"])
        self.part_number_input = QLineEdit()
        self.part_number_input.setPlaceholderText("Manufacturer part number")
        self.drawing_number_input = QLineEdit()
        self.drawing_number_input.setPlaceholderText("Drawing or DWG ref")

        if self.filename:
            # Filename row (read-only)
            file_h = QHBoxLayout()
            self.filename_input = QLineEdit()
            self.filename_input.setText(self.filename)
            self.filename_input.setReadOnly(True)
            self.filename_browse = QPushButton("Browse")
            self.filename_browse.setObjectName("primary")
            self.filename_browse.setEnabled(False)
            file_h.addWidget(self.filename_input)
            file_h.addWidget(self.filename_browse)
            
        else:
            # Filename row
            file_h = QHBoxLayout()
            self.filename_input = QLineEdit()
            self.filename_input.setPlaceholderText("Select file...")
            self.filename_browse = QPushButton("Browse")
            self.filename_browse.setObjectName("primary")
            self.filename_browse.clicked.connect(lambda: self.browse_file(self.filename_input))
            file_h.addWidget(self.filename_input)
            file_h.addWidget(self.filename_browse)

        # Drawing file row
        dwg_h = QHBoxLayout()
        self.drawing_input = QLineEdit()
        self.drawing_input.setPlaceholderText("Select file...")
        self.drawing_browse = QPushButton("Browse")
        self.drawing_browse.setObjectName("primary")
        self.drawing_browse.clicked.connect(lambda: self.browse_file(self.drawing_input))
        dwg_h.addWidget(self.drawing_input)
        dwg_h.addWidget(self.drawing_browse)

        # PDF metadata row
        pdf_h = QHBoxLayout()
        self.pdf_path_input = QLineEdit()
        self.pdf_path_input.setPlaceholderText("Select PDF file...")
        self.pdf_browse = QPushButton("Browse")
        self.pdf_browse.setObjectName("primary")
        self.pdf_browse.clicked.connect(lambda: self.browse_file_path(self.pdf_path_input, "PDF Files (*.pdf);;All Files (*)"))
        pdf_h.addWidget(self.pdf_path_input)
        pdf_h.addWidget(self.pdf_browse)

        # STEP metadata row
        step_h = QHBoxLayout()
        self.step_path_input = QLineEdit()
        self.step_path_input.setPlaceholderText("Select STEP file...")
        self.step_browse = QPushButton("Browse")
        self.step_browse.setObjectName("primary")
        self.step_browse.clicked.connect(lambda: self.browse_file_path(self.step_path_input, "STEP Files (*.step *.stp);;All Files (*)"))
        step_h.addWidget(self.step_path_input)
        step_h.addWidget(self.step_browse)

        self.material_input = QLineEdit()
        self.material_input.setPlaceholderText("Material (e.g. Aluminum)")

        # weight as double spin
        self.weight_input = QDoubleSpinBox()
        self.weight_input.setRange(0, 1_000_000)
        self.weight_input.setDecimals(3)
        self.weight_input.setSuffix(" g")

        self.quantity_input = QSpinBox()
        self.quantity_input.setRange(1, 1000000)

        self.notes_input = QTextEdit()
        self.notes_input.setMaximumHeight(120)

        self.status_input = QComboBox()
        self.status_input.addItems(["Design", "Released", "Obsolete"])

        self.created_input = QDateTimeEdit(QDateTime.currentDateTime())
        self.created_input.setDisplayFormat("yyyy-MM-dd HH:mm:ss")

        self.modified_input = QDateTimeEdit(QDateTime.currentDateTime())
        self.modified_input.setDisplayFormat("yyyy-MM-dd HH:mm:ss")

        form.addRow("AES Number*:", self.part_aes_input)
        form.addRow("Name*:", self.part_name_input)
        form.addRow("Type:", self.part_type_input)
        form.addRow("Part Number:", self.part_number_input)
        form.addRow("Drawing No:", self.drawing_number_input)
        form.addRow("Filename:", file_h)
        form.addRow("Drawing File:", dwg_h)
        form.addRow("PDF File:", pdf_h)
        form.addRow("STEP File:", step_h)
        form.addRow("Material:", self.material_input)
        form.addRow("Weight:", self.weight_input)
        form.addRow("Quantity:", self.quantity_input)
        form.addRow("Status:", self.status_input)
        form.addRow("Notes:", self.notes_input)
        form.addRow("Created:", self.created_input)
        form.addRow("Modified:", self.modified_input)

        layout.addLayout(form)

        # Buttons
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        ok_button = button_box.button(QDialogButtonBox.Ok)
        cancel_button = button_box.button(QDialogButtonBox.Cancel)
        if ok_button:
            ok_button.setObjectName("primary")
        if cancel_button:
            cancel_button.setObjectName("neutral")

    def browse_file(self, target_widget):
        path, _ = QFileDialog.getOpenFileName(self, "Select file")
        if path:
            #get the filaname only from the full path
            filename = os.path.basename(path)
            

            target_widget.setText(filename)

    def browse_file_path(self, target_widget, file_filter: str = "All Files (*)"):
        path, _ = QFileDialog.getOpenFileName(self, "Select file", "", file_filter)
        if path:
            target_widget.setText(path)

    def load_data(self):
        if not self.part_data:
            return

        self.part_aes_input.setText(self.part_data.get("aes_number", ""))
        self.part_name_input.setText(self.part_data.get("name", ""))
        self.part_type_input.setCurrentText(self.part_data.get("type", "prt"))
        self.part_number_input.setText(self.part_data.get("part_number", ""))
        self.drawing_number_input.setText(self.part_data.get("drawing_number", ""))

        #set filename and drwing lowercase
        selfilename = self.part_data.get("filename", "")
        selfrawingname = self.part_data.get("drawing", "")
        if selfilename:
            self.filename_input.setText(selfilename.lower())
        if selfrawingname:
            self.drawing_input.setText(selfrawingname.lower())
        self.material_input.setText(self.part_data.get("material", ""))

        self.pdf_path_input.setText(self.part_data.get("pdf_path", "") or "")
        self.step_path_input.setText(self.part_data.get("step_path", "") or "")

        # weight
        weight = self.part_data.get("weight", 0)
        if isinstance(weight, str):
            try:
                weight = float(weight)
            except (ValueError, TypeError):
                weight = 0.0
        try:
            self.weight_input.setValue(float(weight))
        except Exception:
            self.weight_input.setValue(0.0)

        # quantity
        quantity = self.part_data.get("quantity", 1)
        if isinstance(quantity, str):
            try:
                quantity = int(quantity)
            except (ValueError, TypeError):
                quantity = 1
        try:
            self.quantity_input.setValue(int(quantity))
        except Exception:
            self.quantity_input.setValue(1)

        self.status_input.setCurrentText(self.part_data.get("status", "Design"))
        self.notes_input.setText(self.part_data.get("notes", ""))

        if "created" in self.part_data:
            try:
                self.created_input.setDateTime(QDateTime.fromString(
                    self.part_data["created"], "yyyy-MM-dd HH:mm:ss"))
            except Exception:
                self.created_input.setDateTime(QDateTime.currentDateTime())
        if "modified" in self.part_data:
            try:
                self.modified_input.setDateTime(QDateTime.fromString(
                    self.part_data["modified"], "yyyy-MM-dd HH:mm:ss"))
            except Exception:
                self.modified_input.setDateTime(QDateTime.currentDateTime())

    def get_data(self):
        return {
            "aes_number": self.part_aes_input.text().strip(),
            "name": self.part_name_input.text().strip(),
            "type": self.part_type_input.currentText(),
            "part_number": self.part_number_input.text().strip(),
            "drawing_number": self.drawing_number_input.text().strip(),
            #filename and drawing to lowercase
            "filename": self.filename_input.text().strip().lower(),
            "drawing": self.drawing_input.text().strip().lower(),
            "material": self.material_input.text().strip(),
            "weight": self.weight_input.value(),
            "quantity": self.quantity_input.value(),
            "notes": self.notes_input.toPlainText().strip(),
            "pdf_path": self.pdf_path_input.text().strip(),
            "step_path": self.step_path_input.text().strip(),
            "status": self.status_input.currentText(),
            "created": self.created_input.dateTime().toString("yyyy-MM-dd HH:mm"),
            "modified": self.modified_input.dateTime().toString("yyyy-MM-dd HH:mm"),
        }
