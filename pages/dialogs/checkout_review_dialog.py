import html

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from pages.dialogs.professional_style import (
    apply_professional_dialog_style,
    make_dialog_header,
    set_banner_style,
)
from utils import safe_startfile


class CheckoutReviewDialog(QDialog):
    ACTION_CANCEL = "cancel"
    ACTION_CHECKIN = "checkin"
    ACTION_COMMIT = "commit"

    def __init__(self, analysis: dict, parent=None):
        super().__init__(parent)
        self.analysis = dict(analysis or {})
        self.action = self.ACTION_CANCEL
        item_number = str(self.analysis.get("part_number") or "").strip()
        item_name = str(self.analysis.get("name") or "").strip()
        self.setWindowTitle(
            f"Check In - {item_number or item_name or 'Item'} "
            f"{self.analysis.get('current_version') or ''}"
        )
        self.setModal(True)
        self.setMinimumWidth(560)
        self.setMaximumWidth(700)
        apply_professional_dialog_style(self)
        self._build_ui()

    @property
    def mode(self) -> str:
        if self.analysis.get("requires_commit"):
            return "commit"
        if self.analysis.get("structure_requires_cad"):
            return "blocked_structure"
        if self.analysis.get("has_non_cad_changes"):
            return "checkin"
        return "no_changes"

    def _build_ui(self) -> None:
        item_number = str(self.analysis.get("part_number") or "").strip()
        aes_number = str(self.analysis.get("aes_number") or "").strip()
        item_name = str(self.analysis.get("name") or "").strip()
        version = str(self.analysis.get("current_version") or "").strip()
        identity = " — ".join(value for value in (item_number, item_name) if value)
        context = "  |  ".join(
            value for value in (
                f"Iteration {version}" if version else "",
                f"AES {aes_number}" if aes_number else "",
            ) if value
        )

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(
            make_dialog_header(
                identity or "Item Check In",
                context or "Review controlled changes before check-in.",
                kicker="WORKSPACE CONTROL",
            )
        )

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        root_layout.addWidget(body)
        layout.addWidget(self._section("Detected changes", self._change_rows()))

        self.comment_edit = QTextEdit()
        self.comment_edit.setPlaceholderText("Describe the completed BOM-object changes...")
        self.comment_edit.setFixedHeight(64)

        if self.mode == "blocked_structure":
            warning = QLabel(
                "<b>Native assembly file has not changed.</b><br><br>"
                "This assembly uses CAD-controlled structure. Update the native assembly "
                "before check-in."
            )
            set_banner_style(warning, "warning")
            layout.addWidget(warning)
        elif self.mode == "commit":
            info = QLabel(
                "Native Creo content changed. Nexus will continue in the Commit page so the "
                "CAD files can follow the existing review and push-to-master workflow."
            )
            set_banner_style(info, "info")
            layout.addWidget(info)
        elif self.mode == "checkin":
            native_name = self.analysis.get("native_cad", {}).get("baseline_filename") or "None"
            drawing_name = self.analysis.get("drawing", {}).get("baseline_filename") or "None"
            result = QLabel(
                f"<b>Result</b><br>New iteration: "
                f"<b>{html.escape(self.analysis.get('next_version') or '')}</b><br>"
                f"Native CAD inherited: {html.escape(native_name)}<br>"
                f"Drawing inherited: {html.escape(drawing_name)}"
            )
            set_banner_style(result, "success")
            layout.addWidget(result)
        else:
            info = QLabel(
                "No controlled changes were detected. You can still check in to close the Item reservation, "
                "or close this dialog and use Undo Checkout if the work should be discarded."
            )
            set_banner_style(info, "neutral")
            layout.addWidget(info)
            layout.addWidget(QLabel("Comment:"))
            layout.addWidget(self.comment_edit)
        if self.mode == "checkin":
            layout.addWidget(QLabel("Comment:"))
            layout.addWidget(self.comment_edit)

        action_bar = QFrame()
        action_bar.setObjectName("professionalFooter")
        actions = QHBoxLayout(action_bar)
        actions.setContentsMargins(8, 6, 8, 6)
        actions.setSpacing(6)
        if self.mode in {"blocked_structure", "commit"}:
            folder_button = QPushButton("Open Working Folder")
            folder_button.clicked.connect(self._open_working_folder)
            actions.addWidget(folder_button)
            creo_button = QPushButton("Open in Creo")
            creo_button.setEnabled(bool(self._creo_path()))
            creo_button.clicked.connect(self._open_in_creo)
            actions.addWidget(creo_button)
        actions.addStretch(1)
        cancel_button = QPushButton("Close" if self.mode == "no_changes" else "Cancel")
        cancel_button.clicked.connect(self.reject)
        actions.addWidget(cancel_button)
        if self.mode == "commit":
            commit_button = QPushButton("Continue to Commit")
            commit_button.setObjectName("primary")
            commit_button.clicked.connect(self._choose_commit)
            actions.addWidget(commit_button)
        elif self.mode in {"checkin", "no_changes"}:
            checkin_button = QPushButton("Check In")
            checkin_button.setObjectName("primary")
            checkin_button.clicked.connect(self._choose_checkin)
            actions.addWidget(checkin_button)
        layout.addWidget(action_bar)

    def _section(self, title: str, rows: list[tuple[str, bool, list[str]]]) -> QFrame:
        frame = QFrame()
        frame.setObjectName("professionalSection")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(9, 7, 9, 7)
        layout.setSpacing(4)
        heading = QLabel(f"<b>{html.escape(title)}</b>")
        heading.setObjectName("professionalSectionTitle")
        layout.addWidget(heading)
        for label, changed, details in rows:
            status = "CHANGED" if changed else "UNCHANGED"
            color = "#147d36" if changed else "#6b7280"
            line = QLabel(
                f"<span style='color:{color}; font-weight:600'>{status}</span>&nbsp;&nbsp;"
                f"{html.escape(label)}"
            )
            line.setStyleSheet("border:0; background:transparent; padding:1px 3px;")
            layout.addWidget(line)
            for detail in details:
                detail_label = QLabel(f"&nbsp;&nbsp;&nbsp;&nbsp;{html.escape(str(detail))}")
                detail_label.setWordWrap(True)
                detail_label.setStyleSheet("color:#4b5563; border:0; background:transparent;")
                layout.addWidget(detail_label)
        return frame

    def _change_rows(self) -> list[tuple[str, bool, list[str]]]:
        metadata = list(self.analysis.get("metadata_changes") or [])
        structure = list(self.analysis.get("structure_changes") or [])
        documents = list(self.analysis.get("document_changes") or [])
        native = dict(self.analysis.get("native_cad") or {})
        drawing = dict(self.analysis.get("drawing") or {})
        return [
            ("Revision attributes", bool(metadata), [item.get("label") or "Attribute" for item in metadata]),
            ("Native CAD", bool(native.get("modified")), [native.get("working_filename")] if native.get("modified") else []),
            ("Drawing", bool(drawing.get("modified")), [drawing.get("working_filename")] if drawing.get("modified") else []),
            ("Structure", bool(structure), [item.get("text") or "Structure changed" for item in structure]),
            ("Associated documents", bool(documents), [item.get("text") or "Document changed" for item in documents]),
        ]

    def _creo_path(self) -> str:
        native = self.analysis.get("native_cad") or {}
        path = str(native.get("working_path") or "").strip()
        return path if path and native.get("working_exists") else ""

    def _open_working_folder(self) -> None:
        path = str(self.analysis.get("working_dir") or "").strip()
        if not path:
            QMessageBox.warning(self, "Working Folder", "The project working folder is not configured.")
            return
        try:
            safe_startfile(path)
        except Exception as exc:
            QMessageBox.warning(self, "Working Folder", str(exc))

    def _open_in_creo(self) -> None:
        path = self._creo_path()
        if not path:
            return
        try:
            safe_startfile(path)
        except Exception as exc:
            QMessageBox.warning(self, "Open in Creo", str(exc))

    def _choose_commit(self) -> None:
        self.action = self.ACTION_COMMIT
        self.accept()

    def _choose_checkin(self) -> None:
        if not self.comment_edit.toPlainText().strip():
            QMessageBox.warning(self, "Check In", "Enter a check-in comment.")
            self.comment_edit.setFocus()
            return
        self.action = self.ACTION_CHECKIN
        self.accept()

    def comment(self) -> str:
        return self.comment_edit.toPlainText().strip()
