from PyQt5.QtCore import QDate, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QBrush
from PyQt5.QtWidgets import (
    QAbstractItemView,
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
        self.description_edit = QTextEdit(self.issue.get("description", ""))
        self.description_edit.setMinimumHeight(130)
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
                "description": self.description_edit.toPlainText().strip(),
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


class EngineeringIssuePage(QWidget):
    issue_changed = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.service = IssueService()
        self.current_issue_id = None
        self._build_ui()
        if self.service.project_id:
            self.refresh()

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
        splitter.addWidget(self.issue_table)

        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        self.detail_title = QLabel("Select an issue")
        self.detail_title.setStyleSheet("font-size:16px;font-weight:700;color:#111827;")
        self.detail_meta = QLabel("")
        self.detail_meta.setWordWrap(True)
        self.detail_description = QTextEdit()
        self.detail_description.setReadOnly(True)
        self.detail_description.setMaximumHeight(130)
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
        actions.addWidget(edit_btn)
        actions.addWidget(self.transition_combo)
        actions.addWidget(transition_btn)
        actions.addWidget(archive_btn)
        detail_layout.addLayout(actions)

        tabs = QTabWidget()
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
        tabs.addTab(self.history_table, "Audit Trail")

        self.commit_table = QTableWidget()
        self.commit_table.setColumnCount(5)
        self.commit_table.setHorizontalHeaderLabels(["Commit", "Validation", "Commit Status", "Merge", "Snapshot"])
        self.commit_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.commit_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tabs.addTab(self.commit_table, "Commits")

        attachments_tab = QWidget()
        attachments_layout = QVBoxLayout(attachments_tab)
        self.attachments_list = QListWidget()
        self.attachments_list.itemDoubleClicked.connect(
            lambda item: os.startfile(item.toolTip()) if item and item.toolTip() else None
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
            if selected_row is not None:
                self.issue_table.selectRow(selected_row)
        finally:
            self.issue_table.blockSignals(signals_were_blocked)
        self.issue_table.resizeColumnsToContents()
        self.issue_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.issue_table.horizontalHeader().setSectionResizeMode(7, QHeaderView.Stretch)
        if selected_issue_id is not None:
            self.refresh_current_issue_details()

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
        self.current_issue_id = int(selected[0].data(Qt.UserRole))
        self.refresh_current_issue_details()

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
        self.detail_description.setPlainText(issue.get("description") or "")
        self.transition_combo.setCurrentText(issue["status"])
        self._load_comments()
        self._load_history()
        self._load_commits()
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
        links = self.service.repo.commit_links_for_issue(self.current_issue_id)
        self.commit_table.setRowCount(len(links))
        for row, link in enumerate(links):
            self.commit_table.setItem(row, 0, QTableWidgetItem(link["commit_id"]))
            self.commit_table.setItem(row, 1, QTableWidgetItem(link["validation_status"]))
            self.commit_table.setItem(row, 2, QTableWidgetItem(link.get("commit_status") or ""))
            self.commit_table.setItem(row, 3, QTableWidgetItem(link.get("merge_id") or ""))
            self.commit_table.setItem(row, 4, QTableWidgetItem(link.get("snapshotted_in") or ""))

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
