"""Shared presentation helpers for dense Nexus PDM dialogs.

This module deliberately contains no workflow or persistence behavior.  It keeps
standalone dialogs visually consistent with the Item Master and Item Structure
surfaces without requiring each dialog to maintain a large private stylesheet.
"""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QFrame, QLabel, QVBoxLayout


PROFESSIONAL_DIALOG_QSS = """
QDialog {
    background: #e9edf1;
    color: #1c2733;
    font-family: "Segoe UI";
    font-size: 9pt;
}
QWidget {
    color: #1c2733;
}
QFrame#professionalDialogHeader {
    background: #263746;
    border: 0;
}
QLabel#professionalDialogKicker {
    color: #9fc7e8;
    background: transparent;
    font-size: 8pt;
    font-weight: 700;
}
QLabel#professionalDialogTitle {
    color: #ffffff;
    background: transparent;
    font-size: 13pt;
    font-weight: 600;
}
QLabel#professionalDialogSubtitle {
    color: #d4dde5;
    background: transparent;
    font-size: 8.5pt;
}
QFrame#professionalSection,
QFrame#professionalCommandBar,
QFrame#professionalFooter {
    background: #f4f6f8;
    border: 1px solid #aeb8c2;
    border-radius: 0;
}
QLabel#professionalSectionTitle {
    color: #203243;
    background: #dfe5ea;
    border: 1px solid #aeb8c2;
    padding: 4px 7px;
    font-size: 8.5pt;
    font-weight: 700;
}
QLabel#professionalMuted {
    color: #586673;
    background: transparent;
    font-size: 8.5pt;
}
QLabel[severity="info"] {
    color: #164f86;
    background: #e9f2fb;
    border: 1px solid #9dbbd8;
    padding: 7px 9px;
}
QLabel[severity="success"] {
    color: #155b2a;
    background: #edf6ef;
    border: 1px solid #9ec6a8;
    padding: 7px 9px;
}
QLabel[severity="warning"] {
    color: #704700;
    background: #fff5d7;
    border: 1px solid #d7b24b;
    padding: 7px 9px;
}
QLabel[severity="neutral"] {
    color: #4f5c68;
    background: #f0f2f4;
    border: 1px solid #c4ccd3;
    padding: 7px 9px;
}
QLineEdit,
QComboBox,
QSpinBox,
QDoubleSpinBox,
QPlainTextEdit,
QTextEdit {
    color: #17222d;
    background: #ffffff;
    border: 1px solid #9faab5;
    border-radius: 0;
    min-height: 23px;
    padding: 1px 5px;
    selection-color: #ffffff;
    selection-background-color: #2d6f9f;
}
QLineEdit:focus,
QComboBox:focus,
QSpinBox:focus,
QDoubleSpinBox:focus,
QPlainTextEdit:focus,
QTextEdit:focus {
    border: 1px solid #246a9b;
}
QLineEdit:disabled,
QComboBox:disabled,
QPlainTextEdit:disabled,
QTextEdit:disabled {
    color: #76818b;
    background: #e5e8eb;
}
QPushButton,
QToolButton {
    min-height: 25px;
    min-width: 72px;
    color: #1c2733;
    background: #e4e8eb;
    border: 1px solid #8f9aa4;
    border-radius: 0;
    padding: 2px 10px;
}
QPushButton:hover,
QToolButton:hover {
    background: #d6dde3;
}
QPushButton:pressed,
QToolButton:pressed {
    background: #cbd3da;
}
QPushButton:disabled,
QToolButton:disabled {
    color: #8b949c;
    background: #eceff1;
    border-color: #c3c9ce;
}
QPushButton#primary,
QToolButton#primary {
    color: #ffffff;
    background: #246a9b;
    border-color: #1d587f;
    font-weight: 600;
}
QPushButton#primary:hover,
QToolButton#primary:hover {
    background: #1f5d88;
}
QPushButton#danger,
QToolButton#danger {
    color: #8f2f2a;
    background: #f5eceb;
    border-color: #b98e8a;
}
QPushButton#danger:hover,
QToolButton#danger:hover {
    background: #ecd9d7;
}
QTableWidget,
QTreeWidget,
QListWidget {
    color: #17222d;
    background: #ffffff;
    alternate-background-color: #f3f5f7;
    border: 1px solid #aeb8c2;
    border-radius: 0;
    gridline-color: #d0d6dc;
    selection-color: #17222d;
    selection-background-color: #cbddeb;
    outline: 0;
}
QTableWidget::item,
QTreeWidget::item,
QListWidget::item {
    min-height: 22px;
}
QTableWidget::item:selected,
QTreeWidget::item:selected,
QListWidget::item:selected {
    border: 0;
}
QHeaderView::section {
    color: #263746;
    background: #dfe5ea;
    border: 0;
    border-right: 1px solid #aeb8c2;
    border-bottom: 1px solid #aeb8c2;
    padding: 4px 6px;
    font-size: 8.5pt;
    font-weight: 700;
}
QSplitter::handle {
    background: #c8d0d7;
}
QSplitter::handle:horizontal {
    width: 3px;
}
QSplitter::handle:vertical {
    height: 3px;
}
QProgressBar {
    color: #263746;
    background: #ffffff;
    border: 1px solid #9faab5;
    border-radius: 0;
    min-height: 20px;
    text-align: center;
}
QProgressBar::chunk {
    background: #4c88b2;
}
QCheckBox {
    spacing: 5px;
}
QToolTip {
    color: #17222d;
    background: #fffbe8;
    border: 1px solid #8f9aa4;
    padding: 3px;
}
"""


def apply_professional_dialog_style(dialog) -> None:
    """Apply the shared enterprise dialog stylesheet."""

    dialog.setStyleSheet(PROFESSIONAL_DIALOG_QSS)


def make_dialog_header(
    title: str,
    subtitle: str = "",
    *,
    kicker: str = "NEXUS PDM",
) -> QFrame:
    """Create the compact title band used by standalone PDM dialogs."""

    frame = QFrame()
    frame.setObjectName("professionalDialogHeader")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(14, 8, 14, 9)
    layout.setSpacing(1)

    kicker_label = QLabel(str(kicker or "").upper())
    kicker_label.setObjectName("professionalDialogKicker")
    layout.addWidget(kicker_label)

    title_label = QLabel(str(title or ""))
    title_label.setObjectName("professionalDialogTitle")
    title_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    layout.addWidget(title_label)

    if subtitle:
        subtitle_label = QLabel(str(subtitle))
        subtitle_label.setObjectName("professionalDialogSubtitle")
        subtitle_label.setWordWrap(True)
        layout.addWidget(subtitle_label)
    return frame


def make_section_title(title: str) -> QLabel:
    """Create a flush, table-like section caption."""

    label = QLabel(str(title or ""))
    label.setObjectName("professionalSectionTitle")
    return label


def set_banner_style(label: QLabel, severity: str) -> None:
    """Apply a restrained semantic state to an informational label."""

    label.setProperty("severity", str(severity or "neutral").lower())
    label.setWordWrap(True)

