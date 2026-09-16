import os
import sqlite3
import tempfile
import unittest

from core.repositories.bom_revision_repository import BomRevisionRepository
from core.repositories.project_repository import ProjectRepository
from core.services.project_service import ProjectService
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

    def test_compare_assembly_iterations_reports_exact_occurrence_changes(self):
        left_iteration_id = self.repo.get_current_context(1)["current_iteration_id"]
        with self.repo.get_conn() as conn:
            conn.execute(
                "UPDATE bom SET name='Child Updated', filename='child.prt.2' WHERE id=2"
            )
        self.repo.record_checkin(2, 7, "Child version", "commit-child")
        self.repo.initialize_checkout(1, 7)
        with self.repo.get_conn() as conn:
            conn.execute(
                "UPDATE bom_children SET quantity=3, sort_order=20 WHERE parent_id=1 AND child_id=2"
            )
            conn.execute(
                "INSERT INTO bom_children(parent_id,child_id,quantity,sort_order) VALUES(1,3,1,5)"
            )
        self.repo.sync_working_bindings(1, 7)
        self.repo.update_children_to_latest(1, [2], 7)
        right = self.repo.record_checkin(1, 7, "Assembly configuration", "commit-parent")

        comparison = self.repo.compare_assembly_iterations(
            1, int(left_iteration_id), int(right["current_iteration_id"])
        )

        self.assertEqual(comparison["left"]["version_label"], "A.1")
        self.assertEqual(comparison["right"]["version_label"], "A.2")
        self.assertEqual(comparison["summary"]["changed"], 2)
        self.assertEqual(comparison["summary"]["added"], 1)
        self.assertEqual(comparison["summary"]["version_changed"], 1)
        self.assertEqual(comparison["summary"]["quantity_changed"], 1)
        self.assertEqual(comparison["summary"]["order_changed"], 1)
        by_aes = {row["aes_number"]: row for row in comparison["rows"]}
        self.assertEqual(by_aes["P01"]["left"]["child_version"], "A.1")
        self.assertEqual(by_aes["P01"]["right"]["child_version"], "A.2")
        self.assertEqual(by_aes["P01"]["left"]["name"], "Child")
        self.assertEqual(by_aes["P01"]["right"]["name"], "Child Updated")
        self.assertEqual(by_aes["P01"]["left"]["filename"], "")
        self.assertEqual(by_aes["P01"]["right"]["filename"], "child.prt.2")
        self.assertEqual(
            set(by_aes["P01"]["change_types"]),
            {"version_changed", "quantity_changed", "order_changed"},
        )
        self.assertEqual(by_aes["P02"]["change"], "Added")

    def test_compare_assembly_iterations_keeps_repeated_component_occurrences_separate(self):
        left_iteration_id = self.repo.get_current_context(1)["current_iteration_id"]
        self.repo.initialize_checkout(1, 7)
        with self.repo.get_conn() as conn:
            conn.execute(
                "INSERT INTO bom_children(parent_id,child_id,quantity,sort_order) VALUES(1,2,1,20)"
            )
        self.repo.sync_working_bindings(1, 7)
        right = self.repo.record_checkin(1, 7, "Second child occurrence", "commit-parent")

        comparison = self.repo.compare_assembly_iterations(
            1, int(left_iteration_id), int(right["current_iteration_id"])
        )

        child_rows = [row for row in comparison["rows"] if row["aes_number"] == "P01"]
        self.assertEqual(len(child_rows), 2)
        self.assertEqual(comparison["summary"]["added"], 1)
        self.assertEqual(comparison["summary"]["unchanged"], 1)
        self.assertEqual(len({row["occurrence_key"] for row in child_rows}), 2)

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

    def test_project_version_copy_remaps_pdm_cad_layer_and_preserves_document_paths(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(
                """
                UPDATE bom
                SET pdf_path='D:/released/item.pdf', step_path='D:/released/item.step'
                WHERE id=1;

                CREATE TABLE cad_documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL,
                    number TEXT NOT NULL,
                    name TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    base_file_name TEXT NOT NULL,
                    authoring_application TEXT DEFAULT 'CREO',
                    category TEXT DEFAULT 'COMPONENT',
                    document_type TEXT DEFAULT 'CAD_DOCUMENT',
                    lifecycle_state TEXT DEFAULT 'IN_WORK',
                    revision TEXT DEFAULT 'A',
                    iteration INTEGER DEFAULT 1,
                    build_excluded INTEGER DEFAULT 0,
                    supplier_owner_item_id INTEGER,
                    legacy_bom_id INTEGER,
                    drawing_owner_cad_document_id INTEGER,
                    checked_out_by INTEGER,
                    checked_out_at TEXT,
                    checkout_item_id INTEGER,
                    checkout_workspace_id TEXT,
                    checkout_workspace_name TEXT,
                    checkout_workspace_machine_id TEXT,
                    latest_creo_file_version INTEGER,
                    latest_creo_file_name TEXT,
                    created_at TEXT DEFAULT (datetime('now')),
                    modified_at TEXT DEFAULT (datetime('now')),
                    UNIQUE(project_id, file_name)
                );
                CREATE TABLE cad_document_iterations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cad_document_id INTEGER NOT NULL,
                    revision TEXT DEFAULT 'A',
                    iteration INTEGER DEFAULT 1,
                    lifecycle_state TEXT DEFAULT 'IN_WORK',
                    primary_path TEXT,
                    source_file_name TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                );
                CREATE TABLE cad_document_contents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cad_document_id INTEGER NOT NULL,
                    content_role TEXT DEFAULT 'SECONDARY',
                    format TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    storage_path TEXT,
                    delivery_required INTEGER DEFAULT 0,
                    derived_from_content_id INTEGER,
                    created_at TEXT DEFAULT (datetime('now'))
                );
                CREATE TABLE cad_document_members (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    parent_cad_document_id INTEGER NOT NULL,
                    child_cad_document_id INTEGER NOT NULL,
                    quantity INTEGER DEFAULT 1,
                    sort_order INTEGER DEFAULT 0,
                    reference_designator TEXT,
                    component_path TEXT,
                    build_excluded INTEGER DEFAULT 0,
                    legacy_usage_id INTEGER,
                    created_at TEXT DEFAULT (datetime('now'))
                );
                CREATE TABLE cad_item_associations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL,
                    item_id INTEGER NOT NULL,
                    cad_document_id INTEGER NOT NULL,
                    association_type TEXT NOT NULL,
                    drives_structure INTEGER DEFAULT 0,
                    drives_attributes INTEGER DEFAULT 0,
                    participates_in_structure INTEGER DEFAULT 0,
                    active INTEGER DEFAULT 1,
                    created_by INTEGER,
                    created_at TEXT DEFAULT (datetime('now')),
                    modified_at TEXT DEFAULT (datetime('now')),
                    is_primary_drawing INTEGER DEFAULT 0,
                    drawing_model_cad_document_id INTEGER
                );
                CREATE TABLE item_usages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL,
                    parent_item_id INTEGER NOT NULL,
                    child_item_id INTEGER NOT NULL,
                    quantity INTEGER DEFAULT 1,
                    unit TEXT DEFAULT 'EA',
                    sort_order INTEGER DEFAULT 0,
                    source TEXT DEFAULT 'MANUAL',
                    cad_member_id INTEGER,
                    build_status TEXT DEFAULT 'COMPLETED',
                    legacy_usage_id INTEGER,
                    created_by INTEGER,
                    created_at TEXT DEFAULT (datetime('now')),
                    modified_at TEXT DEFAULT (datetime('now'))
                );
                CREATE TABLE item_occurrences (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_usage_id INTEGER NOT NULL,
                    occurrence_name TEXT,
                    source_cad_member_id INTEGER,
                    created_at TEXT DEFAULT (datetime('now'))
                );
                CREATE TABLE pdm_build_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL,
                    root_cad_document_id INTEGER NOT NULL,
                    direction TEXT DEFAULT 'CAD_TO_EBOM',
                    multi_level INTEGER DEFAULT 1,
                    status TEXT DEFAULT 'COMPLETED',
                    created_by INTEGER,
                    started_at TEXT DEFAULT (datetime('now')),
                    completed_at TEXT,
                    summary_json TEXT
                );
                CREATE TABLE pdm_build_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    build_run_id INTEGER NOT NULL,
                    cad_member_id INTEGER,
                    parent_item_id INTEGER,
                    child_item_id INTEGER,
                    status TEXT NOT NULL,
                    message TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                );
                CREATE TABLE item_structure_iterations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL,
                    parent_item_id INTEGER NOT NULL,
                    structure_iteration INTEGER NOT NULL,
                    item_revision TEXT DEFAULT 'A',
                    item_iteration_id INTEGER,
                    source TEXT NOT NULL,
                    build_run_id INTEGER,
                    structure_json TEXT NOT NULL,
                    created_by INTEGER,
                    created_at TEXT DEFAULT (datetime('now'))
                );
                CREATE TABLE part_files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    part_id INTEGER NOT NULL,
                    file_type TEXT,
                    display_name TEXT,
                    active_version_id INTEGER
                );
                CREATE TABLE part_file_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_id INTEGER NOT NULL,
                    version_no INTEGER,
                    original_filename TEXT,
                    vault_rel_path TEXT,
                    sha256 TEXT,
                    size_bytes INTEGER,
                    lifecycle_state TEXT DEFAULT 'WIP',
                    object_iteration_id INTEGER,
                    root_project_id INTEGER,
                    project_version_label TEXT
                );
                INSERT INTO cad_documents(
                    id,project_id,number,name,file_name,base_file_name,category,
                    legacy_bom_id,drawing_owner_cad_document_id,checked_out_by,
                    checkout_item_id,checkout_workspace_id,latest_creo_file_version,
                    latest_creo_file_name
                ) VALUES
                    (100,10,'assembly.asm','Assembly CAD','assembly.asm','assembly','ASSEMBLY',
                     1,NULL,7,1,'old-ws',3,'assembly.asm.3'),
                    (101,10,'child.prt','Child CAD','child.prt','child','COMPONENT',
                     2,NULL,NULL,NULL,NULL,2,'child.prt.2'),
                    (102,10,'assembly.drw','Assembly Drawing','assembly.drw','assembly','DRAWING',
                     1,100,NULL,NULL,NULL,1,'assembly.drw.1');
                INSERT INTO cad_document_iterations(cad_document_id,revision,iteration,primary_path,source_file_name)
                VALUES(100,'A',1,'assembly.asm','assembly.asm.3');
                INSERT INTO cad_document_contents(
                    id,cad_document_id,content_role,format,file_name,storage_path,derived_from_content_id
                ) VALUES
                    (200,100,'SECONDARY','PDF','item.pdf','D:/released/item.pdf',NULL),
                    (201,100,'SECONDARY','STEP','item.step','D:/released/item.step',200);
                INSERT INTO cad_document_members(
                    id,parent_cad_document_id,child_cad_document_id,quantity,sort_order,legacy_usage_id
                ) VALUES(300,100,101,2,10,1);
                INSERT INTO cad_item_associations(
                    project_id,item_id,cad_document_id,association_type,drives_structure,
                    drives_attributes,participates_in_structure,active,is_primary_drawing,
                    drawing_model_cad_document_id
                ) VALUES
                    (10,1,100,'OWNER',1,1,1,1,0,NULL),
                    (10,1,102,'CONTENT',0,0,1,1,1,100);
                INSERT INTO item_usages(
                    id,project_id,parent_item_id,child_item_id,quantity,source,cad_member_id
                ) VALUES(400,10,1,2,2,'CAD_BUILD',300);
                INSERT INTO item_occurrences(item_usage_id,occurrence_name,source_cad_member_id)
                VALUES(400,'child-1',300);
                INSERT INTO pdm_build_runs(id,project_id,root_cad_document_id,status,summary_json)
                VALUES(500,10,100,'COMPLETED','{}');
                INSERT INTO pdm_build_results(
                    build_run_id,cad_member_id,parent_item_id,child_item_id,status,message
                ) VALUES(500,300,1,2,'CREATED','ok');
                INSERT INTO item_structure_iterations(
                    project_id,parent_item_id,structure_iteration,item_revision,
                    item_iteration_id,source,build_run_id,structure_json,created_by
                ) VALUES(
                    10,1,1,'A',
                    (SELECT current_iteration_id FROM bom WHERE id=1),
                    'CAD_BUILD',500,
                    '[{"id":400,"parent_item_id":1,"child_item_id":2,"cad_member_id":300}]',
                    7
                );
                INSERT INTO part_files(id,part_id,file_type,display_name,active_version_id)
                VALUES(600,1,'PDF','Released PDF',700);
                INSERT INTO part_file_versions(
                    id,file_id,version_no,original_filename,vault_rel_path,sha256,
                    size_bytes,lifecycle_state,object_iteration_id,root_project_id,
                    project_version_label
                ) VALUES(
                    700,600,1,'item.pdf','vault/part_1/file_600/v1/item.pdf',
                    'abc',123,'Released',
                    (SELECT current_iteration_id FROM bom WHERE id=1),
                    10,'A'
                );
                """
            )

        new_project_id = ProjectRepository(self.db_path).create_project_version(
            source_project_id=10,
            user_id=7,
            new_working_directory="C:/target",
            version_label="B",
        )

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            copied_items = {
                row["aes_number"]: int(row["id"])
                for row in conn.execute(
                    "SELECT id,aes_number FROM bom WHERE project_id=?",
                    (new_project_id,),
                )
            }
            self.assertEqual(
                dict(conn.execute(
                    "SELECT pdf_path,step_path FROM bom WHERE id=?",
                    (copied_items["A01"],),
                ).fetchone()),
                {"pdf_path": "D:/released/item.pdf", "step_path": "D:/released/item.step"},
            )
            docs = {
                row["file_name"]: dict(row)
                for row in conn.execute(
                    "SELECT * FROM cad_documents WHERE project_id=?",
                    (new_project_id,),
                )
            }
            self.assertEqual(docs["assembly.asm"]["legacy_bom_id"], copied_items["A01"])
            self.assertIsNone(docs["assembly.asm"]["checked_out_by"])
            self.assertIsNone(docs["assembly.asm"]["checkout_workspace_id"])
            self.assertEqual(
                docs["assembly.drw"]["drawing_owner_cad_document_id"],
                docs["assembly.asm"]["id"],
            )
            member = conn.execute(
                """
                SELECT * FROM cad_document_members
                WHERE parent_cad_document_id=? AND child_cad_document_id=?
                """,
                (docs["assembly.asm"]["id"], docs["child.prt"]["id"]),
            ).fetchone()
            new_relation_id = conn.execute(
                "SELECT id FROM bom_children WHERE parent_id=? AND child_id=?",
                (copied_items["A01"], copied_items["P01"]),
            ).fetchone()[0]
            self.assertEqual(member["legacy_usage_id"], new_relation_id)
            drawing_assoc = conn.execute(
                """
                SELECT * FROM cad_item_associations
                WHERE project_id=? AND cad_document_id=?
                """,
                (new_project_id, docs["assembly.drw"]["id"]),
            ).fetchone()
            self.assertEqual(drawing_assoc["item_id"], copied_items["A01"])
            self.assertEqual(
                drawing_assoc["drawing_model_cad_document_id"],
                docs["assembly.asm"]["id"],
            )
            usage = conn.execute(
                "SELECT * FROM item_usages WHERE project_id=?",
                (new_project_id,),
            ).fetchone()
            self.assertEqual(usage["parent_item_id"], copied_items["A01"])
            self.assertEqual(usage["child_item_id"], copied_items["P01"])
            self.assertEqual(usage["cad_member_id"], member["id"])
            occurrence = conn.execute(
                "SELECT * FROM item_occurrences WHERE item_usage_id=?",
                (usage["id"],),
            ).fetchone()
            self.assertEqual(occurrence["source_cad_member_id"], member["id"])
            content_rows = [
                dict(row) for row in conn.execute(
                    "SELECT * FROM cad_document_contents WHERE cad_document_id=? ORDER BY id",
                    (docs["assembly.asm"]["id"],),
                )
            ]
            self.assertEqual(content_rows[0]["storage_path"], "D:/released/item.pdf")
            self.assertEqual(content_rows[1]["storage_path"], "D:/released/item.step")
            self.assertEqual(content_rows[1]["derived_from_content_id"], content_rows[0]["id"])
            copied_version = conn.execute(
                """
                SELECT v.*,pf.part_id
                FROM part_file_versions v
                JOIN part_files pf ON pf.id=v.file_id
                WHERE pf.part_id=?
                """,
                (copied_items["A01"],),
            ).fetchone()
            self.assertEqual(copied_version["vault_rel_path"], "vault/part_1/file_600/v1/item.pdf")
            self.assertEqual(copied_version["project_version_label"], "A")
            snapshot = conn.execute(
                "SELECT * FROM item_structure_iterations WHERE project_id=?",
                (new_project_id,),
            ).fetchone()
            self.assertIn(f'"parent_item_id":{copied_items["A01"]}', snapshot["structure_json"])
            self.assertIn(f'"child_item_id":{copied_items["P01"]}', snapshot["structure_json"])
            self.assertIn(f'"cad_member_id":{member["id"]}', snapshot["structure_json"])

    def test_project_version_working_directory_copy_skips_transient_and_document_folders(self):
        with tempfile.TemporaryDirectory() as src_dir, tempfile.TemporaryDirectory() as dst_parent:
            dst_dir = os.path.join(dst_parent, "version_b")
            os.makedirs(os.path.join(src_dir, "commits"), exist_ok=True)
            os.makedirs(os.path.join(src_dir, "pull resuests"), exist_ok=True)
            os.makedirs(os.path.join(src_dir, "branches"), exist_ok=True)
            os.makedirs(os.path.join(src_dir, "vault"), exist_ok=True)
            os.makedirs(os.path.join(src_dir, "cad"), exist_ok=True)
            with open(os.path.join(src_dir, "part.prt.1"), "w", encoding="utf-8") as handle:
                handle.write("old")
            with open(os.path.join(src_dir, "part.prt.3"), "w", encoding="utf-8") as handle:
                handle.write("new")
            with open(os.path.join(src_dir, "cad", "asm.asm.2"), "w", encoding="utf-8") as handle:
                handle.write("asm")
            for folder in ("commits", "pull resuests", "branches", "vault"):
                with open(os.path.join(src_dir, folder, "skip.txt"), "w", encoding="utf-8") as handle:
                    handle.write("skip")

            service = ProjectService()
            service._purge_copy_working_directory(src_dir, dst_dir)

            self.assertFalse(os.path.exists(os.path.join(dst_dir, "part.prt.1")))
            self.assertTrue(os.path.exists(os.path.join(dst_dir, "part.prt.3")))
            self.assertTrue(os.path.exists(os.path.join(dst_dir, "cad", "asm.asm.2")))
            self.assertFalse(os.path.exists(os.path.join(dst_dir, "commits")))
            self.assertFalse(os.path.exists(os.path.join(dst_dir, "pull resuests")))
            self.assertFalse(os.path.exists(os.path.join(dst_dir, "branches")))
            self.assertFalse(os.path.exists(os.path.join(dst_dir, "vault")))

    def test_project_version_cleanup_removes_previous_pull_request_folders(self):
        with tempfile.TemporaryDirectory() as version_a, tempfile.TemporaryDirectory() as version_b:
            for folder in ("pull resuests", "pull_requests"):
                os.makedirs(os.path.join(version_a, folder), exist_ok=True)
            os.makedirs(os.path.join(version_b, "pull resuests"), exist_ok=True)
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "UPDATE projects SET working_directory=? WHERE id=10",
                    (version_a,),
                )
                conn.execute(
                    """
                    INSERT INTO projects(
                        id,name,description,working_directory,root_project_id,
                        version_label,version_state,is_readonly
                    ) VALUES(11,'Demo','Target',?,10,'B','WIP',0)
                    """,
                    (version_b,),
                )

            service = ProjectService()
            service.project_repo = ProjectRepository(self.db_path)
            service._cleanup_previous_pull_request_dirs(10, 11)

            self.assertFalse(os.path.exists(os.path.join(version_a, "pull resuests")))
            self.assertFalse(os.path.exists(os.path.join(version_a, "pull_requests")))
            self.assertTrue(os.path.exists(os.path.join(version_b, "pull resuests")))

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
