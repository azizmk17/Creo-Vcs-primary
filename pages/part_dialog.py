from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QLineEdit, QComboBox, QHBoxLayout,
    QPushButton, QTextEdit, QDateTimeEdit, QSpinBox, QDialogButtonBox, QFileDialog,
    QDoubleSpinBox, QCheckBox, QLabel
)
from PyQt5.QtCore import QDateTime
import os
from core.ebom_policy import (
    CLASSIFICATIONS,
    CAD_CONTROL_MODES,
    EBOM_BEHAVIORS,
    REQUIREMENTS,
    recommended_default_behavior,
    recommended_requirements,
    requires_aes_number,
)


class PartDialog(QDialog):
    """Dialog for adding/editing parts (full feature set)"""

    def __init__(self, parent=None, part_data=None, filename=None, representation_targets=None):
        super().__init__(parent)
        self.setWindowTitle("Add Part" if part_data is None else "Edit Part")
        self.setModal(True)
        self.resize(540, 760)

        self.part_data = part_data or {}
        self.filename = filename
        if representation_targets is None:
            try:
                representation_targets = parent.bom_service.list_representation_targets(
                    exclude_id=self.part_data.get("id")
                )
            except Exception:
                representation_targets = []
        self.representation_targets = list(representation_targets or [])
        self.init_ui()
        self.load_data()
        

    def init_ui(self):
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.part_aes_input = QLineEdit()
        self.part_aes_input.setPlaceholderText("e.g. AES-0001")
        self.aes_number_label = QLabel("AES Number*:")
        self.represented_part_input = QComboBox()
        self.represented_part_input.addItem(
            "None — this is the deliverable physical part", None
        )
        for target in self.representation_targets:
            label = f"{target.get('aes_number') or 'No AES'} — {target.get('name') or target.get('id')}"
            self.represented_part_input.addItem(label, int(target["id"]))
        self.represented_part_input.setToolTip(
            "Select the deliverable physical part when this row is only an alternate CAD representation."
        )
        self.part_name_input = QLineEdit()
        self.part_name_input.setPlaceholderText("Part display name")
        self.part_type_input = QComboBox()
        self.part_type_input.addItems(["prt", "asm"])
        self.part_number_input = QLineEdit()
        self.part_number_input.setPlaceholderText("Manufacturer part number")
        self.drawing_number_input = QLineEdit()
        self.drawing_number_input.setPlaceholderText("Drawing or DWG ref")
        self.classification_input = QComboBox()
        self.classification_input.addItems(list(CLASSIFICATIONS))
        self.cad_control_mode_input = QComboBox()
        control_labels = {
            "CONTROLLED": "CONTROLLED — individual BOM/CAD integrity",
            "SUPPLIER_PACKAGE": "SUPPLIER PACKAGE — black-box assembly",
        }
        for mode in CAD_CONTROL_MODES:
            self.cad_control_mode_input.addItem(control_labels[mode], mode)
        self.cad_control_mode_input.setToolTip(
            "A supplier package controls only this assembly and its delivery files; internal CAD dependencies are assigned in Diagnostics."
        )
        self.default_ebom_behavior_input = QComboBox()
        behavior_labels = {
            "NORMAL": "NORMAL — deliver this item",
            "FLATTEN": "FLATTEN — hide item, promote children",
            "EXCLUDE": "EXCLUDE — not for delivery",
        }
        for behavior in EBOM_BEHAVIORS:
            self.default_ebom_behavior_input.addItem(
                behavior_labels[behavior], behavior
            )
        self.default_ebom_behavior_input.setToolTip(
            "FLATTEN and EXCLUDE objects never appear as Released EBOM rows."
        )
        self.not_for_delivery_input = QCheckBox(
            "Not for delivery (exclude this object and all descendants)"
        )
        self.not_for_delivery_input.setToolTip(
            "Stores default EBOM behavior EXCLUDE; the object remains visible in CAD Structure."
        )
        self.cad_requirement_input = QComboBox()
        self.cad_requirement_input.addItems(list(REQUIREMENTS))
        self.cad_requirement_input.setCurrentText("OPTIONAL")
        self.drawing_requirement_input = QComboBox()
        self.drawing_requirement_input.addItems(list(REQUIREMENTS))
        self.drawing_requirement_input.setCurrentText("OPTIONAL")
        self.classification_input.currentTextChanged.connect(
            self._apply_recommended_policy_defaults
        )
        self.default_ebom_behavior_input.currentIndexChanged.connect(
            self._sync_not_for_delivery_checkbox
        )
        self.default_ebom_behavior_input.currentIndexChanged.connect(
            self._sync_aes_requirement
        )
        self.not_for_delivery_input.toggled.connect(
            self._set_not_for_delivery
        )

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

        self.represented_part_input.currentIndexChanged.connect(
            self._sync_representation_controls
        )
        self.cad_control_mode_input.currentIndexChanged.connect(
            self._sync_cad_control_mode
        )

        self.created_input = QDateTimeEdit(QDateTime.currentDateTime())
        self.created_input.setDisplayFormat("yyyy-MM-dd HH:mm:ss")

        self.modified_input = QDateTimeEdit(QDateTime.currentDateTime())
        self.modified_input.setDisplayFormat("yyyy-MM-dd HH:mm:ss")

        form.addRow(self.aes_number_label, self.part_aes_input)
        form.addRow("CAD Representation Of:", self.represented_part_input)
        form.addRow("Name*:", self.part_name_input)
        form.addRow("Type:", self.part_type_input)
        form.addRow("Part Number:", self.part_number_input)
        form.addRow("Drawing No:", self.drawing_number_input)
        form.addRow("Classification:", self.classification_input)
        form.addRow("CAD Control:", self.cad_control_mode_input)
        form.addRow("Default EBOM Behavior:", self.default_ebom_behavior_input)
        form.addRow("Delivery:", self.not_for_delivery_input)
        form.addRow("Native CAD Requirement:", self.cad_requirement_input)
        form.addRow("Drawing Requirement:", self.drawing_requirement_input)
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
        self._sync_aes_requirement()

    def _apply_recommended_policy_defaults(self, classification: str):
        if self.part_data:
            return
        cad_requirement, drawing_requirement = recommended_requirements(classification)
        self.cad_requirement_input.setCurrentText(cad_requirement)
        self.drawing_requirement_input.setCurrentText(drawing_requirement)
        self._set_combo_data(
            self.default_ebom_behavior_input,
            recommended_default_behavior(classification),
        )

    @staticmethod
    def _set_combo_data(combo: QComboBox, value: str) -> None:
        index = combo.findData(str(value or ""))
        if index >= 0:
            combo.setCurrentIndex(index)

    def _sync_not_for_delivery_checkbox(self, _index: int = 0) -> None:
        excluded = self.default_ebom_behavior_input.currentData() == "EXCLUDE"
        blocked = self.not_for_delivery_input.blockSignals(True)
        self.not_for_delivery_input.setChecked(excluded)
        self.not_for_delivery_input.blockSignals(blocked)

    def _set_not_for_delivery(self, checked: bool) -> None:
        behavior = "EXCLUDE" if checked else "NORMAL"
        if checked or self.default_ebom_behavior_input.currentData() == "EXCLUDE":
            self._set_combo_data(self.default_ebom_behavior_input, behavior)

    def _sync_aes_requirement(self, _index: int = 0) -> None:
        required = requires_aes_number(
            self.default_ebom_behavior_input.currentData(),
            self.represented_part_input.currentData(),
        )
        self.aes_number_label.setText("AES Number*:" if required else "AES Number:")
        self.part_aes_input.setPlaceholderText(
            "Required for delivery" if required else "Optional — not delivered"
        )

    def _representation_target(self):
        selected_id = self.represented_part_input.currentData()
        if selected_id is None:
            return None
        for target in self.representation_targets:
            if int(target.get("id") or 0) == int(selected_id):
                return target
        return None

    def _sync_representation_controls(self, _index: int = 0) -> None:
        target = self._representation_target()
        is_representation = target is not None
        if is_representation:
            self.part_aes_input.setText(str(target.get("aes_number") or ""))
            self.classification_input.setCurrentText("CAD_ONLY")
            self._set_combo_data(self.default_ebom_behavior_input, "EXCLUDE")
            self.cad_requirement_input.setCurrentText("REQUIRED")
            self.drawing_requirement_input.setCurrentText("NOT_REQUIRED")
            self._set_combo_data(self.cad_control_mode_input, "CONTROLLED")
            self.drawing_number_input.clear()
            self.drawing_input.clear()
            self.pdf_path_input.clear()
            self.step_path_input.clear()

        self.part_aes_input.setReadOnly(is_representation)
        self._sync_aes_requirement()
        for widget in (
            self.classification_input,
            self.default_ebom_behavior_input,
            self.not_for_delivery_input,
            self.cad_requirement_input,
            self.drawing_requirement_input,
            self.cad_control_mode_input,
        ):
            widget.setEnabled(not is_representation)
        for widget in (
            self.drawing_number_input,
            self.drawing_input,
            self.drawing_browse,
            self.pdf_path_input,
            self.pdf_browse,
            self.step_path_input,
            self.step_browse,
        ):
            widget.setEnabled(not is_representation)

    def _sync_cad_control_mode(self, _index: int = 0) -> None:
        if self.cad_control_mode_input.currentData() != "SUPPLIER_PACKAGE":
            return
        if self.represented_part_input.currentData() is not None:
            self.represented_part_input.setCurrentIndex(0)
        self.classification_input.setCurrentText("PHYSICAL")
        self._set_combo_data(self.default_ebom_behavior_input, "NORMAL")

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
        self.classification_input.setCurrentText(
            self.part_data.get("classification", "PHYSICAL")
        )
        self._set_combo_data(
            self.cad_control_mode_input,
            self.part_data.get("cad_control_mode", "CONTROLLED"),
        )
        self._set_combo_data(
            self.default_ebom_behavior_input,
            self.part_data.get("default_ebom_behavior", "NORMAL"),
        )
        self.cad_requirement_input.setCurrentText(
            self.part_data.get("cad_requirement", "OPTIONAL")
        )
        self.drawing_requirement_input.setCurrentText(
            self.part_data.get("drawing_requirement", "OPTIONAL")
        )

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

        represented_part_id = self.part_data.get("represented_part_id")
        if represented_part_id is not None:
            index = self.represented_part_input.findData(int(represented_part_id))
            if index >= 0:
                self.represented_part_input.setCurrentIndex(index)
        self._sync_representation_controls()

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
            "represented_part_id": self.represented_part_input.currentData(),
            "name": self.part_name_input.text().strip(),
            "type": self.part_type_input.currentText(),
            "part_number": self.part_number_input.text().strip(),
            "drawing_number": self.drawing_number_input.text().strip(),
            "classification": self.classification_input.currentText(),
            "cad_control_mode": self.cad_control_mode_input.currentData(),
            "default_ebom_behavior": self.default_ebom_behavior_input.currentData(),
            "cad_requirement": self.cad_requirement_input.currentText(),
            "drawing_requirement": self.drawing_requirement_input.currentText(),
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
