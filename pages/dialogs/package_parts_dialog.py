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
    QWidget,
    QSizePolicy,
)
from PyQt5.QtCore import QSize

from core.repositories.bom_repository import BomRepository
from core.session_manager import SessionManager


_DOC_BADGE_STYLE = {
    "ok": ("#EAF3DE", "#3B6D11", "#639922"),
    "outdated": ("#FAEEDA", "#854F0B", "#BA7517"),
    "missing": ("#FCEBEB", "#A32D2D", "#E24B4A"),
    "na": ("#F0F0F0", "#737373", "#AAAAAA"),
}


class PackagePartsDialog(QDialog):
    def __init__(self, parent=None, project_id=None, preselected_ids=None):
        super().__init__(parent)
        self.setWindowTitle("Select Parts")
        self.setModal(True)
        self.resize(860, 620)
        self.setMinimumSize(720, 520)

        self.session = SessionManager()
        self.project_id = project_id or self.session.project_id
        self._selected_ids = {int(part_id) for part_id in (preselected_ids or [])}
        self._rebuilding = False

        self.bom_repo = BomRepository()
        self._all_parts = []  # list of dicts {id,aes,name,type}
        self._doc_status_cache = {}

        self._build_ui()
        self._load_parts()
        self._apply_filter()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        title = QLabel("Select parts")
        title.setStyleSheet("font-size:16px;font-weight:700;color:#172033;")
        layout.addWidget(title)
        layout.addWidget(QLabel("Selections are preserved while searching and filtering."))

        search_row = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Filter by AES / name / type...")
        self.search_input.textChanged.connect(self._apply_filter)
        search_row.addWidget(self.search_input)

        self.type_filter = QComboBox()
        self.type_filter.addItem("All types", "")
        self.type_filter.currentIndexChanged.connect(self._apply_filter)
        search_row.addWidget(self.type_filter)

        self.selected_only = QCheckBox("Selected only")
        self.selected_only.toggled.connect(self._apply_filter)
        search_row.addWidget(self.selected_only)
        layout.addLayout(search_row)

        summary = QFrame()
        summary.setStyleSheet(
            "QFrame{background:#f4f7fb;border:1px solid #dbe3ec;border-radius:5px;}"
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
        self.select_all_btn = QPushButton("Select Visible")
        self.select_all_btn.clicked.connect(self._select_all_visible)
        self.select_none_btn = QPushButton("Clear Visible")
        self.select_none_btn.clicked.connect(self._select_none_visible)
        self.invert_btn = QPushButton("Invert Visible")
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
            "QFrame{background:#edf1f5;border:1px solid #d8dee6;border-radius:4px;}"
            "QLabel{background:transparent;border:none;color:#526071;font-size:10px;font-weight:700;}"
        )
        columns_layout = QHBoxLayout(columns)
        columns_layout.setContentsMargins(34, 5, 7, 5)
        columns_layout.setSpacing(8)
        aes_header = QLabel("AES / NUMBER")
        aes_header.setFixedWidth(130)
        name_header = QLabel("PART NAME")
        name_header.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        type_header = QLabel("TYPE")
        type_header.setFixedWidth(72)
        docs_header = QLabel("DOCUMENT STATUS")
        docs_header.setFixedWidth(126)
        columns_layout.addWidget(aes_header)
        columns_layout.addWidget(name_header, 1)
        columns_layout.addWidget(type_header)
        columns_layout.addWidget(docs_header)
        layout.addWidget(columns)

        self.list_widget = QListWidget()
        self.list_widget.setAlternatingRowColors(True)
        self.list_widget.itemChanged.connect(self._on_item_changed)
        self.list_widget.itemDoubleClicked.connect(self._toggle_item)
        layout.addWidget(self.list_widget)

        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _load_parts(self):
        if not self.project_id:
            self._all_parts = []
            return

        parts = self.bom_repo.get_all(self.project_id)
        rows = []
        for p in parts:
            rows.append(
                {
                    "id": int(p.id),
                    "aes_number": (p.aes_number or ""),
                    "name": (p.name or ""),
                    "type": (p.type or ""),
                    "pdf": self._document_status(int(p.id), "pdf"),
                    "step": self._document_status(int(p.id), "step"),
                }
            )

        # simple sort: AES then name
        rows.sort(key=lambda r: (r["aes_number"].lower(), r["name"].lower()))
        self._all_parts = rows
        for part_type in sorted({row["type"] for row in rows if row["type"]}, key=str.lower):
            self.type_filter.addItem(part_type, part_type)
        valid_ids = {row["id"] for row in rows}
        self._selected_ids.intersection_update(valid_ids)

    def _apply_filter(self):
        q = (self.search_input.text() or "").strip().lower()
        part_type = str(self.type_filter.currentData() or "").lower()
        selected_only = self.selected_only.isChecked()

        self._rebuilding = True
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for p in self._all_parts:
            label = f"{p['aes_number']} | {p['name']} | {p['type']}"
            if q and q not in label.lower():
                continue
            if part_type and p["type"].lower() != part_type:
                continue
            if selected_only and p["id"] not in self._selected_ids:
                continue

            item = QListWidgetItem(label)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if p["id"] in self._selected_ids else Qt.Unchecked)
            item.setData(Qt.UserRole, p["id"])
            item.setText("")
            item.setSizeHint(QSize(0, 34))
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

        aes = QLabel(part["aes_number"] or "-")
        aes.setFixedWidth(130)
        aes.setStyleSheet("background:transparent;color:#334155;font-size:11px;font-weight:600;")
        name = QLabel(part["name"] or "-")
        name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        name.setStyleSheet("background:transparent;color:#172033;font-size:11px;font-weight:600;")
        name.setToolTip(part["name"] or "")
        part_type = QLabel(part["type"] or "-")
        part_type.setFixedWidth(72)
        part_type.setStyleSheet("background:transparent;color:#64748b;font-size:10px;")

        layout.addWidget(aes)
        layout.addWidget(name, 1)
        layout.addWidget(part_type)
        layout.addWidget(self._document_badge("PDF", part["pdf"]))
        layout.addWidget(self._document_badge("STEP", part["step"]))
        row.setToolTip(
            f"{part['aes_number']} | {part['name']} | {part['type']}\n"
            f"{part['pdf']['tooltip']}\n{part['step']['tooltip']}"
        )
        return row

    def _document_badge(self, label, status):
        kind = str((status or {}).get("kind") or "na")
        bg, fg, dot = _DOC_BADGE_STYLE.get(kind, _DOC_BADGE_STYLE["na"])
        badge = QLabel(label)
        badge.setFixedSize(59, 22)
        badge.setAlignment(Qt.AlignCenter)
        badge.setToolTip(str((status or {}).get("tooltip") or f"{label}: unknown"))
        badge.setStyleSheet(
            f"background:{bg};color:{fg};border:1px solid {dot};border-radius:4px;"
            f"font-size:10px;font-weight:700;"
        )
        return badge

    def _update_summary(self):
        selected = len(self._selected_ids)
        visible = self.list_widget.count()
        total = len(self._all_parts)
        self.selected_count_label.setText(f"{selected} selected")
        self.visible_count_label.setText(f"{visible} shown / {total} total")
        self.clear_all_btn.setEnabled(selected > 0)

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
