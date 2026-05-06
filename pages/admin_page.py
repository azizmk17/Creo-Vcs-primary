from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget,
    QComboBox, QFrame, QGroupBox, QTabWidget, QTableWidget, QTableWidgetItem,
    QMessageBox, QHeaderView, QInputDialog, QFileDialog, QLineEdit
)
from PyQt5.QtGui import QFont, QIcon
from PyQt5.QtCore import Qt

from core.services.admin_service import AdminService
from core.services.role_service import RoleService
from core.services.permission_service import PermissionService
from core.services.user_service import UserService
from core.services.project_service import ProjectService
from core.repositories.user_repository import UserRepository


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
        layout = QVBoxLayout()
        layout.setContentsMargins(20, 15, 20, 15)
        layout.setSpacing(15)

        # Header
        layout.addWidget(self.create_header())

        # Tabs
        tabs = QTabWidget()
        tabs.addTab(self.create_user_role_tab(), "Users & Roles")
        tabs.addTab(self.create_role_perm_tab(), "Roles & Permissions")
        tabs.addTab(self.create_project_tab(), "Projects")

        layout.addWidget(tabs)
        self.setLayout(layout)

    def create_header(self):
        frame = QFrame()
        frame.setObjectName("header")
        hbox = QHBoxLayout()
        title = QLabel("Admin")
        title.setFont(QFont("Segoe UI", 16, QFont.Bold))

        self.stats_label = QLabel("")
        self.stats_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.refresh_stats()

        hbox.addWidget(title)
        hbox.addWidget(self.stats_label)
        frame.setLayout(hbox)
        return frame

    def refresh_stats(self):
        try:
            users = len(self.user_service.get_all_users())
            roles = len(self.role_service.get_all_roles())
            perms = len(self.permission_service.get_all_permissions())
            self.stats_label.setText(f"Users: {users}   Roles: {roles}   Permissions: {perms}")
        except Exception:
            self.stats_label.setText("Users: -   Roles: -   Permissions: -")

    # -------------------- TAB 1: USER ROLE MANAGEMENT --------------------
    def create_user_role_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Toolbar
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("User Management"))
        toolbar.addStretch()
        btn_new_user = self.create_button("New User", "primary", self.create_user)
        btn_edit_user = self.create_button("Edit User", "neutral", self.edit_user)
        btn_delete_user = self.create_button("Delete User", "danger", self.delete_user)
        toolbar.addWidget(btn_new_user)
        toolbar.addWidget(btn_edit_user)
        toolbar.addWidget(btn_delete_user)
        layout.addLayout(toolbar)

        # Combo and lists
        top = QHBoxLayout()
        self.user_combo = QComboBox()
        for u in self.user_service.get_all_users():
            self.user_combo.addItem(u.username, u.id)
        top.addWidget(QLabel("Select User:"))
        top.addWidget(self.user_combo)
        layout.addLayout(top)

        lists = QHBoxLayout()
        self.user_roles_list = QListWidget()
        self.role_combo = QComboBox()
        for r in self.role_service.get_all_roles():
            self.role_combo.addItem(r["name"], r["id"])

        lists.addWidget(self.create_list_group("Assigned Roles", self.user_roles_list))
        lists.addWidget(self.create_list_group("Available Roles", self.role_combo, False))
        layout.addLayout(lists)

        # Buttons
        btns = QHBoxLayout()
        assign = self.create_button("Assign Role", "primary", self.assign_role_to_user)
        remove = self.create_button("Remove Role", "danger", self.remove_role_from_user)
        btns.addWidget(assign)
        btns.addWidget(remove)
        layout.addLayout(btns)

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

        # Toolbar
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Role & Permission Management"))
        toolbar.addStretch()
        btn_new_role = self.create_button("New Role", "primary", self.create_role)
        btn_rename_role = self.create_button("Rename Role", "neutral", self.rename_role)
        btn_delete_role = self.create_button("Delete Role", "danger", self.delete_role)
        toolbar.addWidget(btn_new_role)
        toolbar.addWidget(btn_rename_role)
        toolbar.addWidget(btn_delete_role)
        layout.addLayout(toolbar)

        # Role combo
        top = QHBoxLayout()
        self.role_perm_combo = QComboBox()
        for r in self.role_service.get_all_roles():
            self.role_perm_combo.addItem(r["name"], r["id"])
        top.addWidget(QLabel("Select Role:"))
        top.addWidget(self.role_perm_combo)
        layout.addLayout(top)

        # Lists
        lists = QHBoxLayout()
        self.role_permissions_list = QListWidget()
        self.permission_combo = QComboBox()
        for p in self.permission_service.get_all_permissions():
            self.permission_combo.addItem(p["name"], p["id"])

        lists.addWidget(self.create_list_group("Assigned Permissions", self.role_permissions_list))
        lists.addWidget(self.create_list_group("Available Permissions", self.permission_combo, False))
        layout.addLayout(lists)

        # Buttons
        btns = QHBoxLayout()
        add = self.create_button("Add Permission", "primary", self.add_permission_to_role)
        rem = self.create_button("Remove Permission", "danger", self.remove_permission_from_role)
        btn_new_perm = self.create_button("New Permission", "neutral", self.create_permission)
        btn_del_perm = self.create_button("Delete Permission", "danger", self.delete_permission)
        btns.addWidget(add)
        btns.addWidget(rem)
        btns.addStretch()
        btns.addWidget(btn_new_perm)
        btns.addWidget(btn_del_perm)
        layout.addLayout(btns)

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

        # Toolbar
        toolbar = QHBoxLayout()
        title = QLabel("Manage Projects")
        title.setFont(QFont("Segoe UI", 12, QFont.Bold))
        btn_add = self.create_button("New Project", "primary", self.create_project)
        btn_edit = self.create_button("Edit", "neutral", self.edit_project)
        btn_del = self.create_button("Delete", "danger", self.delete_project)
        toolbar.addWidget(title)
        toolbar.addStretch()
        toolbar.addWidget(btn_add)
        toolbar.addWidget(btn_edit)
        toolbar.addWidget(btn_del)
        layout.addLayout(toolbar)

        # Project Table
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(["ID", "Name", "Version", "State", "Working Dir", "Created At"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table)

        # Project Members
        members_box = QGroupBox("Project Members")
        members_box.setFont(QFont("Segoe UI", 10, QFont.Bold))
        members_layout = QVBoxLayout(members_box)
        members_top = QHBoxLayout()
        self.project_user_combo = QComboBox()
        members_top.addWidget(QLabel("User:"))
        members_top.addWidget(self.project_user_combo)
        btn_add_user = self.create_button("Add", "primary", self.add_user_to_project)
        btn_remove_user = self.create_button("Remove", "danger", self.remove_user_from_project)
        members_top.addWidget(btn_add_user)
        members_top.addWidget(btn_remove_user)
        members_layout.addLayout(members_top)
        self.project_members_list = QListWidget()
        members_layout.addWidget(self.project_members_list)
        layout.addWidget(members_box)

        self.table.itemSelectionChanged.connect(self.load_project_members)
        self.reload_users()
        self.load_projects()
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
