from PyQt5.QtCore import QDateTime, Qt
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.ebom_policy import CAD_CONTROL_MODES, REQUIREMENTS, requires_aes_number
from core.item_policy import (
    ASSEMBLY_MODES,
    DEFAULT_UNITS,
    ITEM_TYPES,
    ITEM_VIEWS,
    PROCUREMENT_SOURCES,
    item_type_defaults,
)


class PartDialog(QDialog):
    """Enterprise Item master editor; CAD associations are managed in Item Structure."""

    ITEM_TYPE_LABELS = {
        "MECHANICAL_PART": "Mechanical Part",
        "SOFTWARE_PART": "Software Part",
        "PURCHASED_PART": "Purchased Part",
        "REFERENCE_PART": "Reference Part",
    }
    ASSEMBLY_MODE_LABELS = {
        "COMPONENT": "Component",
        "SEPARABLE": "Separable",
        "INSEPARABLE": "Inseparable",
    }
    SOURCE_LABELS = {
        "MAKE": "Make",
        "BUY": "Buy",
        "MAKE_OR_BUY": "Make or Buy",
    }
    VIEW_LABELS = {
        "DESIGN": "Design",
        "MANUFACTURING": "Manufacturing",
        "SERVICE": "Service",
    }
    UNIT_LABELS = {
        "EA": "each",
        "KG": "kg",
        "M": "m",
        "MM": "mm",
        "L": "L",
        "SET": "set",
    }

    def __init__(
        self,
        parent=None,
        part_data=None,
        filename=None,
        representation_targets=None,
    ):
        super().__init__(parent)
        self.part_data = dict(part_data or {})
        self.filename = filename
        self.representation_targets = list(representation_targets or [])
        self._represented_part_id = self.part_data.get("represented_part_id")
        self._loading = True

        self.setWindowTitle("New Item" if not self.part_data else "Edit Attributes")
        self.setModal(True)
        self.resize(760, 720)
        self.setMinimumSize(680, 620)
        self._build_ui()
        self._load_data()
        self._loading = False
        self._sync_aes_requirement()

    def _build_ui(self) -> None:
        self.setObjectName("itemMasterDialog")
        self.setStyleSheet(
            """
            QDialog#itemMasterDialog {
                background: #e9edf1;
                color: #1c2733;
                font-family: "Segoe UI";
                font-size: 9pt;
            }
            QFrame#itemDialogHeader {
                background: #263746;
                border: 0;
            }
            QLabel#itemDialogKicker {
                color: #9fc7e8;
                font-size: 8pt;
                font-weight: 700;
                letter-spacing: 1px;
            }
            QLabel#itemDialogTitle {
                color: white;
                font-size: 15pt;
                font-weight: 600;
            }
            QLabel#itemDialogSubtitle {
                color: #d4dde5;
                font-size: 8.5pt;
            }
            QFrame#itemContextStrip {
                background: #d9e0e6;
                border-top: 1px solid #aab5bf;
                border-bottom: 1px solid #aab5bf;
            }
            QLabel#fieldCaption {
                color: #354451;
                font-size: 8pt;
                font-weight: 600;
            }
            QGroupBox {
                background: #f8f9fa;
                border: 1px solid #aeb8c2;
                border-radius: 0;
                margin-top: 19px;
                padding: 10px 10px 8px 10px;
                font-weight: 600;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 0;
                top: 0;
                padding: 4px 10px;
                color: #203243;
                background: #dfe5ea;
                border-right: 1px solid #aeb8c2;
                border-bottom: 1px solid #aeb8c2;
            }
            QLineEdit, QComboBox, QDoubleSpinBox, QTextEdit {
                background: white;
                color: #17222d;
                border: 1px solid #9faab5;
                border-radius: 0;
                min-height: 23px;
                padding: 1px 5px;
                selection-background-color: #2d6f9f;
            }
            QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus, QTextEdit:focus {
                border: 1px solid #246a9b;
            }
            QLineEdit[readOnly="true"] {
                background: #edf0f2;
                color: #4f5c68;
            }
            QLabel#policyNote {
                color: #52616e;
                background: #eef2f5;
                border: 1px solid #c4ccd3;
                padding: 6px 8px;
            }
            QFrame#itemDialogFooter {
                background: #f4f6f8;
                border-top: 1px solid #aeb8c2;
            }
            QPushButton {
                min-width: 84px;
                min-height: 25px;
                padding: 2px 12px;
                border: 1px solid #8f9aa4;
                border-radius: 0;
                background: #e4e8eb;
                color: #1c2733;
            }
            QPushButton:hover { background: #d6dde3; }
            QPushButton#primary {
                background: #246a9b;
                border-color: #1d587f;
                color: white;
                font-weight: 600;
            }
            QPushButton#primary:hover { background: #1f5d88; }
            """
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QFrame()
        header.setObjectName("itemDialogHeader")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(20, 13, 20, 14)
        header_layout.setSpacing(2)
        kicker = QLabel("ITEM MASTER")
        kicker.setObjectName("itemDialogKicker")
        title = QLabel("New Item" if not self.part_data else "Edit Item Attributes")
        title.setObjectName("itemDialogTitle")
        subtitle = QLabel(
            "Create the business Item independently from CAD Documents and delivery content."
            if not self.part_data
            else "Modify controlled Item attributes. Number is the immutable PLM identity."
        )
        subtitle.setObjectName("itemDialogSubtitle")
        header_layout.addWidget(kicker)
        header_layout.addWidget(title)
        header_layout.addWidget(subtitle)
        root.addWidget(header)

        context = QFrame()
        context.setObjectName("itemContextStrip")
        context_layout = QGridLayout(context)
        context_layout.setContentsMargins(20, 9, 20, 9)
        context_layout.setHorizontalSpacing(12)
        context_layout.setVerticalSpacing(3)

        product_caption = QLabel("PRODUCT")
        product_caption.setObjectName("fieldCaption")
        type_caption = QLabel("TYPE *")
        type_caption.setObjectName("fieldCaption")
        number_caption = QLabel("NUMBER")
        number_caption.setObjectName("fieldCaption")
        context_layout.addWidget(product_caption, 0, 0)
        context_layout.addWidget(type_caption, 0, 1)
        context_layout.addWidget(number_caption, 0, 2)

        self.product_input = QLineEdit(self._project_label())
        self.product_input.setReadOnly(True)
        self.item_type_input = QComboBox()
        for value in ITEM_TYPES:
            self.item_type_input.addItem(self.ITEM_TYPE_LABELS[value], value)
        self.part_number_input = QLineEdit()
        self.part_number_input.setReadOnly(True)
        self.part_number_input.setToolTip(
            "PLM Item Number (bom.part_number). Generated at Finish and immutable afterward."
        )
        context_layout.addWidget(self.product_input, 1, 0)
        context_layout.addWidget(self.item_type_input, 1, 1)
        context_layout.addWidget(self.part_number_input, 1, 2)
        context_layout.setColumnStretch(0, 2)
        context_layout.setColumnStretch(1, 1)
        context_layout.setColumnStretch(2, 1)
        root.addWidget(context)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(18, 12, 18, 14)
        body_layout.setSpacing(9)

        attributes = QGroupBox("Set Attributes")
        attributes_form = self._form_layout(attributes)
        self.part_name_input = QLineEdit()
        self.part_name_input.setPlaceholderText("Required Item name")
        self.assembly_mode_input = QComboBox()
        for value in ASSEMBLY_MODES:
            self.assembly_mode_input.addItem(self.ASSEMBLY_MODE_LABELS[value], value)
        self.procurement_source_input = QComboBox()
        for value in PROCUREMENT_SOURCES:
            self.procurement_source_input.addItem(self.SOURCE_LABELS[value], value)
        self.item_view_input = QComboBox()
        for value in ITEM_VIEWS:
            self.item_view_input.addItem(self.VIEW_LABELS[value], value)
        self.default_unit_input = QComboBox()
        for value in DEFAULT_UNITS:
            self.default_unit_input.addItem(self.UNIT_LABELS[value], value)
        self.material_input = QLineEdit()
        self.weight_input = QDoubleSpinBox()
        self.weight_input.setRange(0, 1_000_000)
        self.weight_input.setDecimals(3)
        self.weight_input.setSuffix(" g")
        attributes_form.addRow("Name *", self.part_name_input)
        attributes_form.addRow("Assembly Mode", self.assembly_mode_input)
        attributes_form.addRow("Source", self.procurement_source_input)
        attributes_form.addRow("View", self.item_view_input)
        attributes_form.addRow("Default Unit", self.default_unit_input)
        attributes_form.addRow("Material", self.material_input)
        attributes_form.addRow("Weight", self.weight_input)
        body_layout.addWidget(attributes)

        delivery = QGroupBox("Delivery and Engineering Requirements")
        delivery_form = self._form_layout(delivery)
        self.delivery_input = QComboBox()
        self.delivery_input.addItem("Yes — released EBOM Item", "NORMAL")
        self.delivery_input.addItem("No — excluded from delivery", "EXCLUDE")
        if str(self.part_data.get("default_ebom_behavior") or "").upper() == "FLATTEN":
            self.delivery_input.addItem("Legacy — flatten children in EBOM", "FLATTEN")
        self.part_aes_input = QLineEdit()
        self.part_aes_input.setPlaceholderText("Delivery reference")
        self.aes_number_label = QLabel("AES Number *")
        self.cad_requirement_input = QComboBox()
        self.drawing_requirement_input = QComboBox()
        requirement_labels = {
            "REQUIRED": "Required",
            "OPTIONAL": "Optional",
            "NOT_REQUIRED": "Not required",
        }
        for value in REQUIREMENTS:
            self.cad_requirement_input.addItem(requirement_labels[value], value)
            self.drawing_requirement_input.addItem(requirement_labels[value], value)
        self.cad_control_mode_input = QComboBox()
        control_labels = {
            "CONTROLLED": "Controlled — individual CAD integrity",
            "SUPPLIER_PACKAGE": "Supplier package — black-box control",
        }
        for value in CAD_CONTROL_MODES:
            self.cad_control_mode_input.addItem(control_labels[value], value)
        delivery_form.addRow("Deliverable", self.delivery_input)
        delivery_form.addRow(self.aes_number_label, self.part_aes_input)
        delivery_form.addRow("Native CAD", self.cad_requirement_input)
        delivery_form.addRow("Drawing", self.drawing_requirement_input)
        delivery_form.addRow("CAD Control", self.cad_control_mode_input)
        body_layout.addWidget(delivery)

        notes_group = QGroupBox("Technical Description")
        notes_layout = QVBoxLayout(notes_group)
        notes_layout.setContentsMargins(10, 12, 10, 9)
        self.notes_input = QTextEdit()
        self.notes_input.setMaximumHeight(78)
        self.notes_input.setPlaceholderText("Engineering description or controlled note")
        notes_layout.addWidget(self.notes_input)
        body_layout.addWidget(notes_group)

        association_note = QLabel(
            "CAD Documents, native drawings, PDF and STEP content are associated from the "
            "selected Item in Item Structure. They are not Item master attributes."
        )
        association_note.setObjectName("policyNote")
        association_note.setWordWrap(True)
        body_layout.addWidget(association_note)

        if self._represented_part_id not in (None, "", 0, "0"):
            legacy_note = QLabel(
                "Legacy CAD-representation Item detected. This relationship is read-only here; "
                "new CAD relationships must use CAD–Item Associations in Item Structure."
            )
            legacy_note.setObjectName("policyNote")
            legacy_note.setWordWrap(True)
            body_layout.addWidget(legacy_note)

        body_layout.addStretch(1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        footer = QFrame()
        footer.setObjectName("itemDialogFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(18, 8, 18, 8)
        footer_layout.addStretch(1)
        self.button_box = QDialogButtonBox()
        finish_button = QPushButton("Finish")
        finish_button.setObjectName("primary")
        finish_button.setDefault(True)
        cancel_button = QPushButton("Cancel")
        self.button_box.addButton(finish_button, QDialogButtonBox.AcceptRole)
        self.button_box.addButton(cancel_button, QDialogButtonBox.RejectRole)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        footer_layout.addWidget(self.button_box)
        root.addWidget(footer)

        self.item_type_input.currentIndexChanged.connect(self._apply_item_type_defaults)
        self.delivery_input.currentIndexChanged.connect(self._sync_aes_requirement)

    @staticmethod
    def _form_layout(group: QGroupBox) -> QFormLayout:
        form = QFormLayout(group)
        form.setContentsMargins(10, 13, 10, 9)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(7)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        return form

    def _project_label(self) -> str:
        parent = self.parent()
        try:
            project_id = int(parent.session.project_id)
            project = parent.project_service.get_project_by_id(project_id) or {}
            name = str(project.get("name") or f"Product {project_id}")
            version = str(project.get("version_label") or "").strip()
            return f"{name} / {version}" if version else name
        except Exception:
            try:
                return f"Product {int(parent.session.project_id)}"
            except Exception:
                return "Current Product"

    @staticmethod
    def _set_combo_data(combo: QComboBox, value: str) -> None:
        index = combo.findData(str(value or "").strip().upper())
        if index >= 0:
            combo.setCurrentIndex(index)

    def _apply_item_type_defaults(self, _index: int = 0) -> None:
        if self._loading:
            return
        defaults = item_type_defaults(str(self.item_type_input.currentData()))
        self._set_combo_data(
            self.procurement_source_input, defaults["procurement_source"]
        )
        self._set_combo_data(self.cad_requirement_input, defaults["cad_requirement"])
        self._set_combo_data(
            self.drawing_requirement_input, defaults["drawing_requirement"]
        )
        self._set_combo_data(
            self.delivery_input, "NORMAL" if defaults["deliverable"] else "EXCLUDE"
        )
        self._sync_aes_requirement()

    def _sync_aes_requirement(self, _index: int = 0) -> None:
        required = requires_aes_number(
            self.delivery_input.currentData(), self._represented_part_id
        )
        self.aes_number_label.setText("AES Number *" if required else "AES Number")
        self.part_aes_input.setPlaceholderText(
            "Required delivery reference" if required else "Optional for non-deliverable Item"
        )

    def _load_data(self) -> None:
        number = str(self.part_data.get("part_number") or "").strip()
        self.part_number_input.setText(number or "(Generated on Finish)")
        self._set_combo_data(
            self.item_type_input,
            self.part_data.get("item_type") or "MECHANICAL_PART",
        )
        self.part_name_input.setText(str(self.part_data.get("name") or ""))

        assembly_mode = str(self.part_data.get("assembly_mode") or "").upper()
        legacy_assembly = str(self.part_data.get("type") or "").lower() in {
            "asm", "assembly"
        }
        if not assembly_mode or (assembly_mode == "COMPONENT" and legacy_assembly):
            assembly_mode = (
                "SEPARABLE"
                if legacy_assembly
                else "COMPONENT"
            )
        self._set_combo_data(self.assembly_mode_input, assembly_mode)
        self._set_combo_data(
            self.procurement_source_input,
            self.part_data.get("procurement_source") or "MAKE",
        )
        self._set_combo_data(
            self.item_view_input, self.part_data.get("item_view") or "DESIGN"
        )
        self._set_combo_data(
            self.default_unit_input, self.part_data.get("default_unit") or "EA"
        )
        self.material_input.setText(str(self.part_data.get("material") or ""))
        try:
            self.weight_input.setValue(float(self.part_data.get("weight") or 0))
        except (TypeError, ValueError):
            self.weight_input.setValue(0)

        behavior = str(
            self.part_data.get("default_ebom_behavior") or "NORMAL"
        ).upper()
        self._set_combo_data(self.delivery_input, behavior)
        self.part_aes_input.setText(str(self.part_data.get("aes_number") or ""))
        self._set_combo_data(
            self.cad_requirement_input,
            self.part_data.get("cad_requirement") or "OPTIONAL",
        )
        self._set_combo_data(
            self.drawing_requirement_input,
            self.part_data.get("drawing_requirement") or "OPTIONAL",
        )
        self._set_combo_data(
            self.cad_control_mode_input,
            self.part_data.get("cad_control_mode") or "CONTROLLED",
        )
        self.notes_input.setText(str(self.part_data.get("notes") or ""))

    def accept(self) -> None:
        if not self.part_name_input.text().strip():
            QMessageBox.warning(self, "Item Validation", "Name is required.")
            self.part_name_input.setFocus()
            return
        if requires_aes_number(
            self.delivery_input.currentData(), self._represented_part_id
        ) and not self.part_aes_input.text().strip():
            QMessageBox.warning(
                self,
                "Item Validation",
                "AES Number is required because this Item is for delivery.",
            )
            self.part_aes_input.setFocus()
            return
        super().accept()

    def get_data(self) -> dict:
        now = QDateTime.currentDateTime().toString("yyyy-MM-dd HH:mm")
        assembly_mode = str(self.assembly_mode_input.currentData() or "COMPONENT")
        item_type = str(self.item_type_input.currentData() or "MECHANICAL_PART")
        classification = str(self.part_data.get("classification") or "PHYSICAL")
        if item_type == "REFERENCE_PART":
            classification = "REFERENCE"
        elif classification == "REFERENCE":
            classification = "PHYSICAL"
        return {
            "aes_number": self.part_aes_input.text().strip(),
            "represented_part_id": self._represented_part_id,
            "name": self.part_name_input.text().strip(),
            "type": "asm" if assembly_mode != "COMPONENT" else "prt",
            "part_number": str(self.part_data.get("part_number") or "").strip(),
            "generate_part_number": not bool(
                str(self.part_data.get("part_number") or "").strip()
            ),
            "item_type": item_type,
            "assembly_mode": assembly_mode,
            "procurement_source": str(
                self.procurement_source_input.currentData() or "MAKE"
            ),
            "item_view": str(self.item_view_input.currentData() or "DESIGN"),
            "default_unit": str(self.default_unit_input.currentData() or "EA"),
            "drawing_number": str(self.part_data.get("drawing_number") or ""),
            "classification": classification,
            "cad_control_mode": str(
                self.cad_control_mode_input.currentData() or "CONTROLLED"
            ),
            "default_ebom_behavior": str(
                self.delivery_input.currentData() or "NORMAL"
            ),
            "cad_requirement": str(
                self.cad_requirement_input.currentData() or "OPTIONAL"
            ),
            "drawing_requirement": str(
                self.drawing_requirement_input.currentData() or "OPTIONAL"
            ),
            # Content is deliberately not edited here.  Preserve legacy values;
            # filename is populated only by the commit-page create-and-associate flow.
            "filename": str(
                self.filename or self.part_data.get("filename") or ""
            ).strip().lower(),
            "drawing": str(self.part_data.get("drawing") or "").strip().lower(),
            "pdf_path": str(self.part_data.get("pdf_path") or ""),
            "step_path": str(self.part_data.get("step_path") or ""),
            "material": self.material_input.text().strip(),
            "weight": self.weight_input.value(),
            "notes": self.notes_input.toPlainText().strip(),
            "status": str(self.part_data.get("status") or "Design"),
            "created": str(self.part_data.get("created") or now),
            "modified": now,
        }
