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
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)

        identity_panel = QFrame()
        identity_panel.setObjectName("loginIdentityPanel")
        identity_panel.setMinimumWidth(320)
        identity_layout = QVBoxLayout(identity_panel)
        identity_layout.setContentsMargins(38, 34, 38, 30)
        identity_layout.setSpacing(8)

        brand = QLabel("NEXUS")
        brand.setObjectName("loginBrand")
        product_name = QLabel("PRODUCT DATA MANAGEMENT")
        product_name.setObjectName("loginProductName")
        identity_layout.addWidget(brand)
        identity_layout.addWidget(product_name)

        identity_rule = QFrame()
        identity_rule.setObjectName("loginIdentityRule")
        identity_rule.setFrameShape(QFrame.HLine)
        identity_layout.addWidget(identity_rule)
        identity_layout.addStretch(2)

        identity_section = QLabel("ENGINEERING INFORMATION CONTROL")
        identity_section.setObjectName("loginIdentitySection")
        identity_title = QLabel("Controlled engineering\nworkspace")
        identity_title.setObjectName("loginIdentityTitle")
        identity_title.setWordWrap(True)
        identity_description = QLabel(
            "CAD DOCUMENTS  |  ITEM MASTERS  |  EBOM  |  CHANGE CONTROL"
        )
        identity_description.setObjectName("loginIdentityDescription")
        identity_description.setWordWrap(True)
        identity_layout.addWidget(identity_section)
        identity_layout.addWidget(identity_title)
        identity_layout.addWidget(identity_description)
        identity_layout.addStretch(3)

        environment = QLabel("AUTHORIZED ENVIRONMENT\nAccess is recorded and traceable")
        environment.setObjectName("loginEnvironment")
        identity_layout.addWidget(environment)
        page_layout.addWidget(identity_panel, 4)

        access_panel = QFrame()
        access_panel.setObjectName("loginAccessPanel")
        access_layout = QVBoxLayout(access_panel)
        access_layout.setContentsMargins(44, 30, 44, 28)
        access_layout.setSpacing(0)
        access_layout.addStretch(1)

        access_content = QFrame()
        access_content.setObjectName("loginAccessContent")
        access_content.setMinimumWidth(360)
        access_content.setMaximumWidth(430)
        content_layout = QVBoxLayout(access_content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(8)

        logo = QLabel("Nexus")
        logo.setObjectName("loginBrandLogo")
        logo.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        logo_pixmap = QPixmap(
            _resource_path("assets/pictures/nexus_logo_landscape.png")
        )
        if not logo_pixmap.isNull():
            logo.setPixmap(
                logo_pixmap.scaled(
                    230, 68, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
            )
        content_layout.addWidget(logo)

        section_rule = QFrame()
        section_rule.setObjectName("loginAccessRule")
        section_rule.setFrameShape(QFrame.HLine)
        content_layout.addWidget(section_rule)
        content_layout.addSpacing(12)

        access_label = QLabel("CONTROLLED WORKSPACE ACCESS")
        access_label.setObjectName("loginAccessLabel")
        heading = QLabel("Sign in")
        heading.setObjectName("loginHeading")
        subtitle = QLabel(
            "Use your assigned Nexus account to open the engineering workspace."
        )
        subtitle.setObjectName("loginSubtitle")
        subtitle.setWordWrap(True)
        content_layout.addWidget(access_label)
        content_layout.addWidget(heading)
        content_layout.addWidget(subtitle)
        content_layout.addSpacing(12)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form.setFormAlignment(Qt.AlignTop)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(9)

        self.username_input = QLineEdit()
        self.username_input.setObjectName("loginField")
        self.username_input.setPlaceholderText("Account name")
        self.username_input.setMinimumHeight(30)
        self.password_input = QLineEdit()
        self.password_input.setObjectName("loginField")
        self.password_input.setEchoMode(QLineEdit.Password)
        self.password_input.setPlaceholderText("Password")
        self.password_input.setMinimumHeight(30)

        username_label = QLabel("USERNAME")
        username_label.setObjectName("loginFieldLabel")
        password_label = QLabel("PASSWORD")
        password_label.setObjectName("loginFieldLabel")
        form.addRow(username_label, self.username_input)
        form.addRow(password_label, self.password_input)
        content_layout.addLayout(form)

        self.status_label = QLabel("")
        self.status_label.setObjectName("loginStatus")
        self.status_label.setWordWrap(True)
        self.status_label.setMinimumHeight(28)
        content_layout.addWidget(self.status_label)

        self.login_btn = QPushButton("Sign in")
        self.login_btn.setObjectName("primary")
        self.login_btn.setMinimumHeight(32)
        self.login_btn.clicked.connect(self.login)
        content_layout.addWidget(self.login_btn)

        self.username_input.returnPressed.connect(self.login)
        self.password_input.returnPressed.connect(self.login)

        access_layout.addWidget(access_content, 0, Qt.AlignHCenter)
        access_layout.addStretch(1)

        access_footer = QLabel(
            "NEXUS PDM  |  CONTROLLED ACCESS  |  AUTHORIZED USERS ONLY"
        )
        access_footer.setObjectName("loginFooter")
        access_footer.setAlignment(Qt.AlignCenter)
        access_layout.addWidget(access_footer)
        page_layout.addWidget(access_panel, 6)

        self.setStyleSheet(
            """
            QWidget#loginPage {
                background-color: #e9edf0;
            }

            QFrame#loginIdentityPanel {
                background-color: #20364b;
                border: 0;
                border-right: 1px solid #142638;
            }
            QFrame#loginIdentityPanel QLabel {
                background-color: transparent;
            }
            QLabel#loginBrand {
                color: #ffffff;
                font-size: 23pt;
                font-weight: bold;
                letter-spacing: 2px;
            }
            QLabel#loginProductName {
                color: #9fb1bf;
                font-size: 7.5pt;
                font-weight: bold;
                letter-spacing: 1px;
            }
            QFrame#loginIdentityRule {
                background-color: #53697b;
                border: 0;
                max-height: 1px;
                min-height: 1px;
                margin-top: 8px;
            }
            QLabel#loginIdentitySection {
                color: #54b6da;
                font-size: 7.5pt;
                font-weight: bold;
                letter-spacing: 0.8px;
            }
            QLabel#loginIdentityTitle {
                color: #ffffff;
                font-size: 20pt;
                font-weight: bold;
            }
            QLabel#loginIdentityDescription {
                color: #b8c6d1;
                font-size: 9pt;
                padding-top: 5px;
            }
            QLabel#loginEnvironment {
                color: #91a4b3;
                border-top: 1px solid #506579;
                font-family: "Consolas";
                font-size: 7.5pt;
                padding-top: 10px;
            }

            QFrame#loginAccessPanel,
            QFrame#loginAccessContent {
                background-color: #f7f8f9;
                border: 0;
            }
            QLabel#loginBrandLogo {
                background-color: #f7f8f9;
                color: #20364b;
                font-size: 20pt;
                font-weight: bold;
            }
            QFrame#loginAccessRule {
                background-color: #aeb9c2;
                border: 0;
                max-height: 1px;
                min-height: 1px;
            }
            QLabel#loginAccessLabel {
                color: #32759d;
                font-size: 7.5pt;
                font-weight: bold;
                letter-spacing: 0.7px;
            }
            QLabel#loginHeading {
                color: #1b2d3b;
                font-size: 17pt;
                font-weight: bold;
            }
            QLabel#loginSubtitle {
                color: #61717e;
                font-size: 8.5pt;
            }
            QLabel#loginFieldLabel {
                color: #405564;
                font-size: 7.5pt;
                font-weight: bold;
                letter-spacing: 0.4px;
                min-width: 72px;
            }
            QLabel#loginStatus {
                color: #9b3831;
                font-size: 8pt;
                padding-top: 3px;
            }
            QLabel#loginFooter {
                color: #75848f;
                border-top: 1px solid #c6ced4;
                font-family: "Consolas";
                font-size: 7pt;
                padding-top: 9px;
            }

            QLineEdit#loginField {
                background-color: #ffffff;
                border: 1px solid #aeb9c2;
                border-radius: 1px;
                color: #172630;
                padding: 2px 7px;
                selection-background-color: #176f9d;
                selection-color: #ffffff;
            }
            QLineEdit#loginField:hover {
                border-color: #7f929f;
            }
            QLineEdit#loginField:focus {
                border-color: #087dad;
            }
            QLineEdit#loginField:disabled {
                background-color: #e4e8eb;
                border-color: #c7cfd5;
                color: #84919b;
            }

            QPushButton#primary {
                background-color: #176f9d;
                border: 1px solid #0d5f88;
                border-radius: 1px;
                color: #ffffff;
                font-weight: bold;
                padding: 2px 12px;
            }
            QPushButton#primary:hover {
                background-color: #0e7eae;
            }
            QPushButton#primary:pressed {
                background-color: #0c648b;
            }
            QPushButton#primary:disabled {
                background-color: #9baab5;
                border-color: #8e9ca6;
                color: #e8edf0;
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
        self.status_label.setStyleSheet("color: #2f7656; font-weight: bold;")
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
