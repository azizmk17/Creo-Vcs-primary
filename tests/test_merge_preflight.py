import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.services.merge_service import MergeService


class MergePreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = self.temp.name
        self.service = object.__new__(MergeService)
        self.service.commits_dir = self.root
        self.service.session = SimpleNamespace(project_id=7)
        self.service.commit_repository = Mock()
        self.service.bom_service = SimpleNamespace(
            pdm_service=SimpleNamespace(db_name="test.db")
        )

    def tearDown(self):
        self.temp.cleanup()

    def commits(self):
        return [
            SimpleNamespace(id=10, commit_id="batch-a", project_id=7,
                            status="Validated", filename="housing.prt",
                            designer_username="user", cad_document_id=21),
            SimpleNamespace(id=11, commit_id="batch-a", project_id=7,
                            status="Validated", filename="housing.drw",
                            designer_username="user", cad_document_id=22),
        ]

    def write_sources(self, commits):
        for commit in commits:
            path = os.path.join(self.root, commit.designer_username, commit.filename)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as stream:
                stream.write(b"pending")

    def repository_rows(self, commits):
        return [{"id": c.id, "status": c.status} for c in commits]

    def test_preflight_requires_complete_group_and_validates_structure_read_only(self):
        commits = self.commits()
        self.write_sources(commits)
        self.service.commit_repository.get_rows_by_commit_id.return_value = self.repository_rows(commits)
        with patch("core.services.commit_snapshot_service.CommitSnapshotService") as snapshots, \
                patch("core.services.cad_structure_sync_service.CadStructureSyncService") as service_type:
            snapshots.return_value.verify_pending_snapshot.return_value = {
                "housing.prt": {"path": "user/housing.prt", "sha256": "hash-a"},
                "housing.drw": {"path": "user/housing.drw", "sha256": "hash-b"},
                "_snapshot_sha256": "manifest-hash",
            }
            self.service._preflight_submissions(
                commits
            )
        snapshots.return_value.verify_pending_snapshot.assert_called_once()
        service_type.return_value.validate_pending_commit.assert_called_once_with(
            "batch-a", 7, {21, 22}
        )

        with self.assertRaisesRegex(ValueError, "every file"):
            self.service._preflight_submissions(
                    commits[:1]
            )

    def test_preflight_rejects_missing_file_before_merge(self):
        commits = self.commits()
        self.write_sources(commits[:1])
        self.service.commit_repository.get_rows_by_commit_id.return_value = self.repository_rows(commits)
        with patch("core.services.commit_snapshot_service.CommitSnapshotService") as snapshots:
            snapshots.return_value.verify_pending_snapshot.return_value = {
                "housing.prt": {"path": "_snapshots/part", "sha256": "hash-a"},
                "housing.drw": {"path": "_snapshots/missing", "sha256": "hash-b"},
                "_snapshot_sha256": "manifest-hash",
            }
            with self.assertRaisesRegex(ValueError, "missing or outside"):
                self.service._preflight_submissions(
                    commits
                )

    def test_preflight_rejects_mixed_submission_statuses(self):
        commits = self.commits()
        self.write_sources(commits)
        self.service.commit_repository.get_rows_by_commit_id.return_value = [
            {"id": 10, "status": "Validated"}, {"id": 11, "status": "Approved"}
        ]
        with patch("core.services.commit_snapshot_service.CommitSnapshotService"):
            with self.assertRaisesRegex(ValueError, "not wholly awaiting approval"):
                self.service._preflight_submissions(
                    commits
                )


if __name__ == "__main__":
    unittest.main()
