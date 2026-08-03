import sys
from PyQt5.QtWidgets import (
    QAction,
    QActionGroup,
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QSystemTrayIcon,
    QToolBar,
    QVBoxLayout,
    QWidget,
)
from PyQt5.QtCore import (
    QEventLoop,
    QObject,
    QPropertyAnimation,
    QSize,
    Qt,
    QThread,
    QTimer,
    pyqtSignal,
)
from PyQt5.QtGui import QPainter, QColor, QPen, QPixmap, QFont, QIcon
import math
import os


# ── PyInstaller resource helper ─────────────────────────────────────────────
def _resource_path(relative: str) -> str:
    """Return the absolute path to a bundled resource.

    In a frozen PyInstaller EXE all data files are extracted to
    sys._MEIPASS at runtime.  In dev mode they live next to main3.py.
    """
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative)


APP_NAME = "Nexus"
APP_ICON_PATH = "assets/pictures/nexus_logo.ico"
APP_LOADER_LOGO_PATH = "assets/pictures/nexus_logo_glow.png"
APP_TOOLBAR_LOGO_PATH = "assets/pictures/nexus_logo_glow.png"


PAGE_PRESENTATION = {
    6: (
        "MANAGEMENT",
        "Manager Dashboard",
        "Executive readiness, manufacturing health, risks and workload",
    ),
    0: (
        "PRODUCT DATA",
        "Product Structure",
        "CAD Documents, Item masters, EBOM structure and engineering associations",
    ),
    1: (
        "CHANGE CONTROL",
        "Commit Workspace",
        "Stage, review and submit controlled engineering content",
    ),
    2: (
        "QUALITY",
        "Issue Center",
        "Engineering issues, validation evidence and resolution traceability",
    ),
    3: (
        "SYSTEM CONTROL",
        "Diagnostics",
        "Workspace integrity, unmanaged content and corrective actions",
    ),
    4: (
        "ADMINISTRATION",
        "Administration",
        "Users, permissions and controlled application settings",
    ),
    5: (
        "CONFIGURATION",
        "Snapshots",
        "Recorded product configurations and comparison baselines",
    ),
}


def _run_migrations_safely():
    try:
        from setup.migrations import migrate

        migrate()
    except Exception as _e:
        # Don't block app startup on migration issues.
        print(f"[migrations] warning: {_e}")



# Import the startup screen eagerly; heavy application pages are loaded after login.
from pages.login_page import LoginPage
from pages.dialogs.progress_dialog import ProgressDialog

import threading

#import services and repositories
from core.services.bom_service import BomService
from core.repositories.bom_repository import BomRepository
from core.repositories.bom_children_repository import BomChildrenRepository
from core.repositories.lock_repository import LockRepository
from core.repositories.signature_repository import SignatureRepository
from core.repositories.user_repository import UserRepository
from core.services.project_service import ProjectService
from core.services.ui_permission import UIPermissionHelper
from core.session_manager import SessionManager
from core.startup_gate import MINIMUM_STARTUP_LOADER_MS, StartupGate


class AdvancedSpinner(QWidget):
    def __init__(self, logo_path=None, parent=None):
        super().__init__(parent)
        self.setFixedSize(180, 180)
        self.angle = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.rotate)
        self.timer.start(33)

        self.logo = QPixmap(logo_path).scaled(
            128, 128, Qt.KeepAspectRatio, Qt.SmoothTransformation
        ) if logo_path else None

    def rotate(self):
        self.angle = (self.angle + 7) % 360
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        center = self.rect().center()
        radius = min(self.width(), self.height()) / 2 - 18

        for i in range(10):
            color = QColor(37, 99, 235)
            color.setAlpha(35 + i * 20)
            painter.setPen(QPen(color, 6, Qt.SolidLine, Qt.RoundCap))
            angle_rad = math.radians((self.angle + i * 28) % 360)
            x = center.x() + math.cos(angle_rad) * radius
            y = center.y() + math.sin(angle_rad) * radius
            painter.drawPoint(int(x), int(y))

        if self.logo:
            logo_rect = self.logo.rect()
            logo_rect.moveCenter(center)
            painter.drawPixmap(logo_rect, self.logo)

class AdvancedLoader(QWidget):
    def __init__(self, logo_path=None, parent=None):
        super().__init__(parent)
        self.setObjectName("startupLoader")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(14)

        self.spinner = AdvancedSpinner(logo_path)
        layout.addWidget(self.spinner, 0, Qt.AlignCenter)

        self.loading_label = QLabel(f"Preparing {APP_NAME}", self)
        self.loading_label.setAlignment(Qt.AlignCenter)
        self.loading_label.setObjectName("loaderTitle")
        self.loading_label.setFont(QFont("Segoe UI", 18, QFont.Bold))
        layout.addWidget(self.loading_label)

        self.detail_label = QLabel("Checking application data...", self)
        self.detail_label.setAlignment(Qt.AlignCenter)
        self.detail_label.setObjectName("loaderDetail")
        layout.addWidget(self.detail_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedWidth(320)
        self.progress.setFixedHeight(5)
        layout.addWidget(self.progress, 0, Qt.AlignCenter)

        self.setStyleSheet(
            """
            QWidget#startupLoader { background: #f3f6fa; }
            QLabel#loaderTitle { color: #16263d; }
            QLabel#loaderDetail { color: #66758a; font-size: 13px; }
            QProgressBar {
                border: none;
                border-radius: 2px;
                background: #dbe4ef;
            }
            QProgressBar::chunk {
                border-radius: 2px;
                background: #2563eb;
            }
            """
        )

    def set_status(self, message):
        self.detail_label.setText(message)


class _MigrateWorker(QObject):
    finished = pyqtSignal()

    def run(self):
        _run_migrations_safely()
        self.finished.emit()


class StartupWindow(QMainWindow):
    """Hosts login and startup progress in one stable, full-size window."""

    def __init__(self, logo_path=None):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1400, 900)
        self.setWindowIcon(QIcon(_resource_path(APP_ICON_PATH)))

        self.pages = QStackedWidget()
        self.setCentralWidget(self.pages)
        self.login_page = LoginPage()
        self.loader_page = AdvancedLoader(logo_path)
        self.pages.addWidget(self.login_page)
        self.pages.addWidget(self.loader_page)
        self.pages.setCurrentWidget(self.login_page)

        self.main_page = None
        self._migrate_thread = None
        self._migrate_worker = None
        self._main_shown = False
        self._startup_gate = StartupGate()
        self._minimum_loader_timer = QTimer(self)
        self._minimum_loader_timer.setSingleShot(True)
        self._minimum_loader_timer.setTimerType(Qt.PreciseTimer)
        self._minimum_loader_timer.timeout.connect(self._on_minimum_loader_elapsed)
        self.login_page.login_succeeded.connect(self.begin_loading)

    def begin_loading(self):
        self.pages.setCurrentWidget(self.loader_page)
        self._startup_gate.reset()
        self.loader_page.set_status("Checking database migrations...")
        QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)
        # Measure the minimum from the first painted loader frame.
        self._minimum_loader_timer.start(MINIMUM_STARTUP_LOADER_MS)

        self._migrate_thread = QThread(self)
        self._migrate_worker = _MigrateWorker()
        self._migrate_worker.moveToThread(self._migrate_thread)
        self._migrate_thread.started.connect(self._migrate_worker.run)
        self._migrate_worker.finished.connect(self._migrate_thread.quit)
        self._migrate_worker.finished.connect(self._migrate_worker.deleteLater)
        self._migrate_thread.finished.connect(self._migrate_thread.deleteLater)
        self._migrate_thread.finished.connect(self._build_main_window)
        self._migrate_thread.start()

    def _set_loading_status(self, message):
        self.loader_page.set_status(message)
        QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)

    def _build_main_window(self):
        self._set_loading_status("Preparing application services...")
        try:
            self.main_page = BomGUI(startup_progress=self._set_loading_status)
        except Exception as exc:
            self._minimum_loader_timer.stop()
            self._startup_gate.reset()
            QMessageBox.critical(self, "Startup Error", f"{APP_NAME} could not start:\n\n{exc}")
            self.pages.setCurrentWidget(self.login_page)
            self.login_page._set_busy(False)
            raise

        self._set_loading_status("Loading the initial BOM...")
        if not getattr(self.main_page.session, "project_id", None):
            self._mark_page_ready()
            return
        bom_page = self.main_page.bom_page
        bom_page.initial_tree_ready.connect(self._mark_page_ready)
        # The page may have become ready while BomGUI was being built and
        # processing startup events, before this signal was connected.
        if bool(bom_page.initial_tree_is_ready):
            self._mark_page_ready()

    def _on_minimum_loader_elapsed(self):
        if self._startup_gate.mark_minimum_elapsed():
            self.show_main_window()

    def _mark_page_ready(self):
        self.loader_page.set_status("Application ready...")
        if self._startup_gate.mark_page_ready():
            self.show_main_window()

    def show_main_window(self):
        if self._main_shown or self.main_page is None or not self._startup_gate.released:
            return
        self._main_shown = True
        self.main_page.setGeometry(self.geometry())
        if self.isMaximized():
            self.main_page.showMaximized()
        else:
            self.main_page.show()
        self.close()


class Notifier(QObject):
    notify = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        # simulate background task
        QTimer.singleShot(3000, lambda: self.notify.emit("Task completed!"))
        


# Ed25519 public key baked into the build. Override via CREOVCS_PUBLIC_KEY_HEX for dev.
_PRODUCTION_PUBLIC_KEY = "fc941ecde885df70bfdcd71498d80fa8cfa7fe340c40c2292bbfc2b7960f529f"


class BomGUI(QMainWindow):
    """Nexus PDM desktop shell with persistent module and product context."""

    def __init__(self, startup_progress=None):
        super().__init__()
        self.setObjectName("nexusMainWindow")
        self.setWindowTitle(f"{APP_NAME} PDM — Product Development")
        self.resize(1400, 900)
        self.setMinimumSize(1100, 700)
        self.setWindowIcon(QIcon(_resource_path(APP_ICON_PATH)))

        self.session = SessionManager()
        self.user_repo = UserRepository()
        # Services
        self.bom_service = BomService(BomRepository(), BomChildrenRepository(), LockRepository(), SignatureRepository())
        self.project_service = ProjectService()

        self._project_combo_initializing = False
        self._projects_for_user = []
        self._ensure_valid_current_project()

        # Persistent application shell
        self._configure_status_bar()
        self.init_toolbar()

        self._build_ui(startup_progress=startup_progress)

    def _configure_status_bar(self):
        status = self.statusBar()
        status.setObjectName("applicationStatusBar")
        status.setSizeGripEnabled(False)
        status.showMessage("Ready")

        self.status_identity_label = QLabel()
        self.status_identity_label.setObjectName("statusIdentity")
        self.status_identity_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        status.addPermanentWidget(self.status_identity_label)

    def _ensure_valid_current_project(self):
        """If the session points to a missing project directory, clear it and continue startup."""

        pid = getattr(self.session, "project_id", None)
        if not pid:
            return

        try:
            project = self.project_service.get_project_by_id(pid) or {}
            working_dir = (project.get("working_directory") or "").strip()
        except Exception:
            working_dir = ""

        if not working_dir or not os.path.isdir(working_dir):
            try:
                QMessageBox.warning(
                    self,
                    "Project Directory Not Found",
                    "The previously selected project directory cannot be found.\n"
                    "The app will open with no project loaded.\n\n"
                    f"{working_dir or '(missing path)'}",
                )
            except Exception:
                pass
            self.session.update_project(None)

            # Clear persisted last project too (best-effort).
            try:
                if getattr(self.session, "user_id", None):
                    self.user_repo.set_last_project_id(self.session.user_id, None)
            except Exception:
                pass
    
    def reload_main_window(self):

        # --- 1️⃣ Remove old central widget ---
        old_central = self.centralWidget()
        if old_central:
            old_central.deleteLater()
            self.setCentralWidget(None)

        # --- 2️⃣ Remove old toolbars (if re-created in _build_ui) ---
        # for toolbar in self.findChildren(QToolBar):
        #     self.removeToolBar(toolbar)
        #     toolbar.deleteLater()

        self.refresh_project_label()

        # --- 3️⃣ Rebuild the full UI ---
        self._build_ui()

        # --- 4️⃣ Refresh display ---
        self.update()

    def _build_ui(self, startup_progress=None):
        # Controlled workspace header and central page stack.
        workspace = QWidget()
        workspace.setObjectName("applicationWorkspace")
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(0)

        workspace_header = QFrame()
        workspace_header.setObjectName("workspaceHeader")
        header_layout = QHBoxLayout(workspace_header)
        header_layout.setContentsMargins(16, 6, 16, 7)
        header_layout.setSpacing(14)

        heading_block = QVBoxLayout()
        heading_block.setContentsMargins(0, 0, 0, 0)
        heading_block.setSpacing(0)
        self.workspace_module_label = QLabel()
        self.workspace_module_label.setObjectName("workspaceModule")
        self.workspace_title_label = QLabel()
        self.workspace_title_label.setObjectName("workspaceTitle")
        heading_block.addWidget(self.workspace_module_label)
        heading_block.addWidget(self.workspace_title_label)
        header_layout.addLayout(heading_block)

        self.workspace_subtitle_label = QLabel()
        self.workspace_subtitle_label.setObjectName("workspaceSubtitle")
        self.workspace_subtitle_label.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Preferred
        )
        header_layout.addWidget(self.workspace_subtitle_label, 1)

        self.workspace_position_label = QLabel()
        self.workspace_position_label.setObjectName("workspacePosition")
        self.workspace_position_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        header_layout.addWidget(self.workspace_position_label)
        workspace_layout.addWidget(workspace_header)

        self.pages = QStackedWidget()
        self.pages.setObjectName("workspacePages")
        workspace_layout.addWidget(self.pages, 1)
        self.setCentralWidget(workspace)

        def startup_stage(message):
            if startup_progress:
                startup_progress(message)

        # Add pages
        startup_stage("Loading BOM workspace...")
        from pages.bom_page import BomPage
        self.bom_page = BomPage(self.bom_service)
        startup_stage("Loading commit workspace...")
        from pages.commit_page import CommitPage
        self.commit_page = CommitPage(self.bom_service)
        startup_stage("Loading engineering issues...")
        from pages.issue_page import EngineeringIssuePage
        self.issue_page = EngineeringIssuePage()
        startup_stage("Preparing manager dashboard...")
        from pages.dashboard_page import ManagerDashboardPage
        self.dashboard_page = ManagerDashboardPage()
        startup_stage("Preparing secondary tools...")
        self.diag_page = self._lazy_page_placeholder("Diagnostic")
        self.admin_page = self._lazy_page_placeholder("Admin")
        self.snap_page = self._lazy_page_placeholder("Snapshots")
        startup_stage("Connecting application modules...")
        self.pages.addWidget(self.bom_page)
        self.pages.addWidget(self.commit_page)
        self.pages.addWidget(self.issue_page)
        self.pages.addWidget(self.diag_page)
        self.pages.addWidget(self.admin_page)
        self.pages.addWidget(self.snap_page)
        self.pages.addWidget(self.dashboard_page)
        self.bom_page.issue_requested.connect(self.open_issues_for_part)
        self.bom_page.create_issue_requested.connect(self.create_issue_for_part)
        self.issue_page.issue_changed.connect(self.bom_page.refresh_issue_indicators)
        self.issue_page.issue_changed.connect(lambda _part_ids: self.commit_page._refresh_resolved_issues())
        self.issue_page.issue_changed.connect(lambda _part_ids: self.dashboard_page.refresh())

        

        # self.notifier = Notifier()
        # self.notifier.notify.connect(self.show_toast)

        
        # Apply the shared enterprise visual system after all shell widgets exist.
        self.apply_styles()
        self._set_active_shell_page(0)

        # Restore last project
        #self.load_last_project()

        # ------------------ System Tray ------------------
        # self.tray = QSystemTrayIcon(QIcon(), self)
        # self.tray.setVisible(True)

        # # Optional menu for tray icon
        # menu = QMenu()
        # exit_action = QAction("Exit")
        # exit_action.triggered.connect(sys.exit)
        # menu.addAction(exit_action)
        # self.tray.setContextMenu(menu)

        # # Example: show notification after 3 seconds
        # QTimer.singleShot(3000, lambda: self.show_notification("Hello!", "This is a dynamic notification."))

        # Version update notification — runs after the window is fully visible.
        QTimer.singleShot(500, self._check_version_notification)

    def _lazy_page_placeholder(self, label):
        page = QWidget()
        page.setObjectName("lazyWorkspacePage")
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignCenter)
        msg = QLabel(f"{label} will load when opened.")
        msg.setObjectName("workspaceEmptyState")
        layout.addWidget(msg)
        return page

    def _check_version_notification(self):
        """Show a popup dialog if a newer Nexus version is available in the DB."""
        try:
            import os as _os
            from core.licensing.version_check import check_version_notification
            _pub_hex = _os.environ.get("CREOVCS_PUBLIC_KEY_HEX", _PRODUCTION_PUBLIC_KEY)
            _pub_bytes = bytes.fromhex(_pub_hex)
            msg = check_version_notification(_pub_bytes)
            if msg:
                QMessageBox.information(
                    self,
                    "Update Available",
                    msg,
                    QMessageBox.Ok,
                )
        except Exception:
            pass  # Version check is non-critical — never block the user.

    def show_notification(self, title, message):
        # Display a Windows toast notification
        self.tray.showMessage(title, message, QSystemTrayIcon.Information, 5000)  # duration in ms


    def init_toolbar(self):
        self.navigation_actions = {}
        self.navigation_action_group = QActionGroup(self)
        self.navigation_action_group.setExclusive(True)

        def standard_icon(name):
            pixmap = getattr(QStyle, name, QStyle.SP_FileIcon)
            return self.style().standardIcon(pixmap)

        def add_action(
            target_toolbar,
            label,
            callback,
            icon_name,
            *,
            role="utility",
            page_index=None,
            tooltip="",
        ):
            action = QAction(standard_icon(icon_name), label, self)
            if tooltip:
                action.setToolTip(tooltip)
            if page_index is not None:
                action.setCheckable(True)
                self.navigation_action_group.addAction(action)
                self.navigation_actions[int(page_index)] = action
            action.triggered.connect(callback)
            target_toolbar.addAction(action)
            button = target_toolbar.widgetForAction(action)
            if button is not None:
                button.setProperty("shellRole", role)
                button.setCursor(Qt.PointingHandCursor)
            return action

        toolbar = QToolBar("Application Navigation")
        toolbar.setObjectName("applicationBar")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        toolbar.setContextMenuPolicy(Qt.PreventContextMenu)
        toolbar.setIconSize(QSize(14, 14))
        toolbar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.addToolBar(Qt.TopToolBarArea, toolbar)
        self.application_toolbar = toolbar

        brand = QWidget()
        brand.setObjectName("shellBrand")
        brand_layout = QHBoxLayout(brand)
        brand_layout.setContentsMargins(2, 0, 10, 0)
        brand_layout.setSpacing(5)

        logo_label = QLabel()
        logo_label.setObjectName("shellLogo")
        logo = QPixmap(_resource_path(APP_TOOLBAR_LOGO_PATH))
        if not logo.isNull():
            logo_label.setPixmap(
                logo.scaled(23, 23, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        logo_label.setFixedSize(25, 25)
        logo_label.setAlignment(Qt.AlignCenter)

        brand_text = QVBoxLayout()
        brand_text.setContentsMargins(0, 0, 0, 0)
        brand_text.setSpacing(0)
        brand_title = QLabel("NEXUS")
        brand_title.setObjectName("shellBrandTitle")
        brand_caption = QLabel("PRODUCT DATA MANAGEMENT")
        brand_caption.setObjectName("shellBrandCaption")
        brand_text.addWidget(brand_title)
        brand_text.addWidget(brand_caption)
        brand_layout.addWidget(logo_label)
        brand_layout.addLayout(brand_text)
        toolbar.addWidget(brand)
        toolbar.addSeparator()

        structure_action = add_action(
            toolbar,
            "Structure",
            lambda _checked=False: self.switch_page(0),
            "SP_FileDialogListView",
            role="navigation",
            page_index=0,
            tooltip="Open CAD Structure and EBOM / Item Structure",
        )
        add_action(
            toolbar,
            "Dashboard",
            lambda _checked=False: self.switch_page(6),
            "SP_FileDialogDetailedView",
            role="navigation",
            page_index=6,
            tooltip="Open manager dashboard for readiness, risks and workload",
        )
        add_action(
            toolbar,
            "Commit",
            lambda _checked=False: self.switch_page(1),
            "SP_DialogApplyButton",
            role="navigation",
            page_index=1,
            tooltip="Open the controlled commit workspace",
        )
        add_action(
            toolbar,
            "Issues",
            lambda _checked=False: self.switch_page(2),
            "SP_MessageBoxWarning",
            role="navigation",
            page_index=2,
            tooltip="Open engineering issues and traceability",
        )
        toolbar.addSeparator()
        add_action(
            toolbar,
            "Diagnostics",
            lambda _checked=False: self.switch_page(3),
            "SP_ComputerIcon",
            role="navigation",
            page_index=3,
            tooltip="Inspect workspace and managed-content integrity",
        )
        add_action(
            toolbar,
            "Snapshots",
            lambda _checked=False: self.switch_page(5),
            "SP_DirOpenIcon",
            role="navigation",
            page_index=5,
            tooltip="Open recorded configuration snapshots",
        )

        # Admin remains permission-controlled; only its presentation moved.
        if UIPermissionHelper().can("admin_panel"):
            add_action(
                toolbar,
                "Administration",
                lambda _checked=False: self.switch_page(4),
                "SP_FileDialogInfoView",
                role="navigation",
                page_index=4,
                tooltip="Open controlled administration functions",
            )

        toolbar.addSeparator()
        add_action(
            toolbar,
            "CAD Viewer",
            self._launch_cad_viewer,
            "SP_DesktopIcon",
            role="workspace",
            tooltip="Open the Advanced CAD Viewer (STEP / IGES / BREP)",
        )

        spacer = QWidget()
        spacer.setObjectName("shellNavigationSpacer")
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        toolbar.addWidget(spacer)

        add_action(
            toolbar,
            "Refresh",
            self.refresh_current_page,
            "SP_BrowserReload",
            role="utility",
            tooltip="Refresh the current workspace",
        )
        add_action(
            toolbar,
            "Export",
            self.export_current,
            "SP_DialogSaveButton",
            role="utility",
            tooltip="Export from the current workspace",
        )
        toolbar.addSeparator()
        self.shell_user_label = QLabel(
            str(getattr(self.session, "username", None) or "SIGNED IN").upper()
        )
        self.shell_user_label.setObjectName("shellUserLabel")
        self.shell_user_label.setToolTip("Current Nexus user")
        toolbar.addWidget(self.shell_user_label)
        structure_action.setChecked(True)

        # Product/version context is deliberately separated from module navigation.
        self.addToolBarBreak(Qt.TopToolBarArea)
        context_toolbar = QToolBar("Product Context")
        context_toolbar.setObjectName("contextBar")
        context_toolbar.setMovable(False)
        context_toolbar.setFloatable(False)
        context_toolbar.setContextMenuPolicy(Qt.PreventContextMenu)
        self.addToolBar(Qt.TopToolBarArea, context_toolbar)
        self.context_toolbar = context_toolbar

        context_title = QLabel("PRODUCT CONTEXT")
        context_title.setObjectName("contextBarTitle")
        context_toolbar.addWidget(context_title)
        context_toolbar.addSeparator()

        self.project_combo = QComboBox()
        self.project_combo.setObjectName("productSelector")
        self.project_combo.currentIndexChanged.connect(self._on_project_root_changed)
        self.project_combo.setMinimumWidth(220)
        self.project_combo.setMaximumWidth(310)

        self.project_version_combo = QComboBox()
        self.project_version_combo.setObjectName("versionSelector")
        self.project_version_combo.currentIndexChanged.connect(self.save_current_project)
        self.project_version_combo.setMinimumWidth(105)
        self.project_version_combo.setMaximumWidth(145)

        project_caption = QLabel("PRODUCT")
        project_caption.setObjectName("shellFieldCaption")
        context_toolbar.addWidget(project_caption)
        context_toolbar.addWidget(self.project_combo)

        version_caption = QLabel("VERSION")
        version_caption.setObjectName("shellFieldCaption")
        context_toolbar.addWidget(version_caption)
        context_toolbar.addWidget(self.project_version_combo)

        new_version_button = QPushButton("New Version...")
        new_version_button.setObjectName("contextPrimaryAction")
        new_version_button.setCursor(Qt.PointingHandCursor)
        new_version_button.clicked.connect(self.show_new_version_dialog)
        context_toolbar.addWidget(new_version_button)

        context_spacer = QWidget()
        context_spacer.setObjectName("shellContextSpacer")
        context_spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        context_toolbar.addWidget(context_spacer)

        self.project_label = QLabel("Current: None")
        self.project_label.setObjectName("currentProjectLabel")
        self.project_label.setMinimumWidth(180)
        self.project_label.setMaximumWidth(360)
        self.project_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.project_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        context_toolbar.addWidget(self.project_label)
        self.refresh_project_selector(
            select_project_id=self.session.project_id, reload_on_change=False
        )

    def _set_active_shell_page(self, index):
        module, title, subtitle = PAGE_PRESENTATION.get(
            int(index), ("WORKSPACE", "Nexus", "Product data management")
        )
        if hasattr(self, "workspace_module_label"):
            self.workspace_module_label.setText(module)
            self.workspace_title_label.setText(title)
            self.workspace_subtitle_label.setText(subtitle)
            self.workspace_position_label.setText(f"{int(index) + 1:02d} / 06")
        action = getattr(self, "navigation_actions", {}).get(int(index))
        if action is not None:
            action.setChecked(True)

    def _project_root_id(self, project: dict):
        root_id = project.get("root_project_id") or project.get("id")
        try:
            return int(root_id) if root_id is not None else None
        except Exception:
            return None

    def _clean_project_display_name(self, name: str, version_label: str = "") -> str:
        text = str(name or "").strip()
        version = str(version_label or "").strip()
        if version and text.upper().endswith(f"__{version.upper()}"):
            text = text[:-(len(version) + 2)].rstrip()
        return text

    def _project_display_name(self, project: dict) -> str:
        root_name = self._clean_project_display_name(project.get("root_name") or "", "")
        if root_name:
            return root_name
        return self._clean_project_display_name(project.get("name") or project.get("id"), project.get("version_label") or "")

    def _version_sort_key(self, project: dict):
        label = str(project.get("version_label") or "A").strip().upper()
        n = 0
        for ch in label:
            if not ch.isalpha():
                return (999999, label)
            n = n * 26 + (ord(ch) - ord("A") + 1)
        return (n or 1, label)

    def _projects_for_root(self, root_id):
        try:
            root_id = int(root_id)
        except Exception:
            return []
        return sorted(
            [p for p in self._projects_for_user if self._project_root_id(p) == root_id],
            key=self._version_sort_key,
        )

    def refresh_project_selector(self, select_project_id=None, reload_on_change: bool = False):
        try:
            self._projects_for_user = self.project_service.get_projects_for_user(self.session.user_id) or []
        except Exception:
            self._projects_for_user = []

        try:
            self._project_combo_initializing = True
            self.project_combo.clear()
            self.project_version_combo.clear()

            if not self._projects_for_user:
                self.project_combo.addItem("No Projects", None)
                self.project_combo.setEnabled(False)
                self.project_version_combo.addItem("-", None)
                self.project_version_combo.setEnabled(False)
                self.refresh_project_label()
                return

            self.project_combo.setEnabled(True)
            self.project_version_combo.setEnabled(True)
            if not select_project_id:
                self.project_combo.addItem("Select a project...", None)

            roots = {}
            for project in self._projects_for_user:
                root_id = self._project_root_id(project)
                if root_id is not None and root_id not in roots:
                    roots[root_id] = self._project_display_name(project)

            for root_id, label in sorted(roots.items(), key=lambda item: str(item[1]).lower()):
                self.project_combo.addItem(str(label or root_id), root_id)

            selected_project = None
            if select_project_id:
                for project in self._projects_for_user:
                    if int(project.get("id")) == int(select_project_id):
                        selected_project = project
                        break

            selected_root_id = self._project_root_id(selected_project) if selected_project else None
            if selected_root_id is not None:
                idx = self.project_combo.findData(selected_root_id)
                if idx >= 0:
                    self.project_combo.setCurrentIndex(idx)
                    self._populate_project_version_combo(selected_root_id, select_project_id)
            elif self.project_combo.count() > 0:
                self.project_combo.setCurrentIndex(0)
                self._populate_project_version_combo(self.project_combo.currentData(), None)
            self.refresh_project_label()
        finally:
            self._project_combo_initializing = False

        if reload_on_change:
            self.reload_main_window()

    def _populate_project_version_combo(self, root_id, select_project_id=None):
        self.project_version_combo.clear()
        if root_id is None:
            self.project_version_combo.addItem("-", None)
            self.project_version_combo.setEnabled(False)
            return

        versions = self._projects_for_root(root_id)
        self.project_version_combo.setEnabled(bool(versions))
        for project in versions:
            label = str(project.get("version_label") or "A").strip() or "A"
            state = str(project.get("version_state") or "").strip()
            display = f"{label} - {state}" if state else label
            self.project_version_combo.addItem(display, project.get("id"))

        if select_project_id:
            idx = self.project_version_combo.findData(int(select_project_id))
            if idx >= 0:
                self.project_version_combo.setCurrentIndex(idx)
                return
        if self.project_version_combo.count() > 0:
            self.project_version_combo.setCurrentIndex(0)

    def _on_project_root_changed(self):
        if getattr(self, "_project_combo_initializing", False):
            return
        try:
            self._project_combo_initializing = True
            root_id = self.project_combo.currentData()
            self._populate_project_version_combo(root_id, None)
        finally:
            self._project_combo_initializing = False
        self.save_current_project()

    def show_new_version_dialog(self):
        if not self.session.project_id:
            QMessageBox.warning(self, "Error", "No project selected!")
            return

        version_label, ok = QInputDialog.getText(self, "New Version", "Version label (leave empty for auto: A..Z..AA..):")
        if not ok:
            return

        new_wd = QFileDialog.getExistingDirectory(self, "Select NEW working directory")
        if not new_wd:
            return

        cancel_event = threading.Event()

        class NewVersionWorker(QThread):
            progress = pyqtSignal(int, str)
            done = pyqtSignal(int)
            failed = pyqtSignal(str)

            def __init__(self, outer, src_project_id, user_id, wd, vlabel):
                super().__init__(outer)
                self._outer = outer
                self._src_project_id = int(src_project_id)
                self._user_id = int(user_id)
                self._wd = wd
                self._vlabel = vlabel

            def run(self):
                try:
                    def _progress(pct, msg=""):
                        try:
                            self.progress.emit(int(pct), str(msg or ""))
                        except Exception:
                            pass

                    def _cancelled():
                        return cancel_event.is_set()

                    new_project_id = self._outer.project_service.create_new_version(
                        source_project_id=self._src_project_id,
                        user_id=self._user_id,
                        new_working_directory=self._wd,
                        version_label=self._vlabel,
                        progress_cb=_progress,
                        cancel_cb=_cancelled,
                    )
                    self.done.emit(int(new_project_id))
                except Exception as e:
                    self.failed.emit(str(e))

        dlg = ProgressDialog(
            title="Creating New Version",
            message="Starting",
            cancel_callback=lambda: cancel_event.set(),
        )
        try:
            dlg.bar.setValue(0)
        except Exception:
            pass

        worker = NewVersionWorker(
            self,
            self.session.project_id,
            self.session.user_id,
            new_wd,
            (version_label.strip() if isinstance(version_label, str) else "") or None,
        )
        self._new_version_worker = worker

        worker.progress.connect(lambda pct, msg: dlg.update_progress(pct, msg))

        def _on_done(new_project_id: int):
            try:
                dlg.stop_animation()
                dlg.accept()
            except Exception:
                pass
            QMessageBox.information(self, "Success", f"New version created. New project ID: {new_project_id}")
            self.session.update_project(int(new_project_id))
            try:
                if getattr(self.session, "user_id", None):
                    self.user_repo.set_last_project_id(self.session.user_id, int(new_project_id))
            except Exception:
                pass
            self.refresh_project_selector(select_project_id=int(new_project_id))
            self.reload_main_window()

        def _on_failed(err: str):
            try:
                dlg.stop_animation()
                dlg.reject()
            except Exception:
                pass
            if "cancel" in (err or "").lower():
                QMessageBox.information(self, "Cancelled", "New version creation was cancelled.")
            else:
                QMessageBox.critical(self, "Error", f"Failed to create new version:\n{err}")

        worker.done.connect(_on_done)
        worker.failed.connect(_on_failed)
        worker.start()
        dlg.exec_()



    # === Project Persistence ===
    def save_current_project(self):
        if getattr(self, "_project_combo_initializing", False):
            return

        pid = self.project_version_combo.currentData() if hasattr(self, "project_version_combo") else self.project_combo.currentData()

        # Placeholder / "no project"
        if pid is None:
            self.session.update_project(None)
            try:
                if getattr(self.session, "user_id", None):
                    self.user_repo.set_last_project_id(self.session.user_id, None)
            except Exception:
                pass
            try:
                self.refresh_project_label()
            except Exception:
                pass
            self.statusBar().showMessage("No project selected")
            self.reload_main_window()
            return

        if not pid:
            # fallback to old behavior (name-based)
            project = self.project_version_combo.currentText() if hasattr(self, "project_version_combo") else self.project_combo.currentText()
            pid = self.project_service.get_project_id(project)

        # Validate working directory before switching.
        working_dir = ""
        try:
            project = self.project_service.get_project_by_id(pid) or {}
            working_dir = (project.get("working_directory") or "").strip()
        except Exception:
            working_dir = ""

        if not working_dir or not os.path.isdir(working_dir):
            try:
                QMessageBox.warning(
                    self,
                    "Project Directory Not Found",
                    "Cannot load the selected project because its working directory does not exist.\n"
                    "The app will keep running with no project loaded.\n\n"
                    f"{working_dir or '(missing path)'}",
                )
            except Exception:
                pass

            self.session.update_project(None)
            try:
                if getattr(self.session, "user_id", None):
                    self.user_repo.set_last_project_id(self.session.user_id, None)
            except Exception:
                pass

            # revert UI selection to placeholder if present
            try:
                self._project_combo_initializing = True
                placeholder_idx = self.project_combo.findData(None)
                if placeholder_idx >= 0:
                    self.project_combo.setCurrentIndex(placeholder_idx)
                if hasattr(self, "project_version_combo"):
                    self.project_version_combo.clear()
                    self.project_version_combo.addItem("-", None)
                    self.project_version_combo.setEnabled(False)
            finally:
                self._project_combo_initializing = False

            self.refresh_project_label()
            self.reload_main_window()
            return

        self.session.update_project(pid)
        try:
            if getattr(self.session, "user_id", None):
                self.user_repo.set_last_project_id(self.session.user_id, pid)
        except Exception:
            pass
        self.refresh_project_label()
        self.statusBar().showMessage(f"Project set to: {self.project_label.text().replace('Current: ', '')}")
        self.reload_main_window()
    
    def refresh_project_label(self):
        p = self.project_service.get_project_by_id(self.session.project_id) or {}
        if not p:
            label = "None"
            version = "-"
        else:
            label = self._clean_project_display_name(p.get("name") or str(self.session.project_id), p.get("version_label") or "")
            version = str(p.get("version_label") or "-").strip() or "-"

        self._active_project_label = label
        if hasattr(self, "project_label"):
            context = label if label == "None" else f"{label}  /  {version}"
            self.project_label.setText(f"Current: {context}")
            self.project_label.setToolTip(
                f"Active product: {label}\nActive version: {version}"
            )

        if hasattr(self, "status_identity_label"):
            username = str(getattr(self.session, "username", None) or "Signed in")
            self.status_identity_label.setText(
                f"{username.upper()}   |   PRODUCT {label}   |   VERSION {version}"
            )

    def switch_page(self, index):
        self._ensure_lazy_page(index)
        self.pages.setCurrentIndex(index)
        self._set_active_shell_page(index)
        _module, title, _subtitle = PAGE_PRESENTATION.get(
            int(index), ("WORKSPACE", "Nexus", "")
        )
        self.statusBar().showMessage(f"{title} ready")

    def _ensure_lazy_page(self, index):
        if index == 3 and not getattr(self, "_diag_loaded", False):
            self.statusBar().showMessage("Loading Diagnostic page...")
            QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)
            from pages.diag_page import DiagPage
            page = DiagPage()
            self.pages.removeWidget(self.diag_page)
            self.diag_page.deleteLater()
            self.diag_page = page
            self.pages.insertWidget(3, self.diag_page)
            self._diag_loaded = True
        elif index == 4 and not getattr(self, "_admin_loaded", False):
            self.statusBar().showMessage("Loading Admin page...")
            QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)
            from pages.admin_page import AdminPage
            page = AdminPage()
            self.pages.removeWidget(self.admin_page)
            self.admin_page.deleteLater()
            self.admin_page = page
            self.pages.insertWidget(4, self.admin_page)
            self._admin_loaded = True
        elif index == 5 and not getattr(self, "_snap_loaded", False):
            self.statusBar().showMessage("Loading Snapshot page...")
            QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)
            from pages.snapshot_page import SnapshotPage
            page = SnapshotPage()
            self.pages.removeWidget(self.snap_page)
            self.snap_page.deleteLater()
            self.snap_page = page
            self.pages.insertWidget(5, self.snap_page)
            self._snap_loaded = True

    def open_issues_for_part(self, part_id):
        self.issue_page.open_for_part(int(part_id))
        self.switch_page(2)

    def create_issue_for_part(self, part_id):
        self.issue_page.create_for_part(int(part_id))
        self.switch_page(2)

    def _launch_cad_viewer(self):
        """Open the Advanced CAD Viewer as a standalone subprocess (pyoccenv)."""
        import sys as _sys
        if getattr(_sys, "frozen", False):
            QMessageBox.information(
                self,
                "CAD Viewer",
                "The Advanced CAD Viewer will be available in the next version.",
            )
            return
        try:
            from tools.CAD.step_viewer.launcher import _build_cmd, _repo_root
            import subprocess as _sp
            cmd = _build_cmd([])
            _sp.Popen(cmd, cwd=_repo_root())
            self.statusBar().showMessage("CAD Viewer launched")
        except Exception as exc:
            QMessageBox.critical(self, "CAD Viewer", f"Failed to launch:\n{exc}")

    def refresh_current_page(self):
        current_index = self.pages.currentIndex()
        if current_index == 0:
            try:
                self.bom_page._bom_mode = "ebom"
                self.bom_page.bom_mode_selector.setCurrentIndex(
                    self.bom_page.bom_mode_selector.findData("ebom")
                )
                self.bom_page._load_released_ebom_tree()
            except Exception:
                self.bom_page.load_tree()
            self.statusBar().showMessage("BOM refreshed")
            return
        elif current_index == 1:
            self.statusBar().showMessage("Commit page refreshed")
            return
        elif current_index == 2:
            self.issue_page.refresh()
            self.statusBar().showMessage("Issue Center refreshed")
            return
        elif current_index == 6:
            self.dashboard_page.refresh()
            self.statusBar().showMessage("Manager Dashboard refreshing")
            return

    def export_current(self):
        current_index = self.pages.currentIndex()
        if current_index == 0:
            self.bom_page.export_bom()
        elif current_index == 1:
            QMessageBox.information(self, "Info", "Commit export coming soon!")
        elif current_index == 6:
            self.dashboard_page.export_dashboard()


    

    def show_toast(parent, message):
        toast = QLabel(message, parent)
        toast.setStyleSheet("""
            background-color: #20364b;
            color: #ffffff;
            border: 1px solid #4f6476;
            border-radius: 2px;
            padding: 7px 12px;
            font: 9pt "Segoe UI";
        """)
        toast.setWindowFlags(Qt.ToolTip)
        toast.adjustSize()
        # Position relative to the parent window's top-centre on screen.
        parent_pos = parent.mapToGlobal(parent.rect().topLeft())
        x = parent_pos.x() + parent.width() // 2 - toast.width() // 2
        y = parent_pos.y() + 60
        toast.move(x, y)
        toast.show()

        # Store animation on the widget so Python's GC does not destroy it
        # before the fade completes.
        toast._anim = QPropertyAnimation(toast, b"windowOpacity")
        toast._anim.setDuration(2000)
        toast._anim.setStartValue(1)
        toast._anim.setEndValue(0)
        toast._anim.finished.connect(toast.close)
        toast._anim.start()
        QTimer.singleShot(2500, toast.close)

    def apply_styles(self):
        try:
            with open(
                _resource_path("modern_theme.qss"), "r", encoding="utf-8"
            ) as handle:
                self.setStyleSheet(handle.read())
        except OSError as exc:
            print(f"[ui] stylesheet warning: {exc}")

if __name__ == "__main__":

    app = QApplication(sys.argv)
    logo_path = _resource_path(APP_LOADER_LOGO_PATH)
    app.setStyle("Fusion")
    # Keep standard controls at the compact density used by desktop CAD tools.
    app.setFont(QFont("Segoe UI", 8))

    # ------------------------------------------------------------------
    # Workspace resolution — must run before any DB access so that
    # config.DB_NAME is updated to the full shared-folder path.
    #
    # The resolver reads  %APPDATA%/creovcs/config.json, validates the
    # saved path (creo_vcs.db must exist), and — when necessary — shows
    # a folder-picker dialog.  The chosen path is saved back to the
    # config file for next launch.
    # ------------------------------------------------------------------
    from core.workspace_config import resolve_workspace as _resolve_workspace
    import config as _cfg_mod

    _ws_result = _resolve_workspace(parent=None)
    if _ws_result is None:
        QMessageBox.critical(
            None,
            "Workspace Required",
            "No workspace folder was selected.\n\n"
            f"{APP_NAME} needs a shared folder that contains '{_cfg_mod._DB_FILENAME}'.\n"
            "The application cannot start without it.",
        )
        sys.exit(1)

    # Point every repository to the resolved database path.
    _cfg_mod.DB_NAME.set(str(_ws_result.db_path))
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # License verification — runs before login so the application cannot
    # be used at all without a valid license.
    #
    # Development:  set CREOVCS_PUBLIC_KEY_HEX (64-char hex public key
    #               from tools/licensing/keygen.py) and optionally
    #               CREOVCS_LICENSE_PATH (default: creovcs.lic).
    # Production:   bake CREOVCS_PUBLIC_KEY_HEX into a build_constants.py
    #               generated by CI/CD and excluded from source control.
    # ------------------------------------------------------------------
    from pathlib import Path as _Path
    from core.licensing import LicenseManager as _LicenseManager, LicenseError as _LicenseError

    # _PRODUCTION_PUBLIC_KEY is defined at module level above BomGUI.
    _pub_key_hex = os.environ.get("CREOVCS_PUBLIC_KEY_HEX", _PRODUCTION_PUBLIC_KEY).strip()

    # License path resolution order:
    #   1. CREOVCS_LICENSE_PATH env var (dev / testing override)
    #   2. %APPDATA%\CreoVCS\creovcs.lic  (installed by setup_license.py)
    #   3. creovcs.lic next to the exe    (legacy / manual placement)
    _appdata_lic = _Path(os.environ.get("APPDATA", "")) / "CreoVCS" / "creovcs.lic"
    _default_lic = _appdata_lic if _appdata_lic.exists() else _Path("creovcs.lic")
    _license_path = _Path(os.environ.get("CREOVCS_LICENSE_PATH", str(_default_lic)))
    try:
        # revoked.json lives in the same shared folder as creo_vcs.db.
        # Admin edits it to revoke a license — no rebuild needed.
        from config import DB_NAME as _DB_NAME
        _revocation_path = _Path(_DB_NAME).parent / "revoked.json"

        _LicenseManager.initialize(
            _license_path,
            bytes.fromhex(_pub_key_hex),
            revocation_path=_revocation_path,
        )
    except _LicenseError as _lic_exc:
        QMessageBox.critical(
            None,
            "License Error",
            f"{APP_NAME} cannot start:\n\n{_lic_exc}\n\n"
            "Contact your administrator to obtain a valid license.",
        )
        sys.exit(1)
    # ------------------------------------------------------------------

    startup_window = StartupWindow(logo_path)
    startup_window.showMaximized()
    sys.exit(app.exec_())


    
