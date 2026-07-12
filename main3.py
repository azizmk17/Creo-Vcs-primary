import sys
from PyQt5.QtWidgets import QLabel, QInputDialog, QApplication, QMainWindow, QSystemTrayIcon, QStackedWidget, QAction, QToolBar, QMessageBox, QComboBox, QFileDialog, QProgressBar
from PyQt5.QtCore import Qt, QTimer, QPropertyAnimation, pyqtSignal, QObject, QThread, QEventLoop
from PyQt5.QtGui import QPainter, QColor, QPen, QPixmap, QFont, QIcon
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QPushButton
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
    """Modern BOM Management Application with Toolbar Navigation"""

    def __init__(self, startup_progress=None):
        super().__init__()
        self.setWindowTitle(f"BOM Manager - {APP_NAME}")
        self.resize(1400, 900)
        self.setWindowIcon(QIcon(_resource_path(APP_ICON_PATH)))

        self.session = SessionManager()
        self.user_repo = UserRepository()
        # Services
        self.bom_service = BomService(BomRepository(), BomChildrenRepository(), LockRepository(), SignatureRepository())
        self.project_service = ProjectService()

        self._project_combo_initializing = False
        self._projects_for_user = []
        self._ensure_valid_current_project()

        # Toolbar with navigation
        self.init_toolbar()

        # Status bar
        self.statusBar().showMessage("Ready")

        self._build_ui(startup_progress=startup_progress)

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
         # Central stacked pages
        self.pages = QStackedWidget()
        self.setCentralWidget(self.pages)

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
        self.bom_page.issue_requested.connect(self.open_issues_for_part)
        self.bom_page.create_issue_requested.connect(self.create_issue_for_part)
        self.issue_page.issue_changed.connect(self.bom_page.refresh_issue_indicators)
        self.issue_page.issue_changed.connect(lambda _part_ids: self.commit_page._refresh_resolved_issues())

        

        # self.notifier = Notifier()
        # self.notifier.notify.connect(self.show_toast)

        
        # Apply styles
        #info: self.apply_styles()

        with open(_resource_path("modern_theme.qss"), "r") as f:
            self.setStyleSheet(f.read())

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
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignCenter)
        msg = QLabel(f"{label} will load when opened.")
        msg.setStyleSheet("font-size: 14px; color: #66758a;")
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
        toolbar = QToolBar("Main Toolbar")
        toolbar.setMovable(False)
        self.addToolBar(Qt.TopToolBarArea, toolbar)

        logo_label = QLabel()
        logo = QPixmap(_resource_path(APP_TOOLBAR_LOGO_PATH))
        if not logo.isNull():
            logo_label.setPixmap(logo.scaled(34, 34, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        logo_label.setFixedSize(42, 36)
        logo_label.setAlignment(Qt.AlignCenter)
        logo_label.setToolTip(APP_NAME)
        logo_label.setStyleSheet("padding: 0 4px;")
        toolbar.addWidget(logo_label)

        # Navigation buttons
        bom_action = QAction("BOM", self)
        bom_action.triggered.connect(lambda: self.switch_page(0))
        toolbar.addAction(bom_action)

        commit_action = QAction("Commit", self)
        commit_action.triggered.connect(lambda: self.switch_page(1))
        toolbar.addAction(commit_action)

        issue_action = QAction("Issue Center", self)
        issue_action.triggered.connect(lambda: self.switch_page(2))
        toolbar.addAction(issue_action)

        toolbar.addSeparator()

        # Refresh button
        refresh_action = QAction("Refresh", self)
        refresh_action.triggered.connect(self.refresh_current_page)
        toolbar.addAction(refresh_action)

        

        # Export button
        export_action = QAction("Export", self)
        export_action.triggered.connect(self.export_current)
        toolbar.addAction(export_action)

        toolbar.addSeparator()

        diag_action = QAction("Diagnostic", self)
        diag_action.triggered.connect(lambda: self.switch_page(3))
        toolbar.addAction(diag_action)

        snap_action = QAction("Snapshots", self)
        snap_action.triggered.connect(lambda: self.switch_page(5))
        toolbar.addAction(snap_action)

        # Admin page button — only visible to admin-level users
        # Uses UIPermissionHelper so it covers both is_admin DB flag and "admin" role name
        if UIPermissionHelper().can("admin_panel"):
            admin_action = QAction("Admin", self)
            admin_action.triggered.connect(lambda: self.switch_page(4))
            toolbar.addAction(admin_action)

        toolbar.addSeparator()

        # CAD Viewer button  — launches the advanced 3D viewer in a subprocess
        cad_viewer_action = QAction("CAD Viewer", self)
        cad_viewer_action.setToolTip("Open the Advanced CAD Viewer (STEP / IGES / BREP)")
        cad_viewer_action.triggered.connect(self._launch_cad_viewer)
        toolbar.addAction(cad_viewer_action)

        toolbar.addSeparator()

        # === Project / Version Selectors ===
        self.project_combo = QComboBox()
        self.project_combo.currentIndexChanged.connect(self._on_project_root_changed)
        self.project_combo.setMinimumWidth(210)
        self.project_combo.setMaximumWidth(270)

        self.project_version_combo = QComboBox()
        self.project_version_combo.currentIndexChanged.connect(self.save_current_project)
        self.project_version_combo.setMinimumWidth(90)
        self.project_version_combo.setMaximumWidth(130)

        project_caption = QLabel("Project")
        project_caption.setStyleSheet("color: #e5e7eb; font-weight: 700; padding-left: 6px;")
        toolbar.addWidget(project_caption)
        toolbar.addWidget(self.project_combo)

        version_caption = QLabel("Version")
        version_caption.setStyleSheet("color: #e5e7eb; font-weight: 700; padding-left: 6px;")
        toolbar.addWidget(version_caption)
        toolbar.addWidget(self.project_version_combo)

        new_version_button = QPushButton("Create New Version")
        new_version_button.setCursor(Qt.PointingHandCursor)
        new_version_button.setStyleSheet("""
            QPushButton {
                min-height: 26px;
                padding: 3px 10px;
                border-radius: 5px;
                background: #ffffff;
                border: 1px solid #cfd6df;
                color: #111827;
                font-weight: 700;
            }
            QPushButton:hover { background: #f3f5f7; }
        """)
        new_version_button.clicked.connect(self.show_new_version_dialog)
        toolbar.addWidget(new_version_button)
        toolbar.addSeparator()

        
        # Create and store the project label
        self.project_label = QLabel("Current: None")
        self.project_label.setMinimumWidth(160)
        self.project_label.setMaximumWidth(260)
        self.project_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.project_label.setStyleSheet("color: #ffffff; font-weight: 700; padding: 0 8px;")
        toolbar.addWidget(self.project_label)
        self.refresh_project_selector(select_project_id=self.session.project_id, reload_on_change=False)

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
        else:
            label = self._clean_project_display_name(p.get("name") or str(self.session.project_id), p.get("version_label") or "")
        self.project_label.setText(f"Current: {label}")

    def switch_page(self, index):
        self._ensure_lazy_page(index)
        self.pages.setCurrentIndex(index)
        page_names = ["BOM", "Commit", "Issue Center", "Diagnostic", "Admin", "Snapshot"]
        self.statusBar().showMessage(f"Switched to {page_names[index]} page")

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
        self.pages.setCurrentIndex(0)
        if current_index == 0:
            self.bom_page.load_tree()
            self.statusBar().showMessage("BOM refreshed")
        elif current_index == 1:
            self.statusBar().showMessage("Commit page refreshed")
        elif current_index == 2:
            self.issue_page.refresh()
            self.statusBar().showMessage("Issue Center refreshed")

        self.reload_main_window()

    def export_current(self):
        current_index = self.pages.currentIndex()
        if current_index == 0:
            self.bom_page.export_bom()
        elif current_index == 1:
            QMessageBox.information(self, "Info", "Commit export coming soon!")


    

    def show_toast(parent, message):
        toast = QLabel(message, parent)
        toast.setStyleSheet("""
            background-color: #333;
            color: white;
            padding: 8px 15px;
            border-radius: 8px;
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




    #idea: how to use css to style the app
    #idea: self.add_file_btn = QPushButton("📂 Add File(s)")
    #idea: self.add_file_btn.setObjectName("primary")   # blue button

    #idea: self.remove_part_btn = QPushButton("❌ Remove Selected")
    #idea: self.remove_part_btn.setObjectName("danger")  # red button

    #idea: self.revert_btn = QPushButton("↩️ Revert Selected Commit")
    #idea: self.revert_btn.setObjectName("neutral")      # gray button


    def apply_styles(self):
        self.setStyleSheet("""
            QMainWindow {
                background-color: #f4f6f8;  /* light gray app background */
            }

            /* --- Toolbar --- */
            QToolBar {
                background: #1f2937;  /* dark slate */
                border: none;
                padding: 7px 10px;
                spacing: 8px;
            }

            QToolButton {
                color: #e5e7eb;  /* light gray text */
                padding: 6px 14px;
                border-radius: 6px;
            }
            QToolButton:hover {
                background: #374151;
            }
            QToolButton:pressed {
                background: #2563eb;
            }

            /* --- ComboBox --- */
            QComboBox {
                background: #ffffff;
                color: #111827;
                padding: 6px;
                border-radius: 6px;
                border: 1px solid #d1d5db;
                min-width: 120px;
            }
            QComboBox:hover {
                border: 1px solid #3b82f6;
            }

            /* --- GroupBox --- */
            QGroupBox {
                font-weight: bold;
                color: #111827;
                border: 1px solid #d1d5db;
                border-radius: 8px;
                margin-top: 10px;
                background: #ffffff;
                padding: 8px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 0 6px;
                color: #2563eb;
            }

            /* --- ListWidget --- */
            QListWidget {
                background: #ffffff;
                border: 1px solid #d1d5db;
                border-radius: 6px;
                padding: 6px;
            }
            QListWidget::item {
                padding: 6px;
            }
            QListWidget::item:selected {
                background: #2563eb;
                color: white;
                border-radius: 4px;
            }
            QListWidget::item:hover {
                background: #e5e7eb;
            }

            /* --- PushButtons --- */
            QPushButton {
                padding: 6px 14px;
                border-radius: 6px;
                border: none;
                font-weight: 500;
            }

            /* Default hover/pressed */
            QPushButton:hover:enabled {
                opacity: 0.9;
            }
            QPushButton:pressed:enabled {
                transform: scale(0.97);
            }

            /* Primary (blue) */
            QPushButton#primary {
                background-color: #2563eb;
                color: white;
            }
            QPushButton#primary:hover:enabled {
                background-color: #1d4ed8;
            }
            QPushButton#primary:disabled {
                background-color: #93c5fd;  /* soft blue */
                color: #f3f4f6;             /* light gray text */
            }
                           
            /* secondary (green) */
            QPushButton#secondary {
                background-color: #38A169;
                color: white;
            }
            QPushButton#secondary:hover:enabled {
                background-color: #2F855A;
            }
            QPushButton#secondary:disabled {
                background-color: #9FD8BB;  /* soft green */
                color: #f3f4f6;             /* light gray text */
            }

            /* Danger (red) */
            QPushButton#danger {
                background-color: #dc2626;
                color: white;
            }
            QPushButton#danger:hover:enabled {
                background-color: #b91c1c;
            }
            QPushButton#danger:disabled {
                background-color: #fca5a5;  /* soft red */
                color: #f3f4f6;
            }

            /* Neutral (gray) */
            QPushButton#neutral {
                background-color: #e5e7eb;
                color: #111827;
            }
            QPushButton#neutral:hover:enabled {
                background-color: #d1d5db;
            }
            QPushButton#neutral:disabled {
                background-color: #f3f4f6;  /* pale gray */
                color: #9ca3af;             /* muted text */
            }

        """)

        




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


    
