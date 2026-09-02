from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QBrush, QFont
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)


DIFF_ROLE = Qt.UserRole + 501
SIDE_ROLE = Qt.UserRole + 502
NODE_ROLE = Qt.UserRole + 503


@dataclass
class ComparePair:
    left: dict[str, Any] | None
    right: dict[str, Any] | None
    status: str
    children: list["ComparePair"]
    attr_diffs: list[tuple[str, Any, Any]]
    usage_diffs: list[tuple[str, Any, Any]]


class WindchillCompareSetupDialog(QDialog):
    def __init__(
        self,
        parent,
        bom_service,
        project_service,
        project_id: int,
        initial_part_id: int | None = None,
        whole_bom: bool = False,
    ):
        super().__init__(parent)
        self.bom_service = bom_service
        self.project_service = project_service
        self.project_id = int(project_id)
        self.initial_part_id = int(initial_part_id) if initial_part_id else None
        self.whole_bom = bool(whole_bom)
        self._user_id = getattr(getattr(parent, "session", None), "user_id", None)
        self._current_project = self.project_service.get_project_by_id(self.project_id) or {}
        self._versions = self._load_project_versions()
        self._project_by_id = {int(p["id"]): p for p in self._versions if p.get("id") is not None}
        self._syncing_version_widgets = False
        self._source_part = self._get_part(self.project_id, self.initial_part_id) if self.initial_part_id else None
        self.setWindowTitle("Compare to Part Structure")
        self.resize(760, 380)
        self._init_ui()

    def _load_project_versions(self) -> list[dict[str, Any]]:
        current = dict(self._current_project or {})
        if not current:
            return []
        root_id = int(current.get("root_project_id") or current.get("id"))
        current_id = int(current.get("id"))
        versions_by_id: dict[int, dict[str, Any]] = {}
        try:
            if self._user_id:
                for project in self.project_service.get_projects_for_user(int(self._user_id)) or []:
                    pid = int(project.get("id"))
                    proot = int(project.get("root_project_id") or pid)
                    if proot == root_id:
                        full = self.project_service.get_project_by_id(pid) or project
                        if project.get("root_name") and not full.get("root_name"):
                            full = dict(full)
                            full["root_name"] = project.get("root_name")
                        versions_by_id[pid] = dict(full)
        except Exception:
            pass
        try:
            for project in self.bom_service._project_family_ids(self.project_id) or []:
                pid = int(project.get("id"))
                full = self.project_service.get_project_by_id(pid) or project
                versions_by_id[pid] = dict(full)
        except Exception:
            pass
        try:
            all_projects = [dict(p) for p in self.project_service.get_all_projects() or []]
            included_ids = {int(p.get("id")) for p in versions_by_id.values() if p.get("id") is not None}
            included_ids.update({root_id, current_id})
            changed = True
            while changed:
                changed = False
                for project in all_projects:
                    pid = int(project.get("id"))
                    proot = int(project.get("root_project_id") or pid)
                    source_id = int(project.get("created_from_project_id") or 0)
                    if proot in included_ids or source_id in included_ids:
                        if pid not in included_ids:
                            included_ids.add(pid)
                            changed = True
            for project in all_projects:
                pid = int(project.get("id"))
                proot = int(project.get("root_project_id") or pid)
                source_id = int(project.get("created_from_project_id") or 0)
                same_family = pid in included_ids or proot in included_ids or source_id in included_ids
                same_name = str(project.get("name") or "").strip().lower() == str(current.get("name") or "").strip().lower()
                if same_family or same_name:
                    versions_by_id[pid] = dict(project)
        except Exception:
            pass
        versions = list(versions_by_id.values())
        if not versions:
            versions = [current]
        versions.sort(key=lambda p: (self._version_sort_key(p), int(p.get("id") or 0)))
        return versions

    def _version_sort_key(self, project: dict[str, Any]) -> tuple[int, str]:
        label = str(project.get("version_label") or "A").strip().upper()
        n = 0
        for ch in label:
            if not ch.isalpha():
                return (999999, label)
            n = n * 26 + (ord(ch) - ord("A") + 1)
        return (n or 1, label)

    def _version_label(self, project: dict[str, Any] | None) -> str:
        if not project:
            return "-"
        label = str(project.get("version_label") or "A").strip() or "A"
        state = str(project.get("version_state") or "WIP").strip() or "WIP"
        return f"{label} ({state})"

    def _project_label(self, project: dict[str, Any]) -> str:
        name = str(project.get("name") or f"Project {project.get('id')}").strip()
        return f"{name} - {self._version_label(project)}"

    def _get_part(self, project_id: int, part_id: int | None):
        if not part_id:
            return None
        try:
            for part in self.bom_service.bom_repo.get_all(int(project_id)) or []:
                if int(getattr(part, "id", 0) or 0) == int(part_id):
                    return part
        except Exception:
            return None
        return None

    def _part_match_keys(self, part) -> list[tuple[str, str]]:
        keys = []
        for attr in ("part_number", "aes_number", "base_file_name", "filename", "name"):
            value = str(getattr(part, attr, "") or "").strip().lower()
            if value:
                keys.append((attr, value))
        return keys

    def _find_matching_part_id(self, source_part, target_project_id: int) -> int | None:
        if not source_part:
            return None
        keys = self._part_match_keys(source_part)
        if not keys:
            return None
        try:
            target_parts = self.bom_service.bom_repo.get_all(int(target_project_id)) or []
        except Exception:
            target_parts = []
        for attr, value in keys:
            for part in target_parts:
                if str(getattr(part, attr, "") or "").strip().lower() == value:
                    return int(part.id)
        return None

    def _init_ui(self):
        root = QVBoxLayout(self)
        self.tabs = QTabWidget()
        parts_tab = QWidget()
        parts_layout = QVBoxLayout(parts_tab)
        sides = QHBoxLayout()

        self.left_combo, self.left_number, self.left_name, self.left_version = self._make_side()
        self.right_combo, self.right_number, self.right_name, self.right_version = self._make_side()
        sides.addWidget(self._side_widget("Left Side", self.left_combo, self.left_number, self.left_name, self.left_version))
        sides.addWidget(self._side_widget("Right Side", self.right_combo, self.right_number, self.right_name, self.right_version))
        parts_layout.addLayout(sides)
        self.tabs.addTab(parts_tab, "Parts")

        pref_tab = QWidget()
        pref_layout = QVBoxLayout(pref_tab)
        pref_layout.addWidget(QLabel("Compare project versions side by side using identity, attributes, usage, and structure position."))
        pref_layout.addStretch()
        self.tabs.addTab(pref_tab, "Preferences")
        root.addWidget(self.tabs)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._open_compare)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        current_index = 0
        for i in range(self.left_combo.count()):
            if int(self.left_combo.itemData(i) or -1) == self.project_id:
                current_index = i
                break
        self.left_combo.setCurrentIndex(current_index)
        self.right_combo.setCurrentIndex(current_index)
        self._update_side(self.left_combo, self.left_number, self.left_name, self.left_version)
        self._update_side(self.right_combo, self.right_number, self.right_name, self.right_version)

    def _make_side(self):
        combo = QComboBox()
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.NoInsert)
        for project in self._versions:
            combo.addItem(self._project_label(project), int(project["id"]))
        number = QLabel("-")
        name = QLabel("-")
        version = QComboBox()
        version.setEditable(False)
        for project in self._versions:
            version.addItem(self._version_label(project), int(project["id"]))
        combo.currentIndexChanged.connect(lambda *_: self._update_side(combo, number, name, version))
        version.currentIndexChanged.connect(lambda *_: self._select_project_from_version(combo, number, name, version))
        return combo, number, name, version

    def _side_widget(self, title, combo, number, name, version):
        box = QGroupBox(title)
        layout = QVBoxLayout(box)
        combo.setMinimumHeight(24)
        layout.addWidget(combo)
        form = QFormLayout()
        form.addRow("Number:", number)
        form.addRow("Name:", name)
        form.addRow("Version:", version)
        layout.addLayout(form)
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Structure Filter:"))
        for text in ("Cfg", "Edit", "View"):
            btn = QToolButton()
            btn.setText(text)
            btn.setAutoRaise(True)
            filter_row.addWidget(btn)
        filter_row.addStretch()
        layout.addLayout(filter_row)
        layout.addWidget(QLabel("Latest : Design, Working"))
        return box

    def _update_side(self, combo, number, name, version):
        if self._syncing_version_widgets:
            return
        project = self._project_by_id.get(int(combo.currentData() or -1))
        if self.whole_bom:
            number.setText(str(project.get("name") or "-") if project else "-")
            name.setText("Whole BOM")
        else:
            source_number = str(getattr(self._source_part, "part_number", "") or "-")
            source_name = str(getattr(self._source_part, "name", "") or "-")
            number.setText(source_number)
            name.setText(source_name)
        self._syncing_version_widgets = True
        try:
            target_id = int(combo.currentData() or -1)
            for i in range(version.count()):
                if int(version.itemData(i) or -1) == target_id:
                    version.setCurrentIndex(i)
                    break
        finally:
            self._syncing_version_widgets = False

    def _select_project_from_version(self, combo, number, name, version):
        if self._syncing_version_widgets:
            return
        target_id = int(version.currentData() or -1)
        if target_id <= 0:
            return
        self._syncing_version_widgets = True
        try:
            for i in range(combo.count()):
                if int(combo.itemData(i) or -1) == target_id:
                    combo.setCurrentIndex(i)
                    break
        finally:
            self._syncing_version_widgets = False
        self._update_side(combo, number, name, version)

    def _open_compare(self):
        left_project_id = int(self.left_combo.currentData() or -1)
        right_project_id = int(self.right_combo.currentData() or -1)
        if left_project_id <= 0 or right_project_id <= 0:
            return QMessageBox.warning(self, "Compare", "Select both left and right project versions.")

        left_id = None
        right_id = None
        if not self.whole_bom:
            source_part = self._get_part(left_project_id, self.initial_part_id) or self._source_part
            left_id = self._find_matching_part_id(self._source_part, left_project_id) if left_project_id != self.project_id else self.initial_part_id
            right_id = self._find_matching_part_id(source_part or self._source_part, right_project_id)
            if not left_id or not right_id:
                return QMessageBox.warning(
                    self,
                    "Compare",
                    "Could not find the selected BOM item in both project versions.",
                )

        dlg = WindchillPartCompareDialog(
            self,
            self.bom_service,
            self.project_service,
            left_project_id,
            right_project_id,
            left_id,
            right_id,
            self.whole_bom,
        )
        dlg.exec_()
        self.accept()


class WindchillPartCompareDialog(QDialog):
    def __init__(
        self,
        parent,
        bom_service,
        project_service,
        left_project_id: int,
        right_project_id: int,
        left_id: int | None = None,
        right_id: int | None = None,
        whole_bom: bool = False,
    ):
        super().__init__(parent)
        self.bom_service = bom_service
        self.project_service = project_service
        self.left_project_id = int(left_project_id)
        self.right_project_id = int(right_project_id)
        self.left_id = int(left_id) if left_id else None
        self.right_id = int(right_id) if right_id else None
        self.whole_bom = bool(whole_bom)
        self._left_project = self.project_service.get_project_by_id(self.left_project_id) or {}
        self._right_project = self.project_service.get_project_by_id(self.right_project_id) or {}
        self._left_parts = {int(p.id): p for p in self.bom_service.bom_repo.get_all(self.left_project_id) or []}
        self._right_parts = {int(p.id): p for p in self.bom_service.bom_repo.get_all(self.right_project_id) or []}
        self._left_children = self._children_map(self.left_project_id)
        self._right_children = self._children_map(self.right_project_id)
        self._pair_items: dict[int, tuple[QTreeWidgetItem, QTreeWidgetItem, QTreeWidgetItem]] = {}
        self._syncing_tree_state = False
        self._syncing_scroll = False
        self._left_root = self._project_node("left") if self.whole_bom else self._build_node(self.left_id, None, set(), self._left_parts, self._left_children)
        self._right_root = self._project_node("right") if self.whole_bom else self._build_node(self.right_id, None, set(), self._right_parts, self._right_children)
        self._pair_root = self._compare_nodes(self._left_root, self._right_root)
        self.setWindowTitle("Compare to Part Structure")
        self.resize(1220, 820)
        self._init_ui()
        self._populate()

    def _children_map(self, project_id: int):
        out = defaultdict(list)
        for rel in self.bom_service.children_repo.get_all_for_project(int(project_id)) or []:
            out[int(rel.parent_id)].append(rel)
        return out

    def _root_part_ids(self, parts: dict[int, Any], children_map) -> list[int]:
        child_ids = set()
        for rels in children_map.values():
            for rel in rels:
                child_ids.add(int(rel.child_id))
        roots = [pid for pid in parts.keys() if pid not in child_ids]
        return sorted(roots, key=lambda pid: self._part_sort_text(parts[pid]))

    def _part_sort_text(self, part) -> str:
        return str(
            getattr(part, "sort_order", "")
            or getattr(part, "part_number", "")
            or getattr(part, "aes_number", "")
            or getattr(part, "name", "")
            or getattr(part, "id", "")
        ).lower()

    def _project_node(self, side: str) -> dict[str, Any]:
        project = self._left_project if side == "left" else self._right_project
        parts = self._left_parts if side == "left" else self._right_parts
        children = self._left_children if side == "left" else self._right_children
        label = str(project.get("version_label") or "A").strip() or "A"
        state = str(project.get("version_state") or "WIP").strip() or "WIP"
        node = {
            "id": f"project:{project.get('id')}",
            "part_number": project.get("name") or f"Project {project.get('id')}",
            "aes_number": "",
            "name": "Whole BOM",
            "revision": label,
            "status": state,
            "type": "bom",
            "quantity": "",
            "line_number": "",
            "unit": "",
            "trace_code": "",
            "children": [],
        }
        for pid in self._root_part_ids(parts, children):
            child = self._build_node(pid, None, set(), parts, children)
            if child:
                node["children"].append(child)
        return node

    def _node_key(self, node: dict[str, Any] | None) -> str:
        if not node:
            return ""
        for key in ("part_number", "aes_number", "base_file_name", "filename", "name"):
            val = str(node.get(key) or "").strip().lower()
            if val:
                return val
        return str(node.get("id") or "")

    def _build_node(self, part_id: int | None, rel, seen: set[int], parts: dict[int, Any], children_map):
        if not part_id:
            return None
        part = parts.get(int(part_id))
        if not part:
            return None
        d = part.__dict__.copy()
        d["quantity"] = getattr(rel, "quantity", "") if rel is not None else ""
        d["line_number"] = getattr(rel, "sort_order", "") if rel is not None else ""
        d["unit"] = "each"
        d["trace_code"] = "Untraced"
        d["children"] = []
        if int(part_id) in seen:
            return d
        seen = set(seen)
        seen.add(int(part_id))
        for child_rel in children_map.get(int(part_id), []):
            child = self._build_node(int(child_rel.child_id), child_rel, seen, parts, children_map)
            if child:
                d["children"].append(child)
        return d

    def _identity(self, node):
        if not node:
            return ""
        number = node.get("part_number") or "No Number"
        name = node.get("name") or ""
        rev = node.get("revision") or ""
        state = node.get("status") or "Design"
        identity = f"{number}, {name}, {rev} ({state})"
        aes_number = str(node.get("aes_number") or "").strip()
        if aes_number and aes_number.casefold() != str(number or "").strip().casefold():
            identity += f" | AES {aes_number}"
        return identity

    def _compare_nodes(self, left, right) -> ComparePair:
        attr_fields = [
            ("Number", "part_number"),
            ("AES Number", "aes_number"),
            ("Name", "name"),
            ("Version", "revision"),
            ("State", "status"),
            ("Type", "type"),
            ("Material", "material"),
            ("Weight", "weight"),
            ("Drawing Number", "drawing_number"),
        ]
        usage_fields = [
            ("Quantity", "quantity"),
            ("Unit", "unit"),
            ("Line Number", "line_number"),
            ("Trace Code", "trace_code"),
        ]
        if left is None:
            status = "added"
            attr_diffs = []
            usage_diffs = []
        elif right is None:
            status = "removed"
            attr_diffs = []
            usage_diffs = []
        else:
            attr_diffs = [(label, left.get(key), right.get(key)) for label, key in attr_fields if str(left.get(key) or "") != str(right.get(key) or "")]
            usage_diffs = [(label, left.get(key), right.get(key)) for label, key in usage_fields if str(left.get(key) or "") != str(right.get(key) or "")]
            status = "usage" if usage_diffs else ("attr" if attr_diffs else "same")

        left_children = list((left or {}).get("children", []) or [])
        right_children = list((right or {}).get("children", []) or [])
        right_by_key = defaultdict(list)
        for child in right_children:
            right_by_key[self._node_key(child)].append(child)
        children = []
        used_right = set()
        for child in left_children:
            key = self._node_key(child)
            match = None
            while right_by_key.get(key):
                candidate = right_by_key[key].pop(0)
                used_right.add(id(candidate))
                match = candidate
                break
            children.append(self._compare_nodes(child, match))
        for child in right_children:
            if id(child) not in used_right:
                children.append(self._compare_nodes(None, child))
        if status == "same" and any(c.status != "same" for c in children):
            status = "child"
        return ComparePair(left, right, status, children, attr_diffs, usage_diffs)

    def _init_ui(self):
        root = QVBoxLayout(self)
        top = QSplitter(Qt.Horizontal)
        self.left_panel = self._structure_panel("Left")
        self.diff_panel = self._diff_panel()
        self.right_panel = self._structure_panel("Right")
        top.addWidget(self.left_panel)
        top.addWidget(self.diff_panel)
        top.addWidget(self.right_panel)
        top.setStretchFactor(0, 5)
        top.setStretchFactor(1, 0)
        top.setStretchFactor(2, 5)
        top.setSizes([560, 74, 560])
        self._connect_tree_sync()

        bottom = QSplitter(Qt.Horizontal)
        self.left_tabs, self.left_attr, self.left_usage = self._details_tabs()
        self.right_tabs, self.right_attr, self.right_usage = self._details_tabs()
        bottom.addWidget(self.left_tabs)
        bottom.addWidget(self.right_tabs)

        main = QSplitter(Qt.Vertical)
        main.addWidget(top)
        main.addWidget(bottom)
        main.setStretchFactor(0, 3)
        main.setStretchFactor(1, 1)
        root.addWidget(main)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        root.addWidget(buttons)

    def _structure_panel(self, side: str):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("")
        title.setObjectName("compareHeader")
        toolbar = QHBoxLayout()
        for text in ("R", "<", ">", "Grid", "Cfg", "Sync", "v"):
            btn = QToolButton()
            btn.setText(text)
            btn.setAutoRaise(True)
            toolbar.addWidget(btn)
        toolbar.addStretch()
        find = QLineEdit()
        find.setPlaceholderText("Find in Structure")
        tree = QTreeWidget()
        tree.setHeaderLabels(["Identity"])
        tree.setUniformRowHeights(True)
        tree.itemSelectionChanged.connect(lambda s=side: self._on_tree_selection(s))
        layout.addWidget(title)
        layout.addLayout(toolbar)
        layout.addWidget(find)
        layout.addWidget(tree)
        panel.title = title
        panel.tree = tree
        panel.find = find
        find.textChanged.connect(lambda text, t=tree: self._filter_tree(t, text))
        return panel

    def _diff_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("")
        title.setObjectName("compareHeader")
        toolbar_spacer = QWidget()
        toolbar_spacer.setFixedHeight(24)
        find_spacer = QWidget()
        find_spacer.setFixedHeight(30)
        self.diff_tree = QTreeWidget()
        self.diff_tree.setHeaderLabels(["Diff"])
        self.diff_tree.setUniformRowHeights(True)
        self.diff_tree.setMinimumWidth(64)
        self.diff_tree.setMaximumWidth(86)
        self.diff_tree.setIndentation(0)
        self.diff_tree.setRootIsDecorated(False)
        self.diff_tree.itemDoubleClicked.connect(self._open_usage_diff_for_item)
        layout.addWidget(title)
        layout.addWidget(toolbar_spacer)
        layout.addWidget(find_spacer)
        layout.addWidget(self.diff_tree)
        panel.setMinimumWidth(64)
        panel.setMaximumWidth(90)
        return panel

    def _details_tabs(self):
        tabs = QTabWidget()
        attr = QTreeWidget()
        attr.setHeaderLabels(["Part Attributes", ""])
        usage = QTreeWidget()
        usage.setHeaderLabels(["Usage Attributes", ""])
        viz = QLabel("Visualization placeholder")
        viz.setAlignment(Qt.AlignCenter)
        tabs.addTab(attr, "Attributes")
        tabs.addTab(usage, "Usage")
        tabs.addTab(viz, "Visualization")
        return tabs, attr, usage

    def _status_glyph(self, status: str):
        return {
            "same": ("=", QColor("#f4f7f9"), "Same item"),
            "child": ("*", QColor("#e6f2ff"), "Child structure differs"),
            "attr": ("!=", QColor("#fff4cc"), "Attribute differs"),
            "usage": ("<>", QColor("#fff4cc"), "Usage differs"),
            "added": ("+", QColor("#e1f5e1"), "Added on right side"),
            "removed": ("-", QColor("#fde2e2"), "Removed from right side"),
        }.get(status, ("?", QColor("#eeeeee"), "Unknown difference"))

    def _populate(self):
        self.left_panel.title.setText(self._identity(self._left_root))
        self.right_panel.title.setText(self._identity(self._right_root))
        self._add_pair(None, None, None, self._pair_root)
        self.left_panel.tree.expandAll()
        self.right_panel.tree.expandAll()
        self.diff_tree.expandAll()
        self._style()

    def _add_pair(self, left_parent, mid_parent, right_parent, pair: ComparePair):
        left_text = self._identity(pair.left) if pair.left else ""
        right_text = self._identity(pair.right) if pair.right else ""
        glyph, color, tooltip = self._status_glyph(pair.status)
        li = QTreeWidgetItem([left_text])
        mi = QTreeWidgetItem([glyph])
        ri = QTreeWidgetItem([right_text])
        for item, side, node in ((li, "left", pair.left), (ri, "right", pair.right)):
            item.setData(0, DIFF_ROLE, pair)
            item.setData(0, SIDE_ROLE, side)
            item.setData(0, NODE_ROLE, node)
            if pair.status in ("added", "removed", "attr", "usage", "child"):
                item.setBackground(0, QBrush(color))
            if pair.status in ("added", "removed"):
                f = QFont(item.font(0))
                f.setBold(True)
                item.setFont(0, f)
        mi.setTextAlignment(0, Qt.AlignCenter)
        mi.setBackground(0, QBrush(color))
        mi.setToolTip(0, tooltip)
        mi.setData(0, DIFF_ROLE, pair)
        self._pair_items[id(pair)] = (li, mi, ri)
        if left_parent is None:
            self.left_panel.tree.addTopLevelItem(li)
            self.diff_tree.addTopLevelItem(mi)
            self.right_panel.tree.addTopLevelItem(ri)
        else:
            left_parent.addChild(li)
            mid_parent.addChild(mi)
            right_parent.addChild(ri)
        for child in pair.children:
            self._add_pair(li, mi, ri, child)

    def _on_tree_selection(self, side: str):
        tree = self.left_panel.tree if side == "Left" else self.right_panel.tree
        item = tree.currentItem()
        if not item:
            return
        pair = item.data(0, DIFF_ROLE)
        self._populate_details(pair)

    def _populate_details(self, pair: ComparePair):
        self._fill_attrs(self.left_attr, pair.left, pair.attr_diffs, left=True)
        self._fill_attrs(self.right_attr, pair.right, pair.attr_diffs, left=False)
        self._fill_usage(self.left_usage, pair.left, pair.usage_diffs, left=True)
        self._fill_usage(self.right_usage, pair.right, pair.usage_diffs, left=False)

    def _fill_attrs(self, tree, node, diffs, left: bool):
        rows = [
            ("Number", "part_number"),
            ("AES Number", "aes_number"),
            ("Name", "name"),
            ("Version", "revision"),
            ("State", "status"),
            ("Status", "lifecycle_state"),
            ("Modified By", "released_by"),
            ("Last Modified", "modified"),
            ("CHECKED_BY", "released_by"),
            ("COMMENT", "notes"),
        ]
        diff_labels = {d[0] for d in diffs}
        self._fill_tree(tree, node, rows, diff_labels)

    def _fill_usage(self, tree, node, diffs, left: bool):
        rows = [
            ("Quantity", "quantity"),
            ("Unit", "unit"),
            ("Line Number", "line_number"),
            ("Find Number", "line_number"),
            ("Trace Code", "trace_code"),
            ("Reference Designator", "drawing_number"),
        ]
        diff_labels = {d[0] for d in diffs}
        self._fill_tree(tree, node, rows, diff_labels)

    def _fill_tree(self, tree, node, rows, diff_labels):
        tree.clear()
        root = QTreeWidgetItem(["Attributes", ""])
        tree.addTopLevelItem(root)
        for label, key in rows:
            item = QTreeWidgetItem([f"{label}:", str((node or {}).get(key, "") or "")])
            if label in diff_labels:
                item.setForeground(0, QBrush(QColor("#003db8")))
                item.setForeground(1, QBrush(QColor("#003db8")))
                f = QFont(item.font(0))
                f.setBold(True)
                item.setFont(0, f)
            root.addChild(item)
        tree.expandAll()

    def _open_usage_diff_for_item(self, item, _column=0):
        pair = item.data(0, DIFF_ROLE) if item else None
        if not pair or not pair.usage_diffs:
            return
        dlg = UsageAttributeDifferencesDialog(self, pair, self._identity)
        dlg.exec_()

    def _filter_tree(self, tree, text):
        q = (text or "").strip().lower()

        def recurse(item):
            match = (not q) or q in item.text(0).lower()
            child_match = False
            for i in range(item.childCount()):
                child_match = recurse(item.child(i)) or child_match
            item.setHidden(not (match or child_match))
            return match or child_match

        for i in range(tree.topLevelItemCount()):
            recurse(tree.topLevelItem(i))

    def _style(self):
        qss = """
        QDialog { background: #e7eef2; font-size: 12px; }
        QLabel#compareHeader {
            background: #d8d8d8;
            border: 1px solid #9fa8ad;
            padding: 4px;
            font-weight: 700;
            color: #1f2933;
        }
        QTreeWidget {
            background: #ffffff;
            border: 1px solid #c2ccd2;
            alternate-background-color: #f7f7f7;
            font-size: 12px;
        }
        QHeaderView::section {
            background: #e4e8eb;
            border: 1px solid #c2ccd2;
            padding: 3px;
            font-weight: 700;
        }
        QTabBar::tab {
            background: #5b9bc0;
            color: white;
            padding: 4px 10px;
            border-top-left-radius: 2px;
            border-top-right-radius: 2px;
        }
        QTabBar::tab:selected {
            background: #ffffff;
            color: #111827;
            border-top: 2px solid #f6a21a;
        }
        """
        self.setStyleSheet(qss)

    def _connect_tree_sync(self):
        for tree in (self.left_panel.tree, self.diff_tree, self.right_panel.tree):
            tree.itemExpanded.connect(lambda item: self._sync_expansion_from_item(item, True))
            tree.itemCollapsed.connect(lambda item: self._sync_expansion_from_item(item, False))
        for tree in (self.left_panel.tree, self.diff_tree, self.right_panel.tree):
            tree.verticalScrollBar().valueChanged.connect(lambda value, src=tree: self._sync_scroll_from_tree(src, value))

    def _sync_scroll_from_tree(self, source_tree, value: int):
        if self._syncing_scroll:
            return
        self._syncing_scroll = True
        try:
            for tree in (self.left_panel.tree, self.diff_tree, self.right_panel.tree):
                if tree is not source_tree:
                    tree.verticalScrollBar().setValue(value)
        finally:
            self._syncing_scroll = False

    def _sync_expansion_from_item(self, source_item: QTreeWidgetItem, expanded: bool):
        if self._syncing_tree_state:
            return
        pair = source_item.data(0, DIFF_ROLE) if source_item else None
        if not pair:
            return
        items = self._pair_items.get(id(pair))
        if not items:
            return
        self._syncing_tree_state = True
        try:
            for item in items:
                if item is not source_item:
                    item.setExpanded(bool(expanded))
        finally:
            self._syncing_tree_state = False


class UsageAttributeDifferencesDialog(QDialog):
    def __init__(self, parent, pair: ComparePair, identity_fn):
        super().__init__(parent)
        self.pair = pair
        self.identity_fn = identity_fn
        self.setWindowTitle("Usage Attribute Differences")
        self.resize(760, 360)
        layout = QVBoxLayout(self)
        split = QSplitter(Qt.Horizontal)
        self.left = QTreeWidget()
        self.right = QTreeWidget()
        self.left.setHeaderLabels([self.identity_fn(pair.left), ""])
        self.right.setHeaderLabels([self.identity_fn(pair.right), ""])
        split.addWidget(self.left)
        split.addWidget(self.right)
        layout.addWidget(split)
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self._fill()

    def _fill(self):
        left_root = QTreeWidgetItem(["Attributes", ""])
        right_root = QTreeWidgetItem(["Attributes", ""])
        self.left.addTopLevelItem(left_root)
        self.right.addTopLevelItem(right_root)
        for label, lv, rv in self.pair.usage_diffs:
            li = QTreeWidgetItem([f"{label}:", str(lv or "")])
            ri = QTreeWidgetItem([f"{label}:", str(rv or "")])
            for item in (li, ri):
                item.setForeground(0, QBrush(QColor("#003db8")))
                item.setForeground(1, QBrush(QColor("#003db8")))
            left_root.addChild(li)
            right_root.addChild(ri)
        self.left.expandAll()
        self.right.expandAll()
