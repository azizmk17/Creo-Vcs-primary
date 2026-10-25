import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
import zipfile

from core.repositories.bom_revision_repository import BomRevisionRepository
from core.repositories.commit_repository import CommitRepository
from core.services.commit_snapshot_service import CommitSnapshotService
from core.services.master_work_service import MasterWorkService
from core.services.nexus_history_service import NexusHistoryService, file_hash
from setup.migrations import MIGRATIONS


class NexusHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base_temp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.base_temp.name) / "base.db"
        with sqlite3.connect(cls.base) as conn:
            conn.row_factory = sqlite3.Row
            for _, migration in sorted(MIGRATIONS.items()):
                if callable(migration):
                    migration(conn)
                else:
                    conn.executescript(migration)
        cls.addClassCleanup(cls.base_temp.cleanup)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "test.db"
        shutil.copyfile(self.base, self.db)
        self.work = self.root / "project"
        self.work.mkdir()
        for name, content in (("case.prt.1", b"native original"),
                              ("case.drw.1", b"drawing original"),
                              ("case.pdf", b"PDF A010"),
                              ("case.step", b"STEP A010")):
            (self.work / name).write_bytes(content)
        with self.connection() as conn:
            for user_id, role in ((1, "designer"), (2, "master"), (3, "designer")):
                conn.execute("INSERT INTO users(id,username,email,password) VALUES(?,?,?,'x')",
                             (user_id, "user" + str(user_id), str(user_id) + "@example.test"))
                conn.execute("INSERT INTO user_roles(user_id,role_id) SELECT ?,id FROM roles WHERE name=?",
                             (user_id, role))
            conn.execute("INSERT INTO projects(id,name,working_directory,root_project_id,version_label) VALUES(10,'Demo',?,10,'DV12')",
                         (str(self.work),))
            conn.execute("INSERT INTO projects(id,name,working_directory,root_project_id,version_label) VALUES(20,'Other',?,20,'DV13')",
                         (str(self.work),))
            conn.executemany("INSERT INTO user_projects(user_id,project_id) VALUES(?,10)", [(1,), (2,), (3,)])
            conn.execute("""
                INSERT INTO bom(id,type,name,filename,drawing,base_file_name,base_drw_name,
                    material,revision,project_id,created,modified)
                VALUES(1,'prt','CASE TETRA','case.prt.1','case.drw.1','case','case',
                    'Steel','A',10,datetime('now'),datetime('now'))
            """)
            for file_id, kind, filename in ((1, "PDF", "case.pdf"), (2, "STEP", "case.step")):
                conn.execute("INSERT INTO part_files(id,part_id,file_type,display_name) VALUES(?,1,?,?)",
                             (file_id, kind, filename))
                version_id = conn.execute("""
                    INSERT INTO part_file_versions(file_id,version_no,original_filename,vault_rel_path,
                        revision,sha256,lifecycle_state,root_project_id,project_version_label)
                    VALUES(?,1,?,?,'A010',?,'Released',10,'DV12')
                """, (file_id, filename, filename, file_hash(self.work / filename))).lastrowid
                conn.execute("UPDATE part_files SET active_version_id=? WHERE id=?", (version_id, file_id))
            self.original_commit = conn.execute("""
                INSERT INTO commits(part_id,filename,designer,message,project_id,status,commit_id,base_file_name)
                VALUES(1,'case.prt.1',1,'Approved',10,'Approved','original','case')
            """).lastrowid
        self.revisions = BomRevisionRepository(self.db)
        self.revisions.get_current_context(1)
        self.publish("original", b"native original", self.original_commit)
        self.designer = NexusHistoryService(self.db, user_id=1, project_id=10)
        self.master = NexusHistoryService(self.db, user_id=2, project_id=10)

    def connection(self):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        return conn

    def publish(self, commit_id, content, order=10):
        source = self.root / "publish.prt.1"
        source.write_bytes(content)
        MasterWorkService.publish(self.work, commit_id, [
            {"filename": "case.prt.1", "dest_path": str(source),
             "part_ids": [1], "source_order": order},
            {"filename": "case.drw.1", "dest_path": str(self.work / "case.drw.1"),
             "part_ids": [1], "source_order": order},
        ])

    def branch(self, change=b"branch native", *, attributes=None):
        branch_id = self.designer.create_branch([1], "fix-gap", "Increase clearance", False)
        details = self.designer.branch_details(branch_id)
        folder = Path(self.designer.workspace(branch_id))
        (folder / "case.prt.2").write_bytes(change)
        head = self.designer.check_in(branch_id, details["head_snapshot_id"], "Clearance +0.3 mm",
                                      attributes=attributes)
        return branch_id, head, folder

    def test_fresh_schema_adds_history_without_rewriting_existing_deliverables(self):
        with self.connection() as conn:
            before = [tuple(r) for r in conn.execute("SELECT * FROM part_file_versions ORDER BY id")]
            MIGRATIONS[50](conn)
            after = [tuple(r) for r in conn.execute("SELECT * FROM part_file_versions ORDER BY id")]
        self.assertEqual(before, after)

    def test_branch_starts_at_creation_not_at_release(self):
        release_id = self.master.freeze_release(1, "A010", "For manufacturing")
        branch_id = self.designer.create_branch([1], "fix-gap", include_children=False)
        data = self.designer.timeline(1)
        created = next(e for e in data["events"] if e["event_type"] == "BRANCH_CREATED")
        release = next(e for e in data["events"] if e["release_id"] == release_id)
        self.assertEqual(created["branch_id"], branch_id)
        self.assertIsNone(release["branch_id"])
        self.assertGreater(created["created_at"], release["created_at"])
        self.assertNotIn("release_id", created["payload"])
        self.assertEqual(data["cad_revision"], "DV12")

    def test_active_branch_is_per_user_and_can_be_reset_to_master(self):
        branch_id = self.designer.create_branch([1], "fix-gap", include_children=False)
        self.designer.activate_branch([1], branch_id)
        self.assertEqual(self.designer.active_branch_map()[1]["branch_id"], branch_id)
        self.assertEqual(self.designer.branch_choices([1])[0]["id"], branch_id)
        self.master.activate_branch([1], branch_id)
        self.assertEqual(self.master.active_branch_map()[1]["branch_id"], branch_id)
        self.assertEqual(self.designer.active_branch_map()[1]["branch_id"], branch_id)
        self.designer.activate_branch([1], None)
        self.assertEqual(self.designer.active_branch_map(), {})

    def test_timeline_filters_lock_history_and_deduplicates_commit_checkin(self):
        legacy = [
            {"event": "CHECKOUT", "timestamp": "2026-10-24T10:00:00", "part_id": 1},
            {"event": "CHECKIN", "timestamp": "2026-10-24T10:02:00", "part_id": 1,
             "project_id": 10, "object_iteration_id": 7, "object_version": "A.7"},
            {"event": "COMMIT", "timestamp": "2026-10-24T10:03:00", "part_id": 1,
             "project_id": 10, "object_iteration_id": 7, "object_version": "A.7"},
            {"event": "CHECKIN", "timestamp": "2026-10-24T11:00:00", "part_id": 1,
             "project_id": 10, "object_iteration_id": 8, "object_version": "A.8"},
            {"event": "UNDO_CHECKOUT", "timestamp": "2026-10-24T11:02:00", "part_id": 1},
        ]
        event_types = [event["event_type"] for event in self.designer.timeline(1, legacy)["events"]]
        self.assertEqual(event_types.count("COMMIT"), 1)
        self.assertEqual(event_types.count("CHECKIN"), 1)
        self.assertNotIn("CHECKOUT", event_types)
        self.assertNotIn("UNDO_CHECKOUT", event_types)

    def test_release_export_keeps_exact_versions_after_live_files_change(self):
        release_id = self.master.freeze_release(1, "A010", "For manufacturing")
        self.publish("later", b"later native")
        (self.work / "case.pdf").write_bytes(b"new unvalidated PDF")
        target = self.root / "package.zip"
        self.master.export_release(release_id, target)
        with zipfile.ZipFile(target) as package:
            manifest = json.loads(package.read("release.json"))
            self.assertEqual(manifest["cad_revision"], "DV12")
            self.assertEqual(manifest["document_revision"], "A010")
            values = {Path(name).name: package.read(name) for name in package.namelist() if name.startswith("files/")}
            self.assertEqual(values["case.prt.1"], b"native original")
            self.assertEqual(values["case.pdf"], b"PDF A010")
            self.assertEqual(values["case.step"], b"STEP A010")
            names = set(package.namelist())
            self.assertIn("files/Native CAD/case.prt.1", names)
            self.assertIn("files/PDF/case.pdf", names)
            self.assertIn("files/STEP/case.step", names)
            exported = json.loads(package.read("release.json"))
            self.assertTrue(all("blob_path" not in entry
                                for entry in exported["manifest"]["files"].values()))

    def test_release_manifest_and_history_are_immutable(self):
        release_id = self.master.freeze_release(1, "A010", "For printing")
        with self.connection() as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE nexus_releases SET note='edited' WHERE id=?", (release_id,))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM nexus_item_events")

    def test_corrupted_release_blob_cannot_be_exported(self):
        release_id = self.master.freeze_release(1, "A010", "For printing")
        with self.connection() as conn:
            row = conn.execute("SELECT manifest_json FROM nexus_releases WHERE id=?", (release_id,)).fetchone()
        entry = next(iter(json.loads(row[0])["files"].values()))
        (self.work / entry["blob_path"]).write_bytes(b"tampered")
        target = self.root / "bad.zip"
        with self.assertRaisesRegex(ValueError, "has changed"):
            self.master.export_release(release_id, target)
        self.assertFalse(target.exists())

    def test_branch_workspace_and_checkin_do_not_change_master(self):
        branch_id, head, folder = self.branch()
        self.assertEqual((self.work / "master/case.prt").read_bytes(), b"native original")
        snapshot = self.designer.branch_details(branch_id)["manifest"]
        self.assertEqual(snapshot["files"]["case.prt"]["sha256"], file_hash(folder / "case.prt.2"))
        self.assertEqual(self.designer.timeline(1)["master_state"], "Validated")

    def test_pause_resume_and_stale_heads(self):
        branch_id, head, folder = self.branch()
        self.designer.set_branch_state(branch_id, "Paused")
        with self.assertRaisesRegex(ValueError, "Resume"):
            self.designer.check_in(branch_id, head, "Paused work")
        with self.assertRaisesRegex(ValueError, "active branch"):
            self.master.preview_merge(branch_id)
        self.designer.set_branch_state(branch_id, "Active")
        with self.assertRaisesRegex(ValueError, "advanced"):
            self.designer.check_in(branch_id, "old-head", "Stale edit")
        self.assertEqual(self.designer.branch_details(branch_id)["head_snapshot_id"], head)

    def test_refresh_preserves_unchecked_local_files(self):
        branch_id, head, folder = self.branch()
        (folder / "case.prt.3").write_bytes(b"unchecked")
        refreshed = self.designer.workspace(branch_id, refresh=True)
        saved = next(folder.parent.glob(folder.name + "-saved-*"))
        self.assertEqual((saved / "case.prt.3").read_bytes(), b"unchecked")
        self.assertEqual(Path(refreshed), folder)
        self.assertFalse((folder / "case.prt.3").exists())

    def test_project_designer_gets_isolated_branch_workspace_but_cannot_merge(self):
        branch_id, _, _ = self.branch()
        other = NexusHistoryService(self.db, user_id=3, project_id=10)
        other_workspace = Path(other.workspace(branch_id))
        own_workspace = Path(self.designer.workspace(branch_id))
        self.assertNotEqual(other_workspace, own_workspace)
        self.assertEqual((other_workspace / "case.prt").read_bytes(), b"branch native")
        with self.assertRaises(PermissionError):
            self.designer.preview_merge(branch_id)
        outsider = NexusHistoryService(self.db, user_id=1, project_id=20)
        with self.assertRaises(PermissionError):
            outsider.timeline(1)

    def test_three_way_conflicts_require_explicit_decisions(self):
        branch_id, _, _ = self.branch(attributes={"1": {"material": "Aluminium"}})
        self.publish("parallel", b"master changed independently", 30)
        with self.connection() as conn:
            conn.execute("UPDATE bom SET material='Titanium' WHERE id=1")
        preview = self.master.preview_merge(branch_id)
        self.assertEqual(len([c for c in preview["changes"] if c["conflict"]]), 2)
        with self.assertRaisesRegex(ValueError, "Resolve every conflict"):
            self.master.merge(preview, {}, "Unresolved")
        self.assertEqual((self.work / "master/case.prt").read_bytes(), b"master changed independently")

    def test_reviewed_merge_uses_normal_integrated_submission_and_keeps_exports(self):
        release_id = self.master.freeze_release(1, "A010", "For manufacturing")
        branch_id, _, _ = self.branch(attributes={"1": {"material": "Aluminium"}})
        self.designer.activate_branch([1], branch_id)
        with self.connection() as conn:
            active = [tuple(r) for r in conn.execute("SELECT id,active_version_id FROM part_files ORDER BY id")]
        preview = self.master.preview_merge(branch_id)
        result = self.master.merge(preview, {}, "Integrate clearance fix")
        self.assertEqual((self.work / "master/case.prt").read_bytes(), b"branch native")
        self.assertEqual(self.master.branch_details(branch_id)["state"], "Merged")
        self.assertEqual(self.designer.active_branch_map(), {})
        self.assertEqual(self.master.timeline(1)["master_state"], "Under development")
        with self.connection() as conn:
            self.assertEqual(conn.execute("SELECT material FROM bom WHERE id=1").fetchone()[0], "Aluminium")
            self.assertEqual([tuple(r) for r in conn.execute("SELECT id,active_version_id FROM part_files ORDER BY id")], active)
            rows = [dict(r) for r in conn.execute("SELECT * FROM commits WHERE commit_id=?", (result["commit_id"],))]
            self.assertTrue(rows)
            self.assertTrue(all(r["status"] == "Integrated" for r in rows))
            self.assertEqual(MasterWorkService.read_manifest(self.work)["files"]["case.prt"]["source_order"], rows[-1]["id"])
        frozen = CommitSnapshotService(self.db).verify_pending_snapshot(
            str(self.work / "commits"), 10, result["commit_id"], rows)
        self.assertIn("case.prt.2", frozen)
        CommitRepository(self.db).validate(result["commit_id"], 2, 10)
        self.assertEqual(self.master.timeline(1)["master_state"], "Validated")
        self.master.export_release(release_id, self.root / "old-release.zip")
        with self.assertRaisesRegex(ValueError, "Resume"):
            self.designer.workspace(branch_id)

    def test_commit_checkin_is_routed_to_branch_and_not_master(self):
        branch_id = self.designer.create_branch([1], "commit-target", include_children=False)
        details = self.designer.branch_details(branch_id)
        source = self.root / "case.prt.1"
        # This commit is an explicit check-in even though the file bytes match
        # the current branch head; it must still be recorded and leave Pending.
        source.write_bytes(b"native original")
        with self.connection() as conn:
            conn.execute("""INSERT INTO commits(part_id,filename,designer,message,project_id,
                status,commit_id,base_file_name,title,committed_by)
                VALUES(1,'case.prt.1',1,'branch changes',10,'Pending','branch-push-1',
                'case','Branch update',1)""")
        snapshot_id = self.designer.check_in_from_commit(
            branch_id, details["head_snapshot_id"], "Branch update · branch changes",
            [{"filename": "case.prt.1", "path": str(source), "sha256": file_hash(source)}],
            "branch-push-1",
        )
        updated = self.designer.branch_details(branch_id)
        self.assertEqual(updated["head_snapshot_id"], snapshot_id)
        self.assertEqual((self.work / "master/case.prt").read_bytes(), b"native original")
        with self.connection() as conn:
            row = conn.execute("SELECT status,nexus_branch_id FROM commits WHERE commit_id='branch-push-1'").fetchone()
        self.assertEqual(tuple(row), ("Branched", branch_id))
        timeline = self.designer.timeline(1, [{
            "event": "COMMIT", "commit_id": "branch-push-1",
            "timestamp": "2026-10-24T12:00:00", "part_id": 1,
            "project_id": 10, "object_version": "A.2",
        }])
        self.assertEqual(sum(event["event_type"] in {"COMMIT", "BRANCH_COMMIT"}
                             for event in timeline["events"]), 1)
    def test_stale_merge_review_is_rejected(self):
        branch_id, _, _ = self.branch()
        preview = self.master.preview_merge(branch_id)
        self.publish("concurrent", b"new concurrent master", 99)
        with self.assertRaisesRegex(ValueError, "changed after review"):
            self.master.merge(preview, {}, "Stale review")
        self.assertEqual((self.work / "master/case.prt").read_bytes(), b"new concurrent master")

    def test_merge_rejects_new_shared_native_item_association(self):
        branch_id, _, _ = self.branch()
        preview = self.master.preview_merge(branch_id)
        with self.connection() as conn:
            conn.execute("""INSERT INTO bom(id,type,name,filename,drawing,base_file_name,
                base_drw_name,material,revision,project_id,created,modified)
                VALUES(2,'prt','Second Item','case.prt.1','case.drw.1','case','case',
                'Steel','A',10,datetime('now'),datetime('now'))""")
        with self.assertRaisesRegex(ValueError, "associations changed"):
            self.master.merge(preview, {}, "Reject stale CAD scope")

    def test_failed_database_merge_restores_master_and_preserves_branch(self):
        branch_id, _, _ = self.branch()
        preview = self.master.preview_merge(branch_id)
        old_manifest = (self.work / "master/manifest.json").read_bytes()
        with self.connection() as conn:
            conn.execute("""
                CREATE TRIGGER fail_branch_merge BEFORE UPDATE OF state ON nexus_branches
                WHEN NEW.state='Merged' BEGIN SELECT RAISE(ABORT,'Simulated DB failure'); END
            """)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "Simulated DB failure"):
            self.master.merge(preview, {}, "Failure injection")
        self.assertEqual((self.work / "master/case.prt").read_bytes(), b"native original")
        self.assertEqual((self.work / "master/manifest.json").read_bytes(), old_manifest)
        self.assertEqual(self.master.branch_details(branch_id)["state"], "Active")
        with self.connection() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM commits WHERE commit_id LIKE 'NB-%'").fetchone()[0], 0)

    def test_released_item_requires_a_new_revision_before_merge(self):
        branch_id, _, _ = self.branch()
        with self.connection() as conn:
            conn.execute("UPDATE bom SET lifecycle_state='Released',status='Released' WHERE id=1")
        preview = self.master.preview_merge(branch_id)
        with self.assertRaisesRegex(ValueError, "new revision"):
            self.master.merge(preview, {}, "Must preserve release")

    def test_expected_release_versions_must_not_change(self):
        sources = self.master.release_sources(1)
        self.publish("changed", b"new native", 50)
        with self.assertRaisesRegex(ValueError, "file versions changed"):
            self.master.freeze_release(1, "A020", "Late selection",
                                       expected=self.master._master_token(sources))


if __name__ == "__main__":
    unittest.main()
