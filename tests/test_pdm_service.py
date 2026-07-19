import os
import sqlite3
import tempfile
import unittest
import csv

from core.repositories.pdm_repository import PdmRepository
from core.services.pdm_service import PdmBuildError, PdmService
from setup.migrations import _migration_32


class PdmServiceTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE bom (
                    id INTEGER PRIMARY KEY,
                    type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    part_number TEXT,
                    drawing_number TEXT,
                    aes_number TEXT,
                    filename TEXT,
                    drawing TEXT,
                    base_file_name TEXT,
                    base_drw_name TEXT,
                    material TEXT,
                    weight TEXT,
                    notes TEXT,
                    pdf_path TEXT,
                    step_path TEXT,
                    revision TEXT DEFAULT 'A',
                    lifecycle_state TEXT DEFAULT 'WIP',
                    status TEXT DEFAULT 'Design',
                    created TEXT,
                    modified TEXT,
                    project_id INTEGER,
                    represented_part_id INTEGER,
                    default_ebom_behavior TEXT DEFAULT 'NORMAL'
                );
                CREATE TABLE bom_children (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    parent_id INTEGER NOT NULL,
                    child_id INTEGER NOT NULL,
                    quantity INTEGER DEFAULT 1,
                    sort_order INTEGER DEFAULT 0,
                    ebom_behavior TEXT DEFAULT 'INHERIT'
                );
                INSERT INTO bom(
                    id,type,name,aes_number,filename,base_file_name,
                    revision,lifecycle_state,status,project_id
                ) VALUES
                    (1,'asm','Machine','AES-100','machine.asm','machine','A','WIP','Design',7),
                    (2,'prt','Bracket','AES-200','bracket.prt','bracket','A','WIP','Design',7),
                    (3,'prt','Adhesive','AES-300',NULL,NULL,'A','WIP','Design',7);
                INSERT INTO bom(
                    id,type,name,aes_number,filename,base_file_name,
                    revision,lifecycle_state,status,project_id,
                    represented_part_id,default_ebom_behavior
                ) VALUES
                    (4,'prt','Bracket simplified','AES-200','bracket_sim.prt','bracket_sim',
                     'A','WIP','Design',7,2,'EXCLUDE');
                INSERT INTO bom_children(parent_id,child_id,quantity,sort_order)
                VALUES(1,2,2,10);
                """
            )
            _migration_32(conn)

    def tearDown(self):
        try:
            os.remove(self.db_path)
        except OSError:
            pass

    def test_migration_separates_cad_and_items_and_is_idempotent(self):
        with sqlite3.connect(self.db_path) as conn:
            counts_before = {
                table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "cad_documents", "cad_item_associations",
                    "cad_document_members", "item_usages",
                )
            }
            _migration_32(conn)
            counts_after = {
                table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in counts_before
            }
            self.assertEqual(counts_before, counts_after)
            alternate = conn.execute(
                """
                SELECT a.item_id,a.association_type
                FROM cad_documents d
                JOIN cad_item_associations a ON a.cad_document_id=d.id AND a.active=1
                WHERE d.file_name='bracket_sim.prt'
                """
            ).fetchone()
            self.assertEqual(tuple(alternate), (2, "IMAGE"))
            self.assertEqual(
                conn.execute("SELECT quantity FROM item_usages WHERE parent_item_id=1 AND child_item_id=2").fetchone()[0],
                2,
            )

    def test_item_without_cad_is_valid_and_visible_in_persisted_ebom(self):
        service = PdmService(self.db_path)
        repo = PdmRepository(self.db_path)
        repo.add_manual_item_usage(7, 1, 3, 1)
        result = service.get_item_structure_project(7)
        root = next(row for row in result["roots"] if row["bom_id"] == 1)
        self.assertEqual(
            {(row["bom_id"], row["source"]) for row in root["children"]},
            {(2, "CAD_BUILD"), (3, "MANUAL")},
        )

    def test_drawing_is_bound_to_model_and_never_becomes_a_structure_node(self):
        service = PdmService(self.db_path)
        repo = service.repo
        model = repo.get_cad_document_by_file(7, "machine.asm")

        with self.assertRaisesRegex(ValueError, "PRT or ASM"):
            repo.create_cad_document(
                7, "MACHINE-DRW", "Machine drawing", "machine.drw",
                category="DRAWING",
            )
        drawing_id = repo.create_cad_document(
            7, "MACHINE-DRW", "Machine drawing", "machine.drw",
            category="DRAWING",
            drawing_owner_cad_document_id=int(model["id"]),
        )

        structure = service.get_cad_structure_project(7)
        root = next(
            row for row in structure["roots"]
            if int(row["id"]) == int(model["id"])
        )
        self.assertEqual(
            [row["file_name"] for row in root["related_drawings"]],
            ["machine.drw"],
        )
        self.assertNotIn(
            drawing_id,
            {
                int(row["id"])
                for row in structure["roots"]
            },
        )
        with self.assertRaisesRegex(ValueError, "drawings are related files"):
            repo.add_cad_member(int(model["id"]), drawing_id)

    def test_build_reports_unassociated_cad_then_builds_after_image_association(self):
        service = PdmService(self.db_path)
        repo = service.repo
        root = repo.get_cad_document_by_file(7, "machine.asm")
        cad_id = repo.create_cad_document(
            7, "ADHESIVE-CAD", "Adhesive envelope", "adhesive_envelope.prt"
        )
        member_id = repo.add_cad_member(int(root["id"]), cad_id, 3)

        comparison = service.compare_cad_to_item(int(root["id"]))
        row = next(row for row in comparison["rows"] if int(row["id"]) == member_id)
        self.assertEqual(row["status"], "NO_RELATED_ITEM")

        first_build = service.build_part_structure(int(root["id"]))
        self.assertEqual(first_build["no_related_item"], 1)

        service.associate(7, 3, cad_id, "IMAGE")
        second_build = service.build_part_structure(int(root["id"]))
        self.assertGreaterEqual(second_build["created"], 1)
        usages = repo.list_item_usages(1)
        built = next(row for row in usages if int(row.get("cad_member_id") or 0) == member_id)
        self.assertEqual((built["child_item_id"], built["quantity"]), (3, 3))

    def test_owner_constraint_and_manual_usage_survives_rebuild(self):
        service = PdmService(self.db_path)
        repo = service.repo
        root = repo.get_cad_document_by_file(7, "machine.asm")
        other = repo.create_cad_document(7, "MACHINE-ALT", "Other machine", "machine_alt.asm", category="ASSEMBLY")
        with self.assertRaisesRegex(ValueError, "already has an OWNER"):
            service.associate(7, 1, other, "OWNER")

        manual_id = repo.add_manual_item_usage(7, 1, 3, 4)
        service.build_part_structure(int(root["id"]))
        with repo.get_conn() as conn:
            manual = conn.execute("SELECT * FROM item_usages WHERE id=?", (manual_id,)).fetchone()
        self.assertIsNotNone(manual)
        self.assertEqual((manual["source"], manual["quantity"]), ("MANUAL", 4))

    def test_build_requires_owner_root(self):
        service = PdmService(self.db_path)
        cad_id = service.repo.create_cad_document(7, "FREE", "Free CAD", "free.prt")
        with self.assertRaises(PdmBuildError):
            service.build_part_structure(cad_id)

    def test_cad_structure_rejects_cycles_and_removal_clears_built_usage(self):
        service = PdmService(self.db_path)
        root = service.repo.get_cad_document_by_file(7, "machine.asm")
        child = service.repo.get_cad_document_by_file(7, "bracket.prt")
        existing_member = next(
            row for row in service.repo.list_cad_members(int(root["id"]))
            if int(row["child_cad_document_id"]) == int(child["id"])
        )
        with self.assertRaisesRegex(ValueError, "circular"):
            service.add_cad_member(int(child["id"]), int(root["id"]))
        service.build_part_structure(int(root["id"]))
        self.assertTrue(service.remove_cad_member(int(existing_member["id"])))
        with service.repo.get_conn() as conn:
            usage = conn.execute(
                "SELECT id FROM item_usages WHERE cad_member_id=?",
                (int(existing_member["id"]),),
            ).fetchone()
        self.assertIsNotNone(usage)
        statuses = {
            row["status"] for row in service.compare_cad_to_item(int(root["id"]))["rows"]
        }
        self.assertIn("NOT_NEEDED_IN_ITEM_STRUCTURE", statuses)
        service.build_part_structure(int(root["id"]))
        with service.repo.get_conn() as conn:
            usage = conn.execute(
                "SELECT id FROM item_usages WHERE cad_member_id=?",
                (int(existing_member["id"]),),
            ).fetchone()
        self.assertIsNone(usage)

    def test_build_and_manual_changes_capture_item_structure_iterations(self):
        service = PdmService(self.db_path)
        root = service.repo.get_cad_document_by_file(7, "machine.asm")
        service.build_part_structure(int(root["id"]), actor_id=9)
        service.add_manual_item_usage(7, 1, 3, 2, actor_id=9)
        snapshots = service.repo.list_item_structure_iterations(1)
        self.assertEqual([row["source"] for row in snapshots], ["MANUAL", "CAD_BUILD"])
        self.assertEqual([row["structure_iteration"] for row in snapshots], [2, 1])

    def test_persisted_item_structure_export_includes_manual_and_cad_sources(self):
        service = PdmService(self.db_path)
        service.repo.add_manual_item_usage(7, 1, 3, 4)
        export_path = os.path.join(self.tmp_export_dir(), "item_structure.csv")
        result = service.export_item_structure(1, export_path)
        with open(export_path, newline="", encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(result["row_count"], 3)
        self.assertEqual({row["source"] for row in rows}, {"ROOT", "CAD_BUILD", "MANUAL"})

    def tmp_export_dir(self):
        directory = os.path.dirname(self.db_path)
        self.addCleanup(
            lambda: os.path.exists(os.path.join(directory, "item_structure.csv"))
            and os.remove(os.path.join(directory, "item_structure.csv"))
        )
        return directory

    def test_cad_document_checkout_checkin_revision_and_release(self):
        service = PdmService(self.db_path)
        document = service.repo.get_cad_document_by_file(7, "bracket.prt")
        source = os.path.join(os.path.dirname(self.db_path), "bracket.prt.2")
        with open(source, "wb") as handle:
            handle.write(b"new bracket iteration")
        self.addCleanup(lambda: os.path.exists(source) and os.remove(source))
        checked_out = service.checkout_cad_document(int(document["id"]), 44)
        self.assertEqual(checked_out["checked_out_by"], 44)
        with self.assertRaisesRegex(ValueError, "another user"):
            service.checkout_cad_document(int(document["id"]), 45)
        checked_in = service.checkin_cad_document(
            int(document["id"]), 44, source, "geometry update"
        )
        self.assertEqual(checked_in["iteration"], 2)
        self.assertIsNone(checked_in["checked_out_by"])
        revised = service.revise_cad_document(int(document["id"]), 44)
        self.assertEqual((revised["revision"], revised["iteration"]), ("B", 1))
        released = service.release_cad_document(int(document["id"]))
        self.assertEqual(released["lifecycle_state"], "RELEASED")


if __name__ == "__main__":
    unittest.main()
