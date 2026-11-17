import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.services.cad_filename_reconciliation import reconcile_filenames
from core.services.diag_service import DiagService
from core.services.master_work_service import MasterWorkService
from setup.migrations import _migration_54, _migration_55


class _Repository:
    def __init__(self, db):
        self.db = db

    def get_conn(self):
        return sqlite3.connect(self.db)

    def get_working_directory_commit_records(self, _project_id):
        return [
            {"filename": "old.prt.5", "approved_version": "5", "status": "approved", "commit_id": "c1"},
            {"filename": "old.drw.2", "approved_version": "2", "status": "approved", "commit_id": "c2"},
        ]


class _Snapshots:
    def get_all(self, _project_id):
        return []


class FilenameReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="nexus-rename-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = str(self.root / "db.sqlite")
        (self.root / ".nexus").mkdir()
        self.part_hash = self.write_file("new.prt.5", b"model")
        self.drawing_hash = self.write_file("new.drw.2", b"drawing")
        self.write_file("unknown.prt.1", b"unrelated")
        self.manifest = {
            "files": {
                "old.prt": {"path": "old.prt.5", "source_filename": "old.prt.5", "sha256": self.part_hash, "source_commit_id": "c1"},
                "old.drw": {"path": "old.drw.2", "source_filename": "old.drw.2", "sha256": self.drawing_hash, "source_commit_id": "c2"},
            },
            "validated_files": {"old.prt": {"path": "old.prt.5", "source_filename": "old.prt.5", "sha256": self.part_hash}},
            "iteration_high_water": {"old.prt": 5, "old.drw": 2},
        }
        (self.root / ".nexus" / "master-manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")
        conn = sqlite3.connect(self.db)
        try:
            conn.executescript("""
                CREATE TABLE projects(id INTEGER PRIMARY KEY,working_directory TEXT);
                CREATE TABLE cad_documents(id INTEGER PRIMARY KEY,project_id INTEGER,number TEXT,name TEXT,file_name TEXT,base_file_name TEXT,category TEXT,drawing_owner_cad_document_id INTEGER,checked_out_by INTEGER,revision TEXT,iteration INTEGER,latest_creo_file_name TEXT,modified_at TEXT);
                CREATE TABLE cad_document_iterations(id INTEGER PRIMARY KEY,cad_document_id INTEGER,revision TEXT,iteration INTEGER,primary_path TEXT,sha256 TEXT);
                CREATE TABLE cad_item_associations(cad_document_id INTEGER,item_id INTEGER,active INTEGER);
                CREATE TABLE locks(part_id INTEGER);
                CREATE TABLE nexus_branches(id TEXT,project_id INTEGER,state TEXT,head_snapshot_id TEXT);
                CREATE TABLE nexus_branch_items(branch_id TEXT,bom_id INTEGER);
                CREATE TABLE nexus_branch_snapshots(id TEXT,branch_id TEXT,manifest_json TEXT,manifest_sha256 TEXT);
                CREATE TABLE commits(project_id INTEGER,cad_document_id INTEGER,part_id INTEGER,status TEXT,filename TEXT);
                CREATE TABLE bom(id INTEGER PRIMARY KEY,project_id INTEGER,filename TEXT,base_file_name TEXT,drawing TEXT,base_drw_name TEXT);
                CREATE TABLE nexus_item_events(id INTEGER PRIMARY KEY,project_id INTEGER,bom_id INTEGER,actor_id INTEGER,created_at TEXT,event_type TEXT,title TEXT,note TEXT,branch_id TEXT,snapshot_id TEXT,release_id TEXT,payload_json TEXT);
            """)
            _migration_54(conn)
            _migration_55(conn)
            conn.execute("ALTER TABLE cad_documents ADD COLUMN lifecycle_state TEXT")
            conn.execute("INSERT INTO projects VALUES(1,?)", (str(self.root),))
            conn.execute("INSERT INTO cad_documents VALUES(1,1,'old.prt','Model','old.prt','old','COMPONENT',NULL,NULL,'A',1,'old.prt.5',NULL,'RELEASED')")
            conn.execute("INSERT INTO cad_documents VALUES(2,1,'old.drw','Drawing','old.drw','old','DRAWING',1,NULL,'A',1,'old.drw.2',NULL,'RELEASED')")
            conn.execute("INSERT INTO cad_document_iterations VALUES(1,1,'A',1,'old.prt.5',?)", (self.part_hash,))
            conn.execute("INSERT INTO cad_document_iterations VALUES(2,2,'A',1,'old.drw.2',?)", (self.drawing_hash,))
            conn.executemany("INSERT INTO cad_item_associations VALUES(?,?,1)", [(1, 9), (2, 9)])
            conn.execute("INSERT INTO bom VALUES(9,1,'old.prt.5','old.prt','old.drw.2','old.drw')")
            conn.commit()
        finally:
            conn.close()

    def write_file(self, name, data):
        (self.root / name).write_bytes(data)
        return hashlib.sha256(data).hexdigest()

    def reconcile(self):
        with patch("core.repositories.permission_repository.PermissionRepository.user_has_permission", return_value=True):
            return reconcile_filenames(db_name=self.db, project_id=1, actor_id=7, cad_id=1,
                                       names={1: "new.prt", 2: "new.drw"})

    def force_rename(self):
        with patch("core.repositories.permission_repository.PermissionRepository.user_has_permission", return_value=True):
            return reconcile_filenames(db_name=self.db, project_id=1, actor_id=7, cad_id=1,
                                       names={1: "new.prt"}, force=True, reason="Creo rename changed iteration")

    def test_force_adopts_changed_iteration_and_requires_approval(self):
        from core.services.master_work_service import master_commit_for_part, native_work_state
        (self.root / "new.prt.5").unlink()
        digest = self.write_file("new.prt.8", b"changed geometry")
        result = self.force_rename()
        self.assertIn("unverified", result["warning"])
        manifest = MasterWorkService.read_manifest(self.root, db_name=self.db)
        record = manifest["files"]["new.prt"]
        self.assertEqual(record["sha256"], digest)
        self.assertEqual(record["path"], "new.prt.8")
        self.assertIsNone(record["source_commit_id"])
        self.assertNotIn("new.prt", manifest["validated_files"])
        self.assertIn("Unverified", native_work_state(master_commit_for_part([], manifest, 9)))
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT lifecycle_state FROM cad_documents WHERE id=1").fetchone()[0], "IN_WORK")
            self.assertEqual(conn.execute("SELECT primary_path FROM cad_document_iterations WHERE id=1").fetchone()[0], "old.prt.5")
            self.assertEqual(conn.execute("SELECT filename FROM bom WHERE id=9").fetchone()[0], "new.prt.8")
        item = {"filename": "new.prt.8", "dest_path": str(self.root / "new.prt.8"), "part_ids": [9]}
        with patch("config.DB_NAME", self.db):
            MasterWorkService.publish(self.root, "draft", [item])
            self.assertTrue(MasterWorkService.read_manifest(self.root)["files"]["new.prt"]["unverified"])
            item["validated"] = True
            MasterWorkService.publish(self.root, "approved", [item])
            approved = MasterWorkService.read_manifest(self.root)
        self.assertFalse(approved["files"]["new.prt"].get("unverified"))
        self.assertEqual(approved["validated_files"]["new.prt"]["source_commit_id"], "approved")
        self.assertEqual(MasterWorkService.read_manifest(self.root, db_name=self.db), approved)

    def test_force_permission_enforced_by_service(self):
        with patch("core.repositories.permission_repository.PermissionRepository.user_has_permission",
                   side_effect=lambda user, permission, project: permission == "manage_parts"):
            with self.assertRaisesRegex(PermissionError, "Master/merge"):
                reconcile_filenames(db_name=self.db, project_id=1, actor_id=7, cad_id=1,
                                    names={1: "new.prt"}, force=True, reason="test")

    def test_force_requires_a_reason(self):
        with patch("core.repositories.permission_repository.PermissionRepository.user_has_permission", return_value=True):
            with self.assertRaisesRegex(ValueError, "reason"):
                reconcile_filenames(db_name=self.db, project_id=1, actor_id=7, cad_id=1,
                                    names={1: "new.prt"}, force=True)

    def test_force_does_not_bypass_related_checkout(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO locks VALUES(9)")
        with self.assertRaisesRegex(ValueError, "related Item checkouts"):
            self.force_rename()

    def test_force_still_requires_existing_new_file(self):
        (self.root / "new.prt.5").unlink()
        with self.assertRaisesRegex(ValueError, "No renamed"):
            self.force_rename()

    def test_reconciles_model_and_drawing_and_keeps_unexpected_files_visible(self):
        result = self.reconcile()
        self.assertEqual(result["cad_document_ids"], [1, 2])
        manifest = MasterWorkService.read_manifest(str(self.root), db_name=self.db)
        self.assertEqual(manifest["files"]["new.prt"]["path"], "new.prt.5")
        self.assertEqual(manifest["validated_files"]["new.prt"]["sha256"], self.part_hash)
        self.assertEqual(manifest["iteration_high_water"]["new.prt"], 5)
        self.assertEqual(json.loads((self.root / ".nexus" / "master-manifest.json").read_text()), self.manifest)
        conn = sqlite3.connect(self.db)
        try:
            self.assertEqual(conn.execute("SELECT filename,drawing FROM bom").fetchone(), ("new.prt.5", "new.drw.2"))
            self.assertEqual(conn.execute("SELECT count(*) FROM nexus_item_events").fetchone()[0], 2)
        finally:
            conn.close()
        diag = DiagService.__new__(DiagService)
        diag.session = SimpleNamespace(project_id=1)
        diag.repo = diag.pdm_repo = _Repository(self.db)
        diag.snap_repo = _Snapshots()
        with patch("config.DB_NAME", self.db):
            results = diag.check_working_directory(str(self.root), inventory=SimpleNamespace(names=["new.prt.5", "new.drw.2", "unknown.prt.1"]))
        self.assertEqual([row[0] for row in results], ["unknown.prt.1"])

    def test_changed_file_rejects_entire_operation(self):
        self.write_file("new.drw.2", b"modified")
        with self.assertRaisesRegex(ValueError, "contents changed"):
            self.reconcile()
        conn = sqlite3.connect(self.db)
        try:
            self.assertEqual(conn.execute("SELECT file_name FROM cad_documents ORDER BY id").fetchall(), [("old.prt",), ("old.drw",)])
            self.assertEqual(conn.execute("SELECT count(*) FROM cad_filename_reconciliations").fetchone()[0], 0)
        finally:
            conn.close()
        self.assertFalse((self.root / ".nexus" / "master-publish.lock").exists())

    def test_drawing_only_rename_keeps_model_identity_and_master_file(self):
        (self.root / "new.prt.5").rename(self.root / "old.prt.5")
        with patch("core.repositories.permission_repository.PermissionRepository.user_has_permission", return_value=True):
            result = reconcile_filenames(db_name=self.db, project_id=1, actor_id=7, cad_id=1,
                                         names={1: "old.prt", 2: "new.drw"})
        self.assertEqual(result["cad_document_ids"], [2])
        conn = sqlite3.connect(self.db)
        try:
            self.assertEqual(conn.execute("SELECT file_name FROM cad_documents ORDER BY id").fetchall(),
                             [("old.prt",), ("new.drw",)])
            self.assertEqual(conn.execute("SELECT filename,drawing FROM bom").fetchone(),
                             ("old.prt.5", "new.drw.2"))
        finally:
            conn.close()
        manifest = MasterWorkService.read_manifest(str(self.root), db_name=self.db)
        self.assertEqual(manifest["files"]["old.prt"]["path"], "old.prt.5")
        self.assertEqual(manifest["files"]["new.drw"]["path"], "new.drw.2")

    def test_model_only_rename_keeps_drawing_identity(self):
        (self.root / "new.drw.2").rename(self.root / "old.drw.2")
        with patch("core.repositories.permission_repository.PermissionRepository.user_has_permission", return_value=True):
            result = reconcile_filenames(db_name=self.db, project_id=1, actor_id=7, cad_id=1,
                                         names={1: "new.prt", 2: "old.drw"})
        self.assertEqual(result["cad_document_ids"], [1])
        manifest = MasterWorkService.read_manifest(str(self.root), db_name=self.db)
        self.assertEqual(manifest["files"]["new.prt"]["path"], "new.prt.5")
        self.assertEqual(manifest["files"]["old.drw"]["path"], "old.drw.2")

    def test_unchanged_names_report_no_changes(self):
        with patch("core.repositories.permission_repository.PermissionRepository.user_has_permission", return_value=True):
            with self.assertRaisesRegex(ValueError, "No CAD or drawing filenames were changed"):
                reconcile_filenames(db_name=self.db, project_id=1, actor_id=7, cad_id=1,
                                    names={1: "old.prt", 2: "old.drw"})

    def add_branch(self, state, *, item_id=None, files=None):
        encoded = json.dumps({"files": files or {}})
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO nexus_branches VALUES('branch',1,?,'head')", (state,))
            conn.execute("INSERT INTO nexus_branch_snapshots VALUES('head','branch',?,?)",
                         (encoded, hashlib.sha256(encoded.encode()).hexdigest()))
            if item_id is not None:
                conn.execute("INSERT INTO nexus_branch_items VALUES('branch',?)", (item_id,))

    def test_active_related_branch_blocks_operation(self):
        self.add_branch("Active", item_id=9)
        with self.assertRaisesRegex(ValueError, "active Nexus branch"):
            self.reconcile()

    def test_active_branch_with_cad_dependency_blocks_operation(self):
        self.add_branch("Active", files={"old.prt": {"cad_document_id": 1, "filename": "old.prt.5"}})
        with self.assertRaisesRegex(ValueError, "active Nexus branch"):
            self.reconcile()

    def test_active_legacy_branch_with_cad_filename_blocks_operation(self):
        self.add_branch("Active", files={"old.prt": {"filename": "old.prt.5"}})
        with self.assertRaisesRegex(ValueError, "active Nexus branch"):
            self.reconcile()

    def test_damaged_active_snapshot_is_not_silently_ignored(self):
        self.add_branch("Active")
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE nexus_branch_snapshots SET manifest_sha256='invalid'")
        with self.assertRaisesRegex(ValueError, "Cannot verify an active branch snapshot"):
            self.reconcile()

    def test_active_unrelated_branch_does_not_block(self):
        self.add_branch("Active", item_id=99, files={"other.prt": {"cad_document_id": 99}})
        self.reconcile()

    def test_paused_related_branch_does_not_block(self):
        self.add_branch("Paused", item_id=9, files={"old.prt": {"cad_document_id": 1}})
        self.reconcile()

    def test_unrelated_checkouts_do_not_block(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO cad_documents(id,project_id,checked_out_by) VALUES(99,1,5)")
            conn.execute("INSERT INTO locks VALUES(99)")
        self.reconcile()

    def test_related_item_checkout_blocks(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO locks VALUES(9)")
        with self.assertRaisesRegex(ValueError, "related Item checkouts"):
            self.reconcile()

    def test_related_cad_checkout_blocks(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE cad_documents SET checked_out_by=5 WHERE id=2")
        with self.assertRaisesRegex(ValueError, "related CAD checkouts"):
            self.reconcile()

    def test_unverified_latest_iteration_cannot_inherit_master_validation(self):
        self.write_file("new.prt.6", b"unverified newer model")
        conn = sqlite3.connect(self.db)
        try:
            conn.execute("UPDATE cad_documents SET latest_creo_file_name='old.prt.6' WHERE id=1")
            conn.commit()
        finally:
            conn.close()
        with self.assertRaisesRegex(ValueError, "differs from the recorded Master"):
            self.reconcile()
        conn = sqlite3.connect(self.db)
        try:
            self.assertEqual(conn.execute("SELECT file_name FROM cad_documents WHERE id=1").fetchone()[0], "old.prt")
            self.assertEqual(conn.execute("SELECT count(*) FROM cad_filename_reconciliations").fetchone()[0], 0)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
