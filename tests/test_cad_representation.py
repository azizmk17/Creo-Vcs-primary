import os
import gc
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace

from core.models.bom_model import Bom
from core.repositories.bom_repository import BomRepository
from core.services.bom_service import BomService
from core.services.package_export_service import PackageExportService


class _RevisionRepo:
    def ensure_bom(self, bom_id, created_by=None):
        return {"bom_id": bom_id, "created_by": created_by}


class _NoChildren:
    def get_children(self, _part_id):
        return []


class _NoFiles:
    def list_attachments(self, _part_id):
        raise AssertionError("CAD representations must not be inspected for delivery files")


class CadRepresentationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "representation.db")
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE bom (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL, name TEXT NOT NULL, part_number TEXT,
                    drawing_number TEXT, aes_number TEXT, filename TEXT, drawing TEXT,
                    base_file_name TEXT, base_drw_name TEXT, material TEXT, weight TEXT,
                    notes TEXT, pdf_path TEXT, step_path TEXT, revision TEXT DEFAULT 'A',
                    lifecycle_state TEXT DEFAULT 'WIP', released_by INTEGER,
                    released_at TEXT, status TEXT DEFAULT 'Design', created TEXT,
                    modified TEXT, project_id INTEGER, locked INTEGER DEFAULT 0,
                    current_revision_id INTEGER, current_iteration_id INTEGER,
                    pending_revision_code TEXT, classification TEXT DEFAULT 'PHYSICAL',
                    default_ebom_behavior TEXT DEFAULT 'NORMAL',
                    cad_requirement TEXT DEFAULT 'OPTIONAL',
                    drawing_requirement TEXT DEFAULT 'OPTIONAL',
                    represented_part_id INTEGER
                );
                """
            )
        self.repo = BomRepository(self.db_path)
        self.primary_id = self.repo.insert(Bom(
            id=None,
            type="prt",
            name="PRIMARY WIRE 60A T N",
            aes_number="WR03",
            filename="primary_wire.prt.1",
            drawing="primary_wire.drw.1",
            pdf_path="primary_wire.pdf",
            step_path="primary_wire.step",
            project_id=10,
        ))
        self.service = object.__new__(BomService)
        self.service.bom_repo = self.repo
        self.service.revision_repo = _RevisionRepo()
        self.service.session = SimpleNamespace(project_id=10, user_id=1)
        self.service._tree_dirty = set()

    def tearDown(self):
        self.service = None
        self.repo = None
        gc.collect()
        self.tmp.cleanup()

    def test_alternate_representation_inherits_identity_and_has_no_delivery_files(self):
        representation_id = self.service.add_part({
            "type": "prt",
            "name": "w_rig_60_diff_t_n_f",
            "aes_number": "WRONG",
            "filename": "w_rig_60_diff_t_n_f.prt.1",
            "drawing": "must_be_removed.drw.1",
            "pdf_path": "must_be_removed.pdf",
            "step_path": "must_be_removed.step",
            "represented_part_id": self.primary_id,
        })

        representation = self.repo.get_by_id(representation_id)
        self.assertEqual(representation.aes_number, "WR03")
        self.assertEqual(representation.represented_part_id, self.primary_id)
        self.assertEqual(representation.classification, "CAD_ONLY")
        self.assertEqual(representation.default_ebom_behavior, "EXCLUDE")
        self.assertEqual(representation.cad_requirement, "REQUIRED")
        self.assertEqual(representation.drawing_requirement, "NOT_REQUIRED")
        self.assertFalse(representation.drawing)
        self.assertFalse(representation.pdf_path)
        self.assertFalse(representation.step_path)

    def test_primary_aes_change_is_synchronized_to_representation(self):
        representation_id = self.service.add_part({
            "type": "prt", "name": "alternate", "filename": "alternate.prt.1",
            "represented_part_id": self.primary_id,
        })
        self.service._assert_checked_out_for_change = lambda *_args, **_kwargs: None
        primary = self.repo.get_by_id(self.primary_id)
        data = primary.__dict__.copy()
        data["aes_number"] = "WR60"

        self.service.update_part(self.primary_id, data)

        self.assertEqual(self.repo.get_by_id(representation_id).aes_number, "WR60")

    def test_delivery_package_skips_cad_representation_without_missing_markers(self):
        representation_id = self.service.add_part({
            "type": "prt", "name": "alternate", "filename": "alternate.prt.1",
            "represented_part_id": self.primary_id,
        })
        exporter = PackageExportService(
            bom_repo=self.repo,
            bom_children_repo=_NoChildren(),
            part_file_service=_NoFiles(),
        )

        result = exporter.export_package_for_parts(
            [representation_id], self.tmp.name, include_children=False,
            package_name="representation-export",
        )

        self.assertEqual(result["exported"], [])
        self.assertEqual(result["missing"], [])
        self.assertEqual(result["skipped"][0]["reason"], "cad_representation")


if __name__ == "__main__":
    unittest.main()
