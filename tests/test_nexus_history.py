import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from types import SimpleNamespace

from core.repositories.bom_revision_repository import BomRevisionRepository
from core.repositories.commit_repository import CommitRepository
from core.services.commit_snapshot_service import CommitSnapshotService
from core.services.master_work_service import MasterWorkService, deliverable_provenance, native_work_state
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

    def _document_commit(self, *, attach_before_push=True, validated=False, unchanged_outputs=False):
        branch_id = self.designer.create_branch([1], "document-access", include_children=False)
        base = self.designer.branch_details(branch_id)["head_snapshot_id"]
        source = self.root / "case.prt.2"
        source.write_bytes(b"native with new branch outputs")
        with self.connection() as conn:
            conn.execute("""INSERT INTO commits(part_id,filename,designer,message,project_id,status,
                commit_id,base_file_name,title,committed_by) VALUES(1,'case.prt.2',1,'New exports',10,
                'Pending','branch-documents','case','Document update',1)""")
        store = self.work / "commits/user1/Document update_branch-documents/_engineering_attachments"
        def attach():
            store.mkdir(parents=True)
            documents = []
            for kind, filename, content in (("PDF", "case.pdf", b"branch PDF A020"),
                                             ("STEP", "case.step", b"branch STEP A020")):
                if unchanged_outputs:
                    content = (self.work / filename).read_bytes()
                path = store / ("stored_" + filename)
                path.write_bytes(content)
                documents.append({"part_id": 1, "filename": filename, "file_type": kind,
                                  "file_role": "exported_" + kind.lower(), "revision": "A020",
                                  "stored_rel_path": "_engineering_attachments/" + path.name,
                                  "sha256": file_hash(path)})
            (store / "manifest.json").write_text(json.dumps({
                "commit_id": "branch-documents", "attachments": documents,
            }), encoding="utf-8")
        if attach_before_push:
            attach()
        if validated:
            CommitRepository(self.db).validate("branch-documents", 2, 10)
        self.designer.checkout_branch_item(1, branch_id)
        head = self.designer.check_in_from_commit(
            branch_id, base, "Commit with PDF and STEP",
            [{"filename": source.name, "path": str(source), "sha256": file_hash(source)}],
            "branch-documents",
        )
        if not attach_before_push:
            attach()
        return branch_id, base, head, store

    def test_branch_commit_freezes_outputs_and_exports_them_after_merge_cleanup(self):
        branch_id, _, head, store = self._document_commit()
        listing = self.designer.list_branch_files(1, branch_id, snapshot_id=head)
        pdf = next(entry for entry in listing["files"] if entry["file_type"] == "PDF")
        step = next(entry for entry in listing["files"] if entry["file_type"] == "STEP")
        self.assertEqual(pdf["filename"], "case.pdf")
        self.assertEqual(pdf["file_revision"], "A020")
        self.assertEqual(pdf["source_commit_id"], "branch-documents")
        self.assertEqual(pdf["storage"], "Branch snapshot")
        self.assertEqual(step["storage"], "Branch snapshot")
        # The branch exports must be independent of the original commit attachment store.
        shutil.rmtree(store)
        self.assertEqual((self.work / "case.pdf").read_bytes(), b"PDF A010")
        self.master.merge(self.master.preview_merge(branch_id), {}, "Integrate CAD and exports")
        self.assertFalse((self.work / ".nexus/branches" / branch_id / "workspaces").exists())
        with self.connection() as conn:
            merged_documents = conn.execute("""
                SELECT p.file_type,v.revision FROM part_files p
                JOIN part_file_versions v ON v.id=p.active_version_id WHERE p.part_id=1
            """).fetchall()
        self.assertEqual({tuple(row) for row in merged_documents}, {("PDF", "A020"), ("STEP", "A020")})
        for entry, content in ((pdf, b"branch PDF A020"), (step, b"branch STEP A020")):
            destination = self.root / "exports" / entry["filename"]
            self.designer.export_branch_file(1, branch_id, entry["key"], destination,
                                            snapshot_id=head, expected_sha256=entry["sha256"])
            self.assertEqual(destination.read_bytes(), content)

    def test_validated_branch_merge_publishes_outputs_and_keeps_master_validation(self):
        branch_id, _, _, _ = self._document_commit(validated=True)
        preview = self.master.preview_merge(branch_id)
        self.assertEqual(preview["native_state"], "Validated")
        result = self.master.merge(preview, {}, "Publish validated CAD and deliverables")
        self.assertEqual(result["native_state"], "Validated")
        self.assertEqual(self.master.timeline(1)["master_state"], "Validated")
        with self.connection() as conn:
            commits = [dict(row) for row in conn.execute("SELECT * FROM commits WHERE commit_id=?", (result["commit_id"],))]
            versions = [dict(row) for row in conn.execute("""SELECT p.file_type,v.* FROM part_files p
                JOIN part_file_versions v ON v.id=p.active_version_id WHERE p.part_id=1""")]
            paths = dict(conn.execute("SELECT pdf_path,step_path FROM bom WHERE id=1").fetchone())
        self.assertTrue(all(row["status"] == "Approved" and int(row["checked_by"]) == 2 for row in commits))
        self.assertTrue(all(row["approved_version"] and row["merged_by"] for row in commits))
        master_commit = SimpleNamespace(**commits[0])
        self.assertEqual(native_work_state(master_commit), "Validated")
        for version in versions:
            self.assertEqual(version["source_commit_id"], result["commit_id"])
            self.assertEqual(deliverable_provenance(version["file_type"], SimpleNamespace(**version), master_commit, master_commit)["state"], "ok")
        self.assertEqual(Path(paths["pdf_path"]).read_bytes(), b"branch PDF A020")
        self.assertEqual(Path(paths["step_path"]).read_bytes(), b"branch STEP A020")
        self.assertTrue(Path(paths["pdf_path"]).is_relative_to(self.work / "master"))
        self.assertEqual(len(MasterWorkService.read_manifest(self.work)["deliverables"]), 2)
        from tests.test_master_work_service import BomProvenanceIntegrationTests
        page = BomProvenanceIntegrationTests.page_methods()
        with self.connection() as conn:
            all_commits = [SimpleNamespace(**dict(row)) for row in conn.execute("SELECT * FROM commits WHERE part_id=1")]
        page._part_change_commits = lambda *_: all_commits
        page._master_change_commit_for_part = lambda *_: master_commit
        page.part_file_service = self._document_service()
        page.part_doc_ack_service = SimpleNamespace(get_ack=lambda *_: None)
        page._legacy_doc_path = lambda *_: ""
        for kind in ("PDF", "STEP"):
            self.assertEqual(page._doc_indicator_state(1, kind)["state"], "ok")

    def _document_service(self):
        from core.services.part_file_service import PartFileService
        from core.repositories.part_file_repository import PartFileRepository
        documents = PartFileService.__new__(PartFileService)
        documents.repo = PartFileRepository(self.db)
        documents._working_dir_for_root_label = lambda *_: str(self.work)
        documents._working_dir = lambda: str(self.work)
        documents._family_working_dir = lambda *_: str(self.work)
        return documents

    def test_unvalidated_branch_merge_publishes_documents_under_development(self):
        branch_id, _, _, _ = self._document_commit()
        result = self.master.merge(self.master.preview_merge(branch_id), {}, "Publish unfinished branch")
        self.assertEqual(result["native_state"], "Under development")
        self.assertEqual(self.master.timeline(1)["master_state"], "Under development")
        with self.connection() as conn:
            rows = list(conn.execute("SELECT status,checked_by FROM commits WHERE commit_id=?", (result["commit_id"],)))
            path = conn.execute("SELECT step_path FROM bom WHERE id=1").fetchone()[0]
        self.assertTrue(all(row["status"] == "Integrated" and row["checked_by"] is None for row in rows))
        self.assertEqual(Path(path).read_bytes(), b"branch STEP A020")

    def test_validated_outputs_create_managed_document_records_when_item_had_none(self):
        with self.connection() as conn:
            conn.execute("DELETE FROM part_file_versions")
            conn.execute("DELETE FROM part_files")
            conn.execute("DELETE FROM bom_iteration_files WHERE file_type IN ('PDF','STEP')")
        branch_id, _, _, _ = self._document_commit(validated=True)
        result = self.master.merge(self.master.preview_merge(branch_id), {}, "Create first managed outputs")
        from core.services.managed_file_service import ManagedFileService
        from core.repositories.managed_file_repository import ManagedFileRepository
        documents = self._document_service()
        managed = ManagedFileService.__new__(ManagedFileService)
        managed.repo = ManagedFileRepository(self.db)
        managed.revision_repo = self.revisions
        managed.part_file_service = documents
        managed._project_version_dir = lambda *_: str(self.work)
        managed._family_root = lambda *_: str(self.work)
        managed._working_dir = lambda *_: str(self.work)
        managed._username = lambda value: str(value or "")
        files = [row for row in managed.list_current_files(1) if row["file_type"] in {"PDF", "STEP"}]
        self.assertEqual({row["role_label"] for row in files}, {"Export PDF", "Export STEP"})
        self.assertEqual(len(files), 2)
        self.assertTrue(all(row["file_id"] and row["version_id"] and Path(row["path"]).is_file() for row in files))
        self.assertTrue(all(row["source_commit_id"] == result["commit_id"] and row["state"] == "Validated" for row in files))

    def test_edits_after_a_validated_branch_commit_require_validation_again(self):
        branch_id, _, head, _ = self._document_commit(validated=True)
        self.designer.check_in(branch_id, head, "Unchecked material adjustment", attributes={"1": {"material": "Aluminium"}})
        preview = self.master.preview_merge(branch_id)
        self.assertEqual(preview["native_state"], "Under development")
        result = self.master.merge(preview, {}, "Integrate unchecked edit")
        self.assertEqual(result["native_state"], "Under development")

    def test_identical_output_bytes_get_current_validated_commit_binding(self):
        branch_id, _, _, _ = self._document_commit(validated=True, unchanged_outputs=True)
        preview = self.master.preview_merge(branch_id)
        self.assertEqual(len([row for row in preview["changes"] if row["kind"] == "file" and not row["native"]]), 2)
        result = self.master.merge(preview, {}, "Same exports bound to validated native")
        with self.connection() as conn:
            sources = [row[0] for row in conn.execute("SELECT v.source_commit_id FROM part_files p "
                "JOIN part_file_versions v ON v.id=p.active_version_id WHERE p.part_id=1")]
        self.assertEqual(sources, [result["commit_id"], result["commit_id"]])
        self.assertEqual(result["native_state"], "Validated")

    def test_keeping_parallel_master_native_does_not_validate_a_mixed_result(self):
        branch_id, _, _, _ = self._document_commit(validated=True)
        self.publish("parallel-master", b"different Master native", 99)
        preview = self.master.preview_merge(branch_id)
        decisions = {row["id"]: "master" for row in preview["changes"] if row["kind"] == "file" and row["native"]}
        result = self.master.merge(preview, decisions, "Keep Master CAD and branch outputs")
        self.assertEqual(result["native_state"], "Under development")
        self.assertEqual((self.work / "master/case.prt").read_bytes(), b"different Master native")

    def test_same_native_bytes_can_carry_branch_validation_to_an_unvalidated_master(self):
        with self.connection() as conn:
            conn.execute("UPDATE commits SET status='Integrated' WHERE commit_id='original'")
        branch_id = self.designer.create_branch([1], "validate-same-native", include_children=False)
        source = self.root / "case.prt.1"
        source.write_bytes(b"native original")
        with self.connection() as conn:
            conn.execute("""INSERT INTO commits(part_id,filename,designer,message,project_id,status,checked_by,
                commit_id,base_file_name,title,committed_by) VALUES(1,'case.prt.1',1,'Checked',10,
                'Validated',2,'same-native-checked','case','Same native checked',1)""")
        head = self.designer.branch_details(branch_id)["head_snapshot_id"]
        self.designer.check_in_from_commit(branch_id, head, "Checked unchanged geometry",
            [{"filename": source.name, "path": str(source), "sha256": file_hash(source)}], "same-native-checked")
        preview = self.master.preview_merge(branch_id)
        self.assertIn("validation", [value["kind"] for value in preview["changes"]])
        result = self.master.merge(preview, {}, "Carry exact native validation")
        self.assertEqual(result["native_state"], "Validated")
        self.assertEqual(self.master.timeline(1)["master_state"], "Validated")

    def test_multi_item_branch_preserves_each_items_own_validation(self):
        other = self.work / "other.prt.1"
        other.write_bytes(b"other original")
        with self.connection() as conn:
            conn.execute("""INSERT INTO bom(id,type,name,filename,base_file_name,revision,project_id,created,modified)
                VALUES(2,'prt','OTHER','other.prt.1','other','A',10,datetime('now'),datetime('now'))""")
            original = conn.execute("""INSERT INTO commits(part_id,filename,designer,message,project_id,status,commit_id,base_file_name)
                VALUES(2,'other.prt.1',1,'Original other',10,'Approved','original-other','other')""").lastrowid
        self.revisions.get_current_context(2)
        MasterWorkService.publish(self.work, "original-other", [{"filename": other.name, "dest_path": str(other),
                                  "part_ids": [2], "source_order": original}])
        branch_id = self.designer.create_branch([1, 2], "two-independent-items", include_children=False)
        for item_id, name, validated in ((1, "case", True), (2, "other", False)):
            source = self.root / (name + ".prt.2")
            source.write_bytes((name + " updated").encode())
            source_id = "two-items-" + name
            with self.connection() as conn:
                conn.execute("""INSERT INTO commits(part_id,filename,designer,message,project_id,status,checked_by,
                    commit_id,base_file_name,title,committed_by) VALUES(?,?,1,'Update',10,?,?,?,?,?,1)""",
                    (item_id, source.name, "Validated" if validated else "Pending", 2 if validated else None,
                     source_id, name, name + " update"))
            self.designer.checkout_branch_item(item_id, branch_id)
            head = self.designer.branch_details(branch_id)["head_snapshot_id"]
            self.designer.check_in_from_commit(branch_id, head, "Independent update",
                [{"filename": source.name, "path": str(source), "sha256": file_hash(source)}], source_id)
        result = self.master.merge(self.master.preview_merge(branch_id), {}, "Merge mixed validation branch")
        self.assertEqual(result["item_states"], {"1": "Validated", "2": "Under development"})
        self.assertEqual(self.master.timeline(1)["master_state"], "Validated")
        self.assertEqual(self.master.timeline(2)["master_state"], "Under development")

    def test_legacy_validated_branch_recovers_validation_from_checker_and_frozen_push(self):
        save = self.designer._save_snapshot
        def legacy_snapshot(conn, branch_id, parent_id, manifest, note, stamp):
            manifest.pop("validation_receipts", None)
            return save(conn, branch_id, parent_id, manifest, note, stamp)
        with patch.object(self.designer, "_save_snapshot", side_effect=legacy_snapshot):
            branch_id, _, _, _ = self._document_commit(validated=True)
        result = self.master.merge(self.master.preview_merge(branch_id), {}, "Merge earlier validated branch")
        self.assertEqual(result["native_state"], "Validated")

    def test_legacy_validated_branch_imports_documents_left_in_original_commit_store(self):
        save = self.designer._save_snapshot
        def legacy_snapshot(conn, branch_id, parent_id, manifest, note, stamp):
            manifest.pop("validation_receipts", None)
            return save(conn, branch_id, parent_id, manifest, note, stamp)
        with patch.object(self.designer, "_save_snapshot", side_effect=legacy_snapshot):
            branch_id, _, _, _ = self._document_commit(validated=True, attach_before_push=False)
        original = self.designer.branch_details(branch_id)["manifest"]
        result = self.master.merge(self.master.preview_merge(branch_id), {}, "Publish older branch documents")
        self.assertEqual(result["native_state"], "Validated")
        with self.connection() as conn:
            values = conn.execute("SELECT pdf_path,step_path FROM bom WHERE id=1").fetchone()
            sources = [row[0] for row in conn.execute("SELECT v.source_commit_id FROM part_files p "
                "JOIN part_file_versions v ON v.id=p.active_version_id WHERE p.part_id=1")]
        self.assertEqual(Path(values[0]).read_bytes(), b"branch PDF A020")
        self.assertEqual(Path(values[1]).read_bytes(), b"branch STEP A020")
        self.assertEqual(sources, [result["commit_id"], result["commit_id"]])
        self.assertEqual(self.designer.branch_details(branch_id)["manifest"], original)

    def test_failed_validated_merge_rolls_back_master_documents_and_their_database_versions(self):
        branch_id, _, _, _ = self._document_commit(validated=True)
        preview = self.master.preview_merge(branch_id)
        manifest = (self.work / "master/manifest.json").read_bytes()
        with self.connection() as conn:
            versions = [tuple(row) for row in conn.execute("SELECT * FROM part_file_versions ORDER BY id")]
            conn.execute("""CREATE TRIGGER reject_document_merge BEFORE UPDATE OF state ON nexus_branches
                WHEN NEW.state='Merged' BEGIN SELECT RAISE(ABORT,'Simulated document merge failure'); END""")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "Simulated document merge failure"):
            self.master.merge(preview, {}, "Fail after publishing outputs")
        self.assertEqual((self.work / "master/manifest.json").read_bytes(), manifest)
        self.assertFalse(any(path.is_file() for path in (self.work / "master/deliverables").rglob("*")))
        self.assertEqual(self.master.branch_details(branch_id)["state"], "Active")
        with self.connection() as conn:
            self.assertEqual([tuple(row) for row in conn.execute("SELECT * FROM part_file_versions ORDER BY id")], versions)

    def test_older_branch_commit_attachments_remain_accessible_without_rewriting_history(self):
        branch_id, base, head, _ = self._document_commit(attach_before_push=False)
        original = self.designer.branch_details(branch_id)["manifest"]
        self.designer.set_branch_state(branch_id, "Paused")
        listing = self.designer.list_branch_files(1, branch_id, snapshot_id=head)
        pdf = next(entry for entry in listing["files"] if entry["file_type"] == "PDF")
        self.assertEqual(pdf["storage"], "Commit attachment")
        self.designer.export_branch_file(1, branch_id, pdf["key"], self.root / "legacy.pdf", snapshot_id=head)
        self.assertEqual((self.root / "legacy.pdf").read_bytes(), b"branch PDF A020")
        self.assertEqual(self.designer.branch_details(branch_id)["manifest"], original)
        before = self.designer.list_branch_files(1, branch_id, snapshot_id=base)
        self.assertTrue(all(entry.get("source_commit_id") != "branch-documents" for entry in before["files"]))

    def test_branch_file_export_rejects_changed_commit_attachment_and_managed_destination(self):
        branch_id, _, head, store = self._document_commit(attach_before_push=False)
        listing = self.designer.list_branch_files(1, branch_id, snapshot_id=head)
        pdf = next(entry for entry in listing["files"] if entry["file_type"] == "PDF")
        (store / "stored_case.pdf").write_bytes(b"tampered attachment")
        with self.assertRaisesRegex(ValueError, "integrity check"):
            self.designer.export_branch_file(1, branch_id, pdf["key"], self.root / "bad.pdf", snapshot_id=head)
        self.assertFalse((self.root / "bad.pdf").exists())
        with self.assertRaisesRegex(ValueError, "managed project storage"):
            self.designer.export_branch_file(1, branch_id, pdf["key"], self.work / "master/case.pdf", snapshot_id=head)

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

    def test_archive_denied_after_merge_preserves_success_and_retry_cannot_duplicate_publication(self):
        branch_id, _, folder = self.branch()
        self.designer.activate_branch([1], branch_id)
        preview = self.master.preview_merge(branch_id)
        with patch.object(NexusHistoryService, "_archive_merged_branch", side_effect=PermissionError("Access denied")):
            result = self.master.merge(preview, {}, "Merge before archive cleanup")
        self.assertTrue(result["merge_completed"])
        self.assertTrue(result["cleanup_pending"])
        self.assertEqual((self.work / "master/case.prt").read_bytes(), b"branch native")
        self.assertEqual(self.master.branch_details(branch_id)["state"], "Merged")
        self.assertEqual(self.designer.active_branch_map(), {})
        self.assertTrue(folder.exists())
        branch = next(value for value in self.master.timeline(1)["branches"] if value["id"] == branch_id)
        self.assertTrue(branch["cleanup_pending"])
        with self.connection() as conn:
            before = [tuple(row) for row in conn.execute("SELECT * FROM commits ORDER BY id")]
        # A stale Merge action from the old UI may only retry cleanup.
        retried = self.master.merge(preview, {}, "Retry after directory unlock")
        self.assertEqual(retried["commit_id"], result["commit_id"])
        self.assertFalse(retried["cleanup_pending"])
        self.assertFalse(folder.exists())
        self.assertTrue(Path(retried["archive_path"]).is_dir())
        with self.connection() as conn:
            self.assertEqual([tuple(row) for row in conn.execute("SELECT * FROM commits ORDER BY id")], before)

    def test_no_change_merge_reports_cleanup_pending_and_allows_explicit_retry(self):
        branch_id = self.designer.create_branch([1], "matching-master", include_children=False)
        self.designer.workspace(branch_id)
        preview = self.master.preview_merge(branch_id)
        self.assertEqual(preview["changes"], [])
        with patch.object(NexusHistoryService, "_archive_merged_branch", side_effect=PermissionError("Locked folder")):
            result = self.master.merge(preview, {}, "Close identical branch")
        self.assertTrue(result["merge_completed"])
        self.assertTrue(result["cleanup_pending"])
        self.assertIsNone(result["commit_id"])
        result = self.master.cleanup_merged_branch(branch_id)
        self.assertFalse(result["cleanup_pending"])
        self.assertIsNone(result["commit_id"])

    def test_locked_parent_directory_can_archive_contents_without_copying_files(self):
        branch_id, _, folder = self.branch()
        source = self.work / ".nexus/branches" / branch_id
        rename = Path.rename
        def rename_except_parent(path, destination):
            if path == source:
                raise PermissionError("Parent directory is open")
            return rename(path, destination)
        with patch.object(Path, "rename", autospec=True, side_effect=rename_except_parent), \
                patch("core.services.nexus_history_service.shutil.copyfile", side_effect=AssertionError("Must not duplicate files")):
            archive = Path(self.master._archive_merged_branch(self.work, branch_id, "fix-gap"))
        self.assertFalse(source.exists())
        self.assertEqual((archive / folder.relative_to(source) / "case.prt.2").read_bytes(), b"branch native")

    def test_locked_archive_file_is_preserved_and_retry_continues_in_the_same_archive(self):
        branch_id = "locked-file-branch"
        source = self.work / ".nexus/branches" / branch_id
        source.mkdir(parents=True)
        (source / "locked.prt").write_bytes(b"CAD still open in Creo")
        (source / "free.pdf").write_bytes(b"Available PDF")
        rename = Path.rename
        def deny_locked_file(path, destination):
            if path == source or path.name == "locked.prt":
                raise PermissionError("File is in use")
            return rename(path, destination)
        with patch.object(Path, "rename", autospec=True, side_effect=deny_locked_file):
            with self.assertRaises(OSError):
                self.master._archive_merged_branch(self.work, branch_id, "Locked archive")
        archive = self.master._merged_archive_path(self.work, branch_id, "Locked archive")
        self.assertEqual((source / "locked.prt").read_bytes(), b"CAD still open in Creo")
        self.assertEqual((archive / "free.pdf").read_bytes(), b"Available PDF")
        result = self.master._archive_merged_branch(self.work, branch_id, "Locked archive")
        self.assertEqual(Path(result), archive)
        self.assertFalse(source.exists())
        self.assertEqual((archive / "locked.prt").read_bytes(), b"CAD still open in Creo")
        self.assertEqual(len(list(archive.parent.iterdir())), 1)

    def test_archive_path_must_stay_within_branch_storage(self):
        with self.assertRaisesRegex(ValueError, "Invalid branch workspace path"):
            self.master._archive_merged_branch(self.work, ".", "Unsafe")

    def test_retry_cleanup_is_rejected_while_branch_is_still_active(self):
        branch_id, _, folder = self.branch()
        with self.assertRaisesRegex(ValueError, "successfully merged"):
            self.master.cleanup_merged_branch(branch_id)
        self.assertTrue(folder.is_dir())

    def test_archive_collision_preserves_both_files_instead_of_overwriting(self):
        branch_id = "collision-branch"
        source = self.work / ".nexus/branches" / branch_id
        source.mkdir(parents=True)
        (source / "case.prt").write_bytes(b"Remaining workspace file")
        archive = self.master._merged_archive_path(self.work, branch_id, "Collision")
        archive.mkdir(parents=True)
        (archive / "case.prt").write_bytes(b"Previously archived file")
        with self.assertRaisesRegex(OSError, "already exists"):
            self.master._archive_merged_branch(self.work, branch_id, "Collision")
        self.assertEqual((source / "case.prt").read_bytes(), b"Remaining workspace file")
        self.assertEqual((archive / "case.prt").read_bytes(), b"Previously archived file")

    def test_expected_release_versions_must_not_change(self):
        sources = self.master.release_sources(1)
        self.publish("changed", b"new native", 50)
        with self.assertRaisesRegex(ValueError, "file versions changed"):
            self.master.freeze_release(1, "A020", "Late selection",
                                       expected=self.master._master_token(sources))


if __name__ == "__main__":
    unittest.main()
