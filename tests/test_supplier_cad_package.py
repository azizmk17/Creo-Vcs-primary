import gc
import json
import os
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace

from core.models.bom_model import Bom
from core.repositories.bom_repository import BomRepository
from core.services.diag_service import DiagService
from core.services.bom_service import BomService


class _CommitRepo:
    def get_by_status(self, _status, _project_id):
        return []

    def get_forced_integrated_filenames(self, _project_id):
        return []


class _SnapshotRepo:
    def get_last_snapshot_id(self, _project_id):
        return 1

    def get_last_snapshot(self, _project_id):
        return {
            "snapshot_data": json.dumps({"files": [{"filename": "board.asm.1"}]})
        }


class _RevisionRepo:
    def ensure_bom(self, bom_id, created_by=None):
        return {"bom_id": bom_id, "created_by": created_by}


class SupplierCadPackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "supplier-package.db")
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
                    represented_part_id INTEGER,
                    cad_control_mode TEXT DEFAULT 'CONTROLLED'
                );
                """
            )
        self.repo = BomRepository(self.db_path)
        self.owner_id = self.repo.insert(Bom(
            id=None,
            type="asm",
            name="Electronic Card",
            aes_number="EC01",
            filename="board.asm.1",
            base_file_name="board.asm",
            drawing="board.drw.1",
            base_drw_name="board.drw",
            project_id=10,
            classification="PHYSICAL",
            default_ebom_behavior="NORMAL",
            cad_control_mode="SUPPLIER_PACKAGE",
        ))
        for filename in ("board.asm.1", "resistor_0402.prt.1", "stray.prt.1"):
            with open(os.path.join(self.tmp.name, filename), "wb") as handle:
                handle.write(filename.encode("utf-8"))

    def tearDown(self):
        self.repo = None
        gc.collect()
        self.tmp.cleanup()

    def _diag_service(self):
        service = object.__new__(DiagService)
        service.session = SimpleNamespace(project_id=10, user_id=1)
        service.bom_repo = self.repo
        service.repo = _CommitRepo()
        service.snap_repo = _SnapshotRepo()
        return service

    def test_owned_dependency_is_neither_orphan_nor_unexpected(self):
        assigned = self.repo.assign_cad_dependencies(
            10,
            self.owner_id,
            [{
                "base_file_name": "resistor_0402.prt",
                "original_filename": "resistor_0402.prt.1",
            }],
            assigned_by=1,
        )
        service = self._diag_service()

        orphan_names = [row[0] for row in service.check_orphan_files(self.tmp.name)]
        unexpected_names = [row[0] for row in service.check_working_directory(self.tmp.name)]

        self.assertEqual(assigned, 1)
        self.assertNotIn("resistor_0402.prt.1", orphan_names)
        self.assertNotIn("resistor_0402.prt.1", unexpected_names)
        self.assertIn("stray.prt.1", orphan_names)
        self.assertIn("stray.prt.1", unexpected_names)

    def test_controlled_bom_item_cannot_own_unmanaged_dependencies(self):
        controlled_id = self.repo.insert(Bom(
            id=None,
            type="asm",
            name="Controlled Assembly",
            aes_number="CA01",
            project_id=10,
            cad_control_mode="CONTROLLED",
        ))

        with self.assertRaisesRegex(ValueError, "supplier-managed"):
            self.repo.assign_cad_dependencies(
                10,
                controlled_id,
                [{"base_file_name": "resistor_0402.prt"}],
                assigned_by=1,
            )

    def test_dependency_can_be_unassigned_and_becomes_orphan_again(self):
        self.repo.assign_cad_dependencies(
            10,
            self.owner_id,
            [{"base_file_name": "resistor_0402.prt", "original_filename": "resistor_0402.prt.1"}],
        )
        dependency = self.repo.list_cad_dependencies(10)[0]

        removed = self.repo.remove_cad_dependencies(10, [dependency["id"]])
        orphan_names = [row[0] for row in self._diag_service().check_orphan_files(self.tmp.name)]

        self.assertEqual(removed, 1)
        self.assertIn("resistor_0402.prt.1", orphan_names)

    def test_creating_a_controlled_bom_child_promotes_dependency_out_of_package(self):
        self.repo.assign_cad_dependencies(
            10,
            self.owner_id,
            [{"base_file_name": "resistor_0402.prt", "original_filename": "resistor_0402.prt.1"}],
        )
        service = object.__new__(BomService)
        service.bom_repo = self.repo
        service.revision_repo = _RevisionRepo()
        service.session = SimpleNamespace(project_id=10, user_id=1)
        service._tree_dirty = set()

        part_id = service.add_part({
            "type": "prt",
            "name": "Controlled Resistor",
            "aes_number": "R0402",
            "filename": "resistor_0402.prt.1",
            "classification": "PHYSICAL",
            "default_ebom_behavior": "NORMAL",
            "cad_control_mode": "CONTROLLED",
        })

        self.assertIsInstance(part_id, int)
        self.assertEqual(self.repo.list_cad_dependencies(10), [])

    def test_non_deliverable_item_may_have_empty_aes(self):
        service = object.__new__(BomService)
        service.bom_repo = self.repo
        service.revision_repo = _RevisionRepo()
        service.session = SimpleNamespace(project_id=10, user_id=1)
        service._tree_dirty = set()

        part_id = service.add_part({
            "type": "asm",
            "name": "CAD Reference Group",
            "aes_number": "",
            "filename": "reference_group.asm.1",
            "classification": "REFERENCE",
            "default_ebom_behavior": "EXCLUDE",
            "cad_control_mode": "CONTROLLED",
        })

        self.assertIsInstance(part_id, int)
        self.assertEqual(self.repo.get_by_id(part_id).aes_number, "")

    def test_deliverable_item_requires_aes(self):
        service = object.__new__(BomService)
        service.bom_repo = self.repo
        service.revision_repo = _RevisionRepo()
        service.session = SimpleNamespace(project_id=10, user_id=1)
        service._tree_dirty = set()

        with self.assertRaisesRegex(ValueError, "required.*delivery"):
            service.add_part({
                "type": "prt",
                "name": "Deliverable Part",
                "aes_number": "",
                "filename": "deliverable.prt.1",
                "classification": "PHYSICAL",
                "default_ebom_behavior": "NORMAL",
                "cad_control_mode": "CONTROLLED",
            })


if __name__ == "__main__":
    unittest.main()
