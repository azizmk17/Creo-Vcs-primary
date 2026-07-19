import os
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace

from core.repositories.bom_children_repository import BomChildrenRepository
from core.repositories.bom_repository import BomRepository
from core.repositories.bom_revision_repository import BomRevisionRepository
from core.repositories.lock_repository import LockRepository
from core.repositories.signature_repository import SignatureRepository
from core.services.bom_service import BomService
from core.services.pdm_service import PdmService


class _NoAdministrativePermissions:
    @staticmethod
    def user_has_permission(*_args, **_kwargs):
        return False


class PdmCheckoutCoordinationTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY, username TEXT, is_admin INTEGER DEFAULT 0
                );
                CREATE TABLE signature (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    action TEXT,user_id INTEGER,note TEXT,timestamp TEXT
                );
                CREATE TABLE locks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    part_id INTEGER UNIQUE NOT NULL,user_id INTEGER NOT NULL
                );
                CREATE TABLE lock_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    part_id INTEGER NOT NULL,user_id INTEGER NOT NULL,
                    action TEXT NOT NULL,timestamp TEXT NOT NULL,
                    signature INTEGER,object_iteration_id INTEGER
                );
                CREATE TABLE bom (
                    id INTEGER PRIMARY KEY,
                    type TEXT NOT NULL,name TEXT NOT NULL,
                    part_number TEXT,drawing_number TEXT,aes_number TEXT,
                    filename TEXT,drawing TEXT,base_file_name TEXT,base_drw_name TEXT,
                    material TEXT,weight TEXT,notes TEXT,pdf_path TEXT,step_path TEXT,
                    revision TEXT DEFAULT 'A',lifecycle_state TEXT DEFAULT 'WIP',
                    released_by INTEGER,released_at TEXT,status TEXT DEFAULT 'Design',
                    created TEXT,modified TEXT,project_id INTEGER,locked INTEGER DEFAULT 0
                );
                CREATE TABLE bom_children (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    parent_id INTEGER,child_id INTEGER,quantity INTEGER DEFAULT 1,
                    sort_order INTEGER DEFAULT 0,ebom_behavior TEXT DEFAULT 'INHERIT'
                );
                INSERT INTO users(id,username) VALUES(1,'designer'),(2,'other');
                INSERT INTO bom(
                    id,type,name,aes_number,filename,base_file_name,
                    revision,lifecycle_state,status,project_id
                ) VALUES
                    (1,'asm','Machine','AES-100','machine.asm','shared','A','WIP','Design',7),
                    (2,'prt','Legacy sibling','AES-101','legacy.prt','shared','A','WIP','Design',7);
                """
            )

        revision_repo = BomRevisionRepository(self.db_path)
        pdm_service = PdmService(self.db_path)
        lock_repo = LockRepository(self.db_path)

        service = BomService.__new__(BomService)
        service.session = SimpleNamespace(user_id=1, project_id=7)
        service.bom_repo = BomRepository(self.db_path)
        service.children_repo = BomChildrenRepository(self.db_path)
        service.lock_repo = lock_repo
        service.signature_repo = SignatureRepository(self.db_path)
        service.permission_repo = _NoAdministrativePermissions()
        service.revision_repo = revision_repo
        service.pdm_service = pdm_service
        service._tree_dirty = set()
        self.service = service
        self.repo = pdm_service.repo
        self.owner_cad = self.repo.get_cad_document_by_file(7, "machine.asm")

    def tearDown(self):
        try:
            os.remove(self.db_path)
        except OSError:
            pass

    def test_item_checkout_is_exact_and_does_not_checkout_cad(self):
        self.service.checkout_item(1)

        item_lock = self.service.lock_repo.get_by_part(1)
        self.assertEqual(item_lock.checkout_origin, "ITEM")
        self.assertIsNone(self.service.lock_repo.get_by_part(2))
        self.assertIsNone(
            self.repo.get_cad_document(int(self.owner_cad["id"]))["checked_out_by"]
        )

    def test_ebom_item_rows_include_vault_iteration_and_checkout_owner(self):
        self.service.checkout_item(1)

        structure = self.service.get_released_ebom_project(7)
        machine = next(
            row for row in structure["roots"] if int(row["bom_id"]) == 1
        )
        self.assertEqual(machine["version_label"], "A.1")
        self.assertEqual(machine["current_version"], "A.1")
        self.assertEqual(machine["iteration_number"], 1)
        self.assertTrue(machine["locked"])
        self.assertEqual(machine["locked_by_username"], "designer")

    def test_cad_checkout_auto_locks_item_and_item_actions_are_blocked(self):
        result = self.service.checkout_pdm_cad_document(int(self.owner_cad["id"]))

        self.assertTrue(result["item_checkout_auto_created"])
        item_lock = self.service.lock_repo.get_by_part(1)
        self.assertEqual((item_lock.user_id, item_lock.checkout_origin), (1, "CAD"))
        with self.assertRaisesRegex(ValueError, "associated CAD working copies"):
            self.service.undo_item_checkout(1)
        with self.assertRaisesRegex(ValueError, "associated CAD working copies"):
            self.service.checkin_item_data(1, "metadata")

    def test_unassociated_cad_checkout_does_not_lock_an_item(self):
        cad_id = self.repo.create_cad_document(
            7, "FREE-CAD", "Free CAD", "free.prt"
        )
        result = self.service.checkout_pdm_cad_document(cad_id)
        self.assertIsNone(result["associated_item_id"])
        self.assertFalse(result["item_checkout_auto_created"])
        self.assertIsNone(self.service.lock_repo.get_by_part(1))

    def test_cad_undo_releases_clean_auto_item_checkout(self):
        self.service.checkout_pdm_cad_document(int(self.owner_cad["id"]))
        result = self.service.undo_checkout_pdm_cad_document(
            int(self.owner_cad["id"]), "No change"
        )

        self.assertEqual(result["item_checkout"], "AUTO_RELEASED")
        self.assertIsNone(self.service.lock_repo.get_by_part(1))
        self.assertIsNone(result["checked_out_by"])
        self.assertEqual(
            [row["action"] for row in self.repo.list_cad_checkout_history(
                int(self.owner_cad["id"])
            )],
            ["UNDO_CHECKOUT", "CHECKOUT"],
        )

    def test_explicit_item_checkout_upgrades_and_retains_auto_lock(self):
        self.service.checkout_pdm_cad_document(int(self.owner_cad["id"]))
        self.service.checkout_item(1)
        self.assertEqual(
            self.service.lock_repo.get_by_part(1).checkout_origin, "ITEM"
        )

        result = self.service.undo_checkout_pdm_cad_document(
            int(self.owner_cad["id"])
        )
        self.assertEqual(result["item_checkout"], "RETAINED_EXPLICIT")
        self.assertIsNotNone(self.service.lock_repo.get_by_part(1))

    def test_item_changes_promote_auto_checkout_instead_of_discarding_it(self):
        self.service.checkout_pdm_cad_document(int(self.owner_cad["id"]))
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE bom SET name='Machine updated' WHERE id=1")

        result = self.service.undo_checkout_pdm_cad_document(
            int(self.owner_cad["id"])
        )
        self.assertEqual(result["item_checkout"], "RETAINED_WITH_CHANGES")
        self.assertEqual(
            self.service.lock_repo.get_by_part(1).checkout_origin, "ITEM"
        )

    def test_item_owned_by_other_user_blocks_cad_without_partial_checkout(self):
        signature = self.service.signature_repo.add_signature("checkout", 2, "other")
        context = self.service.revision_repo.get_current_context(1)
        self.service.lock_repo.checkout(
            1, 2, signature,
            object_iteration_id=context["current_iteration_id"],
            checkout_origin="ITEM",
        )

        with self.assertRaisesRegex(ValueError, "associated Item"):
            self.service.checkout_pdm_cad_document(int(self.owner_cad["id"]))
        self.assertIsNone(
            self.repo.get_cad_document(int(self.owner_cad["id"]))["checked_out_by"]
        )

    def test_last_of_multiple_cad_checkouts_releases_auto_item(self):
        second_cad_id = self.repo.create_cad_document(
            7, "MACHINE-ALT", "Machine alternate", "machine_alt.prt"
        )
        self.repo.associate(7, 1, second_cad_id, "IMAGE", created_by=1)

        self.service.checkout_pdm_cad_document(int(self.owner_cad["id"]))
        self.service.checkout_pdm_cad_document(second_cad_id)
        first = self.service.undo_checkout_pdm_cad_document(
            int(self.owner_cad["id"])
        )
        self.assertEqual(first["item_checkout"], "RETAINED_FOR_CAD")
        self.assertIsNotNone(self.service.lock_repo.get_by_part(1))

        second = self.service.undo_checkout_pdm_cad_document(second_cad_id)
        self.assertEqual(second["item_checkout"], "AUTO_RELEASED")
        self.assertIsNone(self.service.lock_repo.get_by_part(1))

    def test_cad_checkin_creates_iteration_and_releases_clean_auto_item(self):
        source = os.path.join(os.path.dirname(self.db_path), "machine.asm.2")
        with open(source, "wb") as handle:
            handle.write(b"new CAD iteration")
        self.addCleanup(lambda: os.path.exists(source) and os.remove(source))

        self.service.checkout_pdm_cad_document(int(self.owner_cad["id"]))
        result = self.service.checkin_pdm_cad_document(
            int(self.owner_cad["id"]), source, "geometry"
        )
        self.assertEqual(result["iteration"], 2)
        self.assertEqual(result["item_checkout"], "AUTO_RELEASED")
        self.assertIsNone(self.service.lock_repo.get_by_part(1))

    def test_released_cad_requires_revision_before_checkout(self):
        self.repo.release_cad_document(int(self.owner_cad["id"]))
        with self.assertRaisesRegex(ValueError, "Released"):
            self.service.checkout_pdm_cad_document(int(self.owner_cad["id"]))
        self.assertIsNone(self.service.lock_repo.get_by_part(1))

    def test_released_associated_item_requires_confirmed_target_revision(self):
        self.service.revision_repo.release_current_revision(1, 1, "release")
        with self.assertRaisesRegex(ValueError, "Enter the revision"):
            self.service.checkout_pdm_cad_document(int(self.owner_cad["id"]))
        self.assertIsNone(self.service.lock_repo.get_by_part(1))

        result = self.service.checkout_pdm_cad_document(
            int(self.owner_cad["id"]), released_item_revision_code="B"
        )
        self.assertEqual(result["associated_item_id"], 1)
        self.assertEqual(
            self.service.revision_repo.get_current_context(1)["pending_revision_code"],
            "B",
        )

    def test_cad_membership_changes_require_the_parent_cad_checkout(self):
        child_cad_id = self.repo.create_cad_document(
            7, "CHILD-CAD", "Child CAD", "child.prt"
        )
        owner_cad_id = int(self.owner_cad["id"])

        with self.assertRaisesRegex(ValueError, "Check out the CAD assembly"):
            self.service.add_pdm_cad_member(owner_cad_id, child_cad_id)

        self.service.checkout_pdm_cad_document(owner_cad_id)
        member_id = self.service.add_pdm_cad_member(
            owner_cad_id, child_cad_id, quantity=2
        )
        self.assertEqual(
            int(self.repo.get_cad_member(member_id)["quantity"]), 2
        )
        self.service.undo_checkout_pdm_cad_document(owner_cad_id)

        with self.assertRaisesRegex(ValueError, "Check out the CAD assembly"):
            self.service.remove_pdm_cad_member(member_id)

    def test_checked_out_cad_blocks_item_association_changes(self):
        owner_cad_id = int(self.owner_cad["id"])
        association = self.repo.get_active_association_for_cad(owner_cad_id)
        self.service.checkout_pdm_cad_document(owner_cad_id)

        with self.assertRaisesRegex(ValueError, "before changing"):
            self.service.associate_cad_document(1, owner_cad_id, "IMAGE")
        with self.assertRaisesRegex(ValueError, "before removing"):
            self.service.remove_cad_item_association(int(association["id"]))


if __name__ == "__main__":
    unittest.main()
