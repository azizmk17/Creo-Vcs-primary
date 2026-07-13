from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QBrush
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
)


LEFT_CHILD_ITERATION_ROLE = Qt.UserRole + 701
RIGHT_CHILD_ITERATION_ROLE = Qt.UserRole + 702


class AssemblyIterationCompareDialog(QDialog):
    """Compare two immutable direct-child configurations of one assembly."""

    def __init__(self, parent, bom_service, assembly_id: int, cad_file_opener=None):
        super().__init__(parent)
        self.bom_service = bom_service
        self.assembly_id = int(assembly_id)
        self.cad_file_opener = cad_file_opener
        self.iterations = list(self.bom_service.list_part_iterations(self.assembly_id) or [])
        self._comparison = {}

        details = self.bom_service.get_part_details(self.assembly_id) or {}
        identity = str(details.get("aes_number") or details.get("part_number") or "").strip()
        name = str(details.get("name") or f"Assembly {self.assembly_id}").strip()
        self.setObjectName("assemblyIterationCompareDialog")
        self.setWindowTitle("Compare Assembly Iterations")
        self.resize(1180, 650)
        self.setStyleSheet(
            """
            QDialog#assemblyIterationCompareDialog {
                background: #f5f7fa;
                color: #172033;
                font-size: 10px;
            }
            QDialog#assemblyIterationCompareDialog QLabel,
            QDialog#assemblyIterationCompareDialog QCheckBox {
                color: #172033;
                background: transparent;
                font-size: 10px;
            }
            QDialog#assemblyIterationCompareDialog QComboBox,
            QDialog#assemblyIterationCompareDialog QToolButton,
            QDialog#assemblyIterationCompareDialog QPushButton {
                color: #172033;
                background: #ffffff;
                border: 1px solid #b8c2cf;
                border-radius: 3px;
                min-height: 22px;
                padding: 2px 6px;
                font-size: 10px;
            }
            QDialog#assemblyIterationCompareDialog QComboBox QAbstractItemView {
                color: #172033;
                background: #ffffff;
                selection-color: #ffffff;
                selection-background-color: #1676d2;
            }
            QDialog#assemblyIterationCompareDialog QTableWidget {
                color: #172033;
                background: #ffffff;
                alternate-background-color: #f7f9fc;
                gridline-color: #d8dee8;
                selection-color: #172033;
                selection-background-color: #cfe5fb;
                border: 1px solid #c7d0dc;
                font-size: 10px;
            }
            QDialog#assemblyIterationCompareDialog QHeaderView::section {
                color: #172033;
                background: #e9edf2;
                border: 0;
                border-right: 1px solid #c7d0dc;
                border-bottom: 1px solid #c7d0dc;
                padding: 4px;
                font-size: 10px;
                font-weight: 600;
            }
            """
        )
        self._build_ui(identity, name)
        self._populate_iteration_selectors()
        self._refresh_comparison()

    @staticmethod
    def _iteration_label(iteration: dict) -> str:
        version = str(iteration.get("version_label") or "").strip() or "Unknown"
        state = str(iteration.get("state") or "").strip()
        created = str(iteration.get("created_at") or "").strip()
        parts = [version]
        if state:
            parts.append(state)
        if created:
            parts.append(created)
        return "  |  ".join(parts)

    def _build_ui(self, identity: str, name: str) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        heading = QLabel(f"{identity} - {name}" if identity else name)
        heading.setStyleSheet("font-weight:700;")
        layout.addWidget(heading)

        selector_row = QHBoxLayout()
        selector_row.addWidget(QLabel("Left iteration"))
        self.left_combo = QComboBox()
        self.left_combo.setMinimumWidth(270)
        selector_row.addWidget(self.left_combo, 1)
        self.swap_button = QToolButton()
        self.swap_button.setText("<>")
        self.swap_button.setToolTip("Swap comparison sides")
        self.swap_button.clicked.connect(self._swap_sides)
        selector_row.addWidget(self.swap_button)
        selector_row.addWidget(QLabel("Right iteration"))
        self.right_combo = QComboBox()
        self.right_combo.setMinimumWidth(270)
        selector_row.addWidget(self.right_combo, 1)
        self.changes_only = QCheckBox("Show changes only")
        self.changes_only.setChecked(True)
        self.changes_only.toggled.connect(self._render_rows)
        selector_row.addWidget(self.changes_only)
        layout.addLayout(selector_row)

        self.summary_label = QLabel()
        self.summary_label.setStyleSheet("color:#465568;font-weight:600;")
        layout.addWidget(self.summary_label)

        self.history_warning = QLabel()
        self.history_warning.setWordWrap(True)
        self.history_warning.setStyleSheet(
            "background:#fff7d6;color:#7a4b00;border:1px solid #e7bf45;padding:5px;"
        )
        self.history_warning.hide()
        layout.addWidget(self.history_warning)

        self.table = QTableWidget()
        headers = [
            "Change", "Name", "AES Number", "Usage", "Left Version", "Right Version",
            "Left Qty", "Right Qty", "Left Position", "Right Position", "Left Creo Files",
            "Right Creo Files",
        ]
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(True)
        header = self.table.horizontalHeader()
        for column in range(len(headers)):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(10, QHeaderView.Stretch)
        header.setSectionResizeMode(11, QHeaderView.Stretch)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_row_context_menu)
        layout.addWidget(self.table, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.left_combo.currentIndexChanged.connect(self._refresh_comparison)
        self.right_combo.currentIndexChanged.connect(self._refresh_comparison)

    def _populate_iteration_selectors(self) -> None:
        for iteration in self.iterations:
            label = self._iteration_label(iteration)
            iteration_id = int(iteration["id"])
            self.left_combo.addItem(label, iteration_id)
            self.right_combo.addItem(label, iteration_id)
        if self.iterations:
            self.right_combo.setCurrentIndex(0)
            self.left_combo.setCurrentIndex(1 if len(self.iterations) > 1 else 0)

    def _swap_sides(self) -> None:
        left_id = self.left_combo.currentData()
        right_id = self.right_combo.currentData()
        left_index = self.left_combo.findData(right_id)
        right_index = self.right_combo.findData(left_id)
        self.left_combo.blockSignals(True)
        self.right_combo.blockSignals(True)
        try:
            if left_index >= 0:
                self.left_combo.setCurrentIndex(left_index)
            if right_index >= 0:
                self.right_combo.setCurrentIndex(right_index)
        finally:
            self.left_combo.blockSignals(False)
            self.right_combo.blockSignals(False)
        self._refresh_comparison()

    def _refresh_comparison(self) -> None:
        left_id = self.left_combo.currentData()
        right_id = self.right_combo.currentData()
        if left_id is None or right_id is None:
            self.table.setRowCount(0)
            return
        try:
            self._comparison = self.bom_service.compare_assembly_iterations(
                self.assembly_id, int(left_id), int(right_id)
            ) or {}
        except Exception as exc:
            self._comparison = {}
            self.table.setRowCount(0)
            QMessageBox.warning(self, "Compare Assembly Iterations", str(exc))
            return
        self._update_summary()
        self._render_rows()

    def _update_summary(self) -> None:
        summary = self._comparison.get("summary") or {}
        left = self._comparison.get("left") or {}
        right = self._comparison.get("right") or {}
        details = []
        for key, label in (
            ("added", "added"),
            ("removed", "removed"),
            ("component_changed", "component"),
            ("version_changed", "version"),
            ("quantity_changed", "quantity"),
            ("order_changed", "order"),
        ):
            count = int(summary.get(key) or 0)
            if count:
                details.append(f"{count} {label}")
        suffix = ", ".join(details) if details else "no configuration differences"
        self.summary_label.setText(
            f"{left.get('version_label', '')} -> {right.get('version_label', '')}: "
            f"{int(summary.get('changed') or 0)} changed occurrence(s); {suffix}."
        )

        left_count = int(self._comparison.get("left_binding_count") or 0)
        right_count = int(self._comparison.get("right_binding_count") or 0)
        missing = []
        if left_count == 0:
            missing.append(str(left.get("version_label") or "Left iteration"))
        if right_count == 0:
            missing.append(str(right.get("version_label") or "Right iteration"))
        if left_count == 0 and right_count == 0:
            self.history_warning.setText(
                "Both selected iterations contain no captured direct-child occurrences."
            )
            self.history_warning.show()
        elif missing:
            self.history_warning.setText(
                "No captured direct-child bindings exist for " + ", ".join(missing) + ". "
                "A pre-migration configuration cannot be reconstructed if it was never stored."
            )
            self.history_warning.show()
        else:
            self.history_warning.hide()

    @staticmethod
    def _cad_text(side: dict | None) -> str:
        if not side:
            return "-"
        values = [
            str(side.get("filename") or "").strip(),
            str(side.get("drawing") or "").strip(),
        ]
        values = [value for value in values if value]
        return "\n".join(values) if values else "Not captured"

    @staticmethod
    def _identity_text(left: dict | None, right: dict | None, field: str) -> str:
        left_value = str((left or {}).get(field) or "").strip()
        right_value = str((right or {}).get(field) or "").strip()
        if left and right and left_value != right_value:
            return f"{left_value or '-'} -> {right_value or '-'}"
        return right_value or left_value

    def _render_rows(self) -> None:
        rows = list(self._comparison.get("rows") or [])
        if self.changes_only.isChecked():
            rows = [row for row in rows if row.get("change_types")]
        self.table.setUpdatesEnabled(False)
        try:
            self.table.setRowCount(len(rows))
            for row_index, row in enumerate(rows):
                left = row.get("left")
                right = row.get("right")
                values = [
                    str(row.get("change") or ""),
                    self._identity_text(left, right, "name"),
                    self._identity_text(left, right, "aes_number"),
                    str(row.get("usage_id") or "Legacy"),
                    str((left or {}).get("child_version") or "-"),
                    str((right or {}).get("child_version") or "-"),
                    str((left or {}).get("quantity") if left else "-"),
                    str((right or {}).get("quantity") if right else "-"),
                    str((left or {}).get("position") if left else "-"),
                    str((right or {}).get("position") if right else "-"),
                    self._cad_text(left),
                    self._cad_text(right),
                ]
                if "added" in (row.get("change_types") or []):
                    background = QColor("#e7f6e9")
                    foreground = QColor("#216e39")
                elif "removed" in (row.get("change_types") or []):
                    background = QColor("#fdeaea")
                    foreground = QColor("#9b1c1c")
                elif row.get("change_types"):
                    background = QColor("#fff4d6")
                    foreground = QColor("#7a4b00")
                else:
                    background = QColor("#ffffff")
                    foreground = QColor("#263445")
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setBackground(QBrush(background))
                    item.setForeground(QBrush(foreground))
                    item.setData(
                        LEFT_CHILD_ITERATION_ROLE,
                        (left or {}).get("child_iteration_id"),
                    )
                    item.setData(
                        RIGHT_CHILD_ITERATION_ROLE,
                        (right or {}).get("child_iteration_id"),
                    )
                    self.table.setItem(row_index, column, item)
                self.table.setRowHeight(row_index, 34 if "\n" in values[10] or "\n" in values[11] else 24)
        finally:
            self.table.setUpdatesEnabled(True)

    def _show_row_context_menu(self, position) -> None:
        item = self.table.itemAt(position)
        if item is None or not callable(self.cad_file_opener):
            return
        row = item.row()
        anchor = self.table.item(row, 0)
        if anchor is None:
            return
        menu = QMenu(self.table)
        for label, role, version_column in (
            ("Left child", LEFT_CHILD_ITERATION_ROLE, 4),
            ("Right child", RIGHT_CHILD_ITERATION_ROLE, 5),
        ):
            iteration_id = anchor.data(role)
            if iteration_id is None:
                continue
            version_item = self.table.item(row, version_column)
            version = str(version_item.text() if version_item else "").strip()
            action = menu.addAction(f"View {label} CAD Files ({version})")
            action.triggered.connect(
                lambda _=False, iid=int(iteration_id), side=label, ver=version:
                self.cad_file_opener(iid, side, ver)
            )
        if menu.actions():
            menu.exec_(self.table.viewport().mapToGlobal(position))
