from PyQt5.QtWidgets import QDialog, QVBoxLayout, QFormLayout, QLabel, QLineEdit, QPushButton, QMessageBox
from PyQt5.QtCore import Qt
from core.repositories.user_repository import UserRepository
from core.services.user_service import UserService
from core.session_manager import SessionManager
from core.services.project_service import ProjectService
import os

class LoginPage(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Login")
        self.resize(400, 200)
        self.user_repo = UserRepository()
        self.user_service = UserService(self.user_repo)
        self.project_service = ProjectService()
        self.session = SessionManager()
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft)
        form.setFormAlignment(Qt.AlignTop)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(10)

        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("Username")
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.Password)
        self.password_input.setPlaceholderText("Password")
        self.login_btn = QPushButton("Login")
        self.login_btn.setObjectName("primary")
        self.login_btn.clicked.connect(self.login)

        # Enter key submits
        self.username_input.returnPressed.connect(self.login)
        self.password_input.returnPressed.connect(self.login)

        form.addRow(QLabel("Username"), self.username_input)
        form.addRow(QLabel("Password"), self.password_input)
        layout.addLayout(form)

        layout.addWidget(self.login_btn)
        self.setLayout(layout)

    def login(self):
        username = self.username_input.text().strip()
        password = self.password_input.text().strip()
        user = self.user_service.authenticate(username, password)
        if user:
            userid = user.id
            projects = self.project_service.get_projects_for_user(userid)
            if not projects:
                QMessageBox.warning(self, "No Projects", "You are not assigned to any projects.")
                self.session.start_session(user_id=userid, username=user.username, role_id=user.role_id, is_admin=user.is_admin)
            else:
                # Prefer last opened project for this user.
                last_pid = None
                try:
                    last_pid = self.user_repo.get_last_project_id(userid)
                except Exception:
                    last_pid = None

                def _pid_from_project(p):
                    try:
                        return int(p.get('id'))
                    except Exception:
                        return None

                def _wd_exists(p):
                    try:
                        wd = str(p.get('working_directory') or '').strip()
                        return bool(wd) and os.path.isdir(wd)
                    except Exception:
                        return False

                chosen_pid = None
                if last_pid is not None:
                    for p in projects:
                        if _pid_from_project(p) == int(last_pid) and _wd_exists(p):
                            chosen_pid = int(last_pid)
                            break

                # Fallback to legacy 'is_current' if no last project.
                if chosen_pid is None:
                    for p in projects:
                        try:
                            if int(p.get('is_current') or 0) == 1 and _wd_exists(p):
                                chosen_pid = _pid_from_project(p)
                                break
                        except Exception:
                            continue

                self.session.start_session(
                    user_id=userid,
                    username=user.username,
                    role_id=user.role_id,
                    project_id=chosen_pid,
                    is_admin=user.is_admin,
                )
            
            QMessageBox.information(self, "Welcome", f"Hello {user.username}")
            self.accept()  # closes dialog and returns control
        else:
            QMessageBox.warning(self, "Error", "Invalid credentials")
