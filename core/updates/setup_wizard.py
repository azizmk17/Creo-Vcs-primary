"""First-install window for an extracted, trusted Nexus distribution.

The wizard copies application files, optionally installs a license, and creates
per-user shortcuts. It does not resolve a workspace or open a database.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtGui import QFont, QIcon
from PyQt5.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

from . import install


def _create_shortcuts(root: Path, *, desktop: bool):
    # Import here so the small setup window can also report a shortcut failure
    # after a successful installation and offer a repair without copying again.
    from .shortcuts import create_shortcuts
    return create_shortcuts(root, desktop=desktop)


def _location(value: str) -> Path:
    if not value.strip():
        raise ValueError("Choose an installation folder.")
    return Path(os.path.expandvars(value.strip())).expanduser().resolve()


def _license_destination() -> Path:
    app_data = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    return app_data / "CreoVCS" / "creovcs.lic"


def _install_license(source: Path) -> Path:
    """Install the selected signed license without risking an existing file."""
    source = Path(source)
    if source.suffix.casefold() != ".lic":
        raise ValueError("Choose a license file with the .lic extension.")
    if not source.is_file():
        raise FileNotFoundError(f"License file not found: {source}")

    destination = _license_destination()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=".creovcs-license-", suffix=".tmp", dir=destination.parent, delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        shutil.copy2(source, temporary_path)
        os.replace(temporary_path, destination)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return destination


def _destination(root: Path) -> tuple[str, dict, str]:
    """Inspect only local installation metadata; never create folders here."""
    if not root.exists():
        return "empty", {}, "Ready to install for your Windows account."
    if not root.is_dir():
        return "blocked", {}, "Choose an empty folder. The selected location is a file."
    if (root / "current.json").is_file() and (root / "install.json").is_file():
        try:
            install.current_application(root)
            if not (root / "bootstrap" / "NexusLauncher.exe").is_file():
                raise install.InstallError("The Nexus launcher is missing.")
            current = install._read_json(root / "current.json")
            return "installed", current, (
                f"Nexus {current.get('version', '')} is already installed here. "
                "Open Nexus or repair its shortcuts. Use Updates inside Nexus to change versions."
            )
        except (OSError, ValueError, install.InstallError):
            pass
    if any(root.iterdir()):
        return "blocked", {}, (
            "This folder is not empty or contains an incomplete installation. "
            "Choose a new empty folder to install Nexus."
        )
    return "empty", {}, "Ready to install for your Windows account."


class _SetupWorker(QThread):
    progress = pyqtSignal(str)
    result = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, source: Path, root: Path, public_key_hex: str, feed: str,
                 desktop: bool, license_path: Path | None = None, *, repair=False, parent=None):
        super().__init__(parent)
        self.source, self.root = source, root
        self.public_key_hex, self.feed = public_key_hex, feed
        self.desktop, self.repair = desktop, repair
        self.license_path = license_path

    def run(self):
        try:
            license_destination = ""
            if self.license_path:
                self.progress.emit("Installing your CreoVCS license…")
                license_destination = str(_install_license(self.license_path))
            if self.repair:
                current = install._read_json(self.root / "current.json")
            else:
                self.progress.emit("Copying Nexus and its PDF/CAD libraries. This may take a few minutes…")
                current = install.initial_install(
                    self.source, self.root, self.feed, self.public_key_hex,
                )
            warnings = []
            self.progress.emit("Creating your Start menu and desktop shortcuts…")
            shortcuts = []
            try:
                shortcuts = [str(path) for path in _create_shortcuts(self.root, desktop=self.desktop)]
            except Exception as exc:
                # Installation has committed already. Keep that success visible
                # and let the user retry only the failed shortcut operation.
                warnings.append(f"Some shortcuts could not be created: {exc}")
            self.result.emit({
                "current": current,
                "shortcuts": shortcuts,
                "license_destination": license_destination,
                "warning": "\n".join(warnings),
            })
        except Exception as exc:
            self.error.emit(str(exc))


class SetupDialog(QDialog):
    def __init__(self, source, public_key_hex: str, *, root=None, feed="", parent=None):
        super().__init__(parent)
        self.source = Path(source).resolve()
        self.public_key_hex = public_key_hex
        self._worker = None
        self._job_result = None
        self._job_error = ""
        self._completed = False
        self._state = "empty"
        self._current = {}
        self.setWindowTitle("Nexus Setup")
        self.setMinimumWidth(680)
        self.setFont(QFont("Segoe UI", 10))
        bundle = Path(getattr(sys, "_MEIPASS", self.source / "_internal"))
        icon = bundle / "assets/pictures/nexus_logo.ico"
        if icon.is_file():
            self.setWindowIcon(QIcon(str(icon)))
        self.setStyleSheet("QLineEdit { min-height: 26px; } QPushButton { min-height: 26px; padding: 2px 12px; }")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)
        heading = QLabel("Install Nexus")
        heading.setStyleSheet("font-size: 22px; font-weight: 600;")
        layout.addWidget(heading)
        introduction = QLabel(
            "Setup installs Nexus for your Windows account and adds shortcuts. "
            "No administrator password is required."
        )
        introduction.setWordWrap(True)
        layout.addWidget(introduction)

        form = QFormLayout()
        self.location_edit = QLineEdit(str(Path(root or install.default_install_root()).resolve()))
        self.location_edit.setCursorPosition(0)
        self.location_button = QPushButton("Browse…")
        form.addRow("Install location:", self._path_row(self.location_edit, self.location_button))
        self.feed_edit = QLineEdit(str(feed or ""))
        self.feed_edit.setPlaceholderText("Optional — company release folder, e.g. \\\\server\\NexusReleases")
        self.feed_button = QPushButton("Browse…")
        form.addRow("Company updates:", self._path_row(self.feed_edit, self.feed_button))
        self.license_edit = QLineEdit()
        self.license_edit.setPlaceholderText("Optional — select your administrator-provided .lic file")
        self.license_button = QPushButton("Browse…")
        form.addRow("CreoVCS license:", self._path_row(self.license_edit, self.license_button))
        layout.addLayout(form)
        self.desktop_checkbox = QCheckBox("Create a Desktop shortcut during installation")
        self.desktop_checkbox.setChecked(True)
        layout.addWidget(self.desktop_checkbox)
        self.options_help = QLabel(
            f"A Start Menu shortcut is included. A selected license is copied to "
            f"{_license_destination()}. Leave the license field blank to keep any license already installed."
        )
        self.options_help.setWordWrap(True)
        layout.addWidget(self.options_help)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.hide()
        layout.addWidget(self.progress)

        buttons = QHBoxLayout()
        self.repair_button = QPushButton("Repair shortcuts")
        self.repair_button.hide()
        buttons.addWidget(self.repair_button)
        buttons.addStretch(1)
        self.cancel_button = QPushButton("Cancel")
        self.install_button = QPushButton("Install")
        self.install_button.setStyleSheet("background: #226998; color: white; border: 1px solid #19567e; border-radius: 3px;")
        self.install_button.setDefault(True)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.install_button)
        layout.addLayout(buttons)

        self.location_edit.textChanged.connect(self._refresh)
        self.location_button.clicked.connect(self._browse_location)
        self.feed_button.clicked.connect(self._browse_feed)
        self.license_button.clicked.connect(self._browse_license)
        self.install_button.clicked.connect(self._primary_action)
        self.repair_button.clicked.connect(self._repair_shortcuts)
        self.cancel_button.clicked.connect(self._close_requested)
        self._refresh()

    @staticmethod
    def _path_row(edit: QLineEdit, button: QPushButton) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(edit)
        layout.addWidget(button)
        return row

    @property
    def busy(self) -> bool:
        return self._worker is not None

    def _refresh(self):
        if self.busy:
            return
        try:
            self._state, self._current, message = _destination(_location(self.location_edit.text()))
        except (OSError, ValueError) as exc:
            self._state, self._current, message = "blocked", {}, str(exc)
        installed = self._state == "installed"
        self.status_label.setText(message)
        self.install_button.setText("Open Nexus" if installed else "Install")
        self.install_button.setEnabled(self._state != "blocked")
        self.repair_button.setVisible(installed)
        self.cancel_button.setText("Close" if installed or self._completed else "Cancel")
        self.feed_edit.setEnabled(not installed)
        self.feed_button.setEnabled(not installed)
        self.license_edit.setEnabled(not installed)
        self.license_button.setEnabled(not installed)
        self.progress.hide()

    def _browse_location(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose installation folder", self.location_edit.text())
        if folder:
            self.location_edit.setText(folder)

    def _browse_feed(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose company release folder", self.feed_edit.text())
        if folder:
            self.feed_edit.setText(folder)

    def _browse_license(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose CreoVCS license", str(Path.home()),
            "CreoVCS license (*.lic);;All files (*.*)",
        )
        if path:
            self.license_edit.setText(path)

    def _primary_action(self):
        if self.busy:
            return
        if self._state == "installed":
            self._open_nexus()
        elif self._state == "empty":
            self._start_job(repair=False)

    def _repair_shortcuts(self):
        if not self.busy and self._state == "installed":
            self._start_job(repair=True)

    def _start_job(self, *, repair: bool):
        self._job_result, self._job_error = None, ""
        try:
            root = _location(self.location_edit.text())
        except (OSError, ValueError) as exc:
            self.status_label.setText(str(exc))
            return
        selected_license = self.license_edit.text().strip()
        license_path = Path(selected_license).expanduser() if selected_license else None
        if license_path and (license_path.suffix.casefold() != ".lic" or not license_path.is_file()):
            self.status_label.setText("Choose an existing license file with the .lic extension, or leave the field blank.")
            return
        self._worker = _SetupWorker(
            self.source, root, self.public_key_hex, self.feed_edit.text().strip(),
            self.desktop_checkbox.isChecked(), license_path=license_path,
            repair=repair, parent=self,
        )
        self._worker.progress.connect(self.status_label.setText)
        self._worker.result.connect(self._record_result)
        self._worker.error.connect(self._record_error)
        self._worker.finished.connect(self._job_finished)
        for widget in (self.location_edit, self.location_button, self.feed_edit,
                       self.feed_button, self.license_edit, self.license_button,
                       self.desktop_checkbox, self.install_button,
                       self.repair_button, self.cancel_button):
            widget.setEnabled(False)
        self.status_label.setText("Preparing installation…" if not repair else "Repairing shortcuts…")
        self.progress.show()
        self._worker.start()

    def _record_result(self, result: dict):
        self._job_result = result

    def _record_error(self, message: str):
        self._job_error = message

    def _job_finished(self):
        worker, self._worker = self._worker, None
        worker.deleteLater()
        for widget in (self.location_edit, self.location_button, self.feed_edit,
                       self.feed_button, self.license_edit, self.license_button,
                       self.desktop_checkbox, self.install_button,
                       self.repair_button, self.cancel_button):
            widget.setEnabled(True)
        self._refresh()
        if self._job_error:
            self.status_label.setText(f"Setup could not finish. {self._job_error}")
            return
        if not self._job_result:
            self.status_label.setText("Setup did not return a result. Choose an empty folder and try again.")
            return
        self._completed = True
        self._state, self._current = "installed", self._job_result["current"]
        self.install_button.setText("Open Nexus")
        self.install_button.setEnabled(True)
        self.repair_button.show()
        self.cancel_button.setText("Close")
        self.feed_edit.setEnabled(False)
        self.feed_button.setEnabled(False)
        self.license_edit.setEnabled(False)
        self.license_button.setEnabled(False)
        if self._job_result["warning"]:
            self.status_label.setText(
                f"Nexus {self._current.get('version', '')} is installed with setup warnings. "
                f"{self._job_result['warning']}\nOpen Nexus to continue. Use Repair shortcuts if shortcut creation failed."
            )
        else:
            license_note = (
                f" License installed at {self._job_result['license_destination']}."
                if self._job_result["license_destination"] else ""
            )
            self.status_label.setText(
                f"Nexus {self._current.get('version', '')} is ready. "
                f"Use the Nexus shortcut whenever you want to start the app.{license_note}"
            )

    def _open_nexus(self):
        try:
            root = _location(self.location_edit.text())
            launcher = root / "bootstrap" / "NexusLauncher.exe"
            if not launcher.is_file():
                raise install.InstallError("The installed Nexus launcher is missing.")
            environment = os.environ.copy()
            # A frozen Qt process must not pass its plugin paths to another
            # bundle. The launcher chooses the application's own runtime.
            for key in ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QT_QPA_FONTDIR", "PYTHONHOME", "PYTHONPATH"):
                environment.pop(key, None)
            environment["NEXUS_INSTALL_ROOT"] = str(root)
            subprocess.Popen([str(launcher)], cwd=str(launcher.parent), env=environment)
        except Exception as exc:
            self.status_label.setText(f"Nexus could not be opened. {exc}")
            return
        self.accept()

    def _close_requested(self):
        if not self.busy:
            self.accept() if self._completed or self._state == "installed" else self.reject()

    def reject(self):
        # Escape, the close button, and Cancel all keep the worker alive until
        # copying and shortcut creation have returned. No files are forcefully
        # removed and no background thread can outlive its owning dialog.
        if not self.busy:
            super().reject()

    def accept(self):
        if not self.busy:
            super().accept()

    def closeEvent(self, event):
        if self.busy:
            event.ignore()
        else:
            super().closeEvent(event)


def run_setup(source, public_key_hex: str, *, root=None, feed="") -> int:
    app = QApplication.instance() or QApplication(["Nexus Setup"])
    dialog = SetupDialog(source, public_key_hex, root=root, feed=feed)
    return 0 if dialog.exec_() == QDialog.Accepted else 1
