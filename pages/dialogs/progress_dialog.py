# pages/dialogs/progress_dialog.py
from PyQt5.QtWidgets import (
    QDialog, QFrame, QVBoxLayout, QLabel, QProgressBar, QPushButton, QHBoxLayout,
    QWidget
)
from PyQt5.QtCore import Qt, QTimer

from pages.dialogs.professional_style import (
    apply_professional_dialog_style,
    make_dialog_header,
)


class ProgressDialog(QDialog):
    def __init__(self, title="Processing...", message="Please wait...", cancel_callback=None):
        super().__init__()
        self.setWindowTitle(title)
        self.setModal(True)
        self.setFixedSize(460, 190)
        apply_professional_dialog_style(self)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        header = make_dialog_header(
            title,
            "Controlled operation in progress.",
            kicker="NEXUS PDM",
        )
        self.lbl_title = header.findChild(QLabel, "professionalDialogTitle")
        root_layout.addWidget(header)

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(8)
        root_layout.addWidget(body, 1)

        # Message
        self.lbl_message = QLabel(message)
        self.lbl_message.setObjectName("professionalMuted")
        self.lbl_message.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        layout.addWidget(self.lbl_message)

        # Progress bar
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(True)
        layout.addWidget(self.bar)

        # Cancel button
        footer = QFrame()
        footer.setObjectName("professionalFooter")
        btn_layout = QHBoxLayout(footer)
        btn_layout.setContentsMargins(8, 5, 8, 5)
        btn_layout.addStretch()
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setEnabled(bool(cancel_callback))
        if cancel_callback:
            self.btn_cancel.clicked.connect(cancel_callback)
        btn_layout.addWidget(self.btn_cancel)
        layout.addWidget(footer)

        # Loading animation (three dots)
        self._dot_count = 0
        self._custom_message = message or ""
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._animate_text)
        self._timer.start(400)

    def _animate_text(self):
        self._dot_count = (self._dot_count + 1) % 4
        dots = "." * self._dot_count
        base = (self._custom_message or "Working").strip() or "Working"
        self.lbl_message.setText(f"{base}{dots}")

    def update_progress(self, value, message=""):
        try:
            self.bar.setValue(int(value))
        except Exception:
            pass
        if message is not None:
            msg = str(message)
            if msg:
                self._custom_message = msg
                self.lbl_message.setText(msg)

    def stop_animation(self):
        self._timer.stop()
