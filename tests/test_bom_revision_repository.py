import os
import sqlite3
import tempfile
import unittest

from core.repositories.bom_revision_repository import BomRevisionRepository
from core.repositories.project_repository import ProjectRepository
from setup.migrations import _migration_22, _migration_23


class BomRevisionRepositoryTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE bom (
                    id INTEGER PRIMARY KEY,
                    type TEXT, name TEXT, aes_number TEXT, part_number TEXT,
                    drawing_number TEXT, filename TEXT, drawing TEXT,
                    base_file_name TEXT, base_drw_name TEXT, material TEXT,
                    weight TEXT, notes TEXT, pdf_path TEXT, step_path TEXT,
                    revision TEXT DEFAULT 'A', lifecycle_state TEXT DEFAULT 'WIP',
                    status TEXT DEFAULT 'Design', modified TEXT, project_id INTEGER,
                    released_at TEXT, released_by INTEGER
                );
                CREATE TABLE bom_children (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    parent_id INTEGER, child_id INTEGER,
                    quantity INTEGER DEFAULT 1, sort_order INTEGER DEFAULT 0
                );
                CREATE TABLE projects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL, description TEXT DEFAULT '',
                    working_directory TEXT DEFAULT '',
                    created_at TEXT DEFAULT (datetime('now')),
                    root_project_id INTEGER, version_label TEXT,
                    version_state TEXT DEFAULT 'WIP', created_from_project_id INTEGER,
                    created_from_baseline_id INTEGER, is_readonly INTEGER DEFAULT 0
                );
                CREATE UNIQUE INDEX ux_test_project_version
                    ON projects(root_project_id, version_label);
                CREATE TABLE user_projects (
                    user_id INTEGER, project_id INTEGER, is_current INTEGER DEFAULT 0,
                    UNIQUE(user_id, project_id)
                );
                INSERT INTO projects(
                    id,name,description,working_directory,root_project_id,
                    version_label,version_state,is_readonly
                ) VALUES(10,'Demo','Source','C:/source',10,'A','WIP',0);
                INSERT INTO user_projects(user_id,project_id) VALUES(7,10);
                INSERT INTO bom(
                    id,type,name,aes_number,material,revision,lifecycle_state,status,project_id
                ) VALUES
                    (1,'asm','Assembly','A01','Steel','A','WIP','Design',10),
                    (2,'prt','Child','P01','Steel','A','WIP','Design',10),
                    (3,'prt','Other','P02','Plastic','A','WIP','Design',10);
                INSERT INTO bom_children(parent_id,child_id,quantity,sort_order)
                VALUES(1,2,2,10);
                """
            )
        self.repo = BomRevisionRepository(self.db_path)

    def tearDown(self):
        try:
            os.remove(self.db_path)
        except OSError:
            pass

    def test_legacy_rows_are_backfilled_as_iteration_one(self):
        self.assertEqual(self.repo.get_current_context(1)["version_label"], "A.1")
        status = self.repo.list_child_version_status(1)[0]
        self.assertEqual(status["bound_version"], "A.1")
        self.assertTrue(status["is_latest"])

    def test_new_child_iteration_does_not_silently_change_assembly(self):
        self.repo.initialize_checkout(1, 7)
        self.repo.record_checkin(2, 7, "Child geometry changed", "commit-child")

        self.assertEqual(self.repo.get_parent_binding_update_counts(10), {1: 1})

        status = self.repo.list_child_version_status(1)[0]
        self.assertEqual(status["bound_version"], "A.1")
        self.assertEqual(status["latest_version"], "A.2")
        self.assertFalse(status["is_latest"])

        self.repo.update_children_to_latest(1, [2], 7)
        self.assertEqual(self.repo.get_parent_binding_update_counts(10), {})
        parent = self.repo.record_checkin(1, 7, "Adopted child", "commit-parent")
        self.assertEqual(parent["version_label"], "A.2")
        self.assertEqual(self.repo.list_child_version_status(1)[0]["bound_version"], "A.2")

    def test_release_is_immutable_and_new_revision_copies_configuration(self):
        self.repo.release_current_revision(1, 7, "Approved")
        with self.assertRaises(ValueError):
            self.repo.assert_mutable(1)
        created = self.repo.create_revision(1, "A010", 7, "Change request")
        self.assertEqual(created["version_label"], "A010.1")
        self.assertEqual(created["state"], "In Work")
        self.assertEqual(self.repo.list_child_version_status(1)[0]["bound_version"], "A.1")

    def test_released_checkout_creates_revision_on_commit_and_preserves_cad_files(self):
        with self.repo.get_conn() as conn:
            conn.execute(
                "UPDATE bom SET filename='child.prt.17', drawing='child.drw.14' WHERE id=2"
            )
        released_iteration = self.repo.record_checkin(
            2, 7, "Captured released Creo files", "commit-a2"
        )
        self.repo.release_current_revision(2, 7, "Approved")

        pending = self.repo.prepare_released_checkout(2, "B")
        self.assertEqual(pending["version_label"], "A.2")
        self.repo.initialize_checkout(2, 7)
        still_released = self.repo.get_current_context(2)
        self.assertEqual(still_released["version_label"], "A.2")
        self.assertEqual(still_released["state"], "Released")
        self.assertEqual(still_released["pending_revision_code"], "B")

        with self.repo.get_conn() as conn:
            conn.execute(
                "UPDATE bom SET filename='child.prt.18', drawing='child.drw.15' WHERE id=2"
            )
        checked_in = self.repo.record_checkin(2, 7, "Revision B work", "commit-b1")
        self.assertEqual(checked_in["version_label"], "B.1")
        self.assertEqual(checked_in["state"], "In Work")
        self.assertIsNone(checked_in["pending_revision_code"])

        released_files = self.repo.get_iteration_cad_files(
            int(released_iteration["current_iteration_id"])
        )
        current_files = self.repo.get_iteration_cad_files(
            int(checked_in["current_iteration_id"])
        )
        self.assertEqual(
            (released_files["filename"], released_files["drawing"]),
            ("child.prt.17", "child.drw.14"),
        )
        self.assertEqual(
            (current_files["filename"], current_files["drawing"]),
            ("child.prt.18", "child.drw.15"),
        )

    def test_undo_released_checkout_removes_pending_revision(self):
        self.repo.release_current_revision(3, 7, "Approved")
        self.repo.prepare_released_checkout(3, "B")
        self.repo.initialize_checkout(3, 7)
        with self.repo.get_conn() as conn:
            conn.execute("UPDATE bom SET material='Changed' WHERE id=3")

        restored = self.repo.restore_checked_in_state(3)

        self.assertEqual(restored["version_label"], "A.1")
        self.assertEqual(self.repo.get_current_context(3)["state"], "Released")
        with self.repo.get_conn() as conn:
            row = conn.execute(
                "SELECT material, pending_revision_code FROM bom WHERE id=3"
            ).fetchone()
        self.assertEqual(tuple(row), ("Plastic", None))

    def test_revision_suggestions_cover_common_creo_schemes(self):
        suggest = self.repo.suggest_next_revision_code
        self.assertEqual(suggest("A"), "B")
        self.assertEqual(suggest("Z"), "AA")
        self.assertEqual(suggest("A010"), "A020")
        self.assertEqual(suggest("A0"), "A1")

    def test_undo_checkout_restores_attributes_and_structure_without_iteration(self):
        original_iteration_id = self.repo.get_current_context(1)["current_iteration_id"]
        self.repo.initialize_checkout(1, 7)
        with self.repo.get_conn() as conn:
            conn.execute("UPDATE bom SET material='Changed' WHERE id=1")
            conn.execute("DELETE FROM bom_children WHERE parent_id=1")
            conn.execute(
                "INSERT INTO bom_children(parent_id,child_id,quantity,sort_order) VALUES(1,3,1,10)"
            )
        self.repo.sync_working_bindings(1, 7)

        self.repo.restore_checked_in_state(1)

        with self.repo.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT material FROM bom WHERE id=1").fetchone()[0], "Steel")
            children = [
                row[0]
                for row in conn.execute(
                    "SELECT child_id FROM bom_children WHERE parent_id=1 ORDER BY sort_order,id"
                )
            ]
        self.assertEqual(children, [2])
        self.assertEqual(
            self.repo.get_current_context(1)["current_iteration_id"], original_iteration_id
        )

    def test_project_version_copy_remaps_exact_configuration(self):
        self.repo.record_checkin(2, 7, "Child update", "commit-child")
        self.repo.initialize_checkout(1, 7)
        self.repo.update_children_to_latest(1, [2], 7)
        self.repo.record_checkin(1, 7, "Assembly update", "commit-parent")

        new_project_id = ProjectRepository(self.db_path).create_project_version(
            source_project_id=10,
            user_id=7,
            new_working_directory="C:/target",
            version_label="B",
        )
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            copied = conn.execute(
                "SELECT id, aes_number FROM bom WHERE project_id=? ORDER BY id",
                (int(new_project_id),),
            ).fetchall()
        by_aes = {row["aes_number"]: int(row["id"]) for row in copied}
        copied_repo = BomRevisionRepository(self.db_path)
        self.assertEqual(copied_repo.get_current_context(by_aes["A01"])["version_label"], "A.2")
        self.assertEqual(copied_repo.get_current_context(by_aes["P01"])["version_label"], "A.2")
        status = copied_repo.list_child_version_status(by_aes["A01"])[0]
        self.assertEqual(status["child_bom_id"], by_aes["P01"])
        self.assertEqual(status["bound_version"], "A.2")

    def test_project_snapshot_contains_checked_in_versions_and_bindings(self):
        snapshot = self.repo.project_configuration_snapshot(10)
        versions = {row["aes_number"]: row["version"] for row in snapshot["objects"]}
        self.assertEqual(versions, {"A01": "A.1", "P01": "A.1", "P02": "A.1"})
        self.assertEqual(len(snapshot["bindings"]), 1)
        self.assertEqual(snapshot["bindings"][0]["child_aes_number"], "P01")
        self.assertEqual(snapshot["bindings"][0]["child_version"], "A.1")

    def test_legacy_iteration_table_is_upgraded_without_losing_history(self):
        fd, legacy_db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            with sqlite3.connect(legacy_db) as conn:
                conn.row_factory = sqlite3.Row
                conn.executescript(
                    """
                    CREATE TABLE bom (
                        id INTEGER PRIMARY KEY,
                        type TEXT, name TEXT, aes_number TEXT, part_number TEXT,
                        drawing_number TEXT, filename TEXT, drawing TEXT,
                        base_file_name TEXT, base_drw_name TEXT, material TEXT,
                        weight TEXT, notes TEXT, pdf_path TEXT, step_path TEXT,
                        revision TEXT DEFAULT 'A', lifecycle_state TEXT DEFAULT 'WIP',
                        status TEXT DEFAULT 'Design', modified TEXT, project_id INTEGER,
                        released_at TEXT, released_by INTEGER
                    );
                    CREATE TABLE bom_children (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        parent_id INTEGER, child_id INTEGER,
                        quantity INTEGER DEFAULT 1, sort_order INTEGER DEFAULT 0
                    );
                    CREATE TABLE baseline_files (id INTEGER PRIMARY KEY AUTOINCREMENT);
                    CREATE TABLE bom_revisions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        bom_id INTEGER NOT NULL,
                        revision_code TEXT NOT NULL COLLATE NOCASE,
                        state TEXT NOT NULL DEFAULT 'In Work',
                        created_at TEXT NOT NULL DEFAULT (datetime('now')),
                        created_by INTEGER, released_at TEXT, released_by INTEGER,
                        release_note TEXT, UNIQUE(bom_id, revision_code)
                    );
                    CREATE TABLE bom_iterations (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        revision_id INTEGER NOT NULL,
                        iteration_number INTEGER NOT NULL,
                        folder_path TEXT NOT NULL,
                        checkin_note TEXT,
                        created_at TEXT NOT NULL DEFAULT (datetime('now')),
                        created_by INTEGER,
                        commit_id INTEGER,
                        object_data_json TEXT,
                        UNIQUE(revision_id, iteration_number)
                    );
                    INSERT INTO bom(
                        id,type,name,aes_number,revision,lifecycle_state,status,project_id
                    ) VALUES(1,'prt','Legacy Part','L01','C','WIP','Design',10);
                    INSERT INTO bom_revisions(id,bom_id,revision_code,state)
                    VALUES(10,1,'A020','Released'),(11,1,'A030','In Work');
                    INSERT INTO bom_iterations(
                        id,revision_id,iteration_number,folder_path,checkin_note,commit_id
                    ) VALUES
                        (20,10,1,'plm/Rev_A020/A020.1','Released history',99),
                        (21,11,1,'plm/Rev_A030/A030.1','Working history',NULL);
                    """
                )
                _migration_22(conn)
                _migration_23(conn)

            legacy_repo = BomRevisionRepository(legacy_db)
            self.assertEqual(legacy_repo.get_current_context(1)["version_label"], "C.1")
            checked_in = legacy_repo.record_checkin(1, 7, "Legacy-compatible check-in", "new-commit")
            self.assertEqual(checked_in["version_label"], "C.2")

            with sqlite3.connect(legacy_db) as conn:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(bom_iterations)")}
                old_row = conn.execute(
                    "SELECT folder_path, source_commit_id FROM bom_iterations WHERE id=20"
                ).fetchone()
                new_folder = conn.execute(
                    "SELECT folder_path FROM bom_iterations WHERE id=?",
                    (int(checked_in["current_iteration_id"]),),
                ).fetchone()[0]
                count = conn.execute("SELECT COUNT(*) FROM bom_iterations").fetchone()[0]
            self.assertIn("source_commit_id", columns)
            self.assertEqual(old_row, ("plm/Rev_A020/A020.1", "99"))
            self.assertEqual(new_folder, "")
            self.assertEqual(count, 4)
        finally:
            try:
                os.remove(legacy_db)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
