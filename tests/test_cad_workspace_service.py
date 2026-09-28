import os
import sqlite3
import stat
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

from core.services.cad_workspace_service import CadWorkspaceService


class _ProjectService:
    def __init__(self, projects):
        self.projects = projects

    def get_project_by_id(self, project_id):
        return dict(self.projects.get(int(project_id)) or {})


class _PdmRepo:
    def __init__(self, documents):
        self.documents = {int(row["id"]): dict(row) for row in documents}

    def get_cad_document(self, cad_id):
        row = self.documents.get(int(cad_id))
        return dict(row) if row else None

    def get_cad_document_by_file(self, project_id, file_name):
        logical = CadWorkspaceService.logical_name(file_name).casefold()
        for row in self.documents.values():
            if (
                int(row["project_id"]) == int(project_id)
                and CadWorkspaceService.logical_name(row["file_name"]).casefold() == logical
            ):
                return dict(row)
        return None

    def get_current_cad_iteration(self, cad_id):
        row = self.documents[int(cad_id)]
        return {"primary_path": row.get("primary_path")}

    def list_checked_out_cad_by_workspace(self, workspace_id):
        return [
            dict(row) for row in self.documents.values()
            if row.get("checked_out_by") is not None
            and row.get("checkout_workspace_id") == workspace_id
        ]


class CadWorkspaceServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.project_one = self.base / "project-one"
        self.project_two = self.base / "project-two"
        self.project_one.mkdir()
        self.project_two.mkdir()
        (self.project_one / "housing.prt.4").write_bytes(b"controlled-v4")
        (self.project_two / "housing.prt.9").write_bytes(b"other-project")
        documents = [
            {
                "id": 11, "project_id": 1, "file_name": "housing.prt",
                "latest_creo_file_name": "housing.prt.4", "revision": "A",
                "iteration": 2, "checked_out_by": 7,
            },
            {
                "id": 22, "project_id": 2, "file_name": "housing.prt",
                "latest_creo_file_name": "housing.prt.9", "revision": "A",
                "iteration": 1, "checked_out_by": 7,
            },
        ]
        self.repo = _PdmRepo(documents)
        self.service = CadWorkspaceService(
            self.base / "workspaces",
            pdm_service=SimpleNamespace(repo=self.repo),
            project_service=_ProjectService({
                1: {"id": 1, "working_directory": str(self.project_one)},
                2: {"id": 2, "working_directory": str(self.project_two)},
            }),
        )

    def test_approval_workspace_release_queue_retries_locally(self):
        db_path = self.base / "workspace-queue.db"
        with closing(sqlite3.connect(db_path)) as conn, conn:
            conn.execute("""
                CREATE TABLE cad_workspace_release_queue(
                    id INTEGER PRIMARY KEY,approval_key TEXT,workspace_id TEXT,
                    machine_id TEXT,cad_document_id INTEGER,completed_at TEXT,last_error TEXT
                )
            """)
            conn.executemany(
                """
                INSERT INTO cad_workspace_release_queue(
                    id,approval_key,workspace_id,machine_id,cad_document_id
                ) VALUES(?,?,?,?,?)
                """,
                [
                    (1, "approval-a", "ws-local", self.service.machine_id, 11),
                    (2, "approval-b", "ws-other", "another-machine", 22),
                ],
            )

        attempts = []
        def fail_once(workspace_id, cad_document_id):
            attempts.append((workspace_id, cad_document_id))
            if len(attempts) == 1:
                raise OSError("workspace is temporarily unavailable")

        self.service.release_cad_document = fail_once
        first = self.service.process_pending_approval_releases(str(db_path))
        self.assertEqual(first["failed_ids"], [1])
        self.assertEqual(attempts, [("ws-local", 11)])
        with closing(sqlite3.connect(db_path)) as conn:
            self.assertIn(
                "temporarily unavailable",
                conn.execute(
                    "SELECT last_error FROM cad_workspace_release_queue WHERE id=1"
                ).fetchone()[0],
            )

        second = self.service.process_pending_approval_releases(str(db_path))
        self.assertEqual(second, {"completed": 1, "failed_ids": []})
        with closing(sqlite3.connect(db_path)) as conn:
            local = conn.execute(
                "SELECT completed_at FROM cad_workspace_release_queue WHERE id=1"
            ).fetchone()[0]
            remote = conn.execute(
                "SELECT completed_at FROM cad_workspace_release_queue WHERE id=2"
            ).fetchone()[0]
        self.assertTrue(local)
        self.assertIsNone(remote)

    def test_named_workspace_materializes_and_finds_latest_modified_iteration(self):
        workspace = self.service.create_workspace("Gearbox redesign")
        copied = self.service.materialize_cad_document(workspace["id"], 11)
        self.assertEqual(Path(copied["path"]).read_bytes(), b"controlled-v4")

        work_path = Path(workspace["path"])
        (work_path / "housing.prt.5").write_bytes(b"modified-v5")
        rows = self.service.scan_workspace(workspace["id"], 1, 7)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["filename"], "housing.prt.5")
        self.assertEqual(rows[0]["status"], "READY")
        self.assertTrue(rows[0]["selectable"])

    def test_workspace_scan_blocks_checkout_when_approved_iteration_advanced(self):
        workspace = self.service.create_workspace("Stale baseline")
        self.service.materialize_cad_document(workspace["id"], 11)
        self.repo.documents[11]["iteration"] = 3

        rows = self.service.scan_workspace(workspace["id"], 1, 7)

        self.assertEqual(rows[0]["status"], "OUT_OF_DATE")
        self.assertFalse(rows[0]["selectable"])
        self.assertEqual(rows[0]["baseline_cad_iteration"], 2)

    def test_reconcile_archives_local_edits_and_refreshes_read_only_approved_copy(self):
        workspace = self.service.create_workspace("Reconcile draft")
        copied = self.service.materialize_cad_document(workspace["id"], 11)
        Path(copied["path"]).write_bytes(b"my local draft")
        (self.project_one / "housing.prt.5").write_bytes(b"approved-v5")
        self.repo.documents[11].update({
            "latest_creo_file_name": "housing.prt.5",
            "revision": "B",
            "iteration": 1,
            "checked_out_by": None,
        })

        result = self.service.reconcile_cad_document(workspace["id"], 11)

        latest_path = Path(result["path"])
        archive_file = self.service.workspace_path(workspace["id"]) / result[
            "archived_draft_files"
        ][0]["path"]
        self.assertEqual(latest_path.read_bytes(), b"approved-v5")
        self.assertEqual(archive_file.read_bytes(), b"my local draft")
        self.assertFalse(bool(latest_path.stat().st_mode & stat.S_IWRITE))
        self.assertEqual(result["baseline_cad_revision"], "B")
        self.assertEqual(result["baseline_cad_iteration"], 1)

        rows = self.service.scan_workspace(workspace["id"], 1, 7)
        self.assertEqual([row["filename"] for row in rows], ["housing.prt.5"])

    def test_unversioned_creo_file_is_visible_as_unmapped(self):
        workspace = self.service.create_workspace("Unmapped local files")
        path = Path(workspace["path"]) / "scratch.prt"
        path.write_bytes(b"#UGC:2 PART\nlocal-only")

        rows = self.service.scan_workspace(workspace["id"], 1, 7)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["filename"], "scratch.prt")
        self.assertEqual(rows[0]["status"], "UNMAPPED")
        self.assertFalse(rows[0]["selectable"])

    def test_invalid_creo_extension_is_not_offered_as_new_cad(self):
        workspace = self.service.create_workspace("Invalid local file")
        path = Path(workspace["path"]) / "not_a_part.prt"
        path.write_bytes(b"SketchUp STL payload")

        rows = self.service.scan_workspace(workspace["id"], 1, 7)

        self.assertEqual(rows[0]["status"], "INVALID_NATIVE_FILE")
        self.assertFalse(rows[0]["selectable"])

    def test_workspace_is_not_project_bound_but_blocks_flat_name_collision(self):
        workspace = self.service.create_workspace("Mixed project work")
        self.service.materialize_cad_document(workspace["id"], 11)
        with self.assertRaisesRegex(ValueError, "another CAD Document"):
            self.service.materialize_cad_document(workspace["id"], 22)

    def test_retrieved_file_is_read_only_until_made_editable(self):
        workspace = self.service.create_workspace("Read-only review")
        copied = self.service.materialize_cad_document(
            workspace["id"], 11, editable=False
        )
        copied_path = Path(copied["path"])
        self.assertFalse(bool(copied_path.stat().st_mode & stat.S_IWRITE))

        changed = self.service.set_document_files_editable(
            workspace["id"], 11, True
        )
        self.assertEqual(changed, [str(copied_path)])
        self.assertTrue(bool(copied_path.stat().st_mode & stat.S_IWRITE))

        self.service.release_cad_document(workspace["id"], 11)
        self.assertFalse(bool(copied_path.stat().st_mode & stat.S_IWRITE))

    def test_local_edit_intent_preserves_changes_when_checkout_adopts_them(self):
        workspace = self.service.create_workspace("Local intent")
        copied = self.service.materialize_cad_document(workspace["id"], 11, editable=False)
        copied_path = Path(copied["path"])
        intent = self.service.set_edit_intent(
            workspace["id"], 11, 7, "Continue locally"
        )
        copied_path.write_bytes(b"local-edit")
        adopted = self.service.materialize_cad_document(
            workspace["id"],
            11,
            preserve_local_changes=True,
            editable=True,
        )

        self.assertTrue(intent["enabled"])
        self.assertIsNone(self.service.get_edit_intent(workspace["id"], 11))
        self.assertEqual(Path(adopted["path"]).read_bytes(), b"local-edit")
        self.assertTrue(adopted["stage_ready_after_checkout"])
        self.assertTrue(bool(Path(adopted["path"]).stat().st_mode & stat.S_IWRITE))
        rows = self.service.scan_workspace(workspace["id"], 1, 7)
        self.assertTrue(rows[0]["modified"])

    def test_delete_blocks_active_checkout_and_then_requires_force_for_files(self):
        workspace = self.service.create_workspace("Delete guard")
        self.repo.documents[11]["checkout_workspace_id"] = workspace["id"]
        self.service.materialize_cad_document(workspace["id"], 11)
        with self.assertRaisesRegex(ValueError, "active CAD checkouts"):
            self.service.delete_workspace(workspace["id"], force=True)

        self.repo.documents[11]["checked_out_by"] = None
        self.repo.documents[11]["checkout_workspace_id"] = None
        self.service.release_cad_document(workspace["id"], 11)
        with self.assertRaisesRegex(ValueError, "force deletion"):
            self.service.delete_workspace(workspace["id"])
        self.service.delete_workspace(workspace["id"], force=True)
        self.assertFalse(Path(workspace["path"]).exists())


if __name__ == "__main__":
    unittest.main()
