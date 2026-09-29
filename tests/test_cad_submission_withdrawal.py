import os
import sqlite3
import tempfile
import unittest

from core.repositories.commit_repository import CommitRepository
from setup.migrations import _migration_46, _migration_47, _migration_48, _migration_49


class CadSubmissionWithdrawalTests(unittest.TestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE commits(
                    id INTEGER PRIMARY KEY,project_id INTEGER,commit_id TEXT,
                    designer INTEGER,status TEXT
                )
            """)
            conn.executemany(
                "INSERT INTO commits VALUES(?,?,?,?,?)",
                [(1, 3, "batch-1", 7, "Pending"),
                 (2, 3, "batch-1", 7, "Pending")],
            )
            _migration_46(conn)
            _migration_47(conn)
            _migration_48(conn)
            _migration_49(conn)
        self.repo = object.__new__(CommitRepository)
        self.repo.db_name = self.db_path

    def tearDown(self):
        try:
            os.remove(self.db_path)
        except OSError:
            pass

    def test_withdrawal_is_group_atomic_audited_and_retains_rows(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO cad_submission_approvals(
                    approval_id,project_id,commit_id,snapshot_sha256,approver_id,merge_id,status
                ) VALUES('approval-1',3,'batch-1','hash',9,'merge-1','FILES_READY')
            """)
            conn.execute("""
                INSERT INTO cad_submission_approval_events(
                    approval_id,project_id,commit_id,actor_user_id,from_status,to_status
                ) VALUES('approval-1',3,'batch-1',9,'PREPARING','FILES_READY')
            """)

        result = self.repo.withdraw_cad_submission("batch-1", 3, 7, "obsolete")
        self.assertEqual(result["row_count"], 2)
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(
                conn.execute("SELECT status FROM commits ORDER BY id").fetchall(),
                [("Withdrawn",), ("Withdrawn",)],
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM commits").fetchone()[0], 2
            )
            self.assertEqual(
                conn.execute(
                    "SELECT from_status,to_status,reason FROM cad_submission_lifecycle_events"
                ).fetchone(),
                ("Pending", "Withdrawn", "obsolete"),
            )
            self.assertEqual(
                conn.execute(
                    "SELECT status FROM cad_submission_approvals WHERE approval_id='approval-1'"
                ).fetchone()[0],
                "WITHDRAWN",
            )

    def test_only_submitter_can_withdraw_and_failure_changes_nothing(self):
        with self.assertRaisesRegex(PermissionError, "original submitter"):
            self.repo.withdraw_cad_submission("batch-1", 3, 8)
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM commits WHERE status='Pending'").fetchone()[0],
                2,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM cad_submission_lifecycle_events").fetchone()[0],
                0,
            )

    def test_approved_or_mixed_group_cannot_be_withdrawn(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE commits SET status='Approved' WHERE id=1")
        with self.assertRaisesRegex(ValueError, "Pending or Validated"):
            self.repo.withdraw_cad_submission("batch-1", 3, 7)

    def test_rejection_requires_reason_and_different_reviewer_then_closes_journal(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE commits SET status='Validated'")
            conn.execute("""
                INSERT INTO cad_submission_approvals(
                    approval_id,project_id,commit_id,snapshot_sha256,approver_id,merge_id,status
                ) VALUES('approval-reject',3,'batch-1','hash',9,'merge-2','FILES_READY')
            """)
            conn.execute("""
                INSERT INTO cad_submission_approval_events(
                    approval_id,project_id,commit_id,actor_user_id,from_status,to_status
                ) VALUES('approval-reject',3,'batch-1',9,'PREPARING','FILES_READY')
            """)

        with self.assertRaisesRegex(ValueError, "reason is required"):
            self.repo.reject_cad_submission("batch-1", 3, 9, " ")
        with self.assertRaisesRegex(PermissionError, "cannot reject their own"):
            self.repo.reject_cad_submission("batch-1", 3, 7, "Needs redesign")

        result = self.repo.reject_cad_submission(
            "batch-1", 3, 9, "Dependency baseline is outdated."
        )
        self.assertEqual(result["status"], "Rejected")
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(
                conn.execute("SELECT status FROM commits ORDER BY id").fetchall(),
                [("Rejected",), ("Rejected",)],
            )
            self.assertEqual(
                conn.execute(
                    "SELECT actor_user_id,from_status,to_status,reason FROM cad_submission_lifecycle_events"
                ).fetchone(),
                (9, "Validated", "Rejected", "Dependency baseline is outdated."),
            )
            self.assertEqual(
                conn.execute(
                    "SELECT status,last_error FROM cad_submission_approvals WHERE approval_id='approval-reject'"
                ).fetchone(),
                ("REJECTED", "Dependency baseline is outdated."),
            )
        self.assertEqual(
            self.repo.get_submission_lifecycle_events("batch-1", 3)[0]["reason"],
            "Dependency baseline is outdated.",
        )
        with self.assertRaisesRegex(ValueError, "Pending submission"):
            self.repo.validate("batch-1", 9, 3)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE commits SET status='Pending' WHERE id=1")
            conn.execute("UPDATE commits SET status='Validated' WHERE id=2")
        with self.assertRaisesRegex(ValueError, "Pending or Validated"):
            self.repo.withdraw_cad_submission("batch-1", 3, 7)


if __name__ == "__main__":
    unittest.main()
