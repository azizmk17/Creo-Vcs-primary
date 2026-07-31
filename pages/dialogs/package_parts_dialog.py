from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QDialogButtonBox,
    QLabel,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QWidget,
    QSizePolicy,
)
from PyQt5.QtCore import QSize

from core.repositories.bom_repository import BomRepository
from core.session_manager import SessionManager
from pages.dialogs.professional_style import (
    apply_professional_dialog_style,
    make_dialog_header,
)


_DOC_BADGE_STYLE = {
    "ok": ("#EAF3DE", "#3B6D11", "#639922"),
    "outdated": ("#FAEEDA", "#854F0B", "#BA7517"),
    "missing": ("#FCEBEB", "#A32D2D", "#E24B4A"),
    "na": ("#F0F0F0", "#737373", "#AAAAAA"),
}


class PackagePartsDialog(QDialog):
    def __init__(
        self,
        parent=None,
        project_id=None,
        preselected_ids=None,
        title: str = "Select Items",
        subtitle: str = "Build the package scope. Selections remain active while searching and filtering.",
        kicker: str = "DELIVERY PACKAGE",
    ):
        super().__init__(parent)
        self._dialog_title = title or "Select Items"
        self._dialog_subtitle = subtitle or ""
        self._dialog_kicker = kicker or ""
        self.setWindowTitle(self._dialog_title)
        self.setModal(True)
        self.resize(860, 620)
        self.setMinimumSize(720, 520)
        apply_professional_dialog_style(self)

        self.session = SessionManager()
        self.project_id = project_id or self.session.project_id
        self._selected_ids = {int(part_id) for part_id in (preselected_ids or [])}
        self._rebuilding = False

        self.bom_repo = BomRepository()
        self._all_parts = []
        self._doc_status_cache = {}

        self._build_ui()
        self._load_parts()
        self._apply_filter()

    def _build_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(
            make_dialog_header(
                self._dialog_title,
                self._dialog_subtitle,
                kicker=self._dialog_kicker,
            )
        )

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(7)
        root_layout.addWidget(body, 1)

        search_row = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(
            "Find in structure: Item Number, name, AES, asm/prt type, Item type, lifecycle, source, view..."
        )
        self.search_input.textChanged.connect(self._apply_filter)
        search_row.addWidget(self.search_input)

        self.clear_filter_btn = QPushButton("Clear")
        self.clear_filter_btn.clicked.connect(self._clear_filters)
        search_row.addWidget(self.clear_filter_btn)
        layout.addLayout(search_row)

        filter_grid = QGridLayout()
        filter_grid.setHorizontalSpacing(8)
        filter_grid.setVerticalSpacing(5)

        self.type_filter = QComboBox()
        self.type_filter.addItem("All CAD types", "")
        self.type_filter.currentIndexChanged.connect(self._apply_filter)

        self.item_type_filter = QComboBox()
        self.item_type_filter.addItem("All item types", "")
        self.item_type_filter.currentIndexChanged.connect(self._apply_filter)

        self.lifecycle_filter = QComboBox()
        self.lifecycle_filter.addItem("All lifecycle", "")
        self.lifecycle_filter.currentIndexChanged.connect(self._apply_filter)

        self.source_filter = QComboBox()
        self.source_filter.addItem("All sources", "")
        self.source_filter.currentIndexChanged.connect(self._apply_filter)

        self.view_filter = QComboBox()
        self.view_filter.addItem("All views", "")
        self.view_filter.currentIndexChanged.connect(self._apply_filter)

        self.pdf_filter = QComboBox()
        self._populate_doc_filter(self.pdf_filter, "PDF")
        self.pdf_filter.currentIndexChanged.connect(self._apply_filter)

        self.step_filter = QComboBox()
        self._populate_doc_filter(self.step_filter, "STEP")
        self.step_filter.currentIndexChanged.connect(self._apply_filter)

        self.selected_only = QCheckBox("Selected only")
        self.selected_only.toggled.connect(self._apply_filter)
        filter_grid.addWidget(QLabel("Type"), 0, 0)
        filter_grid.addWidget(self.type_filter, 0, 1)
        filter_grid.addWidget(QLabel("Item Type"), 0, 2)
        filter_grid.addWidget(self.item_type_filter, 0, 3)
        filter_grid.addWidget(QLabel("Lifecycle"), 0, 4)
        filter_grid.addWidget(self.lifecycle_filter, 0, 5)
        filter_grid.addWidget(QLabel("View"), 1, 0)
        filter_grid.addWidget(self.view_filter, 1, 1)
        filter_grid.addWidget(QLabel("Source"), 1, 2)
        filter_grid.addWidget(self.source_filter, 1, 3)
        filter_grid.addWidget(QLabel("PDF"), 2, 0)
        filter_grid.addWidget(self.pdf_filter, 2, 1)
        filter_grid.addWidget(QLabel("STEP"), 2, 2)
        filter_grid.addWidget(self.step_filter, 2, 3)
        filter_grid.addWidget(self.selected_only, 2, 4)
        layout.addLayout(filter_grid)

        summary = QFrame()
        summary.setObjectName("professionalCommandBar")
        summary.setStyleSheet(
            "QFrame{background:#f4f7fb;border:1px solid #b7c1ca;border-radius:0;}"
            "QLabel{background:transparent;border:none;}"
        )
        summary_row = QHBoxLayout(summary)
        summary_row.setContentsMargins(10, 7, 10, 7)
        self.selected_count_label = QLabel("0 selected")
        self.selected_count_label.setStyleSheet("font-weight:700;color:#1d4ed8;")
        self.visible_count_label = QLabel("0 shown")
        self.visible_count_label.setStyleSheet("color:#64748b;")
        summary_row.addWidget(self.selected_count_label)
        summary_row.addWidget(self.visible_count_label)
        summary_row.addStretch()
        legend = QLabel("Green: current   Amber: review   Red: missing   Gray: none")
        legend.setStyleSheet("color:#64748b;font-size:9px;")
        summary_row.addWidget(legend)
        layout.addWidget(summary)

        action_row = QHBoxLayout()
        self.select_all_btn = QPushButton("Select All Filtered")
        self.select_all_btn.clicked.connect(self._select_all_visible)
        self.select_none_btn = QPushButton("Clear Filtered")
        self.select_none_btn.clicked.connect(self._select_none_visible)
        self.invert_btn = QPushButton("Invert Filtered")
        self.invert_btn.clicked.connect(self._invert_visible)
        self.clear_all_btn = QPushButton("Clear All")
        self.clear_all_btn.setObjectName("danger")
        self.clear_all_btn.clicked.connect(self._clear_all)
        action_row.addWidget(self.select_all_btn)
        action_row.addWidget(self.select_none_btn)
        action_row.addWidget(self.invert_btn)
        action_row.addStretch()
        action_row.addWidget(self.clear_all_btn)
        layout.addLayout(action_row)

        columns = QFrame()
        columns.setStyleSheet(
            "QFrame{background:#dfe5ea;border:1px solid #aeb8c2;border-radius:0;}"
            "QLabel{background:transparent;border:none;color:#526071;font-size:10px;font-weight:700;}"
        )
        columns_layout = QHBoxLayout(columns)
        columns_layout.setContentsMargins(34, 5, 7, 5)
        columns_layout.setSpacing(8)
        number_header = QLabel("ITEM NUMBER")
        number_header.setFixedWidth(105)
        aes_header = QLabel("AES NUMBER")
        aes_header.setFixedWidth(90)
        name_header = QLabel("ITEM NAME")
        name_header.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        type_header = QLabel("TYPE")
        type_header.setFixedWidth(70)
        item_type_header = QLabel("ITEM TYPE")
        item_type_header.setFixedWidth(115)
        docs_header = QLabel("DOCUMENT STATUS")
        docs_header.setFixedWidth(126)
        columns_layout.addWidget(number_header)
        columns_layout.addWidget(aes_header)
        columns_layout.addWidget(name_header, 1)
        columns_layout.addWidget(type_header)
        columns_layout.addWidget(item_type_header)
        columns_layout.addWidget(docs_header)
        layout.addWidget(columns)

        self.list_widget = QListWidget()
        self.list_widget.setAlternatingRowColors(True)
        self.list_widget.itemChanged.connect(self._on_item_changed)
        self.list_widget.itemDoubleClicked.connect(self._toggle_item)
        layout.addWidget(self.list_widget)

        footer = QFrame()
        footer.setObjectName("professionalFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(8, 6, 8, 6)
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        apply_button = button_box.button(QDialogButtonBox.Ok)
        apply_button.setText("Apply Selection")
        apply_button.setObjectName("primary")
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        footer_layout.addStretch(1)
        footer_layout.addWidget(button_box)
        layout.addWidget(footer)

    def _load_parts(self):
        if not self.project_id:
            self._all_parts = []
            return

        parts = self.bom_repo.get_all(self.project_id)
        rows = []
        for p in parts:
            if getattr(p, "represented_part_id", None) is not None:
                continue
            rows.append(
                {
                    "id": int(p.id),
                    "part_number": (p.part_number or ""),
                    "aes_number": (p.aes_number or ""),
                    "name": (p.name or ""),
                    "type": (p.type or ""),
                    "item_type": (p.item_type or ""),
                    "lifecycle": (p.lifecycle_state or p.status or ""),
                    "source": (p.procurement_source or ""),
                    "view": (p.item_view or ""),
                    "pdf": self._document_status(int(p.id), "pdf"),
                    "step": self._document_status(int(p.id), "step"),
                }
            )

        rows.sort(key=lambda r: (r["part_number"].lower(), r["name"].lower()))
        self._all_parts = rows
        self._populate_dynamic_filter(self.type_filter, rows, "type", "All CAD types")
        self._populate_dynamic_filter(self.item_type_filter, rows, "item_type", "All item types")
        self._populate_dynamic_filter(self.lifecycle_filter, rows, "lifecycle", "All lifecycle")
        self._populate_dynamic_filter(self.source_filter, rows, "source", "All sources")
        self._populate_dynamic_filter(self.view_filter, rows, "view", "All views")
        valid_ids = {row["id"] for row in rows}
        self._selected_ids.intersection_update(valid_ids)

    def _apply_filter(self):
        q = (self.search_input.text() or "").strip().lower()
        part_type = str(self.type_filter.currentData() or "").lower()
        item_type = str(self.item_type_filter.currentData() or "").lower()
        lifecycle = str(self.lifecycle_filter.currentData() or "").lower()
        source = str(self.source_filter.currentData() or "").lower()
        view = str(self.view_filter.currentData() or "").lower()
        pdf_status = str(self.pdf_filter.currentData() or "").lower()
        step_status = str(self.step_filter.currentData() or "").lower()
        selected_only = self.selected_only.isChecked()

        self._rebuilding = True
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for p in self._all_parts:
            label = (
                f"{p['part_number']} | {p['name']} | "
                f"{p['aes_number']} | {p['type']} | {p['item_type']} | {p['lifecycle']} | "
                f"{p['source']} | {p['view']}"
            )
            if q and q not in label.lower():
                continue
            if part_type and p["type"].lower() != part_type:
                continue
            if item_type and p["item_type"].lower() != item_type:
                continue
            if lifecycle and p["lifecycle"].lower() != lifecycle:
                continue
            if source and p["source"].lower() != source:
                continue
            if view and p["view"].lower() != view:
                continue
            if pdf_status and str(p["pdf"].get("kind") or "").lower() != pdf_status:
                continue
            if step_status and str(p["step"].get("kind") or "").lower() != step_status:
                continue
            if selected_only and p["id"] not in self._selected_ids:
                continue

            item = QListWidgetItem(label)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if p["id"] in self._selected_ids else Qt.Unchecked)
            item.setData(Qt.UserRole, p["id"])
            item.setText("")
            item.setSizeHint(QSize(0, 30))
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, self._part_row_widget(p))
        self.list_widget.blockSignals(False)
        self._rebuilding = False
        self._update_summary()

    def _on_item_changed(self, item):
        if self._rebuilding:
            return
        part_id = int(item.data(Qt.UserRole))
        if item.checkState() == Qt.Checked:
            self._selected_ids.add(part_id)
        else:
            self._selected_ids.discard(part_id)
        if self.selected_only.isChecked() and item.checkState() != Qt.Checked:
            self._apply_filter()
        else:
            self._update_summary()

    def _toggle_item(self, item):
        item.setCheckState(Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked)

    def _document_status(self, part_id, doc_key):
        cache_key = (int(part_id), str(doc_key))
        if cache_key in self._doc_status_cache:
            return self._doc_status_cache[cache_key]

        info = {"kind": "na", "tooltip": f"{doc_key.upper()}: unknown"}
        parent = self.parent()
        if parent and hasattr(parent, "_indicator_summary_for_part"):
            try:
                issues = parent._issues_for_part(int(part_id)) if hasattr(parent, "_issues_for_part") else set()
                summary = parent._indicator_summary_for_part(int(part_id), issues) or {}
                doc_info = summary.get(doc_key) or {}
                state = str(doc_info.get("state") or "absent").lower()
                tooltip = str(doc_info.get("tooltip") or info["tooltip"])
                tooltip_lower = tooltip.lower()
                if state in ("ok", "ack"):
                    kind = "ok"
                elif state == "absent":
                    kind = "na"
                elif "missing" in tooltip_lower or "no attachment" in tooltip_lower:
                    kind = "missing"
                else:
                    kind = "outdated"
                info = {"kind": kind, "tooltip": tooltip}
            except Exception:
                pass
        self._doc_status_cache[cache_key] = info
        return info

    def _part_row_widget(self, part):
        row = QWidget()
        row.setStyleSheet("background:transparent;")
        layout = QHBoxLayout(row)
        # Leave room for the QListWidget's native checkbox.
        layout.setContentsMargins(28, 2, 6, 2)
        layout.setSpacing(8)

        number = QLabel(part["part_number"] or "-")
        number.setFixedWidth(105)
        number.setStyleSheet("background:transparent;color:#172033;font-size:11px;font-weight:700;")
        aes = QLabel(part["aes_number"] or "-")
        aes.setFixedWidth(90)
        aes.setStyleSheet("background:transparent;color:#334155;font-size:11px;font-weight:600;")
        name = QLabel(part["name"] or "-")
        name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        name.setStyleSheet("background:transparent;color:#172033;font-size:11px;font-weight:600;")
        name.setToolTip(part["name"] or "")
        part_type = QLabel(part["type"] or "-")
        part_type.setFixedWidth(70)
        part_type.setStyleSheet("background:transparent;color:#64748b;font-size:10px;")
        item_type = QLabel(part["item_type"] or "-")
        item_type.setFixedWidth(115)
        item_type.setStyleSheet("background:transparent;color:#64748b;font-size:10px;")

        layout.addWidget(number)
        layout.addWidget(aes)
        layout.addWidget(name, 1)
        layout.addWidget(part_type)
        layout.addWidget(item_type)
        layout.addWidget(self._document_badge("PDF", part["pdf"]))
        layout.addWidget(self._document_badge("STEP", part["step"]))
        row.setToolTip(
            f"{part['part_number']} — {part['name']}"
            + (f" | AES {part['aes_number']}" if part['aes_number'] else "")
            + f" | {part['type']} | {part['item_type']} | {part['lifecycle']} | {part['source']} | {part['view']}\n"
            f"{part['pdf']['tooltip']}\n{part['step']['tooltip']}"
        )
        return row

    def _document_badge(self, label, status):
        kind = str((status or {}).get("kind") or "na")
        bg, fg, dot = _DOC_BADGE_STYLE.get(kind, _DOC_BADGE_STYLE["na"])
        badge = QLabel(label)
        badge.setFixedSize(59, 20)
        badge.setAlignment(Qt.AlignCenter)
        badge.setToolTip(str((status or {}).get("tooltip") or f"{label}: unknown"))
        badge.setStyleSheet(
            f"background:{bg};color:{fg};border:1px solid {dot};border-radius:0;"
            f"font-size:10px;font-weight:700;"
        )
        return badge

    def _update_summary(self):
        selected = len(self._selected_ids)
        visible = self.list_widget.count()
        total = len(self._all_parts)
        self.selected_count_label.setText(f"{selected} selected")
        self.visible_count_label.setText(f"{visible} filtered / {total} total")
        self.clear_all_btn.setEnabled(selected > 0)
        self.select_all_btn.setEnabled(visible > 0)
        self.select_none_btn.setEnabled(visible > 0)
        self.invert_btn.setEnabled(visible > 0)

    @staticmethod
    def _populate_doc_filter(combo: QComboBox, label: str) -> None:
        combo.addItem(f"All {label}", "")
        combo.addItem("Current / OK", "ok")
        combo.addItem("Needs review", "outdated")
        combo.addItem("Missing", "missing")
        combo.addItem("None", "na")

    @staticmethod
    def _populate_dynamic_filter(combo: QComboBox, rows: list, key: str, all_label: str) -> None:
        current = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(all_label, "")
        values = {
            str(row.get(key) or "").strip()
            for row in rows
            if str(row.get(key) or "").strip()
        }
        for value in sorted(values, key=str.lower):
            combo.addItem(value, value)
        index = combo.findData(current)
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

    def _clear_filters(self):
        self.search_input.clear()
        for combo in (
            self.type_filter,
            self.item_type_filter,
            self.lifecycle_filter,
            self.source_filter,
            self.view_filter,
            self.pdf_filter,
            self.step_filter,
        ):
            combo.setCurrentIndex(0)
        self.selected_only.setChecked(False)
        self._apply_filter()

    def _select_all_visible(self):
        for i in range(self.list_widget.count()):
            part_id = int(self.list_widget.item(i).data(Qt.UserRole))
            self._selected_ids.add(part_id)
        self._apply_filter()

    def _select_none_visible(self):
        visible_ids = {
            int(self.list_widget.item(i).data(Qt.UserRole))
            for i in range(self.list_widget.count())
        }
        self._selected_ids.difference_update(visible_ids)
        self._apply_filter()

    def _invert_visible(self):
        visible_ids = [
            int(self.list_widget.item(i).data(Qt.UserRole))
            for i in range(self.list_widget.count())
        ]
        for part_id in visible_ids:
            if part_id in self._selected_ids:
                self._selected_ids.remove(part_id)
            else:
                self._selected_ids.add(part_id)
        self._apply_filter()

    def _clear_all(self):
        self._selected_ids.clear()
        self._apply_filter()

    def selected_part_ids(self):
        return sorted(self._selected_ids)
