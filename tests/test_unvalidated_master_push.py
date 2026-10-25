import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.repositories.permission_repository import PermissionRepository
from core.services.commit_service import CommitService
from core.services.master_work_service import (
    MasterWorkService, master_commit_for_part, native_work_state,
)


class UnvalidatedMasterPushTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db_path = self.root / "permissions.db"
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE users(id INTEGER PRIMARY KEY, is_admin INTEGER);
                CREATE TABLE roles(id INTEGER PRIMARY KEY, name TEXT);
                CREATE TABLE user_roles(user_id INTEGER, role_id INTEGER, project_id INTEGER);
                INSERT INTO roles VALUES(1,'designer'),(2,'checker'),(3,'master'),(4,'admin');
                INSERT INTO users VALUES(1,0),(2,0),(3,0),(4,0),(5,1),(6,0);
                INSERT INTO user_roles VALUES(1,1,7),(2,2,7),(3,3,7),(4,4,7),(6,3,8);
            """)
        conn.close()
        self.permissions = PermissionRepository(self.db_path)
        # SQLite context managers do not close connections on Windows.
        from contextlib import closing
        connect = self.permissions.get_conn
        self.permissions.get_conn = lambda: closing(connect())
        self.service = object.__new__(CommitService)
        self.service.session = SimpleNamespace(project_id=7, user_id=3, is_active=lambda: True)
        self.service.commit_repository = Mock()
        self.rows = [dict(id=10, project_id=7, commit_id="draft", status="Pending",
                          filename="part.prt.5", part_id=11, cad_document_id=21)]
        self.service.commit_repository.get_rows_by_commit_id.return_value = self.rows
        self.service.project_service = Mock()
        self.service.project_service.get_project_by_id.return_value = {"working_directory": str(self.root)}
        frozen = self.root / "commits" / "frozen" / "part.prt.5"
        frozen.parent.mkdir(parents=True)
        frozen.write_bytes(b"new native work")
        self.frozen = frozen
        self.service.snapshot_service = Mock()
        self.service.snapshot_service.verify_pending_snapshot.return_value = {
            "part.prt.5": {"path": "frozen/part.prt.5", "sha256": hashlib.sha256(frozen.read_bytes()).hexdigest()},
        }
        self.service.emit_project_event = Mock()
        self.session_patch = patch("core.services.permission_decorators.SessionManager", return_value=self.service.session)
        self.permission_patch = patch("core.services.permission_decorators.PermissionRepository", return_value=self.permissions)
        self.session_patch.start()
        self.permission_patch.start()
        self.addCleanup(self.session_patch.stop)
        self.addCleanup(self.permission_patch.stop)

    def test_master_and_both_admin_forms_can_push_without_validation(self):
        approved_pdf = self.root / "part.pdf"
        approved_pdf.write_bytes(b"old validated PDF")
        approved_step = self.root / "part.step"
        approved_step.write_bytes(b"old validated STEP")
        for user_id in (3, 4, 5):
            with self.subTest(user_id=user_id):
                self.service.session.user_id = user_id
                result = self.service.push_unvalidated_to_master("draft")
                self.assertEqual(result["native_state"], "Under development")
                self.assertEqual(result["status"], "Integrated")
                self.service.commit_repository.mark_integrated_under_development.assert_called_with("draft", 7, user_id, [10])
                self.assertEqual((self.root / "master" / "part.prt").read_bytes(), self.frozen.read_bytes())
                manifest = MasterWorkService.read_manifest(self.root)
                self.assertEqual(manifest["files"]["part.prt"]["published_by"], user_id)
                self.service.commit_repository.validate.assert_not_called()
                self.service.commit_repository.merge_commit.assert_not_called()
        self.assertEqual(approved_pdf.read_bytes(), b"old validated PDF")
        self.assertEqual(approved_step.read_bytes(), b"old validated STEP")
        self.assertEqual(self.service.emit_project_event.call_args.args[0], "commit.pushed_under_development")

    def test_commit_can_be_pushed_to_selected_nexus_branch(self):
        self.service.commit_repository.db_name = self.db_path
        branch_service = Mock()
        branch_service.branch_details.return_value = {"head_snapshot_id": "head-1"}
        branch_service.check_in_from_commit.return_value = "head-2"
        with patch("core.services.nexus_history_service.NexusHistoryService",
                   return_value=branch_service):
            result = self.service.push_commit_to_nexus_branch("draft", "branch-1")
        self.assertEqual(result["branch_id"], "branch-1")
        self.assertEqual(result["branch_snapshot_id"], "head-2")
        self.assertEqual(result["status"], "Branched")
        branch_service.check_in_from_commit.assert_called_once()
        self.assertEqual(self.service.emit_project_event.call_args.args[0], "commit.pushed_to_branch")

    def test_designers_checkers_and_other_project_masters_cannot_push(self):
        for user_id in (1, 2, 6):
            with self.subTest(user_id=user_id):
                self.service.session.user_id = user_id
                with self.assertRaises(PermissionError):
                    self.service.push_unvalidated_to_master("draft")
        self.service.snapshot_service.verify_pending_snapshot.assert_not_called()
        self.assertFalse((self.root / "master").exists())

    def test_other_project_submission_is_rejected(self):
        self.rows[0]["project_id"] = 8
        with self.assertRaisesRegex(ValueError, "active project"):
            self.service.push_unvalidated_to_master("draft")
        self.assertFalse((self.root / "master").exists())

    def test_non_pending_submissions_cannot_bypass_approval(self):
        for status in ("Validated", "Approved", "Rejected", "Withdrawn"):
            with self.subTest(status=status):
                self.rows[0]["status"] = status
                with self.assertRaisesRegex(ValueError, "wholly Pending"):
                    self.service.push_unvalidated_to_master("draft")
        self.assertFalse((self.root / "master").exists())

    def test_modified_native_is_not_published(self):
        self.frozen.write_bytes(b"changed after sealing")
        with self.assertRaisesRegex(ValueError, "Frozen native file changed"):
            self.service.push_unvalidated_to_master("draft")
        self.assertFalse((self.root / "master" / "manifest.json").exists())
        self.service.emit_project_event.assert_not_called()

    def test_whole_submission_and_shared_item_associations_are_published(self):
        drawing = self.frozen.with_name("part.drw.3")
        drawing.write_bytes(b"new drawing")
        self.rows.extend([
            dict(id=11, project_id=7, commit_id="draft", status="Pending", filename="part.prt.5", part_id=12, cad_document_id=21),
            dict(id=12, project_id=7, commit_id="draft", status="Pending", filename="part.drw.3", part_id=11, cad_document_id=22),
        ])
        self.service.snapshot_service.verify_pending_snapshot.return_value["part.drw.3"] = {
            "path": "frozen/part.drw.3", "sha256": hashlib.sha256(drawing.read_bytes()).hexdigest(),
        }
        result = self.service.push_unvalidated_to_master("draft")
        self.assertEqual(result["affected_part_ids"], [11, 12])
        self.assertEqual(result["affected_cad_document_ids"], [21, 22])
        self.assertEqual((self.root / "master" / "part.drw").read_bytes(), b"new drawing")
        manifest = MasterWorkService.read_manifest(self.root)
        self.assertEqual(manifest["files"]["part.prt"]["part_ids"], [11, 12])
        self.assertEqual(len(self.service.snapshot_service.verify_pending_snapshot.call_args.args[3]), 3)

    def test_published_pending_snapshot_cannot_be_extended_or_replaced(self):
        self.service.push_unvalidated_to_master("draft")
        with self.assertRaisesRegex(ValueError, "published snapshots cannot be replaced or extended"):
            self.service._assert_master_submissions_mutable(["draft"])
        self.service._assert_master_submissions_mutable(["new-unpublished-commit"])

    def test_bom_uses_pushed_native_and_ignores_newer_unpushed_submission(self):
        self.service.push_unvalidated_to_master("draft")
        pushed = SimpleNamespace(id=10, commit_id="draft", status="Pending", committed_at="2026-10-14")
        unpushed = SimpleNamespace(id=20, commit_id="private", status="Pending", committed_at="2026-10-15")
        manifest = MasterWorkService.read_manifest(self.root)
        current = master_commit_for_part([pushed, unpushed], manifest, 11)
        self.assertEqual(current.commit_id, "draft")
        self.assertEqual(native_work_state(current), "Under development")
        pushed.status = "Validated"
        self.assertEqual(native_work_state(master_commit_for_part([pushed, unpushed], manifest, 11)), "Validated")

    def test_unpushed_pending_work_does_not_replace_approved_bom_state(self):
        approved = SimpleNamespace(id=1, commit_id="approved", status="Approved", committed_at="2026-10-13")
        unpushed = SimpleNamespace(id=20, commit_id="private", status="Pending", committed_at="2026-10-15")
        self.assertIs(master_commit_for_part([approved, unpushed], {}, 11), approved)

    def test_approval_of_older_work_does_not_overwrite_newer_pushed_native(self):
        self.service.push_unvalidated_to_master("draft")
        older = self.root / "older.prt"
        older.write_bytes(b"old approved native")
        MasterWorkService.publish(self.root, "old", [{
            "filename": "part.prt.1", "dest_path": str(older), "source_order": 1, "part_ids": [11],
        }], preserve_newer=True)
        self.assertEqual((self.root / "master" / "part.prt").read_bytes(), self.frozen.read_bytes())
        self.assertEqual(MasterWorkService.read_manifest(self.root)["files"]["part.prt"]["source_commit_id"], "draft")

    def test_approved_publication_uses_exact_snapshot_and_preserves_source(self):
        from core.services.merge_service import MergeService
        merge = object.__new__(MergeService)
        merge.working_dir = str(self.root)
        merge.commits_dir = str(self.root / "commits")
        merge.session = self.service.session
        commit = SimpleNamespace(id=10, filename="part.prt.5", commit_id="draft", part_id=11, cad_document_id=21)
        snapshot = self.service.snapshot_service.verify_pending_snapshot.return_value["part.prt.5"]
        merge._publish_approved_master([commit], {10: snapshot})
        self.assertEqual((self.root / "master" / "part.prt").read_bytes(), self.frozen.read_bytes())
        self.assertTrue(self.frozen.exists())

    def test_pending_admin_ui_push_calls_development_action_without_approval_prompt(self):
        import ast
        from types import MethodType
        page_path = Path(__file__).resolve().parents[1] / "pages" / "commit_page.py"
        tree = ast.parse(page_path.read_text(encoding="utf-8"))
        page_type = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "CommitPage")
        method = next(node for node in page_type.body if isinstance(node, ast.FunctionDef) and node.name == "push_to_master")
        prompt = Mock()
        warning = Mock()
        warning.Warning, warning.Yes, warning.Cancel = 2, 1, 4
        warning.return_value.exec_.return_value = warning.Yes
        namespace = {"QMessageBox": warning, "QInputDialog": prompt}
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(page_path), "exec"), namespace)
        commit_service = Mock()
        commit_service.push_unvalidated_to_master.return_value = {"affected_part_ids": [11], "affected_cad_document_ids": [21]}
        commit_service.get_commit_history.return_value = []
        commit_service.get_pending_commits_grouped.return_value = []
        ui = SimpleNamespace(
            session=SimpleNamespace(project_id=7, is_admin=True), is_designer=False,
            commit_service=commit_service, merge_service=Mock(),
            _run_processing_task=lambda label, work, after, error: work(),
        )
        ui.session.user_id = 5
        MethodType(namespace["push_to_master"], ui)({"commit_id": "draft", "status": "Pending", "project_id": 7})
        commit_service.push_unvalidated_to_master.assert_called_once_with("draft")
        ui.merge_service.excute_merge_by_commit_id.assert_not_called()
        prompt.getMultiLineText.assert_not_called()


if __name__ == "__main__":
    unittest.main()
