import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.services.pdm_operations_health_service import PdmOperationsHealthService


class PdmOperationsHealthServiceTests(unittest.TestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.root = Path(tempfile.mkdtemp())
        (self.root / "commits" / "_snapshots" / "objects").mkdir(parents=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE projects(id INTEGER PRIMARY KEY, name TEXT, working_directory TEXT);
                CREATE TABLE cad_submission_approvals(
                    approval_id TEXT, project_id INTEGER, commit_id TEXT,
                    approver_id INTEGER, status TEXT, updated_at TEXT, last_error TEXT
                );
                CREATE TABLE cad_workspace_release_queue(
                    id INTEGER PRIMARY KEY, approval_key TEXT, project_id INTEGER,
                    workspace_id TEXT, machine_id TEXT, cad_document_id INTEGER,
                    completed_at TEXT, last_error TEXT
                );
            """)
            conn.execute(
                "INSERT INTO projects VALUES(1, 'Test Project', ?)",
                (str(self.root),),
            )
            conn.execute(
                "INSERT INTO cad_submission_approvals VALUES(?,?,?,?,?,?,?)",
                ("approval-1", 1, "commit-1", 7, "FILES_READY", "2026-10-04", "storage retry"),
            )
            conn.execute(
                "INSERT INTO cad_workspace_release_queue VALUES(?,?,?,?,?,?,?,?)",
                (1, "approval-1", 1, "workspace-1", "test-machine", 22, None, "read-only"),
            )

    def tearDown(self):
        Path(self.db_path).unlink(missing_ok=True)
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def test_reports_shared_store_and_recoverable_work(self):
        rows = PdmOperationsHealthService(
            self.db_path, machine_id="test-machine"
        ).inspect(1)
        by_area = {}
        for row in rows:
            by_area.setdefault(row["area"], []).append(row)

        self.assertEqual(by_area["Database"][0]["status"], "OK")
        self.assertEqual(by_area["Shared-folder write lock"][0]["status"], "OK")
        self.assertTrue(all(row["status"] == "OK" for row in by_area["Project root"] + by_area["Commit file store"] + by_area["Frozen snapshot store"]))
        approval = by_area["Approval recovery"][0]
        self.assertEqual(approval["status"], "WARNING")
        self.assertIn("original approver", approval["details"])
        cleanup = by_area["Local workspace cleanup"][0]
        self.assertEqual(cleanup["status"], "WARNING")
        self.assertIn("1 pending cleanup", cleanup["details"])

    def test_missing_project_path_is_reported_without_creating_it(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE projects SET working_directory=? WHERE id=1",
                (str(self.root / "missing"),),
            )
        rows = PdmOperationsHealthService(self.db_path, machine_id="test-machine").inspect(1)
        project_root = next(row for row in rows if row["area"] == "Project root")
        self.assertEqual(project_root["status"], "ERROR")
        self.assertFalse((self.root / "missing").exists())


if __name__ == "__main__":
    unittest.main()
