from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock

from core.repositories.bom_repository import BomRepository
from core.services.diag_service import DiagService
from core.working_directory_inventory import WorkingDirectoryInventory


class WorkingDirectoryInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database = self.root / "fixture.db"
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute("CREATE TABLE bom(id PRIMARY KEY,project_id,filename,drawing,base_file_name,base_drw_name,status,pdf_path,step_path)")
            conn.execute("INSERT INTO bom VALUES(1,7,'a.prt.9',NULL,'a.prt',NULL,'Released','drawing.pdf','export.stp')")
        self.repo = BomRepository.__new__(BomRepository)
        self.repo.db_name = str(self.database)
        self.diag = DiagService.__new__(DiagService)
        self.diag.session = SimpleNamespace(project_id=7)
        self.diag.repo = Mock()
        self.diag.repo.get_forced_integrated_filenames.return_value = []
        self.diag.bom_repo = self.repo
        self.repo.get_all = lambda _: self.rows()
        for filename in ("a.prt.9", "a.prt.10", "ab.prt.100", "drawing.pdf", "export.stp"):
            (self.root / filename).write_bytes(b"fixture")

    def rows(self):
        with closing(self.repo.get_conn()) as conn:
            return [SimpleNamespace(**dict(row)) for row in conn.execute("SELECT * FROM bom")]

    def test_numeric_versions_exact_identity_and_no_writes_for_unchanged_rows(self):
        self.repo.update_bom_file_names_batch = Mock(wraps=self.repo.update_bom_file_names_batch)
        result = self.diag.sync_bom_files(str(self.root))
        self.assertEqual(result, [(1, "outdated_file", "a.prt.9")])
        self.repo.update_bom_file_names_batch.assert_not_called()
        self.diag.repo.get_forced_integrated_filenames.return_value = ["a.prt.9"]
        self.assertEqual(self.diag.sync_bom_files(str(self.root)), [])
        (self.root / "a.prt.10").unlink()
        self.diag.repo.get_forced_integrated_filenames.return_value = []
        self.assertEqual(self.diag.sync_bom_files(str(self.root)), [])

    def test_new_scan_detects_external_deletion_and_preserves_saved_state(self):
        (self.root / "drawing.pdf").unlink()
        findings = self.diag.sync_bom_files(str(self.root))
        self.assertIn((1, "missing_pdf", "drawing.pdf"), findings)
        row = self.rows()[0]
        self.assertEqual((row.status, row.pdf_path, row.step_path), ("Released", "drawing.pdf", "export.stp"))

    def test_changed_metadata_batched_and_concurrent_filename_change_not_overwritten(self):
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute("UPDATE bom SET base_file_name='incorrect'")
        self.diag.sync_bom_files(str(self.root))
        self.assertEqual(self.rows()[0].base_file_name, "a.prt")
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute("UPDATE bom SET filename='new.prt.1',base_file_name='new.prt'")
        self.assertEqual(self.repo.update_bom_file_names_batch([(1, 'a.prt.9', None, 'a.prt', None)], 7), 0)
        self.assertEqual(self.rows()[0].base_file_name, 'new.prt')

    def test_cancelled_scan_and_nested_deliverable_paths(self):
        self.repo.update_bom_file_names_batch = Mock()
        self.assertEqual(self.diag.sync_bom_files(str(self.root), cancel_cb=lambda: True), [])
        self.repo.update_bom_file_names_batch.assert_not_called()
        folder = self.root / "vault"
        folder.mkdir()
        (folder / "drawing.pdf").write_bytes(b"fixture")
        index = WorkingDirectoryInventory(self.root)
        self.assertTrue(index.exists("vault/drawing.pdf"))
        self.assertTrue(index.exists(str(folder / "drawing.pdf")))
        self.assertFalse(index.exists("vault/missing.pdf"))


if __name__ == "__main__":
    unittest.main()
