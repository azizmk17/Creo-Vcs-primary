import os
import tempfile
import unittest
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

    def test_workspace_is_not_project_bound_but_blocks_flat_name_collision(self):
        workspace = self.service.create_workspace("Mixed project work")
        self.service.materialize_cad_document(workspace["id"], 11)
        with self.assertRaisesRegex(ValueError, "another CAD Document"):
            self.service.materialize_cad_document(workspace["id"], 22)

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
