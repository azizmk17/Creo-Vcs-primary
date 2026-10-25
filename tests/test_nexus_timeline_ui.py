import os
import unittest
from datetime import datetime, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

from pages.nexus_timeline import (
    EVENT_ROLE, GRAPH_ROLE, VerticalHistoryView, _ReleaseDialog,
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


if __name__ == "__main__":
    unittest.main()
