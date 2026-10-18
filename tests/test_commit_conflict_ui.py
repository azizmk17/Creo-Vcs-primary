import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "windows" if os.name == "nt" else "offscreen")
try:
    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import QApplication, QWidget, QMessageBox, QLineEdit, QComboBox, QCheckBox, QListWidget
except ImportError:
    QT_AVAILABLE = False
else:
    from pages.commit_page import CommitPage, _ProcessingWorker
    from core.services.commit_service import PendingCommitConflict
    QT_AVAILABLE = True


@unittest.skipUnless(QT_AVAILABLE, "PyQt5 is required for dialog tests")
class CommitConflictUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.page = _CommitHarness()
        self.addCleanup(self.page.close)

    def choose(self, label, *, replacement_enabled=True):
        observed = {}

        def click():
            dialog = QApplication.activeModalWidget()
            observed["labels"] = {button.text().replace("&", "") for button in dialog.buttons()}
            replace = next(button for button in dialog.buttons() if button.text() == "Replace")
            observed["replacement_enabled"] = replace.isEnabled()
            selected = next(button for button in dialog.buttons() if button.text().replace("&", "") == label)
            selected.click()

        QTimer.singleShot(0, click)
        conflict = PendingCommitConflict("part.prt", "Already in master" if not replacement_enabled else "")
        result = self.page._ask_pending_conflict_action(conflict)
        self.assertEqual(observed["labels"], {"Replace", "Skip", "Cancel"})
        self.assertEqual(observed["replacement_enabled"], replacement_enabled)
        return result

    def test_dialog_has_working_replace_skip_and_cancel_buttons(self):
        for label, expected in (("Replace", "replace"), ("Skip", "skip"), ("Cancel", None)):
            with self.subTest(label=label):
                self.assertEqual(self.choose(label), expected)

    def test_replace_is_disabled_for_protected_master_work(self):
        self.assertEqual(self.choose("Skip", replacement_enabled=False), "skip")

    def test_replace_choice_retries_the_actual_check_in_with_backend_option(self):
        self.page._ask_pending_conflict_action = Mock(return_value="replace")
        self.page.commit_service.commit_file.side_effect = [
            PendingCommitConflict("part.prt"),
            {"staged_files": ["part.prt.2"], "skipped_files": [], "affected_part_ids": []},
        ]
        with patch("pages.commit_page.QMessageBox.information"):
            self.page.commit_changes()
        calls = self.page.commit_service.commit_file.call_args_list
        self.assertEqual([call.kwargs["duplicate_action"] for call in calls], ["error", "replace"])
        self.assertEqual(self.page.uncommitted_parts, [])

    def test_cancel_keeps_files_and_form_without_retry(self):
        self.page._ask_pending_conflict_action = Mock(return_value=None)
        self.page.commit_service.commit_file.side_effect = PendingCommitConflict("part.prt")
        self.page.commit_changes()
        self.assertEqual(self.page.commit_service.commit_file.call_count, 1)
        self.assertEqual(len(self.page.uncommitted_parts), 1)
        self.assertEqual(self.page.commit_title.text(), "Update")

    def test_skipping_every_file_keeps_staging_and_reports_no_check_in(self):
        self.page._ask_pending_conflict_action = Mock(return_value="skip")
        self.page.commit_service.commit_file.side_effect = [
            PendingCommitConflict("part.prt"),
            {"staged_files": [], "skipped_files": ["part.prt.2"]},
        ]
        with patch("pages.commit_page.QMessageBox.information") as popup:
            self.page.commit_changes()
        self.assertEqual(len(self.page.uncommitted_parts), 1)
        self.assertEqual(self.page.changes_list.count(), 1)
        self.assertEqual(self.page.commit_title.text(), "Update")
        self.assertEqual(popup.call_args.args[1], "Check In Skipped")
        self.assertIn("0 file(s) checked in", popup.call_args.args[2])

    def test_partial_skip_removes_only_successfully_committed_files(self):
        self.page.uncommitted_parts.append({"path": "other.prt.1", "filename": "other.prt.1"})
        self.page.changes_list.addItem("other.prt.1")
        self.page._finish_commit_staging({"staged_files": ["other.prt.1"], "skipped_files": ["part.prt.2"]})
        self.assertEqual([item["filename"] for item in self.page.uncommitted_parts], ["part.prt.2"])
        self.assertEqual(self.page.changes_list.count(), 1)

    def test_worker_preserves_conflict_exception_for_the_ui_callback(self):
        conflict = PendingCommitConflict("part.prt")
        worker = _ProcessingWorker(Mock(side_effect=conflict))
        received = []
        worker.failed.connect(received.append)
        worker.run()
        self.assertIs(received[0], conflict)

    def test_unvalidated_push_warning_cancels_without_publishing(self):
        observed = {}

        def cancel():
            warning = QApplication.activeModalWidget()
            observed["icon"] = warning.icon()
            observed["default"] = warning.standardButton(warning.defaultButton())
            observed["explanation"] = warning.informativeText()
            warning.button(QMessageBox.Cancel).click()

        QTimer.singleShot(0, cancel)
        self.page.push_to_master({"commit_id": "draft", "project_id": 7, "status": "Pending"})
        self.assertEqual(observed["icon"], QMessageBox.Warning)
        self.assertEqual(observed["default"], QMessageBox.Cancel)
        self.assertIn("Under development", observed["explanation"])
        self.page.commit_service.push_unvalidated_to_master.assert_not_called()

    def test_confirmed_warning_pushes_and_refreshes_pending_queue(self):
        self.page.commit_service.push_unvalidated_to_master.return_value = {
            "affected_part_ids": [11], "affected_cad_document_ids": [21], "status": "Integrated",
        }

        def confirm():
            QApplication.activeModalWidget().button(QMessageBox.Yes).click()

        QTimer.singleShot(0, confirm)
        with patch("pages.commit_page.QMessageBox.information") as popup:
            self.page.push_to_master({"commit_id": "draft", "project_id": 7, "status": "Pending"})
        self.page.commit_service.push_unvalidated_to_master.assert_called_once_with("draft")
        self.page._set_pending_commit_groups.assert_called_once_with([])
        self.assertIn("not validated", popup.call_args.args[2])


if QT_AVAILABLE:
    class _CommitHarness(QWidget):
        _ask_pending_conflict_action = CommitPage._ask_pending_conflict_action
        _finish_commit_staging = CommitPage._finish_commit_staging
        commit_changes = CommitPage.commit_changes
        push_to_master = CommitPage.push_to_master

        def __init__(self):
            super().__init__()
            self._validate_workspace_staging = Mock()
            self.commit_title = QLineEdit("Update")
            self.commit_message = Mock()
            self.commit_message.content.return_value = "Changes"
            self.commit_message.has_content.return_value = True
            self.designed_by = QComboBox()
            self.designed_by.addItem("designer")
            self.step_compare_checkbox = QCheckBox()
            self.commit_jira_key = QLineEdit()
            self.commit_jira_url = QLineEdit()
            self.uncommitted_parts = [{"path": "part.prt.2", "filename": "part.prt.2"}]
            self.changes_list = QListWidget()
            self.changes_list.addItem("part.prt.2")
            self.resolved_issues_list = QListWidget()
            self._pending_engineering_attachments = {}
            self.commits_dir = "commits"
            self.session = SimpleNamespace(project_id=7, user_id=3)
            self.is_designer = True
            self.commit_service = Mock()
            self.commit_service.get_commit_history.return_value = []
            self.commit_service.get_pending_commits_grouped.return_value = []
            self._update_staged_count = Mock()
            self._refresh_bom_rows_for_parts = Mock()
            self._refresh_pdm_rows_after_merge = Mock()
            self._refresh_issue_views = Mock()
            self._set_commit_history_rows = Mock()
            self._set_pending_commit_groups = Mock()

        def statusBar(self):
            return Mock()

        def _run_processing_task(self, label, work, success, error):
            try:
                result = work()
            except Exception as exc:
                error(exc)
                return
            finish = success(result)
            for step in finish["steps"]:
                step()
            finish["popup"]()


if __name__ == "__main__":
    unittest.main()
