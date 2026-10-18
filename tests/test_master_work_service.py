import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.services.master_work_service import (
    MasterWorkService, deliverable_provenance, native_work_state,
)


class MasterWorkTests(unittest.TestCase):
    def test_first_master_seeds_latest_native_dependencies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "assembly.asm.1").write_bytes(b"older")
            (root / "assembly.asm.2").write_bytes(b"latest")
            (root / "drawing.pdf").write_bytes(b"export")
            source = root / "part.prt.1"
            source.write_bytes(b"part")
            MasterWorkService.publish(root, "first", [{"filename": source.name, "dest_path": str(source)}])
            self.assertEqual((root / "master" / "assembly.asm").read_bytes(), b"latest")
            self.assertFalse((root / "master" / "drawing.pdf").exists())

    def test_another_publication_cannot_overwrite_an_active_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            master = Path(directory) / "master"
            master.mkdir()
            lock = master / ".publish.lock"
            lock.write_text("active")
            with self.assertRaisesRegex(ValueError, "publication is active"):
                MasterWorkService.publish(directory, "second", [])
            self.assertTrue(lock.exists())

    def test_pending_native_replaces_master_without_touching_approved_or_exports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            approved = root / "part.prt.1"
            approved.write_bytes(b"approved")
            source = root / "pending.prt.5"
            source.write_bytes(b"draft")
            item = {"filename": "part.prt.5", "dest_path": str(source), "part_id": 4}
            MasterWorkService.publish(root, "draft-1", [item])
            self.assertEqual((root / "master" / "part.prt").read_bytes(), b"draft")
            self.assertEqual(approved.read_bytes(), b"approved")
            source.write_bytes(b"newer draft")
            MasterWorkService.publish(root, "draft-2", [item])
            manifest = json.loads((root / "master" / "manifest.json").read_text())
            self.assertEqual(manifest["files"]["part.prt"]["source_commit_id"], "draft-2")
            self.assertEqual((root / "master" / "part.prt").read_bytes(), b"newer draft")
            self.assertFalse(list((root / "master").glob("*.pdf")))

    def test_failed_group_publication_restores_previous_master_and_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.prt"
            source.write_bytes(b"first")
            item = {"filename": "part.prt.1", "dest_path": str(source)}
            MasterWorkService.publish(root, "first", [item])
            manifest = (root / "master" / "manifest.json").read_bytes()
            source.write_bytes(b"second")
            original = os.replace

            def fail_manifest(src, dst):
                if Path(src).name == "manifest.json":
                    raise OSError("disk failure")
                return original(src, dst)

            with patch("core.services.master_work_service.os.replace", side_effect=fail_manifest):
                with self.assertRaisesRegex(OSError, "disk failure"):
                    MasterWorkService.publish(root, "second", [item])
            self.assertEqual((root / "master" / "part.prt").read_bytes(), b"first")
            self.assertEqual((root / "master" / "manifest.json").read_bytes(), manifest)
            self.assertFalse((root / "master" / ".publish.lock").exists())

    def test_newer_native_keeps_old_validated_deliverables_identified(self):
        current = SimpleNamespace(commit_id="v5", status="Pending")
        source = SimpleNamespace(commit_id="v4", status="Approved")
        version = SimpleNamespace(source_commit_id="v4")
        for kind in ("PDF", "STEP"):
            result = deliverable_provenance(kind, version, current, source)
            self.assertEqual(result["state"], "previous")
            self.assertIn("v4", result["tooltip"])
            self.assertIn("v5", result["tooltip"])
        self.assertEqual(native_work_state(current), "Under development")

    def test_validated_native_and_matching_exports_are_current(self):
        current = SimpleNamespace(commit_id="v5", status="Validated")
        result = deliverable_provenance("PDF", SimpleNamespace(source_commit_id="v5"), current, current)
        self.assertEqual(result["state"], "ok")
        self.assertEqual(native_work_state(current), "Validated")

    def test_matching_unvalidated_export_is_not_ready(self):
        current = SimpleNamespace(commit_id="v5", status="Pending")
        result = deliverable_provenance("STEP", SimpleNamespace(source_commit_id="v5"), current, current)
        self.assertEqual(result["state"], "unverified")

    def test_unlinked_legacy_export_cannot_be_current(self):
        self.assertEqual(deliverable_provenance("PDF", None, None, None)["state"], "unverified")


class BomProvenanceIntegrationTests(unittest.TestCase):
    @staticmethod
    def page_methods():
        # Exercise the actual page logic without requiring a Qt installation.
        import ast
        from datetime import datetime
        from types import MethodType
        source = Path(__file__).resolve().parents[1] / "pages" / "bom_page.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        page = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "BomPage")
        names = {"_doc_indicator_state", "_latest_change_commit_for_part", "_parse_dt",
                 "_ack_covers_commit", "_commit_ack_token", "_commit_effective_dt",
                 "_legacy_doc_indicator_state", "_legacy_latest_change_commit_for_part",
                 "_has_published_master_for_part"}
        methods = [node for node in page.body if isinstance(node, ast.FunctionDef) and node.name in names]
        namespace = {"datetime": datetime, "deliverable_provenance": deliverable_provenance,
                     "safe_exists": os.path.exists, "os": os, "MasterWorkService": MasterWorkService}
        exec(compile(ast.Module(body=methods, type_ignores=[]), str(source), "exec"), namespace)
        stub = SimpleNamespace()
        for name in names:
            setattr(stub, name, MethodType(namespace[name], stub))
        stub._has_published_master_for_part = lambda part_id: True
        return stub

    def test_bom_retains_previous_validated_export_over_active_draft(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "export.pdf"
            path.write_bytes(b"export")
            previous = SimpleNamespace(id=1, source_commit_id="v4", deleted_at=None)
            draft = SimpleNamespace(id=2, source_commit_id="v5", deleted_at=None)
            old = SimpleNamespace(id=4, commit_id="v4", status="Approved", committed_at="2026-10-01", merged_at="2026-10-14")
            current = SimpleNamespace(id=5, commit_id="v5", status="Pending", committed_at="2026-10-13")
            page = self.page_methods()
            page._part_change_commits = lambda part_id: [old, current]
            page._legacy_doc_path = lambda *args: ""
            page.part_file_service = SimpleNamespace(
                get_active_version=lambda file_id: draft,
                list_versions=lambda file_id: [previous, draft],
                resolve_version_path=lambda version: str(path))
            latest = page._latest_change_commit_for_part(1)
            self.assertEqual(latest.commit_id, "v5")
            result = page._doc_indicator_state(1, "PDF", [SimpleNamespace(id=1, file_type="PDF")], latest)
            self.assertEqual(result["state"], "previous")
            self.assertEqual(result["source_commit_id"], "v4")

    def test_diag_repository_can_load_native_commit_identity(self):
        import sqlite3
        from core.repositories.diag_repository import DiagRepository
        with tempfile.TemporaryDirectory() as directory:
            db = str(Path(directory) / "diag.db")
            with sqlite3.connect(db) as connection:
                connection.execute("CREATE TABLE commits(id INTEGER, commit_id TEXT, title TEXT, part_id INTEGER, type TEXT, filename TEXT, status TEXT, project_id INTEGER, committed_at TEXT)")
                connection.execute("INSERT INTO commits VALUES(1,'v1','work',2,'Cad','part.prt.1','Pending',3,'2026-10-14')")
            connection.close()
            commit = DiagRepository(db).get_all_commits(3)[0]
            self.assertEqual(commit.type, "Cad")
            self.assertEqual(commit.commit_id, "v1")


if __name__ == "__main__":
    unittest.main()
