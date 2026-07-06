import os
import sys

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.repositories.user_repository import UserRepository
from core.services.project_service import ProjectService
from core.services.user_service import UserService
from core.session_manager import SessionManager


def _resource_path(relative: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, relative)


class LoginPage(QWidget):
    """Full-window, embeddable login experience."""

    login_succeeded = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.user_repo = UserRepository()
        self.user_service = UserService(self.user_repo)
        self.project_service = ProjectService()
        self.session = SessionManager()
        self.init_ui()

    def init_ui(self):
        self.setObjectName("loginPage")

        page_layout = QHBoxLayout(self)
        page_layout.setContentsMargins(48, 48, 48, 48)
        page_layout.setAlignment(Qt.AlignCenter)

        card = QFrame()
        card.setObjectName("loginCard")
        card.setMaximumWidth(460)
        card.setMinimumWidth(380)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(44, 40, 44, 40)
        layout.setSpacing(18)

        brand = QLabel("Nexus")
        brand.setObjectName("loginBrandLogo")
        brand.setAlignment(Qt.AlignCenter)
        logo = QPixmap(_resource_path("assets/pictures/nexus_logo_landscape.png"))
        if not logo.isNull():
            brand.setPixmap(logo.scaled(320, 96, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        subtitle = QLabel("Engineering workspace")
        subtitle.setObjectName("loginSubtitle")
        subtitle.setWordWrap(True)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft)
        form.setFormAlignment(Qt.AlignTop)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(14)

        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("Enter username")
        self.username_input.setMinimumHeight(38)
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.Password)
        self.password_input.setPlaceholderText("Enter password")
        self.password_input.setMinimumHeight(38)

        self.status_label = QLabel("")
        self.status_label.setObjectName("loginStatus")
        self.status_label.setWordWrap(True)
        self.status_label.setMinimumHeight(20)

        self.login_btn = QPushButton("Sign in")
        self.login_btn.setObjectName("primary")
        self.login_btn.setMinimumHeight(40)
        self.login_btn.clicked.connect(self.login)

        self.username_input.returnPressed.connect(self.login)
        self.password_input.returnPressed.connect(self.login)

        form.addRow("Username", self.username_input)
        form.addRow("Password", self.password_input)
        layout.addWidget(brand)
        layout.addWidget(subtitle)
        layout.addSpacing(8)
        layout.addLayout(form)
        layout.addWidget(self.status_label)
        layout.addWidget(self.login_btn)
        page_layout.addWidget(card)

        self.setStyleSheet(
            """
            QWidget#loginPage {
                background: #f3f6fa;
            }
            QFrame#loginCard {
                background: white;
                border: 1px solid #d9e1ea;
                border-radius: 8px;
            }
            QLabel#loginBrandLogo {
                color: #16263d;
                font-size: 28px;
                font-weight: 700;
            }
            QLabel#loginSubtitle {
                color: #66758a;
                font-size: 14px;
            }
            QLabel#loginStatus {
                color: #c53030;
                font-size: 12px;
            }
            QLineEdit {
                border: 1px solid #cbd5e1;
                border-radius: 5px;
                padding: 7px 9px;
                background: white;
            }
            QLineEdit:focus {
                border: 1px solid #2563eb;
            }
            QPushButton#primary {
                background: #2563eb;
                color: white;
                border: none;
                border-radius: 5px;
                font-weight: 600;
            }
            QPushButton#primary:hover {
                background: #1d4ed8;
            }
            QPushButton#primary:disabled {
                background: #93b4ef;
            }
            """
        )

    def showEvent(self, event):
        super().showEvent(event)
        self.username_input.setFocus()

    def _set_busy(self, busy):
        self.login_btn.setDisabled(busy)
        self.username_input.setDisabled(busy)
        self.password_input.setDisabled(busy)
        self.login_btn.setText("Signing in..." if busy else "Sign in")

    def _show_error(self, message):
        self.status_label.setText(message)
        self._set_busy(False)

    def login(self):
        username = self.username_input.text().strip()
        password = self.password_input.text()
        if not username or not password:
            self._show_error("Enter both username and password.")
            return

        self.status_label.clear()
        self._set_busy(True)

        try:
            user = self.user_service.authenticate(username, password)
        except Exception as exc:
            self._show_error(f"Unable to sign in: {exc}")
            return

        if not user:
            self.password_input.clear()
            self.password_input.setFocus()
            self._show_error("Invalid username or password.")
            return

        userid = user.id
        projects = self.project_service.get_projects_for_user(userid)
        chosen_pid = self._choose_project(userid, projects)
        self.session.start_session(
            user_id=userid,
            username=user.username,
            role_id=user.role_id,
            project_id=chosen_pid,
            is_admin=user.is_admin,
        )
        self.status_label.setStyleSheet("color: #16803a;")
        self.status_label.setText(f"Welcome, {user.username}. Preparing your workspace...")
        self.login_succeeded.emit()

    def _choose_project(self, userid, projects):
        if not projects:
            return None

        try:
            last_pid = self.user_repo.get_last_project_id(userid)
        except Exception:
            last_pid = None

        def project_id(project):
            try:
                return int(project.get("id"))
            except Exception:
                return None

        def working_directory_exists(project):
            try:
                working_dir = str(project.get("working_directory") or "").strip()
                return bool(working_dir) and os.path.isdir(working_dir)
            except Exception:
                return False

        if last_pid is not None:
            for project in projects:
                if project_id(project) == int(last_pid) and working_directory_exists(project):
                    return int(last_pid)

        for project in projects:
            try:
                if int(project.get("is_current") or 0) == 1 and working_directory_exists(project):
                    return project_id(project)
            except Exception:
                continue

        return None
