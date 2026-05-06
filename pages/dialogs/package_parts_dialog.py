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
)

from core.repositories.bom_repository import BomRepository
from core.session_manager import SessionManager


class PackagePartsDialog(QDialog):
    def __init__(self, parent=None, project_id=None, preselected_ids=None):
        super().__init__(parent)
        self.setWindowTitle("Select Parts")
        self.setModal(True)
        self.resize(640, 520)

        self.session = SessionManager()
        self.project_id = project_id or self.session.project_id
        self.preselected_ids = set(preselected_ids or [])

        self.bom_repo = BomRepository()
        self._all_parts = []  # list of dicts {id,aes,name,type}

        self._build_ui()
        self._load_parts()
        self._apply_filter()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Search and select parts to include in the package:"))

        search_row = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Filter by AES / name / type...")
        self.search_input.textChanged.connect(self._apply_filter)
        search_row.addWidget(self.search_input)

        self.select_all_btn = QPushButton("Select All")
        self.select_all_btn.clicked.connect(self._select_all_visible)
        self.select_none_btn = QPushButton("Select None")
        self.select_none_btn.clicked.connect(self._select_none_visible)
        search_row.addWidget(self.select_all_btn)
        search_row.addWidget(self.select_none_btn)
        layout.addLayout(search_row)

        self.list_widget = QListWidget()
        self.list_widget.setAlternatingRowColors(True)
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
                }
            )

        # simple sort: AES then name
        rows.sort(key=lambda r: (r["aes_number"].lower(), r["name"].lower()))
        self._all_parts = rows

    def _apply_filter(self):
        q = (self.search_input.text() or "").strip().lower()

        self.list_widget.clear()
        for p in self._all_parts:
            label = f"{p['aes_number']} | {p['name']} | {p['type']}"
            if q and q not in label.lower():
                continue

            item = QListWidgetItem(label)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if p["id"] in self.preselected_ids else Qt.Unchecked)
            item.setData(Qt.UserRole, p["id"])
            self.list_widget.addItem(item)

    def _select_all_visible(self):
        for i in range(self.list_widget.count()):
            self.list_widget.item(i).setCheckState(Qt.Checked)

    def _select_none_visible(self):
        for i in range(self.list_widget.count()):
            self.list_widget.item(i).setCheckState(Qt.Unchecked)

    def selected_part_ids(self):
        ids = []
        for i in range(self.list_widget.count()):
            it = self.list_widget.item(i)
            if it.checkState() == Qt.Checked:
                ids.append(int(it.data(Qt.UserRole)))
        return ids
