import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.repositories.commit_repository import CommitRepository
from core.services.master_work_service import MasterWorkService


class IntegratedMasterLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "test.db"
        with sqlite3.connect(self.db) as conn:
            conn.executescript("""
                CREATE TABLE commits(id INTEGER PRIMARY KEY,project_id INTEGER,commit_id TEXT,status TEXT,checked_by INTEGER);
                CREATE TABLE cad_submission_lifecycle_events(
                    project_id INTEGER,commit_id TEXT,actor_user_id INTEGER,
                    from_status TEXT,to_status TEXT,reason TEXT);
                INSERT INTO commits VALUES(1,7,'draft','Pending',NULL),(2,7,'draft','Pending',NULL),
                                          (3,8,'draft','Pending',NULL);
            """)
        conn.close()
        self.repo = object.__new__(CommitRepository)
        self.repo.db_name = self.db
        connect = self.repo.get_conn
        self.repo.get_conn = lambda: closing_transaction(connect())

    def rows(self):
        with sqlite3.connect(self.db) as conn:
            rows = conn.execute("SELECT id,status,checked_by FROM commits ORDER BY id").fetchall()
        conn.close()
        return rows

    def test_complete_group_leaves_pending_without_becoming_validated(self):
        self.repo.mark_integrated_under_development("draft", 7, 9, [1, 2])
        self.assertEqual(self.rows(), [(1, "Integrated", None), (2, "Integrated", None), (3, "Pending", None)])
        with sqlite3.connect(self.db) as conn:
            event = conn.execute("SELECT from_status,to_status,reason FROM cad_submission_lifecycle_events").fetchone()
        conn.close()
        self.assertEqual(event[:2], ("Pending", "Integrated"))
        self.assertIn("without validation", event[2])

    def test_concurrent_group_change_cannot_be_partially_integrated(self):
        with self.assertRaisesRegex(ValueError, "changed during publication"):
            self.repo.mark_integrated_under_development("draft", 7, 9, [1])
        self.assertEqual(self.rows()[0][1], "Pending")
        self.assertEqual(self.rows()[1][1], "Pending")

    def test_integrated_group_can_be_validated_later(self):
        self.repo.mark_integrated_under_development("draft", 7, 9, [1, 2])
        self.assertTrue(self.repo.validate("draft", 4, 7))
        self.assertEqual(self.rows(), [(1, "Validated", 4), (2, "Validated", 4), (3, "Pending", None)])

    def test_database_failure_restores_master_files_and_manifest(self):
        source = self.root / "part.prt.1"
        source.write_bytes(b"old master")
        item = {"filename": source.name, "dest_path": str(source)}
        MasterWorkService.publish(self.root, "old", [item])
        manifest = (self.root / "master" / "manifest.json").read_bytes()
        source.write_bytes(b"new native")
        with self.assertRaisesRegex(ValueError, "changed during publication"):
            MasterWorkService.publish(self.root, "draft", [item], on_published=lambda:
                self.repo.mark_integrated_under_development("draft", 7, 9, [1]))
        self.assertEqual((self.root / "master" / "part.prt").read_bytes(), b"old master")
        self.assertEqual((self.root / "master" / "manifest.json").read_bytes(), manifest)
        self.assertEqual(self.rows()[0][1], "Pending")


class closing_transaction:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self.connection.__enter__()

    def __exit__(self, *args):
        try:
            return self.connection.__exit__(*args)
        finally:
            self.connection.close()


if __name__ == "__main__":
    unittest.main()
