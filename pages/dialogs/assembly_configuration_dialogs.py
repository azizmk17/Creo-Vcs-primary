import os
import uuid

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressDialog,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.services.assembly_configuration_service import ConfigurationCancelled
from pages.dialogs.professional_style import (
    apply_professional_dialog_style,
    make_dialog_header,
    make_section_title,
)
from utils import safe_exists, safe_startfile


CONFIGURATION_ID_ROLE = Qt.UserRole + 801
BOM_ID_ROLE = Qt.UserRole + 802
ITERATION_ID_ROLE = Qt.UserRole + 803
OCCURRENCE_PATH_ROLE = Qt.UserRole + 804


def _apply_dialog_style(dialog: QDialog) -> None:
    apply_professional_dialog_style(dialog)


def _show_error(parent, title: str, message: str) -> None:
    lines = str(message or "Operation failed.").splitlines()
    warning = QMessageBox(parent)
    warning.setIcon(QMessageBox.Warning)
    warning.setWindowTitle(title)
    warning.setText(lines[0])
    if len(lines) > 1:
        warning.setInformativeText("Open Details to review the affected items.")
        warning.setDetailedText("\n".join(lines[1:]))
    warning.setStandardButtons(QMessageBox.Ok)
    warning.exec_()


class _ConfigurationDropTree(QTreeWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.drop_handler = None
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DropOnly)
        self.setDropIndicatorShown(True)

    def dragEnterEvent(self, event):
        source = event.source()
        if source is not None and source.property("nexusBomSource"):
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event):
        source = event.source()
        if source is not None and source.property("nexusBomSource"):
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event):
        source = event.source()
        if source is None or not source.property("nexusBomSource"):
            event.ignore()
            return
        target = self.itemAt(event.pos())
        selected = list(source.selectedItems())
        if callable(self.drop_handler) and selected:
            self.drop_handler(selected, target)
            event.acceptProposedAction()
            return
        event.ignore()


class AssemblyConfigurationEditorDialog(QDialog):
    """Edit one Draft configuration version using project BOM components."""

    def __init__(
        self,
        parent,
        configuration_service,
        project_id: int,
        *,
        root_bom_id=None,
        bom_service=None,
        configuration=None,
    ):
        super().__init__(parent)
        self.configuration_service = configuration_service
        self.project_id = int(project_id)
        self.bom_service = bom_service
        self.configuration = dict(configuration or {})
        self.configuration_id = self.configuration.get("id")
        self.root_bom_id = int(
            self.configuration.get("root_bom_id") or root_bom_id or 0
        )
        self.created_configuration = None
        self.saved_configuration = None
        self._members = []
        self._loading_root_iteration = False

        self.setWindowTitle(
            "Edit Configuration Draft" if self.configuration_id else "Create Configuration"
        )
        self.resize(1240, 760)
        _apply_dialog_style(self)
        self._build_ui()
        self._load_editor()

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(
            make_dialog_header(
                "Edit Configuration Draft" if self.configuration_id else "Create Configuration",
                "Select exact Item iterations and define the controlled assembly configuration.",
                kicker="CONFIGURATION MANAGEMENT",
            )
        )

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(10, 9, 10, 9)
        layout.setSpacing(7)
        root_layout.addWidget(body, 1)

        attribute_panel = QFrame()
        attribute_panel.setObjectName("professionalSection")
        form = QFormLayout(attribute_panel)
        form.setContentsMargins(9, 7, 9, 7)
        form.setSpacing(6)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Example: Prototype housing trial")
        self.name_edit.setReadOnly(bool(self.configuration_id))
        form.addRow("Configuration", self.name_edit)

        self.root_iteration_combo = QComboBox()
        form.addRow("Root assembly iteration", self.root_iteration_combo)

        self.purpose_combo = QComboBox()
        self.purpose_combo.addItems(self.configuration_service.PURPOSES)
        form.addRow("Purpose", self.purpose_combo)

        self.description_edit = QPlainTextEdit()
        self.description_edit.setFixedHeight(54)
        form.addRow("Description", self.description_edit)
        layout.addWidget(attribute_panel)

        splitter = QSplitter(Qt.Horizontal)
        source_panel = QWidget()
        source_layout = QVBoxLayout(source_panel)
        source_layout.setContentsMargins(0, 0, 0, 0)
        source_layout.addWidget(make_section_title("PROJECT ITEM ITERATIONS"))
        source_search_row = QHBoxLayout()
        self.source_search = QLineEdit()
        self.source_search.setPlaceholderText("Search item number, name, or AES...")
        self.source_search.returnPressed.connect(self._load_source_components)
        search_button = QPushButton("Search")
        search_button.clicked.connect(self._load_source_components)
        source_search_row.addWidget(self.source_search, 1)
        source_search_row.addWidget(search_button)
        source_layout.addLayout(source_search_row)

        self.source_tree = QTreeWidget()
        self.source_tree.setProperty("nexusBomSource", True)
        self.source_tree.setColumnCount(5)
        self.source_tree.setHeaderLabels([
            "Name", "Item Number", "AES Number", "Type", "Latest",
        ])
        self.source_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.source_tree.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.source_tree.setDragEnabled(True)
        self.source_tree.setDragDropMode(QAbstractItemView.DragOnly)
        self.source_tree.setAlternatingRowColors(True)
        self.source_tree.setUniformRowHeights(True)
        source_header = self.source_tree.header()
        source_header.setSectionResizeMode(0, QHeaderView.Stretch)
        for column in (1, 2, 3, 4):
            source_header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        source_layout.addWidget(self.source_tree, 1)
        add_button = QPushButton("Add to Selected Parent")
        add_button.clicked.connect(self._add_selected_sources)
        source_layout.addWidget(add_button)

        structure_panel = QWidget()
        structure_layout = QVBoxLayout(structure_panel)
        structure_layout.setContentsMargins(0, 0, 0, 0)
        structure_layout.addWidget(make_section_title("CONFIGURATION STRUCTURE"))
        self.structure_tree = _ConfigurationDropTree()
        self.structure_tree.drop_handler = self._add_source_items
        self.structure_tree.setColumnCount(8)
        self.structure_tree.setHeaderLabels([
            "Name", "Item Number", "AES Number", "Version", "Qty", "Type",
            "Creo File", "Drawing",
        ])
        self.structure_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.structure_tree.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.structure_tree.setAlternatingRowColors(True)
        self.structure_tree.setUniformRowHeights(True)
        structure_header = self.structure_tree.header()
        structure_header.setSectionResizeMode(0, QHeaderView.Stretch)
        structure_header.setSectionResizeMode(6, QHeaderView.Stretch)
        structure_header.setSectionResizeMode(7, QHeaderView.Stretch)
        for column in (1, 2, 3, 4, 5):
            structure_header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        structure_layout.addWidget(self.structure_tree, 1)

        structure_actions = QHBoxLayout()
        change_version = QPushButton("Change Version")
        change_version.clicked.connect(self._change_selected_version)
        quantity_button = QPushButton("Set Quantity")
        quantity_button.clicked.connect(self._set_selected_quantity)
        remove_button = QPushButton("Remove")
        remove_button.setObjectName("danger")
        remove_button.clicked.connect(self._remove_selected_members)
        structure_actions.addWidget(change_version)
        structure_actions.addWidget(quantity_button)
        structure_actions.addWidget(remove_button)
        structure_actions.addStretch(1)
        structure_layout.addLayout(structure_actions)

        splitter.addWidget(source_panel)
        splitter.addWidget(structure_panel)
        splitter.setSizes([440, 760])
        layout.addWidget(splitter, 1)

        footer = QFrame()
        footer.setObjectName("professionalFooter")
        buttons = QHBoxLayout(footer)
        buttons.setContentsMargins(8, 6, 8, 6)
        buttons.setSpacing(6)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        save_button = QPushButton("Save Draft")
        save_button.clicked.connect(self._save_and_close)
        freeze_button = QPushButton("Save and Freeze")
        freeze_button.setObjectName("primary")
        freeze_button.clicked.connect(self._save_and_freeze)
        buttons.addStretch(1)
        buttons.addWidget(cancel_button)
        buttons.addWidget(save_button)
        buttons.addWidget(freeze_button)
        layout.addWidget(footer)

    def _load_editor(self) -> None:
        if self.configuration_id:
            self.name_edit.setText(
                str(self.configuration.get("display_name") or self.configuration.get("name") or "")
            )
            self.purpose_combo.setCurrentText(str(self.configuration.get("purpose") or ""))
            self.description_edit.setPlainText(
                str(self.configuration.get("description") or "")
            )
            self._members = self.configuration_service.list_members(
                int(self.configuration_id), self.project_id
            )
        elif self.bom_service:
            details = self.bom_service.get_part_details(self.root_bom_id) or {}
            identity = str(details.get("part_number") or "").strip()
            default_name = str(details.get("name") or "").strip()
            if identity and default_name:
                self.name_edit.setPlaceholderText(f"{identity} - {default_name} configuration")
        self._load_root_iterations()
        if not self.configuration_id:
            self._load_selected_root_structure()
        else:
            self._refresh_structure_tree()
        self._load_source_components()

    def _load_root_iterations(self) -> None:
        iterations = self.configuration_service.list_component_iterations(
            int(self.root_bom_id)
        )
        selected_iteration_id = None
        if self.configuration_id:
            selected_iteration_id = int(self.configuration.get("root_iteration_id") or 0)
        self._loading_root_iteration = True
        self.root_iteration_combo.clear()
        selected_index = -1
        for iteration in iterations:
            version = str(iteration.get("version_label") or "")
            state = str(iteration.get("state") or "")
            label = f"{version}  |  {state}" if state else version
            self.root_iteration_combo.addItem(label, int(iteration["id"]))
            if selected_iteration_id and int(iteration["id"]) == selected_iteration_id:
                selected_index = self.root_iteration_combo.count() - 1
        if selected_index >= 0:
            self.root_iteration_combo.setCurrentIndex(selected_index)
        self._loading_root_iteration = False
        self.root_iteration_combo.currentIndexChanged.connect(
            self._root_iteration_changed
        )

    def _root_iteration_changed(self, _index) -> None:
        if self._loading_root_iteration:
            return
        if self._members:
            answer = QMessageBox.question(
                self,
                "Change Root Iteration",
                "Replace the current configuration structure with the exact structure "
                "captured by this root assembly iteration?",
                QMessageBox.Yes | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            if answer != QMessageBox.Yes:
                current_id = int(self._members[0].get("iteration_id") or 0)
                index = self.root_iteration_combo.findData(current_id)
                self._loading_root_iteration = True
                if index >= 0:
                    self.root_iteration_combo.setCurrentIndex(index)
                self._loading_root_iteration = False
                return
        self._load_selected_root_structure()

    def _load_selected_root_structure(self) -> None:
        iteration_id = self.root_iteration_combo.currentData()
        if iteration_id is None:
            self._members = []
        else:
            self._members = self.configuration_service.prepare_draft_structure(
                self.project_id, self.root_bom_id, int(iteration_id)
            )
        self._refresh_structure_tree()

    def _load_source_components(self) -> None:
        try:
            components = self.configuration_service.list_available_components(
                self.project_id, self.source_search.text(), 300
            )
        except Exception as exc:
            _show_error(self, "Load BOM Components", str(exc))
            return
        self.source_tree.clear()
        for component in components:
            iteration_id = component.get("iteration_id")
            if iteration_id is None:
                continue
            item = QTreeWidgetItem([
                str(component.get("name") or ""),
                str(component.get("part_number") or ""),
                str(component.get("aes_number") or ""),
                str(component.get("type") or ""),
                str(component.get("version_label") or ""),
            ])
            item.setData(0, BOM_ID_ROLE, int(component["bom_id"]))
            item.setData(0, ITERATION_ID_ROLE, int(iteration_id))
            item.setToolTip(0, "Drag this component into the Configuration BOM.")
            self.source_tree.addTopLevelItem(item)

    def _member_by_path(self) -> dict[str, dict]:
        return {
            str(member.get("occurrence_path") or ""): member
            for member in self._members
        }

    def _refresh_structure_tree(self, select_path=None) -> None:
        self.structure_tree.clear()
        items_by_path = {}
        root_item = None
        for member in self._members:
            path = str(member.get("occurrence_path") or "")
            item = QTreeWidgetItem([
                str(member.get("name") or ""),
                str(member.get("part_number") or ""),
                str(member.get("aes_number") or ""),
                str(member.get("version_label") or ""),
                str(member.get("quantity") or 1),
                str(member.get("type") or ""),
                os.path.basename(str(member.get("filename") or "")),
                os.path.basename(str(member.get("drawing") or "")),
            ])
            item.setData(0, OCCURRENCE_PATH_ROLE, path)
            item.setData(0, BOM_ID_ROLE, int(member.get("bom_id") or 0))
            item.setData(0, ITERATION_ID_ROLE, int(member.get("iteration_id") or 0))
            parent = items_by_path.get(
                str(member.get("parent_occurrence_path") or "")
            )
            if parent is None:
                self.structure_tree.addTopLevelItem(item)
                root_item = root_item or item
            else:
                parent.addChild(item)
            items_by_path[path] = item
        if root_item is not None:
            root_item.setExpanded(True)
        self.structure_tree.expandAll()
        selected = items_by_path.get(str(select_path or ""))
        if selected is not None:
            self.structure_tree.setCurrentItem(selected)

    def _selected_parent_item(self):
        item = self.structure_tree.currentItem()
        return item or self.structure_tree.topLevelItem(0)

    def _add_selected_sources(self) -> None:
        self._add_source_items(
            list(self.source_tree.selectedItems()), self._selected_parent_item()
        )

    def _add_source_items(self, source_items, target_item) -> None:
        if not source_items:
            return
        if target_item is None:
            QMessageBox.information(
                self, "Add Component", "The configuration has no root assembly."
            )
            return
        parent_path = str(target_item.data(0, OCCURRENCE_PATH_ROLE) or "")
        members_by_path = self._member_by_path()
        parent_member = members_by_path.get(parent_path)
        if not parent_member:
            return
        if str(parent_member.get("type") or "").strip().lower() not in {
            "asm", "assembly", "folder"
        }:
            QMessageBox.information(
                self, "Add Component", "Select an assembly as the target parent."
            )
            return

        ancestor_bom_ids = set()
        cursor = parent_member
        while cursor:
            ancestor_bom_ids.add(int(cursor.get("bom_id") or 0))
            cursor = members_by_path.get(
                str(cursor.get("parent_occurrence_path") or "")
            )
        added_path = None
        pending_members = []
        try:
            for source_item in source_items:
                bom_id = int(source_item.data(0, BOM_ID_ROLE) or 0)
                iteration_id = int(source_item.data(0, ITERATION_ID_ROLE) or 0)
                if bom_id in ancestor_bom_ids:
                    raise ValueError(
                        f"Adding {source_item.text(0)} here would create a circular structure."
                    )
                branch = self.configuration_service.get_component_structure(
                    self.project_id, bom_id, iteration_id
                )
                segment = f"custom_{uuid.uuid4().hex[:12]}"
                branch_root_path = f"{parent_path}/{segment}"
                sibling_count = sum(
                    1
                    for member in [*self._members, *pending_members]
                    if str(member.get("parent_occurrence_path") or "") == parent_path
                )
                for branch_member in branch:
                    source_path = str(branch_member.get("occurrence_path") or "root")
                    source_parent = str(
                        branch_member.get("parent_occurrence_path") or ""
                    )
                    mapped = dict(branch_member)
                    mapped["occurrence_path"] = (
                        branch_root_path
                        if source_path == "root"
                        else branch_root_path + source_path[len("root"):]
                    )
                    mapped["parent_occurrence_path"] = (
                        parent_path
                        if source_path == "root"
                        else branch_root_path + source_parent[len("root"):]
                    )
                    if source_path == "root":
                        mapped["quantity"] = 1
                        mapped["position"] = sibling_count + 1
                        mapped["sort_order"] = sibling_count + 1
                    pending_members.append(mapped)
                added_path = branch_root_path
        except Exception as exc:
            _show_error(self, "Add Component", str(exc))
            return
        self._members.extend(pending_members)
        self._refresh_structure_tree(select_path=added_path)

    def _remove_selected_members(self) -> None:
        paths = {
            str(item.data(0, OCCURRENCE_PATH_ROLE) or "")
            for item in self.structure_tree.selectedItems()
        }
        if not paths:
            return
        if "root" in paths:
            QMessageBox.information(
                self, "Remove Component", "The root assembly cannot be removed."
            )
            paths.discard("root")
        if not paths:
            return
        answer = QMessageBox.question(
            self,
            "Remove Component",
            "Remove the selected occurrence(s) and their configuration children?",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            return
        self._members = [
            member
            for member in self._members
            if not any(
                str(member.get("occurrence_path") or "") == path
                or str(member.get("occurrence_path") or "").startswith(f"{path}/")
                for path in paths
            )
        ]
        self._refresh_structure_tree()

    def _change_selected_version(self) -> None:
        item = self.structure_tree.currentItem()
        if item is None:
            return
        path = str(item.data(0, OCCURRENCE_PATH_ROLE) or "")
        member = self._member_by_path().get(path)
        if not member:
            return
        iterations = self.configuration_service.list_component_iterations(
            int(member["bom_id"])
        )
        if not iterations:
            QMessageBox.information(
                self, "Change Version", "This component has no checked-in iteration."
            )
            return
        labels = [
            f"{row.get('version_label') or ''}  |  {row.get('state') or ''}"
            for row in iterations
        ]
        current_iteration = int(member.get("iteration_id") or 0)
        current_index = next(
            (
                index
                for index, row in enumerate(iterations)
                if int(row.get("id") or 0) == current_iteration
            ),
            0,
        )
        selected, accepted = QInputDialog.getItem(
            self, "Change Version", "Exact revision / iteration", labels,
            current_index, False
        )
        if not accepted:
            return
        selected_index = labels.index(selected)
        iteration_id = int(iterations[selected_index]["id"])
        if iteration_id == current_iteration:
            return
        try:
            replacement = self.configuration_service.get_component_structure(
                self.project_id, int(member["bom_id"]), iteration_id
            )[0]
        except Exception as exc:
            _show_error(self, "Change Version", str(exc))
            return
        preserved = {
            key: member.get(key)
            for key in (
                "occurrence_path", "parent_occurrence_path", "usage_id",
                "quantity", "position", "sort_order",
            )
        }
        member.update(replacement)
        member.update(preserved)
        if path == "root":
            index = self.root_iteration_combo.findData(iteration_id)
            self._loading_root_iteration = True
            if index >= 0:
                self.root_iteration_combo.setCurrentIndex(index)
            self._loading_root_iteration = False
        self._refresh_structure_tree(select_path=path)

    def _set_selected_quantity(self) -> None:
        item = self.structure_tree.currentItem()
        if item is None:
            return
        path = str(item.data(0, OCCURRENCE_PATH_ROLE) or "")
        if path == "root":
            QMessageBox.information(
                self, "Set Quantity", "The root assembly quantity is always 1."
            )
            return
        member = self._member_by_path().get(path)
        if not member:
            return
        quantity, accepted = QInputDialog.getInt(
            self,
            "Set Quantity",
            "Quantity",
            int(member.get("quantity") or 1),
            1,
            1000000,
            1,
        )
        if accepted:
            member["quantity"] = int(quantity)
            self._refresh_structure_tree(select_path=path)

    def _save_draft(self):
        if not self._members:
            raise ValueError("The configuration structure is empty.")
        if self.configuration_id:
            saved = self.configuration_service.save_draft(
                int(self.configuration_id),
                self._members,
                purpose=self.purpose_combo.currentText(),
                description=self.description_edit.toPlainText(),
            )
        else:
            iteration_id = self.root_iteration_combo.currentData()
            saved = self.configuration_service.create_configuration(
                project_id=self.project_id,
                root_bom_id=self.root_bom_id,
                root_iteration_id=int(iteration_id),
                name=self.name_edit.text(),
                purpose=self.purpose_combo.currentText(),
                description=self.description_edit.toPlainText(),
                members=self._members,
            )
            self.configuration_id = int(saved["id"])
            self.created_configuration = saved
            self.name_edit.setReadOnly(True)
        self.saved_configuration = saved
        self.configuration = dict(saved)
        return saved

    def _save_and_close(self) -> None:
        try:
            saved = self._save_draft()
        except Exception as exc:
            _show_error(self, "Save Configuration Draft", str(exc))
            return
        QMessageBox.information(
            self,
            "Configuration Draft",
            f"Saved {saved.get('display_name') or ''} v{saved.get('version_number') or 1} "
            "as Draft. No files were copied.",
        )
        self.accept()

    def _run_progress(self, title: str, callback):
        progress = QProgressDialog("Preparing...", "Cancel", 0, 1, self)
        progress.setWindowTitle(title)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)

        def update_progress(current, total, label):
            progress.setMaximum(max(1, int(total)))
            progress.setValue(int(current))
            progress.setLabelText(str(label))
            QApplication.processEvents()

        try:
            return callback(update_progress, progress.wasCanceled)
        finally:
            progress.close()
            progress.deleteLater()
            QApplication.processEvents()

    def _save_and_freeze(self) -> None:
        try:
            saved = self._save_draft()
            frozen = self._run_progress(
                "Freeze Configuration",
                lambda progress_cb, cancel_cb: self.configuration_service.freeze_configuration(
                    int(saved["id"]),
                    progress_cb=progress_cb,
                    cancel_cb=cancel_cb,
                ),
            )
        except ConfigurationCancelled:
            return
        except Exception as exc:
            _show_error(self, "Freeze Configuration", str(exc))
            return
        self.saved_configuration = frozen
        QMessageBox.information(
            self,
            "Freeze Configuration",
            f"Frozen {frozen.get('display_name') or ''} v{frozen.get('version_number') or 1}. "
            "Create a new version to make further changes.",
        )
        self.accept()


class CreateAssemblyConfigurationDialog(AssemblyConfigurationEditorDialog):
    def __init__(
        self,
        parent,
        configuration_service,
        bom_service,
        project_id: int,
        root_bom_id: int,
    ):
        super().__init__(
            parent,
            configuration_service,
            project_id,
            root_bom_id=root_bom_id,
            bom_service=bom_service,
        )


class ManageAssemblyConfigurationsDialog(QDialog):
    def __init__(self, parent, configuration_service, project_id: int):
        super().__init__(parent)
        self.configuration_service = configuration_service
        self.project_id = int(project_id)
        self._configurations = []

        self.setWindowTitle("Manage Configurations")
        self.resize(1240, 720)
        _apply_dialog_style(self)
        self._build_ui()
        self._load_configurations()

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(
            make_dialog_header(
                "Assembly Configurations",
                "Review, version, freeze, and build controlled configuration baselines.",
                kicker="CONFIGURATION MANAGEMENT",
            )
        )

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(10, 9, 10, 9)
        layout.setSpacing(7)
        root_layout.addWidget(body, 1)

        splitter = QSplitter(Qt.Vertical)
        config_panel = QWidget()
        config_layout = QVBoxLayout(config_panel)
        config_layout.setContentsMargins(0, 0, 0, 0)
        config_layout.addWidget(make_section_title("CONFIGURATION VERSIONS"))
        self.configuration_table = QTableWidget()
        self.configuration_table.setColumnCount(11)
        self.configuration_table.setHorizontalHeaderLabels([
            "Name", "Config Version", "State", "Purpose", "Root Assembly",
            "Root Iteration", "Members", "Files", "Created By", "Created", "Frozen",
        ])
        self.configuration_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.configuration_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.configuration_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.configuration_table.setAlternatingRowColors(True)
        self.configuration_table.verticalHeader().setVisible(False)
        self.configuration_table.verticalHeader().setDefaultSectionSize(24)
        header = self.configuration_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        for column in (1, 2, 3, 5, 6, 7, 8, 9, 10):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.configuration_table.itemSelectionChanged.connect(
            self._configuration_selected
        )
        config_layout.addWidget(self.configuration_table)

        member_panel = QWidget()
        member_layout = QVBoxLayout(member_panel)
        member_layout.setContentsMargins(0, 0, 0, 0)
        self.structure_label = make_section_title("CONFIGURATION STRUCTURE")
        member_layout.addWidget(self.structure_label)
        self.member_tree = QTreeWidget()
        self.member_tree.setColumnCount(8)
        self.member_tree.setHeaderLabels([
            "Name", "Item Number", "AES Number", "Version", "Qty", "Type",
            "Creo File", "Drawing",
        ])
        self.member_tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.member_tree.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.member_tree.setAlternatingRowColors(True)
        self.member_tree.setUniformRowHeights(True)
        member_header = self.member_tree.header()
        member_header.setSectionResizeMode(0, QHeaderView.Stretch)
        member_header.setSectionResizeMode(6, QHeaderView.Stretch)
        member_header.setSectionResizeMode(7, QHeaderView.Stretch)
        for column in (1, 2, 3, 4, 5):
            member_header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        member_layout.addWidget(self.member_tree)

        splitter.addWidget(config_panel)
        splitter.addWidget(member_panel)
        splitter.setSizes([300, 370])
        layout.addWidget(splitter, 1)

        action_bar = QFrame()
        action_bar.setObjectName("professionalFooter")
        action_row = QHBoxLayout(action_bar)
        action_row.setContentsMargins(8, 6, 8, 6)
        action_row.setSpacing(6)
        self.edit_button = QPushButton("Edit Draft")
        self.edit_button.clicked.connect(self._edit_selected)
        self.freeze_button = QPushButton("Freeze")
        self.freeze_button.setObjectName("primary")
        self.freeze_button.clicked.connect(self._freeze_selected)
        self.new_version_button = QPushButton("Create New Version")
        self.new_version_button.clicked.connect(self._create_new_version)
        self.build_button = QPushButton("Build Configuration")
        self.build_button.clicked.connect(self._build_selected)
        self.open_folder_button = QPushButton("Open Build Folder")
        self.open_folder_button.clicked.connect(self._open_build_folder)
        self.open_root_button = QPushButton("Open Root CAD")
        self.open_root_button.clicked.connect(self._open_root_cad)
        self.delete_button = QPushButton("Delete Draft")
        self.delete_button.setObjectName("danger")
        self.delete_button.clicked.connect(self._delete_selected)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.reject)
        for button in (
            self.edit_button, self.freeze_button, self.new_version_button,
            self.build_button, self.open_folder_button, self.open_root_button,
            self.delete_button,
        ):
            button.setEnabled(False)
        action_row.addWidget(self.edit_button)
        action_row.addWidget(self.freeze_button)
        action_row.addWidget(self.new_version_button)
        action_row.addWidget(self.build_button)
        action_row.addWidget(self.open_folder_button)
        action_row.addWidget(self.open_root_button)
        action_row.addWidget(self.delete_button)
        action_row.addStretch(1)
        action_row.addWidget(close_button)
        layout.addWidget(action_bar)

    def _selected_configuration_id(self):
        rows = self.configuration_table.selectionModel().selectedRows()
        if not rows:
            return None
        item = self.configuration_table.item(rows[0].row(), 0)
        return item.data(CONFIGURATION_ID_ROLE) if item is not None else None

    def _configuration_by_id(self, configuration_id: int) -> dict:
        return next(
            (
                configuration
                for configuration in self._configurations
                if int(configuration.get("id") or 0) == int(configuration_id)
            ),
            {},
        )

    def _load_configurations(self, select_id=None) -> None:
        try:
            self._configurations = self.configuration_service.list_configurations(
                self.project_id
            )
        except Exception as exc:
            _show_error(self, "Manage Configurations", str(exc))
            return
        self.configuration_table.blockSignals(True)
        try:
            self.configuration_table.setRowCount(len(self._configurations))
            selected_row = -1
            for row_index, configuration in enumerate(self._configurations):
                values = [
                    configuration.get("display_name") or "",
                    f"v{configuration.get('version_number') or 1}",
                    configuration.get("state") or "",
                    configuration.get("purpose") or "",
                    configuration.get("root_name") or "",
                    configuration.get("root_version_label") or "",
                    configuration.get("member_count") or 0,
                    configuration.get("file_count") or 0,
                    configuration.get("created_by_name") or "",
                    configuration.get("created_at") or "",
                    configuration.get("frozen_at") or "",
                ]
                for column, value in enumerate(values):
                    item = QTableWidgetItem(str(value))
                    if column == 0:
                        item.setData(CONFIGURATION_ID_ROLE, int(configuration["id"]))
                    self.configuration_table.setItem(row_index, column, item)
                if select_id is not None and int(configuration["id"]) == int(select_id):
                    selected_row = row_index
            if selected_row < 0 and self._configurations:
                selected_row = 0
            if selected_row >= 0:
                self.configuration_table.selectRow(selected_row)
        finally:
            self.configuration_table.blockSignals(False)
        self._configuration_selected()

    def _configuration_selected(self) -> None:
        configuration_id = self._selected_configuration_id()
        self.member_tree.clear()
        if configuration_id is None:
            for button in (
                self.edit_button, self.freeze_button, self.new_version_button,
                self.build_button, self.open_folder_button, self.open_root_button,
                self.delete_button,
            ):
                button.setEnabled(False)
            return
        configuration = self._configuration_by_id(int(configuration_id))
        state = str(configuration.get("state") or "").strip().lower()
        is_draft = state == "draft"
        is_frozen = state == "frozen"
        series_key = str(configuration.get("series_key") or "")
        latest_version = max(
            (
                int(row.get("version_number") or 1)
                for row in self._configurations
                if str(row.get("series_key") or "") == series_key
            ),
            default=int(configuration.get("version_number") or 1),
        )
        is_latest = int(configuration.get("version_number") or 1) == latest_version
        self.edit_button.setEnabled(is_draft)
        self.freeze_button.setEnabled(is_draft)
        self.new_version_button.setEnabled(is_frozen and is_latest)
        self.build_button.setEnabled(is_frozen)
        self.delete_button.setEnabled(is_draft)
        self.structure_label.setText(
            f"{configuration.get('display_name') or ''} "
            f"v{configuration.get('version_number') or 1} - "
            f"{configuration.get('state') or ''}"
        )
        try:
            members = self.configuration_service.list_members(
                int(configuration_id), self.project_id
            )
        except Exception as exc:
            _show_error(self, "Manage Configurations", str(exc))
            return
        items_by_path = {}
        root_item = None
        for member in members:
            item = QTreeWidgetItem([
                str(member.get("name") or ""),
                str(member.get("part_number") or ""),
                str(member.get("aes_number") or ""),
                str(member.get("version_label") or ""),
                str(member.get("quantity") or 1),
                str(member.get("type") or ""),
                os.path.basename(str(member.get("filename") or "")),
                os.path.basename(str(member.get("drawing") or "")),
            ])
            path = str(member.get("occurrence_path") or "")
            parent = items_by_path.get(
                str(member.get("parent_occurrence_path") or "")
            )
            if parent is None:
                self.member_tree.addTopLevelItem(item)
                root_item = root_item or item
            else:
                parent.addChild(item)
            items_by_path[path] = item
        if root_item is not None:
            root_item.setExpanded(True)
        last_path = str(configuration.get("last_built_path") or "")
        self.open_folder_button.setEnabled(bool(last_path and os.path.isdir(last_path)))
        self.open_root_button.setEnabled(
            bool(self._root_build_path(configuration, members))
        )

    @staticmethod
    def _root_build_path(configuration: dict, members: list[dict]) -> str:
        build_path = str(configuration.get("last_built_path") or "")
        root = next(
            (member for member in members if member.get("occurrence_path") == "root"),
            {},
        )
        filename = os.path.basename(str(root.get("filename") or ""))
        candidate = os.path.join(build_path, filename) if build_path and filename else ""
        return candidate if candidate and safe_exists(candidate) else ""

    def _run_progress(self, title: str, callback):
        progress = QProgressDialog("Preparing...", "Cancel", 0, 1, self)
        progress.setWindowTitle(title)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)

        def update_progress(current, total, label):
            progress.setMaximum(max(1, int(total)))
            progress.setValue(int(current))
            progress.setLabelText(str(label))
            QApplication.processEvents()

        try:
            return callback(update_progress, progress.wasCanceled)
        finally:
            progress.close()
            progress.deleteLater()
            QApplication.processEvents()

    def _edit_selected(self) -> None:
        configuration_id = self._selected_configuration_id()
        if configuration_id is None:
            return
        configuration = self._configuration_by_id(int(configuration_id))
        dialog = AssemblyConfigurationEditorDialog(
            self,
            self.configuration_service,
            self.project_id,
            configuration=configuration,
        )
        if dialog.exec_() == QDialog.Accepted:
            self._load_configurations(select_id=int(configuration_id))

    def _freeze_selected(self) -> None:
        configuration_id = self._selected_configuration_id()
        if configuration_id is None:
            return
        configuration = self._configuration_by_id(int(configuration_id))
        answer = QMessageBox.question(
            self,
            "Freeze Configuration",
            f"Freeze {configuration.get('display_name') or ''} "
            f"v{configuration.get('version_number') or 1}?\n\n"
            "This version will become immutable.",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            frozen = self._run_progress(
                "Freeze Configuration",
                lambda progress_cb, cancel_cb: self.configuration_service.freeze_configuration(
                    int(configuration_id),
                    progress_cb=progress_cb,
                    cancel_cb=cancel_cb,
                ),
            )
        except ConfigurationCancelled:
            return
        except Exception as exc:
            _show_error(self, "Freeze Configuration", str(exc))
            return
        self._load_configurations(select_id=int(configuration_id))
        QMessageBox.information(
            self,
            "Freeze Configuration",
            f"Frozen {frozen.get('display_name') or ''} "
            f"v{frozen.get('version_number') or 1}.",
        )

    def _create_new_version(self) -> None:
        configuration_id = self._selected_configuration_id()
        if configuration_id is None:
            return
        try:
            created = self.configuration_service.create_new_version(
                int(configuration_id)
            )
        except Exception as exc:
            _show_error(self, "Create New Configuration Version", str(exc))
            return
        self._load_configurations(select_id=int(created["id"]))
        dialog = AssemblyConfigurationEditorDialog(
            self,
            self.configuration_service,
            self.project_id,
            configuration=created,
        )
        dialog.exec_()
        self._load_configurations(select_id=int(created["id"]))

    def _build_selected(self) -> None:
        configuration_id = self._selected_configuration_id()
        if configuration_id is None:
            return
        parent_directory = QFileDialog.getExistingDirectory(
            self, "Select Parent Folder for Configuration Workspace"
        )
        if not parent_directory:
            return
        try:
            result = self._run_progress(
                "Build Configuration",
                lambda progress_cb, cancel_cb: self.configuration_service.build_configuration(
                    int(configuration_id),
                    parent_directory,
                    progress_cb=progress_cb,
                    cancel_cb=cancel_cb,
                ),
            )
        except ConfigurationCancelled:
            return
        except Exception as exc:
            _show_error(self, "Build Configuration", str(exc))
            return
        self._load_configurations(select_id=int(configuration_id))
        QMessageBox.information(
            self,
            "Build Configuration",
            f"Built {result['file_count']} exact Creo file(s) in:\n"
            f"{result['target_directory']}",
        )

    def _selected_members(self, configuration_id: int) -> list[dict]:
        try:
            return self.configuration_service.list_members(
                configuration_id, self.project_id
            )
        except Exception:
            return []

    def _open_build_folder(self) -> None:
        configuration_id = self._selected_configuration_id()
        if configuration_id is None:
            return
        configuration = self._configuration_by_id(int(configuration_id))
        path = str(configuration.get("last_built_path") or "")
        if not path or not os.path.isdir(path):
            QMessageBox.warning(
                self, "Open Build Folder", "The last built folder is not available."
            )
            return
        safe_startfile(path)

    def _open_root_cad(self) -> None:
        configuration_id = self._selected_configuration_id()
        if configuration_id is None:
            return
        configuration = self._configuration_by_id(int(configuration_id))
        path = self._root_build_path(
            configuration, self._selected_members(int(configuration_id))
        )
        if not path:
            QMessageBox.warning(
                self, "Open Root CAD", "Build the configuration first."
            )
            return
        safe_startfile(path)

    def _delete_selected(self) -> None:
        configuration_id = self._selected_configuration_id()
        if configuration_id is None:
            return
        configuration = self._configuration_by_id(int(configuration_id))
        answer = QMessageBox.warning(
            self,
            "Delete Configuration Draft",
            f"Delete Draft v{configuration.get('version_number') or 1} of "
            f"'{configuration.get('display_name') or configuration_id}'?\n\n"
            "The BOM and frozen configuration versions will not be changed.",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            self.configuration_service.delete_configuration(int(configuration_id))
        except Exception as exc:
            _show_error(self, "Delete Configuration Draft", str(exc))
            return
        self._load_configurations()
