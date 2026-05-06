# pages/dialogs/progress_dialog.py
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QProgressBar, QPushButton, QHBoxLayout, QGraphicsDropShadowEffect
)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor, QFont

class ProgressDialog(QDialog):
    def __init__(self, title="Processing...", message="Please wait...", cancel_callback=None):
        super().__init__()
        self.setWindowTitle(title)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setModal(True)
        self.setFixedSize(400, 180)

        # ---------------- Layout ----------------
        layout = QVBoxLayout(self)
        layout.setContentsMargins(25, 25, 25, 25)
        layout.setSpacing(15)

        # Shadow effect
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(25)
        shadow.setColor(QColor(0, 0, 0, 150))
        shadow.setOffset(0, 0)
        self.setGraphicsEffect(shadow)

        # Title
        self.lbl_title = QLabel(f"<h3 style='color:#333;'>{title}</h3>")
        self.lbl_title.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.lbl_title)

        # Message
        self.lbl_message = QLabel(message)
        self.lbl_message.setAlignment(Qt.AlignCenter)
        self.lbl_message.setStyleSheet("color: #555; font-size: 13px;")
        layout.addWidget(self.lbl_message)

        # Progress bar
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(True)
        self.bar.setStyleSheet("""
            QProgressBar {
                background-color: #e5e5e5;
                border: 1px solid #ccc;
                border-radius: 10px;
                text-align: center;
                height: 20px;
                color: #333;
            }
            QProgressBar::chunk {
                background-color: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 #0078d7, stop:1 #00bfff
                );
                border-radius: 10px;
            }
        """)
        layout.addWidget(self.bar)

        # Cancel button
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setFixedWidth(100)
        self.btn_cancel.setObjectName("danger")
        if cancel_callback:
            self.btn_cancel.clicked.connect(cancel_callback)
        btn_layout.addWidget(self.btn_cancel)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        # Loading animation (three dots)
        self._dot_count = 0
        self._custom_message = message or ""
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._animate_text)
        self._timer.start(400)

        self.setStyleSheet("""
            QDialog {
                background-color: #ffffff;
                border-radius: 15px;
            }
        """)

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
