from PyQt5.QtCore import QObject, QDate, QRect, Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QColor, QBrush, QFont, QPainter, QPen
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QInputDialog,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from datetime import date, timedelta
import os

from core.models.issue_model import ISSUE_CATEGORIES, ISSUE_PRIORITIES, ISSUE_STATUSES
from core.repositories.user_repository import UserRepository
from core.services.issue_service import IssueService
from core.services.user_service import UserService
from core.services.project_service import ProjectService
from pages.rich_text_image_editor import RichTextImageEditor, looks_like_html
from utils import safe_exists, safe_startfile


PRIORITY_COLORS = {
    "Low": "#2e7d32",
    "Medium": "#a16207",
    "High": "#c2410c",
    "Critical": "#b91c1c",
}


class IssueDialog(QDialog):
    def __init__(self, service: IssueService, issue=None, preselected_part_id=None, parent=None):
        super().__init__(parent)
        self.service = service
        self.issue = issue or {}
        self.pending_attachments = []
        self.setWindowTitle("Edit Engineering Issue" if issue else "Create Engineering Issue")
        self.resize(760, 700)

        root = QVBoxLayout(self)
        form = QFormLayout()
        self.title_edit = QLineEdit(self.issue.get("title", ""))
        self.description_edit = RichTextImageEditor()
        self.description_edit.set_content(self.issue.get("description", ""))
        self.description_edit.setMinimumHeight(180)
        self.priority_combo = QComboBox()
        self.priority_combo.addItems(ISSUE_PRIORITIES)
        self.priority_combo.setCurrentText(self.issue.get("priority", "Medium"))
        self.category_combo = QComboBox()
        self.category_combo.addItems(ISSUE_CATEGORIES)
        existing_category = self.issue.get("category", "Design")
        if self.category_combo.findText(existing_category) < 0:
            self.category_combo.addItem(existing_category)
        self.category_combo.setCurrentText(existing_category)
        self.assignee_combo = QComboBox()
        self.assignee_combo.addItem("Unassigned", None)
        for user in UserService(UserRepository()).get_all_users():
            self.assignee_combo.addItem(user.username, user.id)
        assigned_to = self.issue.get("assigned_to")
        if assigned_to is not None:
            idx = self.assignee_combo.findData(int(assigned_to))
            if idx >= 0:
                self.assignee_combo.setCurrentIndex(idx)
        self.due_date = QDateEdit()
        self.due_date.setCalendarPopup(True)
        self.due_date.setSpecialValueText("No due date")
        self.due_date.setMinimumDate(QDate(2000, 1, 1))
        self.due_date.setDate(QDate.currentDate().addDays(14))
        if self.issue.get("due_date"):
            parsed = QDate.fromString(str(self.issue["due_date"])[:10], "yyyy-MM-dd")
            if parsed.isValid():
                self.due_date.setDate(parsed)

        form.addRow("Title", self.title_edit)
        form.addRow("Description", self.description_edit)
        form.addRow("Priority", self.priority_combo)
        form.addRow("Category", self.category_combo)
        form.addRow("Assigned To", self.assignee_combo)
        form.addRow("Due Date", self.due_date)
        root.addLayout(form)

        root.addWidget(QLabel("Affected Parts / Assemblies"))
        self.part_search = QLineEdit()
        self.part_search.setPlaceholderText("Filter parts...")
        root.addWidget(self.part_search)
        self.parts_list = QListWidget()
        self.parts_list.setAlternatingRowColors(True)
        selected = {int(x["id"]) for x in self.issue.get("parts", [])}
        if preselected_part_id:
            selected.add(int(preselected_part_id))
        for part in service.bom_repo.get_all(service.project_id):
            item = QListWidgetItem(f"{part.name}  [{part.aes_number or part.id}]")
            item.setData(Qt.UserRole, int(part.id))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if int(part.id) in selected else Qt.Unchecked)
            self.parts_list.addItem(item)
        self.part_search.textChanged.connect(self._filter_parts)
        root.addWidget(self.parts_list, 1)

        attachment_row = QHBoxLayout()
        self.attachment_label = QLabel("No new attachments selected")
        choose_attachment = QPushButton("Add Attachments")
        choose_attachment.clicked.connect(self._choose_attachments)
        attachment_row.addWidget(self.attachment_label, 1)
        attachment_row.addWidget(choose_attachment)
        root.addLayout(attachment_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _filter_parts(self, text):
        term = (text or "").strip().lower()
        for index in range(self.parts_list.count()):
            item = self.parts_list.item(index)
            item.setHidden(bool(term and term not in item.text().lower()))

    def values(self):
        part_ids = [
            int(self.parts_list.item(i).data(Qt.UserRole))
            for i in range(self.parts_list.count())
            if self.parts_list.item(i).checkState() == Qt.Checked
        ]
        return {
            "data": {
                "title": self.title_edit.text().strip(),
                "description": self.description_edit.content(),
                "priority": self.priority_combo.currentText(),
                "category": self.category_combo.currentText(),
                "assigned_to": self.assignee_combo.currentData(),
                "due_date": self.due_date.date().toString("yyyy-MM-dd"),
            },
            "part_ids": part_ids,
            "attachments": list(self.pending_attachments),
        }

    def _choose_attachments(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Select Issue Attachments")
        if paths:
            self.pending_attachments.extend(path for path in paths if path not in self.pending_attachments)
            self.attachment_label.setText(f"{len(self.pending_attachments)} attachment(s) selected")


class MetricCard(QFrame):
    def __init__(self, label, color, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            f"QFrame {{background:#fff;border:1px solid #d9dee7;border-left:4px solid {color};"
            "border-radius:6px;} QLabel {border:none;background:transparent;}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        self.value = QLabel("0")
        self.value.setStyleSheet("font-size:22px;font-weight:700;color:#111827;")
        caption = QLabel(label)
        caption.setStyleSheet("font-size:11px;color:#667085;")
        layout.addWidget(self.value)
        layout.addWidget(caption)


class _IssueProcessingOverlay(QWidget):
    """Full-page busy overlay for Issue Center long-running actions."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._angle = 0
        self._message = "Processing..."
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.setCursor(Qt.WaitCursor)
        self._timer = QTimer(self)
        self._timer.setInterval(45)
        self._timer.timeout.connect(self._tick)
        self.hide()

    def show_overlay(self, message: str):
        self._message = message or "Processing..."
        if self.parent():
            self.setGeometry(self.parent().rect())
        self.raise_()
        self.show()
        self._timer.start()
        self.update()

    def hide_overlay(self):
        self._timer.stop()
        self.hide()

    def _tick(self):
        self._angle = (self._angle + 18) % 360
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(248, 250, 252, 190))

        card_w = min(380, max(280, self.width() - 48))
        card_h = 156
        card_x = (self.width() - card_w) // 2
        card_y = (self.height() - card_h) // 2

        painter.setPen(QPen(QColor("#bfdbfe"), 1))
        painter.setBrush(QBrush(QColor(255, 255, 255, 238)))
        painter.drawRoundedRect(QRect(card_x, card_y, card_w, card_h), 10, 10)

        spinner_size = 50
        spinner_x = card_x + (card_w - spinner_size) // 2
        spinner_y = card_y + 30
        arc_rect = QRect(spinner_x + 5, spinner_y + 5, spinner_size - 10, spinner_size - 10)
        painter.setPen(QPen(QColor("#dbeafe"), 5, Qt.SolidLine, Qt.RoundCap))
        painter.drawArc(arc_rect, 0, 360 * 16)
        painter.setPen(QPen(QColor("#2563eb"), 5, Qt.SolidLine, Qt.RoundCap))
        painter.drawArc(arc_rect, int(self._angle * 16), int(110 * 16))

        painter.setPen(QColor("#111827"))
        title_font = QFont()
        title_font.setPointSize(11)
        title_font.setBold(True)
        painter.setFont(title_font)
        text_rect = QRect(card_x + 20, spinner_y + spinner_size + 18, card_w - 40, 28)
        painter.drawText(text_rect, Qt.AlignCenter, self._message)

        painter.setPen(QColor("#64748b"))
        hint_font = QFont()
        hint_font.setPointSize(9)
        painter.setFont(hint_font)
        hint_rect = QRect(card_x + 20, text_rect.bottom() + 4, card_w - 40, 24)
        painter.drawText(hint_rect, Qt.AlignCenter, "Please wait. The Issue Center is locked while this finishes.")

    def keyPressEvent(self, event):
        event.accept()


class _IssueProcessingWorker(QObject):
    finished = pyqtSignal(object)
    failed = pyqtSignal(object)

    def __init__(self, fn):
        super().__init__()
        self._fn = fn

    def run(self):
        try:
            self.finished.emit(self._fn())
        except Exception as exc:
            self.failed.emit(exc)


class EngineeringIssuePage(QWidget):
    issue_changed = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.service = IssueService()
        self.current_issue_id = None
        self._processing_action = False
        self._processing_thread = None
        self._processing_worker = None
        self._build_ui()
        self.processing_overlay = _IssueProcessingOverlay(self)
        self.processing_overlay.setGeometry(self.rect())
        self.processing_overlay.hide()
        if self.service.project_id:
            QTimer.singleShot(0, self.refresh)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        try:
            self.processing_overlay.setGeometry(self.rect())
            if self.processing_overlay.isVisible():
                self.processing_overlay.raise_()
        except Exception:
            pass

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        cards = QHBoxLayout()
        self.cards = {
            "open_count": MetricCard("Open Issues", "#2563eb"),
            "closed_count": MetricCard("Closed Issues", "#16a34a"),
            "critical_count": MetricCard("Critical Issues", "#dc2626"),
            "overdue_count": MetricCard("Overdue Issues", "#d97706"),
            "avg_resolution_days": MetricCard("Avg Resolution Days", "#475569"),
        }
        for card in self.cards.values():
            cards.addWidget(card)
        root.addLayout(cards)
        self.analytics_label = QLabel("")
        self.analytics_label.setWordWrap(True)
        self.analytics_label.setStyleSheet(
            "font-size:11px;color:#475569;background:#f8fafc;border:1px solid #d9dee7;"
            "border-radius:4px;padding:5px;"
        )
        root.addWidget(self.analytics_label)

        filters = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search issue number, title, or description")
        self.status_filter = QComboBox()
        self.status_filter.addItems(["All"] + list(ISSUE_STATUSES))
        self.priority_filter = QComboBox()
        self.priority_filter.addItems(["All"] + list(ISSUE_PRIORITIES))
        self.assignee_filter = QComboBox()
        self.assignee_filter.addItem("All assignees", None)
        self.creator_filter = QComboBox()
        self.creator_filter.addItem("All creators", None)
        for user in UserService(UserRepository()).get_all_users():
            self.assignee_filter.addItem(user.username, user.id)
            self.creator_filter.addItem(user.username, user.id)
        self.part_filter = QComboBox()
        self.part_filter.addItem("All parts / assemblies", None)
        for part in self.service.bom_repo.get_all(self.service.project_id):
            self.part_filter.addItem(part.name, int(part.id))
        self.date_filter = QComboBox()
        self.date_filter.addItems(["All created dates", "Past 7 days", "Past 30 days"])
        self.overdue_filter = QComboBox()
        self.overdue_filter.addItems(["All dates", "Overdue"])
        for widget in (self.search_edit, self.status_filter, self.priority_filter,
                       self.assignee_filter, self.creator_filter, self.part_filter,
                       self.date_filter, self.overdue_filter):
            filters.addWidget(widget)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh)
        all_btn = QPushButton("All Issues")
        all_btn.clicked.connect(self.clear_part_filter)
        new_btn = QPushButton("New Issue")
        new_btn.setObjectName("primary")
        new_btn.clicked.connect(self.create_issue)
        filters.addWidget(refresh_btn)
        filters.addWidget(all_btn)
        filters.addWidget(new_btn)
        root.addLayout(filters)
        self.search_edit.returnPressed.connect(self.refresh)
        self.status_filter.currentTextChanged.connect(self.refresh)
        self.priority_filter.currentTextChanged.connect(self.refresh)
        self.assignee_filter.currentIndexChanged.connect(self.refresh)
        self.creator_filter.currentIndexChanged.connect(self.refresh)
        self.part_filter.currentIndexChanged.connect(self.refresh)
        self.date_filter.currentIndexChanged.connect(self.refresh)
        self.overdue_filter.currentIndexChanged.connect(self.refresh)

        splitter = QSplitter(Qt.Horizontal)
        self.issue_table = QTableWidget()
        self.issue_table.setColumnCount(8)
        self.issue_table.setHorizontalHeaderLabels(
            ["Issue", "Title", "Status", "Priority", "Category", "Assigned To", "Due", "Affected Parts"]
        )
        self.issue_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.issue_table.horizontalHeader().setSectionResizeMode(7, QHeaderView.Stretch)
        self.issue_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.issue_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.issue_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.issue_table.setAlternatingRowColors(True)
        self.issue_table.itemSelectionChanged.connect(self._selection_changed)
        self.issue_table.cellClicked.connect(lambda row, _col: self._sync_issue_details_from_row(row))
        self.issue_table.itemClicked.connect(lambda item: self._sync_issue_details_from_row(item.row() if item else -1))
        splitter.addWidget(self.issue_table)

        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        self.detail_title = QLabel("Select an issue")
        self.detail_title.setStyleSheet("font-size:16px;font-weight:700;color:#111827;")
        self.detail_meta = QLabel("")
        self.detail_meta.setWordWrap(True)
        self.detail_description = QTextEdit()
        self.detail_description.setReadOnly(True)
        self.detail_description.setMinimumHeight(160)
        self.detail_description.setMaximumHeight(300)
        detail_layout.addWidget(self.detail_title)
        detail_layout.addWidget(self.detail_meta)
        detail_layout.addWidget(self.detail_description)

        actions = QHBoxLayout()
        edit_btn = QPushButton("Edit")
        edit_btn.clicked.connect(self.edit_issue)
        self.transition_combo = QComboBox()
        self.transition_combo.addItems(ISSUE_STATUSES)
        transition_btn = QPushButton("Change Status")
        transition_btn.clicked.connect(self.change_status)
        archive_btn = QPushButton("Archive")
        archive_btn.clicked.connect(self.archive_issue)
        self.export_traceability_btn = QPushButton("Export Traceability")
        self.export_traceability_btn.setObjectName("neutral")
        self.export_traceability_btn.clicked.connect(self.export_current_traceability)
        actions.addWidget(edit_btn)
        actions.addWidget(self.transition_combo)
        actions.addWidget(transition_btn)
        actions.addWidget(archive_btn)
        actions.addWidget(self.export_traceability_btn)
        detail_layout.addLayout(actions)

        tabs = QTabWidget()

        jira_tab = QWidget()
        jira_layout = QVBoxLayout(jira_tab)
        jira_form = QFormLayout()
        self.jira_key_edit = QLineEdit()
        self.jira_key_edit.setPlaceholderText("ENG-123")
        self.jira_url_edit = QLineEdit()
        self.jira_url_edit.setPlaceholderText("https://your-jira/browse/ENG-123")
        jira_form.addRow("Jira Key", self.jira_key_edit)
        jira_form.addRow("Jira URL", self.jira_url_edit)
        jira_layout.addLayout(jira_form)
        add_jira_btn = QPushButton("Add Jira Link")
        add_jira_btn.clicked.connect(self.add_jira_link)
        open_jira_btn = QPushButton("Open Jira Link")
        open_jira_btn.clicked.connect(lambda _checked=False: self.open_selected_jira_link())
        jira_actions = QHBoxLayout()
        jira_actions.addWidget(add_jira_btn)
        jira_actions.addWidget(open_jira_btn)
        jira_actions.addStretch()
        jira_layout.addLayout(jira_actions)
        self.jira_table = QTableWidget()
        self.jira_table.setColumnCount(4)
        self.jira_table.setHorizontalHeaderLabels(["Key", "URL", "Status", "Created"])
        self.jira_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.jira_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.jira_table.itemDoubleClicked.connect(lambda item: self.open_selected_jira_link(item))
        jira_layout.addWidget(self.jira_table, 1)
        tabs.addTab(jira_tab, "Jira")

        self.creo_files_table = QTableWidget()
        self.creo_files_table.setColumnCount(7)
        self.creo_files_table.setHorizontalHeaderLabels(
            ["Part ID", "Part", "File Role", "File Type", "Filename", "Base Name", "Revision"]
        )
        self.creo_files_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.creo_files_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.creo_files_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tabs.addTab(self.creo_files_table, "Linked Creo Files")

        self.engineering_files_table = QTableWidget()
        self.engineering_files_table.setColumnCount(8)
        self.engineering_files_table.setHorizontalHeaderLabels(
            ["Role", "Part", "Name", "Type", "Version", "File", "Checksum", "Linked"]
        )
        self.engineering_files_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.engineering_files_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self.engineering_files_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tabs.addTab(self.engineering_files_table, "Linked Engineering Files")

        comments_tab = QWidget()
        comments_layout = QVBoxLayout(comments_tab)
        self.comments_list = QListWidget()
        self.comment_edit = QTextEdit()
        self.comment_edit.setMaximumHeight(75)
        add_comment_btn = QPushButton("Add Comment")
        add_comment_btn.clicked.connect(self.add_comment)
        comments_layout.addWidget(self.comments_list)
        comments_layout.addWidget(self.comment_edit)
        comments_layout.addWidget(add_comment_btn)
        tabs.addTab(comments_tab, "Comments")

        self.history_table = QTableWidget()
        self.history_table.setColumnCount(3)
        self.history_table.setHorizontalHeaderLabels(["When", "Who", "Action"])
        self.history_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.history_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tabs.addTab(self.history_table, "Timeline")

        self.commit_table = QTableWidget()
        self.commit_table.setColumnCount(8)
        self.commit_table.setHorizontalHeaderLabels(
            ["Commit", "Relation", "Validation", "Commit Status", "Author", "Date", "Merge", "Reverted"]
        )
        self.commit_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.commit_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.commit_table.itemDoubleClicked.connect(self.open_commit_details)
        tabs.addTab(self.commit_table, "Linked Commits")

        attachments_tab = QWidget()
        attachments_layout = QVBoxLayout(attachments_tab)
        self.attachments_list = QListWidget()
        self.attachments_list.itemDoubleClicked.connect(
            lambda item: safe_startfile(item.toolTip()) if item and item.toolTip() else None
        )
        add_attachment_btn = QPushButton("Add Attachment")
        add_attachment_btn.clicked.connect(self.add_attachment)
        attachments_layout.addWidget(self.attachments_list)
        attachments_layout.addWidget(add_attachment_btn)
        tabs.addTab(attachments_tab, "Attachments")
        detail_layout.addWidget(tabs, 1)
        splitter.addWidget(detail)
        splitter.setSizes([850, 500])
        root.addWidget(splitter, 1)

    def _filters(self):
        created_after = None
        if self.date_filter.currentText() == "Past 7 days":
            created_after = (date.today() - timedelta(days=7)).isoformat()
        elif self.date_filter.currentText() == "Past 30 days":
            created_after = (date.today() - timedelta(days=30)).isoformat()
        return {
            "keyword": self.search_edit.text().strip(),
            "status": self.status_filter.currentText(),
            "priority": self.priority_filter.currentText(),
            "assigned_to": self.assignee_filter.currentData(),
            "created_by": self.creator_filter.currentData(),
            "created_after": created_after,
            "overdue": self.overdue_filter.currentText() == "Overdue",
            "part_id": self.part_filter.currentData(),
        }

    def refresh(self, *_):
        if not self.service.project_id:
            return
        selected_issue_id = self.current_issue_id
        metrics = self.service.metrics()
        analytics = self.service.analytics()
        for key, card in self.cards.items():
            value = metrics.get(key, 0)
            card.value.setText(f"{float(value):.1f}" if key == "avg_resolution_days" else str(int(value)))
        hotspots = ", ".join(
            f"{x['name']} ({x['active_count']})" for x in analytics.get("top_parts", [])[:4]
        ) or "None"
        workload = ", ".join(
            f"{x['username']} ({x['active_count']})" for x in analytics.get("by_assignee", [])[:4]
        ) or "None"
        self.analytics_label.setText(f"Risk hotspots: {hotspots}    |    Active workload: {workload}")
        issues = self.service.list_issues(self._filters())
        selected_row = None
        signals_were_blocked = self.issue_table.blockSignals(True)
        try:
            self.issue_table.setRowCount(len(issues))
            for row, issue in enumerate(issues):
                issue_id = int(issue["id"])
                if selected_issue_id is not None and issue_id == int(selected_issue_id):
                    selected_row = row
                values = [
                    issue["issue_number"], issue["title"], issue["status"], issue["priority"],
                    issue["category"], issue.get("assigned_to_name") or "Unassigned",
                    issue.get("due_date") or "", issue.get("affected_parts") or "",
                ]
                for col, value in enumerate(values):
                    item = QTableWidgetItem(str(value))
                    item.setData(Qt.UserRole, issue_id)
                    if col == 3:
                        item.setForeground(QBrush(QColor(PRIORITY_COLORS.get(issue["priority"], "#374151"))))
                    self.issue_table.setItem(row, col, item)
            if selected_row is None and issues:
                selected_row = 0
            if selected_row is not None:
                self.issue_table.selectRow(selected_row)
                self.issue_table.setCurrentCell(selected_row, 0)
        finally:
            self.issue_table.blockSignals(signals_were_blocked)
        self.issue_table.resizeColumnsToContents()
        self.issue_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.issue_table.horizontalHeader().setSectionResizeMode(7, QHeaderView.Stretch)
        if selected_row is not None:
            self._sync_issue_details_from_row(selected_row)
        else:
            self._clear_issue_details()

    def open_for_part(self, part_id: int):
        index = self.part_filter.findData(int(part_id))
        if index >= 0:
            self.part_filter.setCurrentIndex(index)
        self.refresh()

    def clear_part_filter(self):
        self.part_filter.setCurrentIndex(0)
        self.refresh()

    def create_for_part(self, part_id: int):
        self._show_dialog(preselected_part_id=part_id)

    def create_issue(self):
        self._show_dialog()

    def edit_issue(self):
        if not self.current_issue_id:
            return
        self._show_dialog(self.service.get_issue(self.current_issue_id))

    def _show_dialog(self, issue=None, preselected_part_id=None):
        dialog = IssueDialog(self.service, issue, preselected_part_id, self)
        if dialog.exec_() != QDialog.Accepted:
            return
        values = dialog.values()
        affected_part_ids = {
            int(part["id"]) for part in (issue or {}).get("parts", []) if part.get("id") is not None
        }
        affected_part_ids.update(int(part_id) for part_id in values["part_ids"])
        try:
            if issue:
                saved = self.service.update_issue(issue["id"], values["data"], values["part_ids"])
            else:
                saved = self.service.create_issue(values["data"], values["part_ids"])
            for path in values["attachments"]:
                self.service.add_attachment(saved["id"], path, self._working_dir())
            self.refresh()
            self.issue_changed.emit(sorted(affected_part_ids))
        except Exception as exc:
            QMessageBox.critical(self, "Issue", str(exc))

    def _selection_changed(self):
        selected = self.issue_table.selectedItems()
        if not selected:
            return
        self._sync_issue_details_from_row(selected[0].row())

    def _sync_issue_details_from_row(self, row: int):
        if row is None or row < 0 or row >= self.issue_table.rowCount():
            self._clear_issue_details()
            return
        item = self.issue_table.item(int(row), 0)
        if not item:
            self._clear_issue_details()
            return
        issue_id = item.data(Qt.UserRole)
        if issue_id is None:
            self._clear_issue_details()
            return
        self.current_issue_id = int(issue_id)
        self.refresh_current_issue_details()

    def _clear_issue_details(self):
        self.current_issue_id = None
        self.detail_title.setText("Select an issue")
        self.detail_meta.setText("")
        self.detail_description.clear()
        self.comments_list.clear()
        self.jira_table.setRowCount(0)
        self.creo_files_table.setRowCount(0)
        self.history_table.setRowCount(0)
        self.commit_table.setRowCount(0)
        self.engineering_files_table.setRowCount(0)
        self.attachments_list.clear()

    def refresh_current_issue_details(self):
        """Reload the selected issue header, commits, audit trail, and related tabs."""
        if not self.current_issue_id:
            return
        issue = self.service.get_issue(self.current_issue_id)
        if not issue:
            return
        self.detail_title.setText(f"{issue['issue_number']}  {issue['title']}")
        part_names = ", ".join(x["name"] for x in issue.get("parts", [])) or "No affected parts"
        self.detail_meta.setText(
            f"{issue['status']} | {issue['priority']} | {issue['category']}\n"
            f"Assigned: {issue.get('assigned_to_name') or 'Unassigned'} | Due: {issue.get('due_date') or 'None'}\n"
            f"Affected: {part_names}"
        )
        description = issue.get("description") or ""
        if looks_like_html(description):
            self.detail_description.setHtml(description)
        else:
            self.detail_description.setPlainText(description)
        self.transition_combo.setCurrentText(issue["status"])
        self._load_comments()
        self._load_jira()
        self._load_creo_files(issue)
        self._load_history()
        self._load_commits()
        self._load_engineering_files()
        self._load_attachments()

    def _load_comments(self):
        self.comments_list.clear()
        for comment in self.service.comments(self.current_issue_id):
            self.comments_list.addItem(
                f"{comment.get('created_at', '')}  {comment.get('username') or 'System'}\n{comment['comment']}"
            )

    def _load_history(self):
        rows = self.service.history(self.current_issue_id)
        self.history_table.setRowCount(len(rows))
        for row, event in enumerate(rows):
            self.history_table.setItem(row, 0, QTableWidgetItem(event.get("created_at") or ""))
            self.history_table.setItem(row, 1, QTableWidgetItem(event.get("username") or "System"))
            self.history_table.setItem(row, 2, QTableWidgetItem(event.get("action") or ""))

    def _load_commits(self):
        links = self.service.commit_links_for_issue(self.current_issue_id)
        self.commit_table.setRowCount(len(links))
        for row, link in enumerate(links):
            values = [
                link["commit_id"],
                link.get("relation_type") or "solves",
                link.get("validation_status") or "",
                link.get("group_status") or link.get("commit_status") or "",
                link.get("author_name") or "",
                str(link.get("committed_at") or ""),
                link.get("merge_id") or "",
                link.get("reverted_at") or "",
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value or ""))
                item.setData(Qt.UserRole, link["commit_id"])
                self.commit_table.setItem(row, col, item)

    def open_commit_details(self, item=None):
        if not self.current_issue_id:
            return
        commit_id = None
        if item is not None:
            commit_id = item.data(Qt.UserRole)
        if not commit_id and self.commit_table.currentRow() >= 0:
            first = self.commit_table.item(self.commit_table.currentRow(), 0)
            commit_id = first.data(Qt.UserRole) if first else None
        if not commit_id:
            return
        try:
            traceability = self.service.get_issue_traceability(self.current_issue_id)
            commit = next(
                (c for c in traceability.get("linked_commits", []) if c.get("commit_id") == commit_id),
                None,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Commit Details", str(exc))
            return
        if not commit:
            QMessageBox.information(self, "Commit Details", "Commit details were not found.")
            return
        self._show_commit_details_dialog(commit, traceability.get("issue") or {})

    def _show_commit_details_dialog(self, commit: dict, issue: dict):
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Commit Details - {commit.get('commit_id') or ''}")
        dialog.resize(900, 650)
        root = QVBoxLayout(dialog)

        title = QLabel(f"{commit.get('title') or 'Commit'}")
        title.setStyleSheet("font-size:16px;font-weight:700;color:#111827;")
        root.addWidget(title)

        subtitle = QLabel(
            f"{commit.get('commit_id') or ''} | "
            f"{commit.get('group_status') or commit.get('commit_status') or 'Unknown'} | "
            f"{commit.get('author_name') or 'Unknown'} | {commit.get('committed_at') or ''}"
        )
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        tabs = QTabWidget()
        tabs.addTab(self._commit_summary_tab(commit, issue), "Summary")
        tabs.addTab(self._commit_files_tab(commit), "Files Changed")
        tabs.addTab(self._commit_engineering_docs_tab(commit), "Validation Docs")
        tabs.addTab(self._commit_validation_tab(commit), "Validation")
        tabs.addTab(self._commit_step_tab(commit), "STEP")
        root.addWidget(tabs, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.reject)
        root.addWidget(buttons)
        dialog.exec_()

    def _commit_summary_tab(self, commit: dict, issue: dict):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        table = QTableWidget()
        table.setColumnCount(2)
        table.setHorizontalHeaderLabels(["Field", "Value"])
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        rows = [
            ("Internal Issue", f"{issue.get('issue_number') or ''} - {issue.get('title') or ''}"),
            ("Commit ID", commit.get("commit_id")),
            ("Title", commit.get("title")),
            ("Message", commit.get("message")),
            ("Status", commit.get("group_status") or commit.get("commit_status")),
            ("Relation To Issue", commit.get("relation_type") or "solves"),
            ("Author", commit.get("author_name")),
            ("Commit Date", commit.get("committed_at")),
            ("Merge ID", commit.get("merge_id")),
            ("Merged By", commit.get("merged_by_name") or commit.get("merged_by")),
            ("Merged At", commit.get("merged_at")),
            ("Merge Message", commit.get("merge_message")),
            ("Approved Version", commit.get("approved_version")),
            ("PR Path", commit.get("pr_path")),
            ("Snapshot", commit.get("snapshotted_in")),
            ("Reverted At", commit.get("reverted_at")),
            ("Revert Note", commit.get("revert_note")),
        ]
        self._fill_key_value_table(table, rows)
        layout.addWidget(table)
        return tab

    def _commit_files_tab(self, commit: dict):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        files = commit.get("files_changed") or []
        table = QTableWidget()
        table.setColumnCount(8)
        table.setHorizontalHeaderLabels([
            "Change", "Filename", "Type", "Part", "AES", "Commit Row", "Status", "Source Path"
        ])
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(7, QHeaderView.Stretch)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setRowCount(len(files))
        for row, changed in enumerate(files):
            values = [
                changed.get("change_type"),
                changed.get("filename"),
                changed.get("type"),
                changed.get("part_name") or changed.get("part_id"),
                changed.get("aes_number"),
                changed.get("commit_row_id"),
                changed.get("status"),
                changed.get("file_path"),
            ]
            for col, value in enumerate(values):
                table.setItem(row, col, QTableWidgetItem(str(value or "")))
        layout.addWidget(table)
        return tab

    def _commit_validation_tab(self, commit: dict):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        table = QTableWidget()
        table.setColumnCount(2)
        table.setHorizontalHeaderLabels(["Field", "Value"])
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        rows = [
            ("Validation Status", commit.get("validation_status")),
            ("Validated By", commit.get("checked_by_name") or commit.get("validated_by")),
            ("Validated At", commit.get("validated_at")),
            ("Validation Comment", commit.get("validation_comment")),
            ("Resolution Comment", commit.get("resolution_comment")),
            ("Traceability Note", commit.get("note")),
            ("Linked By", commit.get("linked_by")),
            ("Linked At", commit.get("linked_at")),
        ]
        self._fill_key_value_table(table, rows)
        layout.addWidget(table)
        return tab

    def _commit_engineering_docs_tab(self, commit: dict):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        files = commit.get("validation_docs") or []
        table = QTableWidget()
        table.setColumnCount(8)
        table.setHorizontalHeaderLabels([
            "Role", "Type", "Filename", "Part", "Version", "Revision", "Exists", "Path"
        ])
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(7, QHeaderView.Stretch)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setRowCount(len(files))
        for row, doc in enumerate(files):
            path = ""
            try:
                path = doc.get("stored_path") or doc.get("source_path") or ""
            except Exception:
                path = doc.get("source_path") or ""
            exists = bool(path and safe_exists(path))
            values = [
                doc.get("doc_role") or doc.get("file_role"),
                doc.get("file_type"),
                doc.get("original_filename") or doc.get("display_name") or doc.get("filename"),
                doc.get("part_name") or doc.get("part_id"),
                doc.get("version_no") or "",
                doc.get("revision") or "",
                "Yes" if exists else "Missing",
                path,
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value or ""))
                item.setToolTip(str(value or ""))
                item.setData(Qt.UserRole, path)
                table.setItem(row, col, item)
        layout.addWidget(table)

        row = QHBoxLayout()
        row.addStretch()
        open_btn = QPushButton("Open Selected Doc")
        open_btn.clicked.connect(lambda _c=False, t=table: self._open_selected_commit_doc(t))
        row.addWidget(open_btn)
        layout.addLayout(row)
        return tab

    def _open_selected_commit_doc(self, table):
        row = table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Open Document", "Select a document first.")
            return
        item = table.item(row, table.columnCount() - 1)
        path = item.data(Qt.UserRole) if item else ""
        if not path or not safe_exists(path):
            QMessageBox.warning(self, "Open Document", f"File not found:\n{path}")
            return
        try:
            safe_startfile(path)
        except Exception as exc:
            QMessageBox.critical(self, "Open Document", f"Failed to open file:\n{exc}")

    def _commit_step_tab(self, commit: dict):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        table = QTableWidget()
        table.setColumnCount(2)
        table.setHorizontalHeaderLabels(["Field", "Value"])
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        rows = [
            ("STEP Compare Enabled", commit.get("step_compare_enabled")),
            ("STEP Status", commit.get("step_diff_status")),
            ("STEP Summary", commit.get("step_diff_summary")),
            ("Current STEP", commit.get("step_file_path")),
            ("Previous STEP", commit.get("step_prev_file_path")),
            ("Diff Path", commit.get("step_diff_path")),
            ("STEP Error", commit.get("step_error")),
        ]
        self._fill_key_value_table(table, rows)
        layout.addWidget(table)
        return tab

    def _fill_key_value_table(self, table, rows):
        visible_rows = [(label, value) for label, value in rows if value not in (None, "")]
        table.setRowCount(len(visible_rows))
        for row, (label, value) in enumerate(visible_rows):
            table.setItem(row, 0, QTableWidgetItem(str(label)))
            value_item = QTableWidgetItem(str(value))
            value_item.setToolTip(str(value))
            table.setItem(row, 1, value_item)

    def _load_jira(self):
        self.jira_table.setRowCount(0)
        for row, link in enumerate(self.service.jira_links(self.current_issue_id)):
            self.jira_table.insertRow(row)
            self.jira_table.setItem(row, 0, QTableWidgetItem(link.get("jira_key") or ""))
            self.jira_table.setItem(row, 1, QTableWidgetItem(link.get("jira_url") or ""))
            self.jira_table.setItem(row, 2, QTableWidgetItem(link.get("jira_status") or ""))
            self.jira_table.setItem(row, 3, QTableWidgetItem(link.get("created_at") or ""))

    def open_selected_jira_link(self, item=None):
        row = item.row() if hasattr(item, "row") else self.jira_table.currentRow()
        if row < 0:
            return QMessageBox.warning(self, "Jira", "Select a Jira link first.")
        url_item = self.jira_table.item(row, 1)
        url = str(url_item.text() if url_item else "").strip()
        if not url:
            return QMessageBox.information(self, "Jira", "The selected Jira link has no URL.")
        try:
            safe_startfile(url)
        except Exception as exc:
            QMessageBox.warning(self, "Jira", f"Unable to open Jira link:\n{exc}")

    def _load_creo_files(self, issue):
        parts = issue.get("parts", []) if issue else []
        rows = []
        for part in parts:
            cad_name = part.get("filename") or part.get("base_file_name")
            if cad_name:
                rows.append([
                    part.get("id"),
                    part.get("name"),
                    "CAD",
                    part.get("type"),
                    cad_name,
                    part.get("base_file_name"),
                    part.get("revision"),
                ])
            drw_name = part.get("drawing") or part.get("base_drw_name")
            if drw_name:
                rows.append([
                    part.get("id"),
                    part.get("name"),
                    "Drawing",
                    "DRW",
                    drw_name,
                    part.get("base_drw_name"),
                    part.get("revision"),
                ])
            if not cad_name and not drw_name:
                rows.append([
                    part.get("id"),
                    part.get("name"),
                    "BOM Part",
                    part.get("type"),
                    "",
                    "",
                    part.get("revision"),
                ])
        self.creo_files_table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for col, value in enumerate(values):
                self.creo_files_table.setItem(row, col, QTableWidgetItem(str(value or "")))

    def _load_engineering_files(self):
        files = self.service.engineering_files_for_issue(self.current_issue_id)
        self.engineering_files_table.setRowCount(len(files))
        for row, link in enumerate(files):
            values = [
                link.get("file_role"),
                link.get("part_name") or link.get("part_id"),
                link.get("display_name"),
                link.get("file_type"),
                link.get("version_no"),
                link.get("original_filename"),
                (link.get("sha256") or "")[:12],
                link.get("linked_at"),
            ]
            for col, value in enumerate(values):
                self.engineering_files_table.setItem(row, col, QTableWidgetItem(str(value or "")))

    def _load_attachments(self):
        self.attachments_list.clear()
        for attachment in self.service.attachments(self.current_issue_id):
            item = QListWidgetItem(f"{attachment['file_name']}  |  {attachment['created_at']}")
            item.setToolTip(attachment["file_path"])
            self.attachments_list.addItem(item)

    def _working_dir(self):
        project = ProjectService().get_project_by_id(self.service.project_id) or {}
        return project.get("working_directory") or ""

    def add_attachment(self):
        if not self.current_issue_id:
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "Select Issue Attachments")
        try:
            for path in paths:
                self.service.add_attachment(self.current_issue_id, path, self._working_dir())
            self._load_attachments()
            self._load_history()
        except Exception as exc:
            QMessageBox.warning(self, "Attachment", str(exc))

    def add_jira_link(self):
        if not self.current_issue_id:
            return
        try:
            self.service.link_jira(
                self.current_issue_id,
                self.jira_key_edit.text().strip(),
                self.jira_url_edit.text().strip(),
            )
            self.jira_key_edit.clear()
            self.jira_url_edit.clear()
            self._load_jira()
            self._load_history()
        except Exception as exc:
            QMessageBox.warning(self, "Jira", str(exc))

    def _set_processing_state(self, busy: bool, message: str = "Processing..."):
        self._processing_action = bool(busy)
        try:
            if busy:
                self.processing_overlay.show_overlay(message)
                QApplication.setOverrideCursor(Qt.WaitCursor)
            else:
                self.processing_overlay.hide_overlay()
                QApplication.restoreOverrideCursor()
        except Exception:
            pass

        try:
            self.export_traceability_btn.setEnabled(not busy)
        except Exception:
            pass

        try:
            QApplication.processEvents()
        except Exception:
            pass

    def _run_processing_task(self, message: str, work_fn, success_fn=None, error_fn=None):
        if getattr(self, "_processing_action", False):
            return
        self._set_processing_state(True, message)
        thread = QThread(self)
        worker = _IssueProcessingWorker(work_fn)
        worker.moveToThread(thread)

        def cleanup():
            self._set_processing_state(False)
            self._processing_worker = None
            self._processing_thread = None

        def handle_success(result):
            cleanup()
            if success_fn:
                success_fn(result)

        def handle_error(exc):
            cleanup()
            if error_fn:
                error_fn(exc)
            else:
                QMessageBox.critical(self, "Error", str(exc))

        thread.started.connect(worker.run)
        worker.finished.connect(handle_success)
        worker.failed.connect(handle_error)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._processing_thread = thread
        self._processing_worker = worker
        thread.start()

    def export_current_traceability(self):
        if getattr(self, "_processing_action", False):
            return
        if not self.current_issue_id:
            return
        folder = QFileDialog.getExistingDirectory(
            self,
            "Export Issue Package",
        )
        if not folder:
            return

        issue_id = int(self.current_issue_id)

        def work():
            return self.service.export_traceability_package(issue_id, folder)

        def success(manifest):
            QMessageBox.information(
                self,
                "Export",
                "Issue package exported successfully:\n"
                f"{manifest.get('package_dir')}\n\n"
                f"Input files: {len(manifest.get('input_files') or [])}\n"
                f"Engineering output files: {len(manifest.get('output_files') or [])}\n"
                f"Validation docs: {len(manifest.get('validation_docs') or [])}",
            )

        def error(exc):
            QMessageBox.critical(self, "Export", str(exc))

        self._run_processing_task("Exporting traceability...", work, success, error)

    def change_status(self):
        if not self.current_issue_id:
            return
        try:
            issue = self.service.transition(self.current_issue_id, self.transition_combo.currentText())
            self.refresh()
            self.issue_changed.emit([int(part["id"]) for part in issue.get("parts", [])])
        except Exception as exc:
            QMessageBox.warning(self, "Issue Workflow", str(exc))

    def add_comment(self):
        if not self.current_issue_id:
            return
        try:
            self.service.add_comment(self.current_issue_id, self.comment_edit.toPlainText())
            self.comment_edit.clear()
            self._load_comments()
            self._load_history()
            self.issue_changed.emit([])
        except Exception as exc:
            QMessageBox.warning(self, "Comment", str(exc))

    def archive_issue(self):
        if not self.current_issue_id:
            return
        reason, accepted = QInputDialog.getText(
            self, "Archive Issue", "Archive reason (audit history is preserved):"
        )
        if not accepted:
            return
        try:
            issue = self.service.get_issue(self.current_issue_id) or {}
            affected_part_ids = [int(part["id"]) for part in issue.get("parts", [])]
            self.service.archive(self.current_issue_id, reason)
            self.current_issue_id = None
            self.refresh()
            self.issue_changed.emit(affected_part_ids)
        except Exception as exc:
            QMessageBox.warning(self, "Archive Issue", str(exc))
