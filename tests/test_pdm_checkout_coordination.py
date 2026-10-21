import gc
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.repositories.bom_children_repository import BomChildrenRepository
from core.repositories.bom_repository import BomRepository
from core.repositories.bom_revision_repository import BomRevisionRepository
from core.repositories.lock_repository import LockRepository
from core.repositories.managed_file_repository import ManagedFileRepository
from core.repositories.signature_repository import SignatureRepository
from core.services.bom_service import BomService
from core.services.managed_file_service import ManagedFileService
from core.services.pdm_service import PdmService


class _NoAdministrativePermissions:
    @staticmethod
    def user_has_permission(*_args, **_kwargs):
        return False


class PdmCheckoutCoordinationTests(unittest.TestCase):
    def setUp(self):
        workspace_service = patch(
            "core.services.cad_workspace_service.CadWorkspaceService"
        )
        workspace_service.start()
        self.addCleanup(workspace_service.stop)
        self.test_directory = tempfile.TemporaryDirectory(
            dir=Path(__file__).resolve().parents[1]
        )
        self.db_path = os.path.join(self.test_directory.name, "checkout.db")
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
        # sqlite3 connection context managers commit/rollback but Windows keeps
        # their handles open until the connection objects are collected.
        gc.collect()
        self.test_directory.cleanup()

    def _stored_cad(self, cad_id):
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return dict(conn.execute("SELECT * FROM cad_documents WHERE id=?", (cad_id,)).fetchone())

    def _related_drawing(self, name="machine.drw"):
        return self.repo.create_cad_document(
            7, name.upper(), name, name,
            category="DRAWING",
            drawing_owner_cad_document_id=int(self.owner_cad["id"]),
        )

    def _assert_cad_checkout_cleared(self, cad_id):
        document = self._stored_cad(cad_id)
        for field in (
            "checked_out_by", "checked_out_at", "checkout_item_id",
            "checkout_workspace_id", "checkout_workspace_name",
            "checkout_workspace_machine_id",
        ):
            self.assertIsNone(document[field], field)
        self.assertEqual(self.repo.list_cad_checkout_item_ids(cad_id), [])

    def _checkin_item_data(self, item_id=1, *, has_changes=False, managed_files=None):
        # Analyzer/file-service construction uses the application's active DB;
        # isolate it while exercising the real item/CAD repositories below.
        iteration = self.service.revision_repo.get_current_context(item_id)[
            "current_iteration_id"
        ]
        with patch(
            "core.services.checkout_change_service.CheckoutChangeService"
        ) as analyzer, patch(
            "core.services.managed_file_service.ManagedFileService"
        ) as managed_class:
            if managed_files is not None:
                managed_class.return_value = managed_files
            analyzer.return_value.analyze.return_value = {
                "current_iteration_id": iteration,
                "has_non_cad_changes": has_changes,
                "requires_commit": False,
                "structure_requires_cad": False,
            }
            return self.service.checkin_item_data(item_id, "Finish item work")

    def _managed_file_service(self):
        service = ManagedFileService.__new__(ManagedFileService)
        service.repo = ManagedFileRepository(self.db_path)
        service.bom_repo = self.service.bom_repo
        service.revision_repo = self.service.revision_repo
        service.session = self.service.session
        service.part_file_service = SimpleNamespace(list_attachments=lambda _item_id: [])
        service.project_service = SimpleNamespace(
            get_project_by_id=lambda _project_id: {
                "id": 7, "working_directory": self.test_directory.name,
            }
        )
        service._username_cache = {}
        return service

    def test_item_checkout_is_exact_and_does_not_checkout_cad(self):
        self.service.checkout_item(1)

        item_lock = self.service.lock_repo.get_by_part(1)
        self.assertEqual(item_lock.checkout_origin, "ITEM")
        self.assertIsNone(self.service.lock_repo.get_by_part(2))
        self.assertIsNone(
            self.repo.get_cad_document(int(self.owner_cad["id"]))["checked_out_by"]
        )

    def test_item_checkin_writes_share_supplied_transaction(self):
        self.service.checkout_item(1)
        with sqlite3.connect(self.db_path) as conn:
            iteration_count = conn.execute(
                "SELECT COUNT(*) FROM bom_iterations"
            ).fetchone()[0]

        with self.assertRaisesRegex(RuntimeError, "rollback check-in"):
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute("BEGIN IMMEDIATE")
                self.service.checkin_by_part_id(
                    1, 1, "atomic item check-in", "commit-atomic",
                    exact_item=True, connection=conn,
                )
                raise RuntimeError("rollback check-in")

        self.assertIsNotNone(self.service.lock_repo.get_by_part(1))
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM lock_logs WHERE action='checkin'"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM signature WHERE action='checkin'"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM bom_iterations").fetchone()[0],
                iteration_count,
            )

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            self.service.checkin_by_part_id(
                1, 1, "atomic item check-in", "commit-atomic",
                exact_item=True, connection=conn,
            )

        self.assertIsNone(self.service.lock_repo.get_by_part(1))
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM lock_logs WHERE action='checkin'"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM signature WHERE action='checkin'"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM bom_iterations").fetchone()[0],
                iteration_count + 1,
            )

    def test_item_checkout_can_explicitly_include_owner_cad_workspace(self):
        self.service.checkout_item(
            1,
            include_owner_cad=True,
            workspace_id="b" * 32,
            workspace_name="Motor update",
            workspace_machine_id="DESIGN-08",
        )
        item_lock = self.service.lock_repo.get_by_part(1)
        self.assertEqual(item_lock.checkout_origin, "ITEM")
        document = self.repo.get_cad_document(int(self.owner_cad["id"]))
        self.assertEqual(document["checked_out_by"], 1)
        self.assertEqual(document["checkout_workspace_id"], "b" * 32)

    def test_ebom_item_rows_include_vault_iteration_and_checkout_owner(self):
        self.service.bom_repo.set_categories_for_bom(
            1, 7, ["Mechanical"], assigned_by=1
        )
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
        self.assertEqual(machine["category_names"], ["Mechanical"])

    def test_item_undo_closes_auto_cad_checkout_without_creating_a_cad_iteration(self):
        result = self.service.checkout_pdm_cad_document(int(self.owner_cad["id"]))

        self.assertTrue(result["item_checkout_auto_created"])
        item_lock = self.service.lock_repo.get_by_part(1)
        self.assertEqual((item_lock.user_id, item_lock.checkout_origin), (1, "CAD"))
        before = self.repo.get_current_cad_iteration(int(self.owner_cad["id"]))
        self.assertTrue(self.service.undo_item_checkout(1))
        self.assertIsNone(self.service.lock_repo.get_by_part(1))
        self._assert_cad_checkout_cleared(int(self.owner_cad["id"]))
        self.assertEqual(
            self.repo.get_current_cad_iteration(int(self.owner_cad["id"])), before,
        )

    def test_item_undo_closes_related_other_user_cad_but_keeps_sibling_item_checkout(self):
        self.service.checkout_item(1)
        self.service.session.user_id = 2
        self.service.checkout_item(2)
        self.service.session.user_id = 1
        cad_id = int(self.owner_cad["id"])
        self.repo.checkout_cad_document(
            cad_id, 2, workspace_id="c" * 32,
            workspace_name="Other machine work", workspace_machine_id="DESIGN-OTHER",
        )
        iteration = self.service.revision_repo.get_current_context(1)["current_iteration_id"]
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE bom SET material='Working edit' WHERE id=1")

        self.assertTrue(self.service.undo_item_checkout(1))

        self.assertIsNone(self.service.lock_repo.get_by_part(1))
        self.assertEqual(self.service.lock_repo.get_by_part(2).user_id, 2)
        self._assert_cad_checkout_cleared(cad_id)
        self.assertEqual(
            self.repo.list_cad_checkout_history(cad_id)[0]["action"],
            "UNDO_CHECKOUT",
        )
        self.assertEqual(self.service.revision_repo.get_current_context(1)["current_iteration_id"], iteration)
        self.assertIsNone(self.service.bom_repo.get_by_id(1).material)

    def test_item_undo_still_rejects_undoing_another_users_item(self):
        self.service.checkout_item(1)
        self.service.session.user_id = 2
        with self.assertRaisesRegex(ValueError, "checked out by another user"):
            self.service.undo_item_checkout(1)
        self.assertEqual(self.service.lock_repo.get_by_part(1).user_id, 1)

    def test_bom_actions_close_drawing_left_after_model_only_checkin(self):
        for action in ("checkin", "undo"):
            with self.subTest(action=action):
                model_id = int(self.owner_cad["id"])
                drawing_id = self._related_drawing(f"leftover_{action}.drw")
                self.service.checkout_item(
                    1, include_owner_cad=True,
                    workspace_id="d" * 32, workspace_name="Model-only push",
                    workspace_machine_id="DESIGN-08",
                )
                source = os.path.join(self.test_directory.name, f"{action}.asm.2")
                with open(source, "wb") as handle:
                    handle.write(b"model-only geometry update")
                result = self.service.checkin_pdm_cad_document(
                    model_id, source, "Push only 3D"
                )
                self.assertIsNone(result["checked_out_by"])
                self.assertEqual(self._stored_cad(drawing_id)["checked_out_by"], 1)
                self.assertIsNotNone(self.service.lock_repo.get_by_part(1))
                model_iteration = self.repo.get_current_cad_iteration(model_id)
                drawing_iteration = self.repo.get_current_cad_iteration(drawing_id)

                if action == "checkin":
                    self._checkin_item_data()
                else:
                    self.service.undo_item_checkout(1)

                self.assertIsNone(self.service.lock_repo.get_by_part(1))
                self._assert_cad_checkout_cleared(model_id)
                self._assert_cad_checkout_cleared(drawing_id)
                self.assertEqual(
                    self.repo.get_current_cad_iteration(model_id), model_iteration
                )
                self.assertEqual(
                    self.repo.get_current_cad_iteration(drawing_id), drawing_iteration
                )
                self.assertEqual(
                    self.repo.list_cad_checkout_history(drawing_id)[0]["action"],
                    "UNDO_CHECKOUT",
                )

    def test_bom_actions_find_independent_drawing_when_model_is_checked_in(self):
        for action in ("checkin", "undo"):
            with self.subTest(action=action):
                self.service.checkout_item(1)
                drawing_id = self._related_drawing(f"independent_{action}.drw")
                self.repo.checkout_cad_document(
                    drawing_id, 2,
                    workspace_id="e" * 32, workspace_name="Different workspace",
                    workspace_machine_id="DESIGN-OTHER",
                )
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute(
                        "DELETE FROM cad_document_checkout_items WHERE cad_document_id=?",
                        (drawing_id,),
                    )
                    conn.execute(
                        """UPDATE cad_documents
                           SET checkout_item_id=NULL,lifecycle_state=?,
                               latest_creo_file_version=7,latest_creo_file_name=?
                           WHERE id=?""",
                        (
                            "RELEASED" if action == "checkin" else "OBSOLETE",
                            f"independent_{action}.drw.7", drawing_id,
                        ),
                    )
                # No direct Item association or saved checkout Item link: the
                # drawing is related through the model which is already closed.
                self.assertEqual(self.repo.list_cad_checkout_item_ids(drawing_id), [])
                self.assertIsNone(
                    self._stored_cad(int(self.owner_cad["id"]))["checked_out_by"]
                )

                if action == "checkin":
                    self._checkin_item_data()
                else:
                    self.service.undo_item_checkout(1)

                self.assertIsNone(self.service.lock_repo.get_by_part(1))
                self._assert_cad_checkout_cleared(drawing_id)
                self.assertEqual(
                    self._stored_cad(drawing_id)["latest_creo_file_version"], 7
                )
                self.assertEqual(
                    self._stored_cad(drawing_id)["latest_creo_file_name"],
                    f"independent_{action}.drw.7",
                )

    def test_bom_actions_cleanup_orphan_drawing_when_item_is_already_checked_in(self):
        for action in ("checkin", "undo"):
            with self.subTest(action=action):
                drawing_id = self._related_drawing(f"orphan_{action}.drw")
                self.repo.checkout_cad_document(drawing_id, 1)
                self.assertIsNone(self.service.lock_repo.get_by_part(1))

                if action == "checkin":
                    self._checkin_item_data()
                else:
                    self.assertTrue(self.service.undo_item_checkout(1))

                self.assertIsNone(self.service.lock_repo.get_by_part(1))
                self._assert_cad_checkout_cleared(drawing_id)

    def test_bom_actions_cleanup_legacy_cad_with_only_saved_item_id(self):
        for action in ("checkin", "undo"):
            with self.subTest(action=action):
                self.service.checkout_item(1)
                cad_id = self.repo.create_cad_document(
                    7, f"LEGACY-{action.upper()}", "Legacy related CAD",
                    f"legacy_{action}.prt",
                )
                self.repo.checkout_cad_document(cad_id, 2)
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute(
                        "UPDATE cad_documents SET checkout_item_id=1 WHERE id=?",
                        (cad_id,),
                    )
                self.assertEqual(self.repo.list_cad_checkout_item_ids(cad_id), [])

                if action == "checkin":
                    self._checkin_item_data()
                else:
                    self.assertTrue(self.service.undo_item_checkout(1))

                self.assertIsNone(self.service.lock_repo.get_by_part(1))
                self._assert_cad_checkout_cleared(cad_id)

    def test_bom_metadata_checkin_keeps_item_changes_and_closes_related_cad(self):
        drawing_id = self._related_drawing("metadata.drw")
        self.service.checkout_item(1, include_owner_cad=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE bom SET material='New steel' WHERE id=1")
        before = self.service.revision_repo.get_current_context(1)
        drawing_iteration = self.repo.get_current_cad_iteration(drawing_id)

        self._checkin_item_data(has_changes=True)

        self.assertEqual(self.service.bom_repo.get_by_id(1).material, "New steel")
        self.assertNotEqual(
            self.service.revision_repo.get_current_context(1)["current_iteration_id"],
            before["current_iteration_id"],
        )
        self.assertIsNone(self.service.lock_repo.get_by_part(1))
        self._assert_cad_checkout_cleared(int(self.owner_cad["id"]))
        self._assert_cad_checkout_cleared(drawing_id)
        self.assertEqual(self.repo.get_current_cad_iteration(drawing_id), drawing_iteration)

    def _checkin_with_real_manifest(self, *, fail_cleanup):
        managed_files = self._managed_file_service()
        current_id = self.service.revision_repo.get_current_context(1)[
            "current_iteration_id"
        ]
        managed_files.repo.upsert({
            "bom_id": 1, "iteration_id": current_id,
            "file_role": "generated_pdf", "file_type": "PDF",
            "source_kind": "generated", "filename": "machine.pdf",
            "file_revision": "A", "storage_scheme": "legacy_reference",
            "vault_rel_path": "machine.pdf", "sha256": "a" * 64,
            "integrity_status": "Verified", "lifecycle_state": "In Work",
        })
        drawing_id = self._related_drawing("manifest.drw")
        self.service.checkout_item(1, include_owner_cad=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE bom SET material='Manifest checkin steel' WHERE id=1")
            before_iteration_count = conn.execute(
                "SELECT COUNT(*) FROM bom_iterations"
            ).fetchone()[0]
        cleanup = self.service.pdm_service.undo_item_cad_checkouts
        observed_iteration_ids = []

        def cleanup_after_manifest(item_id, actor_id, note, *, connection):
            new_context = self.service.revision_repo.get_current_context(
                item_id, connection=connection
            )
            new_id = new_context["current_iteration_id"]
            observed_iteration_ids.append(new_id)
            self.assertNotEqual(new_id, current_id)
            entries = managed_files.repo.list_for_iteration(
                new_id, connection=connection
            )
            self.assertEqual(
                {entry["file_role"] for entry in entries},
                {"generated_pdf", "native_cad"},
            )
            inherited = next(entry for entry in entries if entry["file_role"] == "generated_pdf")
            self.assertEqual(inherited["sha256"], "a" * 64)
            released = cleanup(item_id, actor_id, note, connection=connection)
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM cad_documents WHERE checked_out_by IS NOT NULL"
                ).fetchone()[0], 0,
            )
            if fail_cleanup:
                raise RuntimeError("CAD cleanup failed after writing manifest")
            return released

        with patch.object(
            self.service.pdm_service, "undo_item_cad_checkouts",
            side_effect=cleanup_after_manifest,
        ):
            if fail_cleanup:
                with self.assertRaisesRegex(RuntimeError, "CAD cleanup failed"):
                    self._checkin_item_data(has_changes=True, managed_files=managed_files)
            else:
                self._checkin_item_data(has_changes=True, managed_files=managed_files)

        self.assertEqual(len(observed_iteration_ids), 1)
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM bom_iterations").fetchone()[0],
                before_iteration_count + (0 if fail_cleanup else 1),
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM bom_iteration_files").fetchone()[0],
                1 if fail_cleanup else 3,
            )
            if fail_cleanup:
                for table in ("signature", "lock_logs"):
                    self.assertEqual(
                        conn.execute(
                            f"SELECT COUNT(*) FROM {table} WHERE action='checkin'"
                        ).fetchone()[0], 0,
                    )
        if fail_cleanup:
            self.assertEqual(
                self.service.revision_repo.get_current_context(1)["current_iteration_id"],
                current_id,
            )
            self.assertIsNotNone(self.service.lock_repo.get_by_part(1))
            for cad_id in (int(self.owner_cad["id"]), drawing_id):
                self.assertEqual(self._stored_cad(cad_id)["checked_out_by"], 1)
                self.assertEqual(
                    [row["action"] for row in self.repo.list_cad_checkout_history(cad_id)],
                    ["CHECKOUT"],
                )
        else:
            self.assertEqual(
                self.service.revision_repo.get_current_context(1)["current_iteration_id"],
                observed_iteration_ids[0],
            )
            self.assertIsNone(self.service.lock_repo.get_by_part(1))
            self._assert_cad_checkout_cleared(int(self.owner_cad["id"]))
            self._assert_cad_checkout_cleared(drawing_id)

    def test_bom_checkin_captures_real_manifest_in_the_item_transaction(self):
        self._checkin_with_real_manifest(fail_cleanup=False)

    def test_bom_cleanup_failure_rolls_back_item_iteration_manifest_and_cad_logs(self):
        self._checkin_with_real_manifest(fail_cleanup=True)

    def test_bom_restore_failure_keeps_item_and_cad_checkout(self):
        for action in ("checkin", "undo"):
            with self.subTest(action=action):
                self.service.checkout_item(1, include_owner_cad=True)
                model_id = int(self.owner_cad["id"])
                drawing_id = self._related_drawing(f"restore_{action}.drw")
                self.repo.checkout_cad_document(drawing_id, 1)
                with patch.object(
                    self.service.revision_repo, "restore_checked_in_state",
                    side_effect=RuntimeError("Cannot restore baseline"),
                ), self.assertRaisesRegex(RuntimeError, "Cannot restore baseline"):
                    if action == "checkin":
                        self._checkin_item_data()
                    else:
                        self.service.undo_item_checkout(1)

                self.assertIsNotNone(self.service.lock_repo.get_by_part(1))
                self.assertEqual(self._stored_cad(model_id)["checked_out_by"], 1)
                self.assertEqual(self._stored_cad(drawing_id)["checked_out_by"], 1)
                self.assertEqual(
                    [row["action"] for row in self.repo.list_cad_checkout_history(drawing_id)],
                    ["CHECKOUT"],
                )

    def test_bom_cleanup_attempts_each_workspace_when_first_manifest_release_fails(self):
        for action in ("checkin", "undo"):
            with self.subTest(action=action):
                self.service.checkout_item(1)
                model_id = int(self.owner_cad["id"])
                drawing_id = self._related_drawing(f"workspace_{action}.drw")
                model_workspace, drawing_workspace = "a" * 32, "b" * 32
                self.repo.checkout_cad_document(
                    model_id, 1, workspace_id=model_workspace,
                )
                self.repo.checkout_cad_document(
                    drawing_id, 1, workspace_id=drawing_workspace,
                )
                attempts, successes = [], []

                def release_manifest(workspace_id, cad_id):
                    attempts.append((workspace_id, cad_id))
                    if len(attempts) == 1:
                        raise OSError("First workspace manifest is unavailable")
                    successes.append((workspace_id, cad_id))

                with patch(
                    "core.services.cad_workspace_service.CadWorkspaceService"
                ) as workspace_class:
                    workspace_class.return_value.release_cad_document.side_effect = release_manifest
                    if action == "checkin":
                        self._checkin_item_data()
                    else:
                        self.assertTrue(self.service.undo_item_checkout(1))

                self.assertIsNone(self.service.lock_repo.get_by_part(1))
                self._assert_cad_checkout_cleared(model_id)
                self._assert_cad_checkout_cleared(drawing_id)
                self.assertEqual(
                    set(attempts),
                    {(model_workspace, model_id), (drawing_workspace, drawing_id)},
                )
                self.assertEqual(len(attempts), 2)
                self.assertEqual(successes, [attempts[1]])

    def test_bom_cleanup_does_not_release_unrelated_cad_or_sibling_item(self):
        unrelated_id = self.repo.create_cad_document(
            7, "UNRELATED-CAD", "Unrelated CAD", "unrelated.prt"
        )
        self.repo.checkout_cad_document(unrelated_id, 2)
        self.service.session.user_id = 2
        self.service.checkout_item(2)
        self.service.session.user_id = 1
        for action in ("checkin", "undo"):
            with self.subTest(action=action):
                self.service.checkout_item(1, include_owner_cad=True)
                if action == "checkin":
                    self._checkin_item_data()
                else:
                    self.service.undo_item_checkout(1)

                self.assertIsNone(self.service.lock_repo.get_by_part(1))
                self.assertEqual(self.service.lock_repo.get_by_part(2).user_id, 2)
                self.assertEqual(self._stored_cad(unrelated_id)["checked_out_by"], 2)

    def test_bom_actions_refuse_another_users_item_and_keep_related_cad_checked_out(self):
        self.service.checkout_item(1, include_owner_cad=True)
        self.service.session.user_id = 2
        for action in ("checkin", "undo"):
            with self.subTest(action=action):
                with self.assertRaisesRegex(ValueError, "checked out by another user"):
                    if action == "checkin":
                        self._checkin_item_data()
                    else:
                        self.service.undo_item_checkout(1)

                self.assertEqual(self.service.lock_repo.get_by_part(1).user_id, 1)
                self.assertEqual(
                    self._stored_cad(int(self.owner_cad["id"]))["checked_out_by"], 1
                )

    def test_cad_checkout_can_create_an_explicit_associated_item_checkout(self):
        result = self.service.checkout_pdm_cad_document(
            int(self.owner_cad["id"]), explicit_item_checkout=True
        )

        item_lock = self.service.lock_repo.get_by_part(1)
        self.assertEqual((item_lock.user_id, item_lock.checkout_origin), (1, "ITEM"))
        self.assertTrue(result["item_checkout_explicit"])
        self.assertEqual(result["item_checkout_explicit_ids"], [1])
        self.assertFalse(result["item_checkout_auto_created"])

    def test_unassociated_cad_checkout_does_not_lock_an_item(self):
        cad_id = self.repo.create_cad_document(
            7, "FREE-CAD", "Free CAD", "free.prt"
        )
        result = self.service.checkout_pdm_cad_document(cad_id)
        self.assertIsNone(result["associated_item_id"])
        self.assertFalse(result["item_checkout_auto_created"])
        self.assertIsNone(self.service.lock_repo.get_by_part(1))

    def test_cad_checkout_records_and_clears_local_workspace_identity(self):
        cad_id = int(self.owner_cad["id"])
        result = self.service.checkout_pdm_cad_document(
            cad_id,
            workspace_id="a" * 32,
            workspace_name="Gearbox redesign",
            workspace_machine_id="DESIGN-07",
        )
        self.assertEqual(result["checkout_workspace_id"], "a" * 32)
        self.assertEqual(result["checkout_workspace_name"], "Gearbox redesign")
        self.assertEqual(result["checkout_workspace_machine_id"], "DESIGN-07")

        self.service.undo_checkout_pdm_cad_document(cad_id, "cancel")
        document = self.repo.get_cad_document(cad_id)
        self.assertIsNone(document["checkout_workspace_id"])
        history = self.repo.list_cad_checkout_history(cad_id)
        self.assertEqual(history[0]["workspace_id"], "a" * 32)
        self.assertEqual(history[1]["workspace_name"], "Gearbox redesign")

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

    def test_related_drawing_conflict_rolls_back_the_entire_checkout(self):
        drawing_id = self.repo.create_cad_document(
            7, "MACHINE-DRW", "Machine drawing", "machine.drw",
            category="DRAWING",
            drawing_owner_cad_document_id=int(self.owner_cad["id"]),
        )
        self.repo.checkout_cad_document(drawing_id, 2)

        with self.assertRaisesRegex(ValueError, "checked out by another user"):
            self.service.checkout_pdm_cad_document(int(self.owner_cad["id"]))

        self.assertIsNone(self.service.lock_repo.get_by_part(1))
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
