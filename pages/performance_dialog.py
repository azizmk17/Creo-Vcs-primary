"""Simple opt-in control for anonymous performance measurements."""
from datetime import datetime
import os
import tempfile
import time

from PyQt5.QtCore import QObject, QStandardPaths, QTimer
from PyQt5.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QMessageBox, QPushButton, QVBoxLayout,
)

from core.performance_recorder import recorder, write_report


def _windows_working_set_reader():
    if os.name != "nt":
        return lambda: None
    try:
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD,
        ]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        handle = kernel.GetCurrentProcess()

        def read():
            try:
                counters = Counters()
                counters.cb = ctypes.sizeof(Counters)
                if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                    return int(counters.WorkingSetSize)
            except (OSError, ValueError, TypeError):
                pass
            return None
        return read
    except (AttributeError, OSError, TypeError):
        return lambda: None


class _UiDelayMonitor(QObject):
    """Measure event-loop timer lateness; this does not diagnose its cause."""
    def __init__(self, parent):
        super().__init__(parent)
        self.interval_seconds = 0.1
        self.last_tick = None
        self.last_resource_wall = None
        self.last_resource_cpu = None
        self._read_working_set = _windows_working_set_reader()
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._tick)

    def start(self):
        self.last_tick = time.perf_counter()
        self.last_resource_wall = self.last_tick
        self.last_resource_cpu = time.process_time()
        self.timer.start()

    def stop(self):
        self.timer.stop()
        self.last_tick = None
        self.last_resource_wall = None
        self.last_resource_cpu = None

    def _tick(self):
        now = time.perf_counter()
        previous = self.last_tick
        self.last_tick = now
        token = recorder.token()
        if previous is None or token is None:
            return
        delay = max(0.0, now - previous - self.interval_seconds)
        recorder.observe("ui.event_loop_delay", delay, token, event=delay >= 0.2)
        if self.last_resource_wall is not None and now - self.last_resource_wall >= 2.0:
            cpu_now = time.process_time()
            elapsed = now - self.last_resource_wall
            cpu_percent = max(0.0, (cpu_now - self.last_resource_cpu) * 100.0 / elapsed)
            recorder.record_resources(token, cpu_percent, self._read_working_set())
            self.last_resource_wall = now
            self.last_resource_cpu = cpu_now


def _monitor():
    app = QApplication.instance()
    if app is None:
        return None
    monitor = getattr(app, "_nexus_performance_monitor", None)
    if monitor is None:
        monitor = _UiDelayMonitor(app)
        app._nexus_performance_monitor = monitor
    return monitor


def _save_active_report(parent):
    if not recorder.active:
        return False
    desktop = QStandardPaths.writableLocation(QStandardPaths.DesktopLocation)
    folder = desktop if desktop and os.path.isdir(desktop) else os.path.expanduser("~")
    default = os.path.join(folder, "Nexus-performance-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".zip")
    filename, _ = QFileDialog.getSaveFileName(
        parent, "Save performance report", default, "ZIP archive (*.zip)"
    )
    if not filename:
        return False
    if not filename.lower().endswith(".zip"):
        filename += ".zip"
        if os.path.exists(filename) and QMessageBox.question(
            parent, "Replace report?", "A report with that name already exists. Replace it?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return False
    temporary = None
    try:
        from config import APP_VERSION
        from core.startup_profile import milestones

        report = recorder.snapshot(version=APP_VERSION, startup=milestones())
        destination = os.path.abspath(filename)
        fd, temporary = tempfile.mkstemp(
            prefix=".nexus-performance-", suffix=".tmp", dir=os.path.dirname(destination)
        )
        os.close(fd)
        write_report(temporary, report)
        os.replace(temporary, destination)
        temporary = None
    except Exception as exc:
        QMessageBox.warning(
            parent, "Report was not saved",
            f"The recording is still running. Choose a writable location and try again.\n\n{exc}",
        )
        return False
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass
    recorder.stop()
    monitor = _monitor()
    if monitor is not None:
        monitor.stop()
    QMessageBox.information(
        parent, "Performance report saved",
        "Your report is ready. Send the ZIP file to the person analyzing the results.\n\n"
        + destination,
    )
    return True


def confirm_recording_before_exit(parent):
    """Give the user a chance to save rather than silently lose a recording."""
    if not recorder.active:
        return True
    box = QMessageBox(parent)
    box.setWindowTitle("Performance recording")
    box.setText("A performance recording is running. Save it before closing Nexus?")
    save = box.addButton("Save report", QMessageBox.AcceptRole)
    discard = box.addButton("Discard", QMessageBox.DestructiveRole)
    box.addButton("Cancel", QMessageBox.RejectRole)
    box.exec_()
    if box.clickedButton() is save:
        return _save_active_report(parent)
    if box.clickedButton() is discard:
        recorder.stop()
        monitor = _monitor()
        if monitor is not None:
            monitor.stop()
        return True
    return False


class PerformanceDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Performance recording")
        self.setMinimumWidth(510)
        layout = QVBoxLayout(self)
        heading = QLabel("Record a real Nexus session")
        heading.setStyleSheet("font-size:15px;font-weight:700;")
        layout.addWidget(heading)
        instructions = QLabel(
            "Start recording before signing in when possible. Use Nexus normally, "
            "then open Performance again and select Stop and save report. "
            "Choose a local folder, such as your Desktop."
        )
        instructions.setWordWrap(True)
        layout.addWidget(instructions)
        privacy = QLabel(
            "The report contains anonymous timings and counts. It does not include "
            "your database, CAD files, SQL text, item names, user names, or file paths."
        )
        privacy.setWordWrap(True)
        layout.addWidget(privacy)
        form = QFormLayout()
        self.network = QComboBox()
        for label, value in (("Company network / Wi-Fi", "company_network"),
                             ("VPN", "vpn"), ("Other", "other"), ("Unknown", "unknown")):
            self.network.addItem(label, value)
        self.run_type = QComboBox()
        for label, value in (("Normal work", "normal_work"),
                             ("First session after starting laptop", "first_session"),
                             ("Repeat session", "repeat_session"), ("Unknown", "unknown")):
            self.run_type.addItem(label, value)
        form.addRow("Connection", self.network)
        form.addRow("Session", self.run_type)
        layout.addLayout(form)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.start_button = QPushButton("Start recording")
        self.start_button.clicked.connect(self._start)
        self.save_button = QPushButton("Stop and save report…")
        self.save_button.clicked.connect(self._save)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.save_button)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self._refresh()

    def _refresh(self):
        active = recorder.active
        self.start_button.setEnabled(not active)
        self.save_button.setEnabled(active)
        self.network.setEnabled(not active)
        self.run_type.setEnabled(not active)
        self.status.setText(
            "Recording is running. You can close this window and continue working."
            if active else "Recording is off."
        )

    def _start(self):
        if recorder.start(self.network.currentData(), self.run_type.currentData()):
            monitor = _monitor()
            if monitor is not None:
                monitor.start()
        self._refresh()

    def _save(self):
        if _save_active_report(self):
            self.accept()
        else:
            self._refresh()


def open_performance_dialog(parent=None):
    PerformanceDialog(parent).exec_()
