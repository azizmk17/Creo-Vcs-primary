import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager

from core.services.commit_snapshot_service import CommitSnapshotService
from setup.migrations import _migration_45, _repair_snapshot_schema


class CommitSnapshotServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = self.temp.name
        self.db_path = os.path.join(self.root, "nexus.db")
        self.commit_dir = os.path.join(self.root, "commits")
        os.makedirs(os.path.join(self.commit_dir, "mkazi", "Release_commit-1"))
        self.source = os.path.join(self.commit_dir, "mkazi", "Release_commit-1", "gear.prt")
        with open(self.source, "wb") as stream:
            stream.write(b"first submitted bytes")
        with self.db_conn() as conn:
            conn.executescript("""
                CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT);
                INSERT INTO users VALUES(1,'mkazi');
                CREATE TABLE cad_documents(
                    id INTEGER PRIMARY KEY,project_id INTEGER,revision TEXT,iteration INTEGER
                );
                INSERT INTO cad_documents VALUES(9,7,'C',4);
                CREATE TABLE cad_document_iterations(
                    id INTEGER PRIMARY KEY,cad_document_id INTEGER,revision TEXT,
                    iteration INTEGER,sha256 TEXT
                );
                INSERT INTO cad_document_iterations VALUES(1,9,'C',4,'approved-hash');
                CREATE TABLE commits(
                    id INTEGER PRIMARY KEY,project_id INTEGER,commit_id TEXT,status TEXT,
                    filename TEXT,cad_document_id INTEGER,title TEXT,designer INTEGER
                );
                INSERT INTO commits VALUES(12,7,'commit-1','Pending','gear.prt',9,'Release',1);
                CREATE TABLE cad_pending_structure_changes(
                    project_id INTEGER,commit_id TEXT,payload_json TEXT,
                    required_cad_document_ids_json TEXT,status TEXT
                );
            """)
            _migration_45(conn)
        self.service = CommitSnapshotService(self.db_path)

    def tearDown(self):
        self.temp.cleanup()

    @contextmanager
    def db_conn(self):
        conn = sqlite3.connect(self.db_path)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def rows(self):
        return [{"id": 12, "filename": "gear.prt", "cad_document_id": 9}]

    def test_snapshot_keeps_submitted_bytes_and_checks_baseline(self):
        snapshot = self.service.seal_pending_commit(
            self.commit_dir, 7, "commit-1", 1,
            baseline_by_cad_id={9: {"revision": "C", "iteration": 4}},
        )
        with open(self.source, "wb") as stream:
            stream.write(b"later local edits")

        files = self.service.verify_pending_snapshot(
            self.commit_dir, 7, "commit-1", self.rows()
        )
        stored = os.path.join(self.commit_dir, files["gear.prt"]["path"])
        with open(stored, "rb") as stream:
            self.assertEqual(stream.read(), b"first submitted bytes")
        self.assertEqual(snapshot["generation"], 1)

        with self.db_conn() as conn:
            conn.execute("UPDATE cad_documents SET iteration=5 WHERE id=9")
        with self.assertRaisesRegex(ValueError, "changed after the submission snapshot"):
            self.service.verify_pending_snapshot(self.commit_dir, 7, "commit-1", self.rows())

    def test_structure_payload_is_bound_to_a_new_snapshot_generation(self):
        self.service.seal_pending_commit(self.commit_dir, 7, "commit-1", 1)
        payload = {"schema": 1, "members": [], "drawings": []}
        with self.db_conn() as conn:
            conn.execute("""
                INSERT INTO cad_pending_structure_changes VALUES(7,'commit-1',?,?, 'PENDING')
            """, (json.dumps(payload), json.dumps([9])))
        result = self.service.record_pending_structure(7, "commit-1", 1)
        self.assertEqual(result["generation"], 2)
        self.service.verify_pending_snapshot(self.commit_dir, 7, "commit-1", self.rows())

        with self.db_conn() as conn:
            changed = {"schema": 1, "members": [{"parent_file_name": "gear.asm"}], "drawings": []}
            conn.execute(
                "UPDATE cad_pending_structure_changes SET payload_json=?",
                (json.dumps(changed),),
            )
        with self.assertRaisesRegex(ValueError, "does not match the reviewed file snapshot"):
            self.service.verify_pending_snapshot(self.commit_dir, 7, "commit-1", self.rows())

    def test_snapshot_rejects_approved_bytes_changed_without_iteration_bump(self):
        self.service.seal_pending_commit(self.commit_dir, 7, "commit-1", 1)
        with self.db_conn() as conn:
            conn.execute(
                "UPDATE cad_document_iterations SET sha256='changed-hash' WHERE id=1"
            )

        with self.assertRaisesRegex(ValueError, "Approved CAD bytes changed"):
            self.service.verify_pending_snapshot(
                self.commit_dir, 7, "commit-1", self.rows()
            )

    def test_corrupted_snapshot_blob_is_rejected(self):
        self.service.seal_pending_commit(self.commit_dir, 7, "commit-1", 1)
        with self.db_conn() as conn:
            manifest = json.loads(conn.execute(
                "SELECT manifest_json FROM cad_commit_snapshots WHERE generation=1"
            ).fetchone()[0])
        blob = os.path.join(self.commit_dir, manifest["files"][0]["blob_path"])
        with open(blob, "wb") as stream:
            stream.write(b"tampered")
        with self.assertRaisesRegex(ValueError, "changed after submission"):
            self.service.verify_pending_snapshot(self.commit_dir, 7, "commit-1", self.rows())

    def test_startup_repairs_snapshot_table_missing_from_migrated_database(self):
        with self.db_conn() as conn:
            conn.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY)")
            conn.execute("INSERT INTO schema_migrations VALUES(45)")
            conn.execute("DROP TABLE cad_commit_snapshots")

            _repair_snapshot_schema(conn)

            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            indexes = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )}
            self.assertIn("cad_commit_snapshots", tables)
            self.assertIn("idx_cad_commit_snapshots_latest", indexes)
            self.assertEqual(
                conn.execute("SELECT version FROM schema_migrations").fetchone()[0], 45
            )


if __name__ == "__main__":
    unittest.main()
