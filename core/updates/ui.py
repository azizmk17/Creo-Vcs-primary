"""Responsive update center shared by startup compatibility and the app shell."""
import os
import json
from pathlib import Path
import subprocess
import sys
import threading

from PyQt5.QtCore import QObject, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication, QDialog, QFileDialog, QHBoxLayout, QLabel, QMessageBox,
    QProgressBar, QPushButton, QTextBrowser, QVBoxLayout,
)

from core.updates import settings
from core.updates.protocol import load_manifest, version_tuple
from core.updates.trust import release_public_key


class _Signals(QObject):
    finished = pyqtSignal(str, object, str)


class UpdateController(QObject):
    changed = pyqtSignal()

    def __init__(self, parent, version, db_path):
        super().__init__(parent)
        self.window = parent
        self.version = version
        self.db_path = str(db_path)
        self.payload = None
        self.status = "Choose the company release folder, then check for updates."
        self.busy = False
        self.staged = False
        self._staged_identity = None
        root = settings.managed_root()
        if root:
            try:
                staged = json.loads((root / "staged.json").read_text(encoding="utf-8"))
                self._staged_identity = self._release_identity(staged["payload"])
            except (OSError, ValueError, KeyError, TypeError):
                pass
        self._notified = None
        self._workers = []
        self._center = None
        self.timer = QTimer(self)
        self.timer.setInterval(15 * 60 * 1000)
        self.timer.timeout.connect(self.check)
        self.timer.start()

    @staticmethod
    def _release_identity(payload):
        return tuple(payload[key] for key in ("version", "build_id", "release_sequence"))

    def open(self):
        if self._center is None:
            self._center = UpdateCenter(self)
        self._center.show()
        self._center.raise_()
        self._center.activateWindow()
        self.check()

    def _run(self, name, operation):
        if self.busy:
            return
        self.busy = True
        signals = _Signals(self)
        signals.finished.connect(self._finished)
        def work():
            try:
                result, error = operation(), ""
            except Exception as exc:
                result, error = None, str(exc)
            try:
                signals.finished.emit(name, result, error)
            except RuntimeError:
                pass  # The app closed while a background check was finishing.
        thread = threading.Thread(target=work, name="Nexus update " + name, daemon=True)
        self._workers.append((thread, signals))
        self.changed.emit()
        thread.start()

    def check(self):
        if self.busy:
            return
        feed = settings.update_feed()
        try:
            public = release_public_key()
        except ValueError as exc:
            self.status = str(exc)
            self.changed.emit()
            return
        if not public:
            self.status = "This build has no release verification key. Ask your administrator for an update-enabled build."
            self.changed.emit()
            return
        if not feed:
            self.changed.emit()
            return
        self.status = "Checking the company release folder..."
        def operation():
            result = load_manifest(Path(feed) / "latest.json", public, last_sequence=settings.last_sequence(public))
            settings.remember_sequence(public, result["release_sequence"])
            return result
        self._run("check", operation)

    def choose_feed(self):
        feed = QFileDialog.getExistingDirectory(self.window, "Select the company release folder", settings.update_feed())
        if not feed:
            return
        if settings.managed_root():
            QMessageBox.information(self.window, "Managed update source", "This installation uses the release folder selected by its installer. Ask your administrator to change it.")
            return
        settings.save_settings(feed=feed)
        self.payload = None
        self.staged = False
        self.check()

    def download(self):
        if not self.payload or self.busy:
            return
        root = settings.managed_root()
        if root is None:
            source = Path(sys.executable).resolve().parent
            installer = source / "Setup.exe"
            if not installer.is_file():
                QMessageBox.information(self.window, "Install Nexus", "Open Setup.exe in the full extracted Nexus folder supplied by your administrator.")
                return
            subprocess.Popen([str(installer)], cwd=str(source))
            QMessageBox.information(self.window, "Nexus Setup", "Complete Setup, save your work and close this copy of Nexus. Start Nexus from its new shortcut to use managed updates.")
            return
        from core.updates.install import stage_release
        self.status = "Downloading and verifying the update. You can continue working."
        self._run("stage", lambda: stage_release(settings.update_feed(), release_public_key(), root, db_path=self.db_path))

    def restart(self):
        if not self.staged:
            return
        answer = QMessageBox.question(self.window, "Restart to update", "Save your work and close any CADViewer windows before continuing. Restart Nexus with the prepared update?", QMessageBox.Yes | QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        root = settings.managed_root()
        updater = root / "bootstrap" / "NexusUpdater.exe"
        if not updater.is_file():
            QMessageBox.warning(self.window, "Updater missing", "Restore the managed installation from an administrator-approved package.")
            return
        subprocess.Popen([str(updater), "--root", str(root), "--activate-staged", "--wait-pid", str(os.getpid()), "--launch"], cwd=str(updater.parent))
        QApplication.instance().quit()

    def _finished(self, operation, result, error):
        self.busy = False
        self._workers = [(thread, signals) for thread, signals in self._workers if thread.is_alive()]
        if error:
            self.payload = None if operation == "check" else self.payload
            if operation == "check":
                self.staged = False
            self.status = "Update could not be prepared: " + error
        elif operation == "stage":
            self._staged_identity = self._release_identity(result["payload"])
            self.staged = True
            self.status = "Update verified and ready. Save your work, then restart when convenient."
        else:
            self.payload = result
            # Local metadata only restores readiness after the feed has been
            # authenticated. Activation verifies the archive and files again.
            self.staged = self._staged_identity == self._release_identity(result)
            newer = version_tuple(result["version"]) > version_tuple(self.version)
            self.status = ("Nexus " + result["version"] + " is available.") if newer else "You are using the latest available version."
            if newer and self._notified != result["version"]:
                self._notified = result["version"]
                if self._center is None or not self._center.isVisible():
                    box = QMessageBox(self.window)
                    box.setWindowTitle("Update available")
                    box.setText(f"Nexus {result['version']} is available. Your version is {self.version}.")
                    view = box.addButton("View update", QMessageBox.AcceptRole)
                    box.addButton("Later", QMessageBox.RejectRole)
                    box.exec_()
                    if box.clickedButton() is view:
                        self.open()
        self.changed.emit()


class UpdateCenter(QDialog):
    def __init__(self, controller):
        super().__init__(controller.window)
        self.controller = controller
        self.setWindowTitle("Nexus updates")
        self.resize(640, 380)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Installed version: " + controller.version))
        self.source = QLabel()
        self.source.setWordWrap(True)
        layout.addWidget(self.source)
        self.message = QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        layout.addWidget(self.progress)
        self.notes = QTextBrowser()
        self.notes.setOpenExternalLinks(False)
        layout.addWidget(self.notes)
        buttons = QHBoxLayout()
        layout.addLayout(buttons)
        for title, callback in (("Release folder", controller.choose_feed), ("Check now", controller.check), ("Download update", controller.download), ("Restart to update", controller.restart), ("Close", self.close)):
            button = QPushButton(title)
            button.clicked.connect(callback)
            buttons.addWidget(button)
            if title == "Download update":
                self.download_button = button
            elif title == "Restart to update":
                self.restart_button = button
            elif title == "Check now":
                self.check_button = button
        controller.changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        controller = self.controller
        self.source.setText("Release folder: " + (settings.update_feed() or "not configured"))
        self.message.setText(controller.status)
        self.progress.setVisible(controller.busy)
        self.notes.setPlainText((controller.payload or {}).get("release_notes", ""))
        available = bool(controller.payload and version_tuple(controller.payload["version"]) > version_tuple(controller.version))
        self.download_button.setEnabled(available and not controller.busy and not controller.staged)
        self.restart_button.setEnabled(controller.staged and not controller.busy)
        self.check_button.setEnabled(not controller.busy)


def show_compatibility_error(message, version, db_path):
    box = QMessageBox()
    box.setWindowTitle("Workspace requires administrator attention")
    box.setIcon(QMessageBox.Warning)
    box.setText(str(message))
    updates = box.addButton("Check for updates", QMessageBox.ActionRole)
    box.addButton("Close", QMessageBox.RejectRole)
    box.exec_()
    if box.clickedButton() is updates:
        # This dialog contains no login repositories or database-writing widgets.
        host = QDialog()
        controller = UpdateController(host, version, db_path)
        center = UpdateCenter(controller)
        controller._center = center
        controller.check()
        center.exec_()
