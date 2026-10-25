import os
import unittest
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from unittest.mock import Mock
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QMessageBox
from PyQt5.QtTest import QTest

from pages.nexus_timeline import (
    EVENT_ROLE, GRAPH_ROLE, VerticalHistoryView, _ReleaseDialog, _BranchFilesDialog, NexusTimelinePanel, _MergeDialog,
)


class NexusTimelineUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_branch_lane_begins_at_creation_and_old_release_stays_on_master(self):
        branch_id = "branch-1"
        events = [
            {"event_type": "BRANCH_CHECKIN", "branch_id": branch_id,
             "created_at": "2026-02-03", "title": "Branch check-in", "payload": {}},
            {"event_type": "BRANCH_CREATED", "branch_id": branch_id,
             "created_at": "2026-02-02", "title": "Branch created", "payload": {}},
            {"event_type": "RELEASE_FROZEN", "branch_id": None,
             "created_at": "2026-02-01", "title": "Frozen package", "payload": {}},
        ]
        view = VerticalHistoryView()
        view.set_events(events, [{"id": branch_id, "state": "Active"}])
        self.addCleanup(view.deleteLater)

        checkin = view.topLevelItem(0).data(0, GRAPH_ROLE)
        created = view.topLevelItem(1).data(0, GRAPH_ROLE)
        old_release = view.topLevelItem(2).data(0, GRAPH_ROLE)
        self.assertTrue(checkin["lanes"][0]["x"] > 17)
        self.assertEqual(created["lanes"][0]["create"], True)
        self.assertTrue(old_release["release"])
        self.assertEqual(old_release["lanes"], [])
        self.assertEqual(view.topLevelItem(2).data(0, EVENT_ROLE)["event_type"], "RELEASE_FROZEN")
        view.resize(900, 500)
        view.show()
        self.app.processEvents()
        self.assertFalse(view.grab().isNull())
        view.close()

    def test_release_dialog_separates_project_and_document_revisions(self):
        dialog = _ReleaseDialog({"cad_revision": "DV12", "files": {
            "pdf": {"filename": "case.pdf", "role": "PDF", "file_revision": "A010"},
            "step": {"filename": "case.step", "role": "STEP", "file_revision": "A010"},
        }}, None)
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.cad_revision.text(), "DV12")
        self.assertTrue(dialog.cad_revision.isReadOnly())
        self.assertEqual(dialog.document_revision.placeholderText(), "e.g. A010")
        self.assertEqual(dialog.files.rowCount(), 2)
        self.assertEqual(dialog.keys(), ["pdf", "step"])

    def test_utc_release_and_legacy_local_event_display_in_same_timezone(self):
        from pages.nexus_timeline import _local_timestamp_text
        local = datetime(2026, 10, 24, 23, 30, 0).astimezone()
        utc_value = local.astimezone(timezone.utc).isoformat()
        self.assertEqual(_local_timestamp_text(utc_value), "2026-10-24 23:30:00")
        self.assertEqual(_local_timestamp_text("2026-10-24T23:30:00"), "2026-10-24 23:30:00")
        utc_naive = local.astimezone(timezone.utc).replace(tzinfo=None).isoformat()
        self.assertEqual(_local_timestamp_text(utc_naive, "UTC"), "2026-10-24 23:30:00")

    def test_branch_documents_can_be_saved_from_the_selected_historical_snapshot(self):
        requests = []
        class FilesService:
            def export_branch_file(self, item_id, branch_id, key, destination, **kwargs):
                requests.append((item_id, branch_id, key, kwargs))
                Path(destination).write_bytes(b"historical PDF")
                return str(destination)
        with tempfile.TemporaryDirectory() as directory:
            data = {"item_id": 7, "branch_id": "branch-1", "branch_name": "Fix gap",
                    "snapshot_id": "earlier-snapshot", "files": [{
                        "key": "7/pdf", "filename": "CASE TETRA.pdf", "file_type": "PDF",
                        "file_revision": "A010", "source_commit_id": "commit-1",
                        "storage": "Branch snapshot", "available": True, "sha256": "abc",
                    }]}
            dialog = _BranchFilesDialog(data, FilesService())
            self.addCleanup(dialog.deleteLater)
            self.assertEqual(dialog.table.item(0, 1).text(), "CASE TETRA.pdf")
            self.assertTrue(dialog.open_button.isEnabled())
            output = Path(directory) / "saved.pdf"
            with patch("pages.nexus_timeline.QFileDialog.getSaveFileName", return_value=(str(output), "")):
                dialog._export(False)
            deadline = time.monotonic() + 5
            while dialog._worker is not None and time.monotonic() < deadline:
                QTest.qWait(10)
            self.assertIsNone(dialog._worker)
            self.assertEqual(output.read_bytes(), b"historical PDF")
            self.assertEqual(requests, [(7, "branch-1", "7/pdf", {
                "snapshot_id": "earlier-snapshot", "expected_sha256": "abc",
            })])
            dialog.reject()

    def test_merged_branch_with_remaining_workspace_offers_cleanup_without_a_new_merge(self):
        panel = NexusTimelinePanel()
        self.addCleanup(panel.deleteLater)
        service = SimpleNamespace(user_id=2, cleanup_merged_branch=Mock(return_value={"cleanup_pending": False}))
        branch = {"id": "already-merged", "name": "Fix gap", "state": "Merged", "owner_id": 1,
                  "cleanup_pending": True}
        panel._service = service
        panel._data = {"branches": [branch], "can_merge": True}
        panel.branch_combo.addItem("Fix gap · Merged", branch["id"])
        self.assertFalse(panel.cleanup_button.isHidden())
        self.assertFalse(panel.merge_button.isEnabled())
        panel._run = Mock()
        panel.cleanup_button.click()
        operation = panel._run.call_args.args[0]
        operation()
        service.cleanup_merged_branch.assert_called_once_with("already-merged")
        branch["cleanup_pending"] = False
        panel._buttons()
        self.assertTrue(panel.cleanup_button.isHidden())

    def test_validated_merge_confirmation_preserves_validation_and_draft_warns(self):
        preview = {"branch_name": "Fix gap", "native_state": "Validated", "changes": [{
            "id": "file:case.prt", "kind": "file", "native": True, "key": "case.prt",
            "label": "case.prt.2", "base": "a", "master": "a", "branch": "b", "conflict": False,
        }]}
        dialog = _MergeDialog(preview, None)
        self.addCleanup(dialog.deleteLater)
        dialog.note.setPlainText("Merge checked work")
        with patch("pages.nexus_timeline.QMessageBox.question", return_value=QMessageBox.No) as question, \
                patch("pages.nexus_timeline.QMessageBox.warning", return_value=QMessageBox.No) as warning:
            dialog.accept()
            self.assertIn("preserving validation", question.call_args.args[2])
            warning.assert_not_called()
        preview["native_state"] = "Under development"
        with patch("pages.nexus_timeline.QMessageBox.warning", return_value=QMessageBox.No) as warning:
            dialog.accept()
            self.assertIn("Under development", warning.call_args.args[2])


if __name__ == "__main__":
    unittest.main()
