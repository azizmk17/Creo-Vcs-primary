import os
import sqlite3
import tempfile
import unittest

from core.repositories.bom_revision_repository import BomRevisionRepository
from core.services.ebom_service import EbomResolutionError, EbomResolver
from core.services.release_validation_service import ReleaseValidationService


class EbomServiceTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE bom (
                    id INTEGER PRIMARY KEY, type TEXT, name TEXT,
                    aes_number TEXT, part_number TEXT, drawing_number TEXT,
                    filename TEXT, drawing TEXT, base_file_name TEXT,
                    base_drw_name TEXT, material TEXT, weight TEXT, notes TEXT,
                    pdf_path TEXT, step_path TEXT, revision TEXT DEFAULT 'A',
                    lifecycle_state TEXT DEFAULT 'WIP', status TEXT DEFAULT 'Design',
                    modified TEXT, project_id INTEGER,
                    classification TEXT NOT NULL DEFAULT 'PHYSICAL',
                    default_ebom_behavior TEXT NOT NULL DEFAULT 'NORMAL',
                    cad_requirement TEXT NOT NULL DEFAULT 'OPTIONAL',
                    drawing_requirement TEXT NOT NULL DEFAULT 'OPTIONAL'
                );
                CREATE TABLE bom_children (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, parent_id INTEGER,
                    child_id INTEGER, quantity INTEGER DEFAULT 1,
                    sort_order INTEGER DEFAULT 0,
                    ebom_behavior TEXT NOT NULL DEFAULT 'INHERIT'
                );
                """
            )

    def tearDown(self):
        try:
            os.remove(self.db_path)
        except OSError:
            pass

    def _insert_bom(
        self, bom_id, item_type, name, *, classification="PHYSICAL",
        default_behavior="NORMAL", cad_requirement="OPTIONAL",
        drawing_requirement="OPTIONAL", filename="", drawing="",
    ):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO bom(
                    id,type,name,aes_number,filename,drawing,revision,
                    lifecycle_state,status,project_id,classification,
                    default_ebom_behavior,cad_requirement,drawing_requirement
                ) VALUES(?,?,?,?,?,?,'A','WIP','Design',10,?,?,?,?)
                """,
                (
                    int(bom_id), item_type, name, name.upper(), filename, drawing,
                    classification, default_behavior, cad_requirement,
                    drawing_requirement,
                ),
            )

    def _add_child(
        self, parent_id, child_id, quantity, behavior="INHERIT", sort_order=10
    ):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO bom_children(
                    parent_id,child_id,quantity,sort_order,ebom_behavior
                ) VALUES(?,?,?,?,?)
                """,
                (parent_id, child_id, quantity, sort_order, behavior),
            )

    def _resolver(self):
        repo = BomRevisionRepository(self.db_path)
        return repo, EbomResolver(self.db_path)

    def test_single_level_flatten_multiplies_quantity(self):
        self._insert_bom(1, "asm", "top")
        self._insert_bom(
            2, "asm", "cad_only", classification="CAD_ONLY",
            default_behavior="FLATTEN",
        )
        self._insert_bom(3, "prt", "bolt")
        self._add_child(1, 2, 2)
        self._add_child(2, 3, 3)
        repo, resolver = self._resolver()

        result = resolver.resolve_bom(1)

        self.assertEqual(len(result["rows"]), 1)
        bolt = result["rows"][0]
        self.assertEqual(bolt["bom_id"], 3)
        self.assertEqual(bolt["source_quantity"], 3)
        self.assertEqual(bolt["effective_quantity"], 6)
        self.assertEqual(bolt["level"], 1)
        self.assertEqual([row["bom_id"] for row in bolt["promoted_through"]], [2])
        del repo

    def test_nested_flatten_multiplies_every_flattened_branch(self):
        self._insert_bom(1, "asm", "top")
        self._insert_bom(2, "asm", "cad_a", default_behavior="FLATTEN")
        self._insert_bom(3, "asm", "cad_b", default_behavior="FLATTEN")
        self._insert_bom(4, "prt", "nut")
        self._add_child(1, 2, 2)
        self._add_child(2, 3, 3)
        self._add_child(3, 4, 5)
        _repo, resolver = self._resolver()

        nut = resolver.resolve_bom(1)["rows"][0]

        self.assertEqual(nut["bom_id"], 4)
        self.assertEqual(nut["source_quantity"], 5)
        self.assertEqual(nut["effective_quantity"], 30)
        self.assertEqual([row["bom_id"] for row in nut["promoted_through"]], [2, 3])

    def test_occurrence_override_replaces_object_default(self):
        self._insert_bom(1, "asm", "top")
        self._insert_bom(2, "asm", "hidden_by_default", default_behavior="EXCLUDE")
        self._insert_bom(3, "prt", "child")
        self._add_child(1, 2, 1, behavior="NORMAL")
        self._add_child(2, 3, 1)
        _repo, resolver = self._resolver()

        result = resolver.resolve_bom(1)

        self.assertEqual([row["bom_id"] for row in result["rows"]], [2, 3])
        self.assertEqual(result["rows"][0]["ebom_behavior"], "NORMAL")
        self.assertEqual(result["rows"][0]["resolved_ebom_behavior"], "NORMAL")

    def test_exclude_removes_entire_branch(self):
        self._insert_bom(1, "asm", "top")
        self._insert_bom(2, "asm", "excluded")
        self._insert_bom(3, "prt", "descendant")
        self._add_child(1, 2, 1, behavior="EXCLUDE")
        self._add_child(2, 3, 1)
        _repo, resolver = self._resolver()

        result = resolver.resolve_bom(1)

        self.assertEqual(result["rows"], [])
        self.assertEqual(result["root"]["children"], [])

    def test_project_view_never_emits_excluded_or_flattened_cad_roots(self):
        self._insert_bom(
            1, "asm", "internal_reference", classification="REFERENCE",
            default_behavior="EXCLUDE",
        )
        self._insert_bom(
            2, "asm", "cad_group", classification="CAD_ONLY",
            default_behavior="FLATTEN",
        )
        self._insert_bom(3, "prt", "deliverable_part")
        self._add_child(2, 3, 4)
        _repo, resolver = self._resolver()

        result = resolver.resolve_project(10)

        self.assertEqual([row["bom_id"] for row in result["roots"]], [3])
        self.assertEqual([row["bom_id"] for row in result["excluded_roots"]], [1])
        self.assertEqual([row["bom_id"] for row in result["flattened_roots"]], [2])
        self.assertEqual(result["roots"][0]["level"], 0)
        self.assertEqual(result["roots"][0]["effective_quantity"], 4)
        self.assertEqual(
            [row["bom_id"] for row in result["roots"][0]["promoted_through"]],
            [2],
        )
        self.assertTrue(
            all(
                row["resolved_ebom_behavior"] == "NORMAL"
                for row in [*result["roots"], *result["rows"]]
            )
        )

    def test_explicit_excluded_root_has_no_exportable_ebom_rows(self):
        self._insert_bom(
            1, "asm", "internal_tooling", classification="CAD_ONLY",
            default_behavior="EXCLUDE",
        )
        self._insert_bom(2, "prt", "internal_child")
        self._add_child(1, 2, 7)
        _repo, resolver = self._resolver()

        result = resolver.resolve_bom(1)

        self.assertEqual(result["roots"], [])
        self.assertEqual(result["rows"], [])
        self.assertFalse(result["root_visible"])
        self.assertEqual(result["root_behavior"], "EXCLUDE")

    def test_historical_occurrence_behavior_is_frozen(self):
        self._insert_bom(1, "asm", "top")
        self._insert_bom(2, "prt", "child")
        self._add_child(1, 2, 1, behavior="NORMAL")
        repo, resolver = self._resolver()
        old_iteration = repo.get_current_context(1)["current_iteration_id"]
        repo.initialize_checkout(1, 7)
        with repo.get_conn() as conn:
            conn.execute(
                "UPDATE bom_children SET ebom_behavior='EXCLUDE' WHERE parent_id=1"
            )
        new_context = repo.record_checkin(1, 7, "Exclude occurrence", "commit-ebom")

        old_result = resolver.resolve_iteration(old_iteration)
        new_result = resolver.resolve_iteration(new_context["current_iteration_id"])

        self.assertEqual([row["bom_id"] for row in old_result["rows"]], [2])
        self.assertEqual(new_result["rows"], [])
        with repo.get_conn() as conn:
            frozen = conn.execute(
                """
                SELECT ebom_behavior FROM bom_iteration_bindings
                WHERE parent_iteration_id IN (?,?) ORDER BY parent_iteration_id
                """,
                (old_iteration, new_context["current_iteration_id"]),
            ).fetchall()
        self.assertEqual([row[0] for row in frozen], ["NORMAL", "EXCLUDE"])

    def test_configuration_members_resolve_their_frozen_occurrence_rules(self):
        self._insert_bom(1, "asm", "top")
        self._insert_bom(2, "asm", "cad_group")
        self._insert_bom(3, "prt", "bolt")
        self._add_child(1, 2, 2)
        self._add_child(2, 3, 3)
        repo, resolver = self._resolver()
        root_iteration = repo.get_current_context(1)["current_iteration_id"]
        snapshot = repo.get_iteration_structure_snapshot(1, root_iteration)
        members = snapshot["members"]
        group = next(member for member in members if member["bom_id"] == 2)
        group["ebom_behavior"] = "FLATTEN"

        resolved = resolver.resolve_configuration_members(1, members)

        self.assertEqual([row["bom_id"] for row in resolved["rows"]], [3])
        self.assertEqual(resolved["rows"][0]["effective_quantity"], 6)
        self.assertEqual(
            [row["bom_id"] for row in resolved["rows"][0]["promoted_through"]],
            [2],
        )

    def test_cad_only_drawing_not_required_does_not_block_release(self):
        self._insert_bom(
            1, "asm", "cad_only", classification="CAD_ONLY",
            cad_requirement="REQUIRED", drawing_requirement="NOT_REQUIRED",
            filename="cad_only.asm.1", drawing="",
        )
        self._insert_bom(
            2, "asm", "missing_cad", classification="CAD_ONLY",
            cad_requirement="REQUIRED", drawing_requirement="NOT_REQUIRED",
            filename="", drawing="",
        )
        repo, _resolver = self._resolver()
        validator = ReleaseValidationService(self.db_path, revision_repo=repo)

        self.assertEqual(validator.validate_bom(1), [])
        findings = validator.validate_bom(2)
        self.assertEqual([row["requirement"] for row in findings], ["cad_requirement"])

    def test_cycle_detection_reports_invalid_recursive_structure(self):
        self._insert_bom(1, "asm", "top")
        self._insert_bom(2, "asm", "sub")
        self._add_child(1, 2, 1)
        self._add_child(2, 1, 1)
        _repo, resolver = self._resolver()

        with self.assertRaisesRegex(EbomResolutionError, "Circular EBOM structure"):
            resolver.resolve_bom(1)


if __name__ == "__main__":
    unittest.main()
