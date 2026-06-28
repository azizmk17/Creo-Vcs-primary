import os
from pathlib import Path

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget,
    QComboBox, QFrame, QGroupBox, QTabWidget, QTableWidget, QTableWidgetItem,
    QMessageBox, QHeaderView, QInputDialog, QFileDialog, QLineEdit,
    QSizePolicy, QSplitter, QAbstractItemView, QApplication, QPlainTextEdit
)
from PyQt5.QtGui import QFont
from PyQt5.QtCore import Qt

from core.services.admin_service import AdminService
from core.services.role_service import RoleService
from core.services.permission_service import PermissionService
from core.services.user_service import UserService
from core.services.project_service import ProjectService
from core.repositories.user_repository import UserRepository


class AdminMetricCard(QFrame):
    def __init__(self, label, accent, parent=None):
        super().__init__(parent)
        self.setObjectName("adminMetricCard")
        self.setProperty("accent", accent)
        self.setMinimumWidth(130)
        self.setFixedHeight(72)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(0)
        self.value_label = QLabel("-")
        self.value_label.setObjectName("adminMetricValue")
        self.value_label.setStyleSheet(f"color: {accent};")
        label_widget = QLabel(label)
        label_widget.setObjectName("adminMetricLabel")
        layout.addWidget(self.value_label)
        layout.addWidget(label_widget)

    def set_value(self, value):
        self.value_label.setText(str(value))


class AdminPage(QWidget):
    def __init__(self):
        super().__init__()
        self.admin_service = AdminService()
        self.user_service = UserService(UserRepository())
        self.role_service = RoleService()
        self.permission_service = PermissionService()
        self.project_service = ProjectService()

        self.init_ui()

    # ----------------------------- UI SETUP -----------------------------
    def init_ui(self):
        self.setObjectName("adminPage")
        self.setStyleSheet(self._page_stylesheet())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        layout.addWidget(self.create_header())

        metrics = QHBoxLayout()
        metrics.setSpacing(10)
        self.user_metric = AdminMetricCard("User accounts", "#2563eb")
        self.role_metric = AdminMetricCard("Security roles", "#7c3aed")
        self.permission_metric = AdminMetricCard("Permissions", "#0891b2")
        self.project_metric = AdminMetricCard("Projects", "#16a34a")
        for card in (self.user_metric, self.role_metric, self.permission_metric, self.project_metric):
            metrics.addWidget(card)
        layout.addLayout(metrics)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("adminTabs")
        self.tabs.addTab(self.create_user_role_tab(), "Users")
        self.tabs.addTab(self.create_role_perm_tab(), "Access Control")
        self.tabs.addTab(self.create_project_tab(), "Projects")
        self.tabs.addTab(self.create_configuration_tab(), "Configuration")
        layout.addWidget(self.tabs, 1)
        self.refresh_stats()

    def create_header(self):
        frame = QFrame()
        frame.setObjectName("adminHeader")
        hbox = QHBoxLayout(frame)
        hbox.setContentsMargins(16, 12, 16, 12)
        hbox.setSpacing(12)

        text = QVBoxLayout()
        text.setSpacing(1)
        title = QLabel("Administration")
        title.setObjectName("adminTitle")
        subtitle = QLabel("Workspace access, project membership, and security policy")
        subtitle.setObjectName("adminSubtitle")
        text.addWidget(title)
        text.addWidget(subtitle)
        hbox.addLayout(text)
        hbox.addStretch()

        self.stats_label = QLabel("System configuration")
        self.stats_label.setObjectName("adminStatus")
        self.stats_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        hbox.addWidget(self.stats_label)
        refresh = self.create_button("Refresh", "neutral", self.refresh_all)
        refresh.setToolTip("Reload administration data")
        hbox.addWidget(refresh)
        return frame

    def refresh_stats(self):
        try:
            users = len(self.user_service.get_all_users())
            roles = len(self.role_service.get_all_roles())
            perms = len(self.permission_service.get_all_permissions())
            projects = len(self.project_service.get_all_projects())
            self.user_metric.set_value(users)
            self.role_metric.set_value(roles)
            self.permission_metric.set_value(perms)
            self.project_metric.set_value(projects)
            self.stats_label.setText("Configuration up to date")
        except Exception:
            for card in (
                getattr(self, "user_metric", None),
                getattr(self, "role_metric", None),
                getattr(self, "permission_metric", None),
                getattr(self, "project_metric", None),
            ):
                if card:
                    card.set_value("-")
            self.stats_label.setText("Unable to load summary")

    def refresh_all(self):
        self.reload_users()
        self._reload_roles()
        self._reload_permissions()
        self.load_projects()
        self.load_user_roles()
        self.load_role_permissions()
        self.load_project_members()
        self.load_configuration_admin()
        self.refresh_stats()

    def _page_stylesheet(self):
        return """
        QWidget#adminPage { background: #f4f6f8; }
        QFrame#adminHeader {
            background: #ffffff; border: 1px solid #dfe3e8; border-radius: 6px;
        }
        QLabel#adminTitle { color: #172033; font-size: 20px; font-weight: 700; }
        QLabel#adminSubtitle { color: #687386; font-size: 11px; font-weight: 400; }
        QLabel#adminStatus { color: #526071; font-size: 10px; font-weight: 600; }
        QFrame#adminMetricCard {
            background: #ffffff; border: 1px solid #dfe3e8; border-radius: 6px;
        }
        QLabel#adminMetricValue { font-size: 21px; font-weight: 700; background: transparent; }
        QLabel#adminMetricLabel { color: #687386; font-size: 10px; font-weight: 600; background: transparent; }
        QTabWidget#adminTabs::pane {
            background: #ffffff; border: 1px solid #dfe3e8; border-radius: 0 6px 6px 6px;
        }
        QTabWidget#adminTabs QTabBar::tab {
            min-width: 112px; padding: 9px 16px; margin-right: 2px;
            background: #e9edf2; color: #526071; border: 1px solid #dfe3e8;
            border-bottom: none; border-radius: 5px 5px 0 0; font-weight: 600;
        }
        QTabWidget#adminTabs QTabBar::tab:selected { background: #ffffff; color: #1d4ed8; }
        QFrame#adminPanel {
            background: #ffffff; border: 1px solid #dfe3e8; border-radius: 6px;
        }
        QLabel#sectionTitle { color: #172033; font-size: 13px; font-weight: 700; }
        QLabel#sectionCaption { color: #738094; font-size: 10px; font-weight: 400; }
        QLabel#fieldLabel { color: #526071; font-size: 10px; font-weight: 600; }
        QComboBox, QLineEdit, QPlainTextEdit {
            min-height: 24px; background: #ffffff; border: 1px solid #cfd6df;
            border-radius: 4px; padding: 3px 7px; color: #172033;
        }
        QComboBox:focus, QLineEdit:focus, QPlainTextEdit:focus { border-color: #2563eb; }
        QListWidget, QTableWidget {
            background: #ffffff; border: 1px solid #dfe3e8; border-radius: 4px;
            alternate-background-color: #f8fafc; selection-background-color: #e5efff;
            selection-color: #172033; outline: none;
        }
        QListWidget::item { min-height: 24px; padding: 3px 7px; }
        QListWidget::item:selected { border-left: 3px solid #2563eb; }
        QTableWidget::item { padding: 5px 7px; border-bottom: 1px solid #edf0f3; }
        QHeaderView::section {
            background: #f3f5f7; color: #526071; border: none;
            border-bottom: 1px solid #dfe3e8; padding: 7px; font-size: 10px; font-weight: 700;
        }
        QPushButton { min-height: 26px; padding: 3px 10px; border-radius: 4px; font-weight: 600; }
        QPushButton#primary { background: #2563eb; color: #ffffff; border: 1px solid #2563eb; }
        QPushButton#primary:hover { background: #1d4ed8; }
        QPushButton#neutral { background: #ffffff; color: #334155; border: 1px solid #cfd6df; }
        QPushButton#neutral:hover { background: #f3f5f7; }
        QPushButton#danger { background: #ffffff; color: #b42318; border: 1px solid #e6b8b3; }
        QPushButton#danger:hover { background: #fff1f0; }
        QSplitter::handle { background: #edf0f3; }
        """

    def _section_header(self, title, caption, actions=None):
        row = QHBoxLayout()
        row.setSpacing(8)
        text = QVBoxLayout()
        text.setSpacing(1)
        title_label = QLabel(title)
        title_label.setObjectName("sectionTitle")
        caption_label = QLabel(caption)
        caption_label.setObjectName("sectionCaption")
        text.addWidget(title_label)
        text.addWidget(caption_label)
        row.addLayout(text)
        row.addStretch()
        for action in actions or []:
            row.addWidget(action)
        return row

    def _panel(self):
        panel = QFrame()
        panel.setObjectName("adminPanel")
        return panel

    def _field_label(self, text):
        label = QLabel(text)
        label.setObjectName("fieldLabel")
        return label

    # -------------------- TAB 1: USER ROLE MANAGEMENT --------------------
    def create_user_role_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        btn_new_user = self.create_button("New User", "primary", self.create_user)
        btn_edit_user = self.create_button("Edit User", "neutral", self.edit_user)
        btn_delete_user = self.create_button("Delete User", "danger", self.delete_user)
        layout.addLayout(self._section_header(
            "User access",
            "Manage accounts and role membership",
            [btn_new_user, btn_edit_user, btn_delete_user],
        ))

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        assigned_panel = self._panel()
        assigned_layout = QVBoxLayout(assigned_panel)
        assigned_layout.setContentsMargins(12, 12, 12, 12)
        assigned_layout.setSpacing(8)
        assigned_layout.addWidget(self._field_label("Selected user"))
        self.user_combo = QComboBox()
        self.user_combo.setMinimumWidth(220)
        for u in self.user_service.get_all_users():
            self.user_combo.addItem(u.username, u.id)
        assigned_layout.addWidget(self.user_combo)
        assigned_layout.addWidget(self._field_label("Assigned roles"))
        self.user_roles_list = QListWidget()
        self.user_roles_list.setAlternatingRowColors(True)
        assigned_layout.addWidget(self.user_roles_list, 1)
        remove = self.create_button("Remove Selected Role", "danger", self.remove_role_from_user)
        assigned_layout.addWidget(remove, 0, Qt.AlignRight)
        splitter.addWidget(assigned_panel)

        available_panel = self._panel()
        available_layout = QVBoxLayout(available_panel)
        available_layout.setContentsMargins(12, 12, 12, 12)
        available_layout.setSpacing(8)
        available_layout.addWidget(self._field_label("Available role"))
        self.role_combo = QComboBox()
        for r in self.role_service.get_all_roles():
            self.role_combo.addItem(r["name"], r["id"])
        available_layout.addWidget(self.role_combo)
        assign = self.create_button("Assign Role", "primary", self.assign_role_to_user)
        available_layout.addWidget(assign, 0, Qt.AlignLeft)
        available_layout.addStretch()
        splitter.addWidget(available_panel)
        splitter.setSizes([620, 360])
        layout.addWidget(splitter, 1)

        self.user_combo.currentIndexChanged.connect(self.load_user_roles)
        self.load_user_roles()
        return tab

    def _selected_user_id(self):
        try:
            return int(self.user_combo.currentData())
        except Exception:
            return None

    def create_user(self):
        username, ok = QInputDialog.getText(self, "New User", "Username:")
        if not (ok and username and username.strip()):
            return
        email, ok = QInputDialog.getText(self, "New User", "Email:")
        if not (ok and email and email.strip()):
            return
        password, ok = QInputDialog.getText(self, "New User", "Password:", QLineEdit.Password)
        if not (ok and password):
            return
        confirm, ok = QInputDialog.getText(self, "New User", "Confirm password:", QLineEdit.Password)
        if not (ok and confirm):
            return
        if password != confirm:
            return QMessageBox.warning(self, "Mismatch", "Passwords do not match.")

        is_admin = QMessageBox.question(
            self,
            "Admin",
            "Make this user an admin?",
            QMessageBox.Yes | QMessageBox.No,
        ) == QMessageBox.Yes

        try:
            new_id = self.user_service.create_user(username.strip(), email.strip(), password, is_admin=is_admin)
        except Exception as e:
            return QMessageBox.warning(self, "Failed", str(e))

        self.reload_users()
        idx = self.user_combo.findData(int(new_id))
        if idx >= 0:
            self.user_combo.setCurrentIndex(idx)
        self.refresh_stats()
        QMessageBox.information(self, "Created", f"User created (ID {new_id}).")

    def edit_user(self):
        uid = self._selected_user_id()
        if not uid:
            return QMessageBox.warning(self, "Select", "Select a user to edit.")

        user = self.user_service.get_user_by_id(int(uid))
        if not user:
            return QMessageBox.warning(self, "Not found", "User not found.")

        username, ok = QInputDialog.getText(self, "Edit User", "Username:", text=str(user.username or ""))
        if not (ok and username and username.strip()):
            return
        email, ok = QInputDialog.getText(self, "Edit User", "Email:", text=str(user.email or ""))
        if not (ok and email and email.strip()):
            return

        # Optional password reset
        password = None
        if QMessageBox.question(
            self,
            "Reset Password",
            "Reset this user's password?",
            QMessageBox.Yes | QMessageBox.No,
        ) == QMessageBox.Yes:
            pwd1, ok = QInputDialog.getText(self, "Reset Password", "New password:", QLineEdit.Password)
            if not (ok and pwd1):
                return
            pwd2, ok = QInputDialog.getText(self, "Reset Password", "Confirm new password:", QLineEdit.Password)
            if not (ok and pwd2):
                return
            if pwd1 != pwd2:
                return QMessageBox.warning(self, "Mismatch", "Passwords do not match.")
            password = pwd1

        is_admin = QMessageBox.question(
            self,
            "Admin",
            "Should this user be admin?",
            QMessageBox.Yes | QMessageBox.No,
        ) == QMessageBox.Yes

        try:
            self.user_service.update_user(int(uid), username=username.strip(), email=email.strip(), password=password, is_admin=is_admin)
        except Exception as e:
            return QMessageBox.warning(self, "Failed", str(e))

        self.reload_users()
        idx = self.user_combo.findData(int(uid))
        if idx >= 0:
            self.user_combo.setCurrentIndex(idx)
        self.load_user_roles()
        self.refresh_stats()
        QMessageBox.information(self, "Updated", "User updated.")

    def delete_user(self):
        uid = self._selected_user_id()
        if not uid:
            return QMessageBox.warning(self, "Select", "Select a user to delete.")

        username = self.user_combo.currentText()
        res = QMessageBox.question(
            self,
            "Confirm",
            f"Delete user '{username}' (ID {uid})?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if res != QMessageBox.Yes:
            return

        try:
            self.user_service.delete_user(int(uid))
        except Exception as e:
            return QMessageBox.warning(self, "Failed", str(e))

        self.reload_users()
        self.user_roles_list.clear()
        self.refresh_stats()
        QMessageBox.information(self, "Deleted", "User deleted.")

    # -------------------- TAB 2: ROLE PERMISSION MANAGEMENT --------------------
    def create_role_perm_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        btn_new_role = self.create_button("New Role", "primary", self.create_role)
        btn_rename_role = self.create_button("Rename Role", "neutral", self.rename_role)
        btn_delete_role = self.create_button("Delete Role", "danger", self.delete_role)
        layout.addLayout(self._section_header(
            "Access control",
            "Configure roles and their permission sets",
            [btn_new_role, btn_rename_role, btn_delete_role],
        ))

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        assigned_panel = self._panel()
        assigned_layout = QVBoxLayout(assigned_panel)
        assigned_layout.setContentsMargins(12, 12, 12, 12)
        assigned_layout.setSpacing(8)
        assigned_layout.addWidget(self._field_label("Selected role"))
        self.role_perm_combo = QComboBox()
        for r in self.role_service.get_all_roles():
            self.role_perm_combo.addItem(r["name"], r["id"])
        assigned_layout.addWidget(self.role_perm_combo)
        assigned_layout.addWidget(self._field_label("Assigned permissions"))
        self.role_permissions_list = QListWidget()
        self.role_permissions_list.setAlternatingRowColors(True)
        assigned_layout.addWidget(self.role_permissions_list, 1)
        rem = self.create_button("Remove Selected Permission", "danger", self.remove_permission_from_role)
        assigned_layout.addWidget(rem, 0, Qt.AlignRight)
        splitter.addWidget(assigned_panel)

        available_panel = self._panel()
        available_layout = QVBoxLayout(available_panel)
        available_layout.setContentsMargins(12, 12, 12, 12)
        available_layout.setSpacing(8)
        available_layout.addWidget(self._field_label("Available permission"))
        self.permission_combo = QComboBox()
        for p in self.permission_service.get_all_permissions():
            self.permission_combo.addItem(p["name"], p["id"])
        available_layout.addWidget(self.permission_combo)
        add = self.create_button("Add Permission", "primary", self.add_permission_to_role)
        btn_new_perm = self.create_button("New Permission", "neutral", self.create_permission)
        btn_del_perm = self.create_button("Delete Permission", "danger", self.delete_permission)
        available_layout.addWidget(add, 0, Qt.AlignLeft)
        available_layout.addStretch()
        permission_actions = QHBoxLayout()
        permission_actions.addWidget(btn_new_perm)
        permission_actions.addWidget(btn_del_perm)
        available_layout.addLayout(permission_actions)
        splitter.addWidget(available_panel)
        splitter.setSizes([620, 360])
        layout.addWidget(splitter, 1)

        self.role_perm_combo.currentIndexChanged.connect(self.load_role_permissions)
        self.load_role_permissions()
        return tab

    def _reload_roles(self):
        roles = self.role_service.get_all_roles()
        self.role_combo.clear()
        self.role_perm_combo.clear()
        for r in roles:
            self.role_combo.addItem(r["name"], r["id"])
            self.role_perm_combo.addItem(r["name"], r["id"])

    def _reload_permissions(self):
        perms = self.permission_service.get_all_permissions()
        self.permission_combo.clear()
        for p in perms:
            self.permission_combo.addItem(p["name"], p["id"])

    def create_role(self):
        name, ok = QInputDialog.getText(self, "New Role", "Role name:")
        if not (ok and name and name.strip()):
            return
        try:
            self.role_service.create_role(name.strip())
        except Exception as e:
            return QMessageBox.warning(self, "Failed", str(e))
        self._reload_roles()
        self.refresh_stats()

    def rename_role(self):
        role_id = self.role_perm_combo.currentData()
        if not role_id:
            return QMessageBox.warning(self, "Select", "Select a role to rename.")
        current_name = self.role_perm_combo.currentText()
        new_name, ok = QInputDialog.getText(self, "Rename Role", "New role name:", text=current_name)
        if not (ok and new_name and new_name.strip()):
            return
        try:
            self.role_service.rename_role(int(role_id), new_name.strip())
        except Exception as e:
            return QMessageBox.warning(self, "Failed", str(e))
        self._reload_roles()
        self.refresh_stats()

    def delete_role(self):
        role_id = self.role_perm_combo.currentData()
        if not role_id:
            return QMessageBox.warning(self, "Select", "Select a role to delete.")
        res = QMessageBox.question(
            self,
            "Confirm",
            f"Delete role '{self.role_perm_combo.currentText()}'?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if res != QMessageBox.Yes:
            return
        try:
            self.role_service.delete_role(int(role_id))
        except Exception as e:
            return QMessageBox.warning(self, "Failed", str(e))
        self._reload_roles()
        self.load_user_roles()
        self.load_role_permissions()
        self.refresh_stats()

    def create_permission(self):
        name, ok = QInputDialog.getText(self, "New Permission", "Permission name:")
        if not (ok and name and name.strip()):
            return
        try:
            self.permission_service.create_permission(name.strip())
        except Exception as e:
            return QMessageBox.warning(self, "Failed", str(e))
        self._reload_permissions()
        self.refresh_stats()

    def delete_permission(self):
        perm_id = self.permission_combo.currentData()
        if not perm_id:
            return QMessageBox.warning(self, "Select", "Select a permission to delete.")
        res = QMessageBox.question(
            self,
            "Confirm",
            f"Delete permission '{self.permission_combo.currentText()}'?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if res != QMessageBox.Yes:
            return
        try:
            self.permission_service.delete_permission(int(perm_id))
        except Exception as e:
            return QMessageBox.warning(self, "Failed", str(e))
        self._reload_permissions()
        self.load_role_permissions()
        self.refresh_stats()

    # -------------------- TAB 3: PROJECT MANAGEMENT --------------------
    def create_project_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        btn_add = self.create_button("New Project", "primary", self.create_project)
        btn_edit = self.create_button("Edit Project", "neutral", self.edit_project)
        btn_del = self.create_button("Delete Project", "danger", self.delete_project)
        layout.addLayout(self._section_header(
            "Project registry",
            "Manage workspaces and project membership",
            [btn_add, btn_edit, btn_del],
        ))

        splitter = QSplitter(Qt.Vertical)
        splitter.setChildrenCollapsible(False)
        project_panel = self._panel()
        project_layout = QVBoxLayout(project_panel)
        project_layout.setContentsMargins(10, 10, 10, 10)
        project_layout.setSpacing(8)
        self.project_search = QLineEdit()
        self.project_search.setPlaceholderText("Search projects")
        self.project_search.setClearButtonEnabled(True)
        self.project_search.textChanged.connect(self._filter_projects)
        project_layout.addWidget(self.project_search)

        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(["ID", "Name", "Version", "State", "Working Dir", "Created At"])
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.setSortingEnabled(True)
        project_layout.addWidget(self.table)
        splitter.addWidget(project_panel)

        members_panel = self._panel()
        members_layout = QVBoxLayout(members_panel)
        members_layout.setContentsMargins(10, 10, 10, 10)
        members_layout.setSpacing(8)
        members_layout.addLayout(self._section_header("Project members", "Membership for the selected project"))
        members_top = QHBoxLayout()
        self.project_user_combo = QComboBox()
        members_top.addWidget(self._field_label("User"))
        members_top.addWidget(self.project_user_combo)
        btn_add_user = self.create_button("Add Member", "primary", self.add_user_to_project)
        btn_remove_user = self.create_button("Remove Member", "danger", self.remove_user_from_project)
        members_top.addWidget(btn_add_user)
        members_top.addWidget(btn_remove_user)
        members_layout.addLayout(members_top)
        self.project_members_list = QListWidget()
        self.project_members_list.setAlternatingRowColors(True)
        members_layout.addWidget(self.project_members_list)
        splitter.addWidget(members_panel)
        splitter.setSizes([430, 220])
        layout.addWidget(splitter, 1)

        self.table.itemSelectionChanged.connect(self.load_project_members)
        self.reload_users()
        self.load_projects()
        return tab

    # -------------------- TAB 4: CONFIGURATION / METADATA --------------------
    def create_configuration_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        refresh_btn = self.create_button("Refresh", "neutral", self.load_configuration_admin)
        layout.addLayout(self._section_header(
            "System configuration",
            "Inspect database, license, workspace paths, and administrative metadata",
            [refresh_btn],
        ))

        splitter = QSplitter(Qt.Vertical)
        splitter.setChildrenCollapsible(False)

        paths_panel = self._panel()
        paths_layout = QVBoxLayout(paths_panel)
        paths_layout.setContentsMargins(10, 10, 10, 10)
        paths_layout.setSpacing(8)
        self.config_summary_label = QLabel("Application version: -")
        self.config_summary_label.setObjectName("sectionCaption")
        paths_layout.addWidget(self.config_summary_label)
        self.config_paths_table = QTableWidget()
        self.config_paths_table.setColumnCount(4)
        self.config_paths_table.setHorizontalHeaderLabels(["Item", "Path", "Type", "Status"])
        self.config_paths_table.setAlternatingRowColors(True)
        self.config_paths_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.config_paths_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.config_paths_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.config_paths_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.config_paths_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.config_paths_table.verticalHeader().setVisible(False)
        self.config_paths_table.verticalHeader().setDefaultSectionSize(30)
        paths_layout.addWidget(self.config_paths_table, 1)
        path_actions = QHBoxLayout()
        path_actions.addStretch()
        path_actions.addWidget(self.create_button("Copy Path", "neutral", self.copy_selected_config_path))
        path_actions.addWidget(self.create_button("Open Folder", "neutral", self.open_selected_config_folder))
        path_actions.addWidget(self.create_button("Open File", "neutral", self.open_selected_config_file))
        paths_layout.addLayout(path_actions)
        splitter.addWidget(paths_panel)

        metadata_panel = self._panel()
        metadata_layout = QVBoxLayout(metadata_panel)
        metadata_layout.setContentsMargins(10, 10, 10, 10)
        metadata_layout.setSpacing(8)
        warning = QLabel(
            "Metadata edits are immediate. Version and signed licensing keys can affect startup checks."
        )
        warning.setObjectName("sectionCaption")
        metadata_layout.addWidget(warning)

        editor_row = QHBoxLayout()
        editor_left = QVBoxLayout()
        editor_left.addWidget(self._field_label("Key"))
        self.metadata_key_input = QLineEdit()
        self.metadata_key_input.setPlaceholderText("Metadata key")
        editor_left.addWidget(self.metadata_key_input)
        editor_right = QVBoxLayout()
        editor_right.addWidget(self._field_label("Value"))
        self.metadata_value_input = QPlainTextEdit()
        self.metadata_value_input.setPlaceholderText("Metadata value")
        self.metadata_value_input.setFixedHeight(62)
        editor_right.addWidget(self.metadata_value_input)
        editor_row.addLayout(editor_left, 1)
        editor_row.addLayout(editor_right, 2)
        metadata_layout.addLayout(editor_row)

        editor_actions = QHBoxLayout()
        editor_actions.addStretch()
        editor_actions.addWidget(self.create_button("New", "neutral", self.clear_metadata_editor))
        editor_actions.addWidget(self.create_button("Save", "primary", self.save_metadata_item))
        editor_actions.addWidget(self.create_button("Delete", "danger", self.delete_metadata_item))
        metadata_layout.addLayout(editor_actions)

        self.metadata_table = QTableWidget()
        self.metadata_table.setColumnCount(2)
        self.metadata_table.setHorizontalHeaderLabels(["Key", "Value"])
        self.metadata_table.setAlternatingRowColors(True)
        self.metadata_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.metadata_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.metadata_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.metadata_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.metadata_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.metadata_table.verticalHeader().setVisible(False)
        self.metadata_table.verticalHeader().setDefaultSectionSize(30)
        self.metadata_table.itemSelectionChanged.connect(self.load_selected_metadata_item)
        metadata_layout.addWidget(self.metadata_table, 1)
        splitter.addWidget(metadata_panel)
        splitter.setSizes([300, 360])

        layout.addWidget(splitter, 1)
        return tab

    # -------------------- HELPERS --------------------
    def create_list_group(self, title, widget, is_list=True):
        box = QGroupBox(title)
        box.setFont(QFont("Segoe UI", 10, QFont.Bold))
        v = QVBoxLayout(box)
        v.addWidget(widget)
        return box

    def create_button(self, text, style, callback):
        btn = QPushButton(text)
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(callback)
        btn.setObjectName(style if style in ("primary", "danger", "neutral") else "neutral")
        return btn

    # -------------------- DATA LOADING --------------------
    def load_user_roles(self):
        self.user_roles_list.clear()
        user_id = self.user_combo.currentData()
        if not user_id:
            return
        roles = self.admin_service.get_roles_for_user(user_id)
        for r in roles:
            from PyQt5.QtWidgets import QListWidgetItem
            li = QListWidgetItem(r["name"])
            li.setData(Qt.UserRole, int(r["id"]))
            self.user_roles_list.addItem(li)

    def load_role_permissions(self):
        self.role_permissions_list.clear()
        role_id = self.role_perm_combo.currentData()
        if not role_id:
            return
        perms = self.admin_service.get_permissions_for_role(role_id)
        for p in perms:
            from PyQt5.QtWidgets import QListWidgetItem
            li = QListWidgetItem(p["name"])
            li.setData(Qt.UserRole, int(p["id"]))
            self.role_permissions_list.addItem(li)

    def reload_users(self):
        # User combos used in multiple tabs
        users = self.user_service.get_all_users()
        self.user_combo.clear()
        self.project_user_combo.clear()
        for u in users:
            self.user_combo.addItem(u.username, u.id)
            self.project_user_combo.addItem(u.username, u.id)

    def load_projects(self):
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        projects = self.project_service.get_all_projects()
        for p in projects:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(str(p["id"])))
            self.table.setItem(row, 1, QTableWidgetItem(p["name"]))
            self.table.setItem(row, 2, QTableWidgetItem(str(p.get("version_label") or "")))
            self.table.setItem(row, 3, QTableWidgetItem(str(p.get("version_state") or "")))
            self.table.setItem(row, 4, QTableWidgetItem(p.get("working_directory") or ""))
            self.table.setItem(row, 5, QTableWidgetItem(p.get("created_at") or ""))
        self.table.setSortingEnabled(True)
        self._filter_projects(self.project_search.text() if hasattr(self, "project_search") else "")

    def _filter_projects(self, text):
        query = str(text or "").strip().lower()
        for row in range(self.table.rowCount()):
            searchable = " ".join(
                self.table.item(row, column).text()
                for column in range(self.table.columnCount())
                if self.table.item(row, column)
            ).lower()
            self.table.setRowHidden(row, bool(query and query not in searchable))

    def load_configuration_admin(self):
        if not hasattr(self, "config_paths_table"):
            return
        try:
            config = self.admin_service.get_configuration_paths()
            self.config_summary_label.setText(
                f"Application version: {config.get('app_version', '-')}   |   "
                f"License source: {config.get('license_source', '-')}"
            )
            self.config_paths_table.setRowCount(0)
            for row_data in config.get("paths", []):
                row = self.config_paths_table.rowCount()
                self.config_paths_table.insertRow(row)
                values = [
                    str(row_data.get("name") or ""),
                    str(row_data.get("path") or ""),
                    str(row_data.get("kind") or ""),
                    str(row_data.get("status") or ""),
                ]
                for col, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    if col == 1:
                        item.setData(Qt.UserRole, values[1])
                    self.config_paths_table.setItem(row, col, item)

            metadata = self.admin_service.list_app_metadata()
            self.metadata_table.setRowCount(0)
            for entry in metadata:
                row = self.metadata_table.rowCount()
                self.metadata_table.insertRow(row)
                self.metadata_table.setItem(row, 0, QTableWidgetItem(str(entry.get("key") or "")))
                self.metadata_table.setItem(row, 1, QTableWidgetItem(str(entry.get("value") or "")))
        except Exception as e:
            QMessageBox.warning(self, "Configuration", str(e))

    def _selected_config_path(self):
        row = self.config_paths_table.currentRow()
        if row < 0:
            return None
        item = self.config_paths_table.item(row, 1)
        if not item:
            return None
        return item.data(Qt.UserRole) or item.text()

    def copy_selected_config_path(self):
        path = self._selected_config_path()
        if not path:
            return QMessageBox.warning(self, "Select", "Select a configuration path first.")
        QApplication.clipboard().setText(str(path))
        self.stats_label.setText("Path copied")

    def open_selected_config_folder(self):
        path = self._selected_config_path()
        if not path:
            return QMessageBox.warning(self, "Select", "Select a configuration path first.")
        target = Path(path)
        folder = target if target.is_dir() else target.parent
        if not folder.exists():
            return QMessageBox.warning(self, "Missing", f"Folder does not exist:\n{folder}")
        try:
            os.startfile(str(folder))
        except Exception as e:
            QMessageBox.warning(self, "Open Folder", str(e))

    def open_selected_config_file(self):
        path = self._selected_config_path()
        if not path:
            return QMessageBox.warning(self, "Select", "Select a configuration path first.")
        target = Path(path)
        if not target.is_file():
            return QMessageBox.warning(self, "Missing", f"File does not exist:\n{target}")
        try:
            os.startfile(str(target))
        except Exception as e:
            QMessageBox.warning(self, "Open File", str(e))

    def load_selected_metadata_item(self):
        if not hasattr(self, "metadata_table"):
            return
        row = self.metadata_table.currentRow()
        if row < 0:
            return
        key_item = self.metadata_table.item(row, 0)
        value_item = self.metadata_table.item(row, 1)
        self.metadata_key_input.setText(key_item.text() if key_item else "")
        self.metadata_value_input.setPlainText(value_item.text() if value_item else "")

    def clear_metadata_editor(self):
        self.metadata_table.clearSelection()
        self.metadata_key_input.clear()
        self.metadata_value_input.clear()
        self.metadata_key_input.setFocus()

    def _metadata_key_needs_warning(self, key):
        sensitive = {
            "app_version",
            "minimum_app_version",
            "latest_version",
            "latest_version_sig",
            "db_schema_version",
        }
        key_lower = str(key or "").strip().lower()
        return key_lower in sensitive or "license" in key_lower or "signature" in key_lower

    def save_metadata_item(self):
        key = self.metadata_key_input.text().strip()
        value = self.metadata_value_input.toPlainText()
        if not key:
            return QMessageBox.warning(self, "Metadata", "Metadata key is required.")
        if self._metadata_key_needs_warning(key):
            res = QMessageBox.question(
                self,
                "Confirm Metadata Change",
                "This metadata key can affect startup, version, or licensing checks.\n\nSave it anyway?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if res != QMessageBox.Yes:
                return
        try:
            self.admin_service.set_app_metadata(key, value)
        except Exception as e:
            return QMessageBox.warning(self, "Metadata", str(e))
        self.load_configuration_admin()
        matches = self.metadata_table.findItems(key, Qt.MatchExactly)
        if matches:
            self.metadata_table.selectRow(matches[0].row())
        self.stats_label.setText("Metadata saved")

    def delete_metadata_item(self):
        key = self.metadata_key_input.text().strip()
        if not key:
            return QMessageBox.warning(self, "Metadata", "Select or enter a metadata key to delete.")
        res = QMessageBox.question(
            self,
            "Delete Metadata",
            f"Delete metadata key '{key}'?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if res != QMessageBox.Yes:
            return
        if self._metadata_key_needs_warning(key):
            res = QMessageBox.question(
                self,
                "Confirm Sensitive Delete",
                "This key can affect startup, version, or licensing checks.\n\nDelete it anyway?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if res != QMessageBox.Yes:
                return
        try:
            self.admin_service.delete_app_metadata(key)
        except Exception as e:
            return QMessageBox.warning(self, "Metadata", str(e))
        self.clear_metadata_editor()
        self.load_configuration_admin()
        self.stats_label.setText("Metadata deleted")

    # -------------------- ACTIONS --------------------
    def assign_role_to_user(self):
        u, r = self.user_combo.currentData(), self.role_combo.currentData()
        if u and r:
            try:
                self.admin_service.assign_role_to_user(u, r)
            except Exception as e:
                return QMessageBox.warning(self, "Failed", str(e))
            QMessageBox.information(self, "Success", "Role assigned.")
            self.load_user_roles()

    def remove_role_from_user(self):
        u = self.user_combo.currentData()
        item = self.user_roles_list.currentItem()
        if not u or not item:
            return QMessageBox.warning(self, "Select", "Select a user and an assigned role to remove.")
        r = item.data(Qt.UserRole)
        if not r:
            return QMessageBox.warning(self, "Select", "Select an assigned role to remove.")
        try:
            self.admin_service.remove_role_from_user(u, int(r))
        except Exception as e:
            return QMessageBox.warning(self, "Failed", str(e))
        QMessageBox.information(self, "Removed", "Role removed.")
        self.load_user_roles()

    def add_permission_to_role(self):
        r, p = self.role_perm_combo.currentData(), self.permission_combo.currentData()
        if r and p:
            try:
                self.admin_service.add_permission_to_role(r, p)
            except Exception as e:
                return QMessageBox.warning(self, "Failed", str(e))
            QMessageBox.information(self, "Added", "Permission added.")
            self.load_role_permissions()

    def remove_permission_from_role(self):
        r = self.role_perm_combo.currentData()
        item = self.role_permissions_list.currentItem()
        if not r or not item:
            return QMessageBox.warning(self, "Select", "Select a role and an assigned permission to remove.")
        p = item.data(Qt.UserRole)
        if not p:
            return QMessageBox.warning(self, "Select", "Select an assigned permission to remove.")
        try:
            self.admin_service.remove_permission_from_role(r, int(p))
        except Exception as e:
            return QMessageBox.warning(self, "Failed", str(e))
        QMessageBox.information(self, "Removed", "Permission removed.")
        self.load_role_permissions()

    def create_project(self):
        name, ok = QInputDialog.getText(self, "New Project", "Enter project name:")
        if not (ok and name and name.strip()):
            return
        wd = QFileDialog.getExistingDirectory(self, "Select Working Directory")
        if not wd:
            return
        desc, ok2 = QInputDialog.getMultiLineText(self, "Description", "Project description:")
        if not ok2:
            desc = ""
        try:
            self.project_service.create_project(name.strip(), wd, desc)
        except Exception as e:
            return QMessageBox.warning(self, "Failed", str(e))
        self.load_projects()
        self.refresh_stats()

    def edit_project(self):
        pid = self._selected_project_id()
        if not pid:
            return QMessageBox.warning(self, "Select", "Please select a project first.")

        project = self.project_service.get_project_by_id(int(pid)) or {}
        current_name = str(project.get("name") or "")
        current_wd = str(project.get("working_directory") or "")
        current_desc = str(project.get("description") or "")

        if int(project.get("is_readonly") or 0) == 1:
            return QMessageBox.warning(self, "Read-only", "This project is read-only and cannot be edited.")

        name, ok = QInputDialog.getText(self, "Edit Project", "Project name:", text=current_name)
        if not (ok and name and name.strip()):
            return

        wd = QFileDialog.getExistingDirectory(
            self,
            "Select Working Directory",
            directory=(current_wd if current_wd else ""),
        )
        if not wd:
            return

        desc, ok2 = QInputDialog.getMultiLineText(self, "Edit Description", "Project description:", text=current_desc)
        if not ok2:
            return

        try:
            self.project_service.update_project(int(pid), name.strip(), wd, desc)
        except Exception as e:
            return QMessageBox.warning(self, "Failed", str(e))

        QMessageBox.information(self, "Updated", "Project updated.")
        self.load_projects()
        self.refresh_stats()

    def delete_project(self):
        row = self.table.currentRow()
        if row < 0:
            return QMessageBox.warning(self, "Select", "Please select a project first.")
        pid = int(self.table.item(row, 0).text())
        res = QMessageBox.question(self, "Confirm", f"Delete project ID {pid}?", QMessageBox.Yes | QMessageBox.No)
        if res != QMessageBox.Yes:
            return
        try:
            self.project_service.delete_project(pid)
        except Exception as e:
            return QMessageBox.warning(self, "Failed", str(e))
        self.load_projects()
        self.project_members_list.clear()
        self.refresh_stats()

    def _selected_project_id(self):
        row = self.table.currentRow()
        if row < 0:
            return None
        try:
            return int(self.table.item(row, 0).text())
        except Exception:
            return None

    def load_project_members(self):
        self.project_members_list.clear()
        pid = self._selected_project_id()
        if not pid:
            return
        try:
            members = self.project_service.get_users_for_project(pid) or []
        except Exception:
            members = []
        from PyQt5.QtWidgets import QListWidgetItem
        for m in members:
            name = str(m.get("username") or "")
            uid = m.get("id")
            li = QListWidgetItem(name)
            if uid is not None:
                li.setData(Qt.UserRole, int(uid))
            self.project_members_list.addItem(li)

    def add_user_to_project(self):
        pid = self._selected_project_id()
        uid = self.project_user_combo.currentData()
        if not pid or not uid:
            return QMessageBox.warning(self, "Select", "Select a project and a user.")
        try:
            self.project_service.add_user_to_project(int(uid), int(pid))
        except Exception as e:
            return QMessageBox.warning(self, "Failed", str(e))
        self.load_project_members()

    def remove_user_from_project(self):
        pid = self._selected_project_id()
        item = self.project_members_list.currentItem()
        if not pid or not item:
            return QMessageBox.warning(self, "Select", "Select a project and a member to remove.")
        uid = item.data(Qt.UserRole)
        if not uid:
            return QMessageBox.warning(self, "Select", "Select a member to remove.")
        try:
            self.project_service.remove_user_from_project(int(uid), int(pid))
        except Exception as e:
            return QMessageBox.warning(self, "Failed", str(e))
        self.load_project_members()

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, "tabs"):
            self.refresh_all()
