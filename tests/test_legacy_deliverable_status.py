import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import test_master_work_service as master_tests


class LegacyDeliverableStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "existing.pdf"
        self.path.write_bytes(b"existing deliverable")
        self.page = master_tests.BomProvenanceIntegrationTests.page_methods()
        self.page._has_published_master_for_part = lambda part_id: False
        self.commit = SimpleNamespace(id=10, commit_id="old", status="Approved",
                                      committed_at="2026-01-01 10:00:00", merged_at="2026-01-03 10:00:00")
        self.page._part_change_commits = lambda part_id: [self.commit]
        self.page._legacy_doc_path = lambda *args: ""
        self.page._resolve_file_path = lambda path: path
        self.page.part_doc_ack_service = Mock()
        self.page.part_doc_ack_service.get_ack.return_value = None
        self.version = SimpleNamespace(id=5, created_at="2026-01-01 09:00:00", original_filename="existing.step",
                                       source_commit_id=None, deleted_at=None)
        self.page.part_file_service = SimpleNamespace(
            get_active_version=lambda file_id: self.version,
            list_versions=lambda file_id: [self.version],
            resolve_version_path=lambda version: str(self.path),
        )

    def state(self, kind="PDF", attachments=True):
        return self.page._doc_indicator_state(1, kind, [SimpleNamespace(id=3, file_type=kind)] if attachments else [])

    def test_previously_acknowledged_pdf_and_step_keep_their_status(self):
        ack = {"acknowledged_against": self.page._commit_ack_token(self.commit)}
        self.page.part_doc_ack_service.get_ack.return_value = ack
        for kind in ("PDF", "STEP"):
            with self.subTest(kind=kind):
                self.assertEqual(self.state(kind)["state"], "ack")
        self.assertEqual(ack, {"acknowledged_against": self.page._commit_ack_token(self.commit)})
        self.page.part_doc_ack_service.mark_up_to_date.assert_not_called()

    def test_old_commit_token_remains_valid_when_merged_timestamp_was_omitted(self):
        self.page.part_doc_ack_service.get_ack.return_value = {
            "acknowledged_against": "commit:10:old:2026-01-01 10:00:00"}
        self.assertEqual(self.state()["state"], "ack")

    def test_timestamp_acknowledgements_remain_valid(self):
        self.page.part_doc_ack_service.get_ack.return_value = {"acknowledged_against": "2026-01-04 00:00:00"}
        self.assertEqual(self.state()["state"], "ack")

    def test_timestamp_acknowledgement_before_merge_still_covers_the_same_native(self):
        self.page.part_doc_ack_service.get_ack.return_value = {"acknowledged_against": "2026-01-02 00:00:00"}
        self.assertEqual(self.state()["state"], "ack")

    def test_current_legacy_file_does_not_require_new_provenance(self):
        self.version.created_at = "2026-01-05 00:00:00"
        self.assertEqual(self.state()["state"], "ok")

    def test_existing_outdated_and_missing_statuses_are_preserved(self):
        self.assertEqual(self.state()["state"], "bad")
        self.assertIn("newer commit", self.state()["tooltip"])
        self.path.unlink()
        self.assertEqual(self.state()["state"], "bad")
        self.assertIn("missing", self.state()["tooltip"])

    def test_legacy_path_without_vault_attachment_keeps_acknowledgement(self):
        self.page._legacy_doc_path = lambda *args: str(self.path)
        self.page.part_doc_ack_service.get_ack.return_value = {
            "acknowledged_against": self.page._commit_ack_token(self.commit)}
        self.assertEqual(self.state(attachments=False)["state"], "ack")

    def test_compared_step_preserves_previous_current_status(self):
        self.commit.step_file_path = "existing.step"
        self.assertEqual(self.state("STEP")["state"], "ok")

    def test_wrong_commit_acknowledgement_does_not_cover_a_newer_change(self):
        self.page.part_doc_ack_service.get_ack.return_value = {"acknowledged_against": "commit:9:previous:2026-01-01"}
        self.assertEqual(self.state()["state"], "bad")

    def test_new_explicit_master_acknowledgement_is_still_respected(self):
        self.page._has_published_master_for_part = lambda part_id: True
        self.page.part_doc_ack_service.get_ack.return_value = {
            "acknowledged_against": self.page._commit_ack_token(self.commit)}
        self.assertEqual(self.page._doc_indicator_state(1, "PDF", [SimpleNamespace(id=3, file_type="PDF")], self.commit)["state"], "ack")

    def test_new_workflow_is_enabled_only_for_explicitly_published_parts(self):
        import ast
        from types import MethodType
        from core.services.master_work_service import MasterWorkService
        source = Path(__file__).resolve().parents[1] / "pages" / "bom_page.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        page_class = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "BomPage")
        method = next(node for node in page_class.body if isinstance(node, ast.FunctionDef) and node.name == "_has_published_master_for_part")
        namespace = {"MasterWorkService": MasterWorkService}
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), "exec"), namespace)
        self.page.session = SimpleNamespace(project_id=7)
        self.page.project_service = Mock()
        self.page.project_service.get_project_by_id.return_value = {"working_directory": self.temp.name}
        has_published = MethodType(namespace["_has_published_master_for_part"], self.page)
        self.assertFalse(has_published(1))
        native = Path(self.temp.name) / "part.prt.1"
        native.write_bytes(b"native")
        MasterWorkService.publish(self.temp.name, "new", [{"filename": native.name, "dest_path": str(native), "part_ids": [2]}])
        self.assertFalse(has_published(1))
        self.assertTrue(has_published(2))


if __name__ == "__main__":
    unittest.main()
