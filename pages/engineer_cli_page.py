from PyQt5.QtCore import QObject, QThread, Qt, pyqtSignal
from PyQt5.QtGui import QFont, QTextCursor
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.services.engineer_cli_service import EngineerCliService


class _CliWorker(QObject):
    finished = pyqtSignal(str, object)

    def __init__(self, service: EngineerCliService, command: str):
        super().__init__()
        self.service = service
        self.command = command

    def run(self):
        try:
            self.finished.emit(self.service.execute(self.command), None)
        except Exception as exc:
            self.finished.emit("", exc)


class EngineerCliPage(QWidget):
    """Professional controlled Nexus command panel."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.service = EngineerCliService()
        self._history: list[str] = []
        self._history_index = 0
        self._thread = None
        self._worker = None
        self._build_ui()

    def _build_ui(self):
        self.setObjectName("engineerCliPage")
        self.setStyleSheet(self._stylesheet())
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 10)
        root.setSpacing(7)

        header = QFrame()
        header.setObjectName("cliHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(10, 6, 8, 6)
        header_layout.setSpacing(8)

        title_block = QVBoxLayout()
        title_block.setSpacing(1)
        title = QLabel("ENGINEER CLI")
        title.setObjectName("cliTitle")
        subtitle = QLabel("Natural-language PDM agent for analysis, planning and controlled action")
        subtitle.setObjectName("cliSubtitle")
        title_block.addWidget(title)
        title_block.addWidget(subtitle)
        header_layout.addLayout(title_block)
        header_layout.addStretch()

        self.status_label = QLabel("Ready")
        self.status_label.setObjectName("cliStatus")
        header_layout.addWidget(self.status_label)

        help_btn = QPushButton("Help")
        help_btn.setObjectName("cliSecondaryButton")
        help_btn.clicked.connect(lambda: self._run_command("help"))
        header_layout.addWidget(help_btn)
        clear_btn = QPushButton("Clear")
        clear_btn.setObjectName("cliSecondaryButton")
        clear_btn.clicked.connect(self._clear)
        header_layout.addWidget(clear_btn)
        root.addWidget(header)

        shortcuts = QFrame()
        shortcuts.setObjectName("cliShortcutStrip")
        shortcuts_layout = QHBoxLayout(shortcuts)
        shortcuts_layout.setContentsMargins(8, 5, 8, 5)
        shortcuts_layout.setSpacing(5)
        for label, command in (
            ("Assist Release", "assist release"),
            ("Ask Blocks", 'ask "what blocks release?"'),
            ("Diag Summary", "diag summary"),
            ("Missing Docs", "diag missing-docs"),
            ("Associations", "diag associations"),
            ("Checkouts", "diag checkouts"),
            ("Auto-Assoc Preview", "auto-associate"),
        ):
            btn = QPushButton(label)
            btn.setObjectName("cliTinyButton")
            btn.clicked.connect(lambda _checked=False, c=command: self._run_command(c))
            shortcuts_layout.addWidget(btn)
        shortcuts_layout.addStretch()
        root.addWidget(shortcuts)

        self.console = QTextEdit()
        self.console.setObjectName("cliConsole")
        self.console.setReadOnly(True)
        self.console.setAcceptRichText(False)
        self.console.setFont(QFont("Consolas", 9))
        root.addWidget(self.console, 1)

        input_frame = QFrame()
        input_frame.setObjectName("cliInputFrame")
        input_layout = QHBoxLayout(input_frame)
        input_layout.setContentsMargins(8, 6, 8, 6)
        input_layout.setSpacing(6)
        prompt = QLabel("nexus>")
        prompt.setObjectName("cliPrompt")
        input_layout.addWidget(prompt)
        self.command_input = QLineEdit()
        self.command_input.setObjectName("cliCommandInput")
        self.command_input.setPlaceholderText(
            'Talk to Nexus, e.g. act "create an item called variant-1 with all project parts as children"'
        )
        self.command_input.returnPressed.connect(self._run_current)
        input_layout.addWidget(self.command_input, 1)
        run_btn = QPushButton("Run")
        run_btn.setObjectName("cliPrimaryButton")
        run_btn.clicked.connect(self._run_current)
        input_layout.addWidget(run_btn)
        root.addWidget(input_frame)

        self._append("Nexus Agent ready. Type naturally, or type 'help' for exact commands.")

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Up and self._history:
            self._history_index = max(0, self._history_index - 1)
            self.command_input.setText(self._history[self._history_index])
            return
        if event.key() == Qt.Key_Down and self._history:
            self._history_index = min(len(self._history), self._history_index + 1)
            self.command_input.setText(
                "" if self._history_index >= len(self._history)
                else self._history[self._history_index]
            )
            return
        super().keyPressEvent(event)

    def refresh_context(self):
        try:
            if not self.service.is_available_for_current_user():
                self.status_label.setText("Disabled")
                self.command_input.setEnabled(False)
                self._append("Engineer CLI is disabled for this user.")
                return
            self.status_label.setText("Ready")
            self.command_input.setEnabled(True)
        except Exception:
            self.status_label.setText("Unavailable")

    def _run_current(self):
        self._run_command(self.command_input.text())
        self.command_input.clear()

    def _run_command(self, command: str):
        command = str(command or "").strip()
        if not command:
            return
        if self._thread is not None:
            QMessageBox.information(self, "Engineer CLI", "A command is already running.")
            return
        self._history.append(command)
        self._history_index = len(self._history)
        self._append(f"\n> {command}", command=True)
        self.status_label.setText("Running")
        self.command_input.setEnabled(False)

        self._thread = QThread(self)
        self._worker = _CliWorker(self.service, command)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_finished)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _on_finished(self, output: str, error):
        if error is not None:
            self._append(f"ERROR: {error}", error=True)
        elif str(output or "").strip():
            self._append(str(output))
        self.status_label.setText("Ready")
        self.command_input.setEnabled(True)
        self.command_input.setFocus()
        if self._worker is not None:
            self._worker.deleteLater()
        self._worker = None
        self._thread = None

    def _append(self, text: str, *, command: bool = False, error: bool = False):
        color = "#9bd4ff" if command else ("#ffb4a9" if error else "#d9e7f2")
        escaped = (
            str(text or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\n", "<br>")
        )
        self.console.append(f'<span style="color:{color}; white-space:pre;">{escaped}</span>')
        self.console.moveCursor(QTextCursor.End)

    def _clear(self):
        self.console.clear()
        self._append("Nexus Agent ready. Type naturally, or type 'help' for exact commands.")

    def _stylesheet(self):
        return """
        QWidget#engineerCliPage {
            background-color: #e8edf2;
            font: 8pt "Segoe UI";
        }
        QFrame#cliHeader {
            background-color: #d8dfe5;
            border: 1px solid #aab5bf;
        }
        QLabel#cliTitle {
            color: #20384c;
            font-weight: bold;
            letter-spacing: 0.8px;
        }
        QLabel#cliSubtitle {
            color: #607385;
        }
        QLabel#cliStatus {
            color: #1d516f;
            border-left: 1px solid #aeb8c0;
            padding-left: 10px;
            font-weight: bold;
        }
        QTextEdit#cliConsole {
            background-color: #101923;
            color: #d9e7f2;
            border: 1px solid #334557;
            selection-background-color: #176f9d;
        }
        QFrame#cliInputFrame {
            background-color: #f5f7f9;
            border: 1px solid #aeb8c0;
        }
        QLabel#cliPrompt {
            color: #176f9d;
            font: bold 9pt "Consolas";
        }
        QLineEdit#cliCommandInput {
            background-color: #ffffff;
            border: 1px solid #aeb8c0;
            padding: 5px;
            font: 9pt "Consolas";
        }
        QPushButton#cliPrimaryButton,
        QPushButton#cliSecondaryButton,
        QPushButton#cliTinyButton {
            border: 1px solid #8da0af;
            padding: 5px 13px;
            background-color: #edf2f6;
        }
        QFrame#cliShortcutStrip {
            background-color: #f5f7f9;
            border: 1px solid #b7c2cc;
        }
        QPushButton#cliTinyButton {
            padding: 3px 9px;
            font-size: 7.5pt;
        }
        QPushButton#cliPrimaryButton {
            color: #ffffff;
            background-color: #176f9d;
            border-color: #176f9d;
            font-weight: bold;
        }
        QPushButton:hover {
            background-color: #dfe8ef;
        }
        """
