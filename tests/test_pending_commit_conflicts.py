import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from core.services.commit_service import CommitService, PendingCommitConflict


class PendingCommitConflictTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "part.prt.2"
        self.source.write_bytes(b"staged native")
        self.service = object.__new__(CommitService)
        self.service.session = SimpleNamespace(project_id=7, user_id=3)
        self.service.user_service = Mock()
        self.service.user_service.get_user_by_username.return_value = SimpleNamespace(id=3)
        self.service.commit_repository = Mock()
        self.service.commit_repository.is_duplicate_commit.return_value = True
        self.service.commit_repository.get_pending_rows_by_base_filename.return_value = [
            {"designer": 3, "commit_id": "old"}]
        self.service.commit_repository.insert.return_value = 101
        self.service.commit_repository.delete_pending_rows_by_base_filename.return_value = []
        self.service._assert_master_submissions_mutable = Mock()
        self.service.pdm_service = Mock()
        self.service.pdm_service.repo.get_cad_document_by_file.return_value = {
            "id": 10, "category": "COMPONENT", "checked_out_by": 3, "revision": "A", "iteration": 1,
        }
        self.service.pdm_service.repo.get_current_cad_iteration.return_value = {}
        self.service._item_ids_for_cad_commit = Mock(return_value=[11])
        self.service._item_id_for_cad_commit = Mock(return_value=11)
        self.service._object_iteration_id = Mock(return_value=None)
        self.service.signature_repo = Mock()
        self.service.traceability_service = Mock()
        self.service.snapshot_service = Mock()
        self.service.emit_project_event = Mock()

    def commit(self, action="error", files=None):
        return CommitService.commit_file.__wrapped__(
            self.service, str(self.root / "commits"), files or [str(self.source)],
            "designer", "changes", "Update", duplicate_action=action,
        )

    def test_conflict_requests_an_explicit_choice_before_writing_files(self):
        with self.assertRaises(PendingCommitConflict) as raised:
            self.commit()
        self.assertEqual(raised.exception.filename, "part.prt")
        self.assertFalse(raised.exception.replacement_blocked_reason)
        self.service.commit_repository.insert.assert_not_called()
        self.assertFalse((self.root / "commits").exists())

    def test_skip_all_creates_no_submission(self):
        result = self.commit("skip")
        self.assertEqual(result["skipped_files"], ["part.prt.2"])
        self.assertEqual(result["staged_files"], [])
        self.assertIsNone(result["commit_id"])
        self.service.commit_repository.insert.assert_not_called()
        self.service.commit_repository.delete_pending_rows_by_base_filename.assert_not_called()

    def test_replace_copies_staged_native_and_replaces_old_pending_rows(self):
        result = self.commit("replace")
        self.assertEqual(result["staged_files"], ["part.prt.2"])
        copies = list((self.root / "commits").rglob("part.prt.2"))
        self.assertEqual(len(copies), 1)
        self.assertEqual(copies[0].read_bytes(), b"staged native")
        self.service.commit_repository.delete_pending_rows_by_base_filename.assert_called_once_with(
            "part.prt", 7, exclude_row_ids=[101])

    def test_skip_keeps_unrelated_files_in_the_submission(self):
        other = self.root / "other.prt.1"
        other.write_bytes(b"other native")
        self.service.commit_repository.is_duplicate_commit.side_effect = lambda name, project: name == "part.prt"
        result = self.commit("skip", [str(self.source), str(other)])
        self.assertEqual(result["staged_files"], ["other.prt.1"])
        self.assertEqual(result["skipped_files"], ["part.prt.2"])
        self.service.commit_repository.delete_pending_rows_by_base_filename.assert_not_called()

    def test_other_users_pending_file_can_be_skipped_but_never_replaced(self):
        self.service.commit_repository.get_pending_rows_by_base_filename.return_value = [{"designer": 9}]
        with self.assertRaises(PendingCommitConflict) as raised:
            self.commit()
        self.assertIn("another user", raised.exception.replacement_blocked_reason)
        self.assertEqual(self.commit("skip")["staged_files"], [])
        with self.assertRaisesRegex(ValueError, "another user"):
            self.commit("replace")
        self.service.commit_repository.insert.assert_not_called()

    def test_master_snapshot_can_be_skipped_but_never_replaced(self):
        self.service._assert_master_submissions_mutable.side_effect = ValueError("Already in master")
        with self.assertRaises(PendingCommitConflict) as raised:
            self.commit()
        self.assertEqual(raised.exception.replacement_blocked_reason, "Already in master")
        self.assertEqual(self.commit("skip")["staged_files"], [])
        with self.assertRaisesRegex(ValueError, "Already in master"):
            self.commit("replace")
        self.service.commit_repository.insert.assert_not_called()


if __name__ == "__main__":
    unittest.main()
