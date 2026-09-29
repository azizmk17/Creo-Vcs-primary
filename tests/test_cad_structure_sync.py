import copy
import json
from pathlib import Path
import tempfile
import unittest

from tests import test_pdm_service as fixture
from core.services.cad_structure_sync_service import CadStructureSyncService


class FakeScanner:
    def __init__(self, documents):
        self.documents = documents
        self.calls = 0
        self.complete = True

    def signature(self):
        return "test-scanner-v1"

    def run(self, sources, roots, *, cancel=None):
        self.calls += 1
        return {"schema": 1, "complete": self.complete,
                "documents": copy.deepcopy(self.documents), "errors": []}


def solid(name, children=()):
    return {"file_name": name, "complete": True, "occurrences": [
        {"child": child, "feature_id": index, "status": "ACTIVE"}
        for index, child in enumerate(children)], "drawing_models": []}


class CadStructureSyncTests(unittest.TestCase):
    def setUp(self):
        fixture.PdmServiceTests.setUp(self)
        self.folder = tempfile.TemporaryDirectory()
        self.directory = Path(self.folder.name)
        self.scanner = FakeScanner([solid("machine.asm", ["bracket.prt"] * 3), solid("bracket.prt")])
        self.service = CadStructureSyncService(self.db_path, self.scanner)
        with self.service.repo.get_conn() as conn:
            conn.execute("CREATE TABLE projects(id INTEGER PRIMARY KEY, working_directory TEXT, version_label TEXT, is_readonly INTEGER)")
            conn.execute("INSERT INTO projects VALUES(7,?,'B',0)", (str(self.directory),))
            self.root = conn.execute("SELECT id FROM cad_documents WHERE file_name='machine.asm'").fetchone()[0]
        for name in ("machine.asm", "bracket.prt", "bracket_sim.prt"):
            (self.directory / name).write_bytes(b"fixture:" + name.encode())

    def tearDown(self):
        self.folder.cleanup()
        fixture.PdmServiceTests.tearDown(self)

    def scan(self):
        return self.service.scan(7, self.root, 1)

    def apply(self, result):
        return self.service.apply(result["id"], 1, can_manage=True, can_merge=True)

    def test_repeated_components_register_and_bind_without_changing_ebom(self):
        self.scanner.documents[0] = solid("machine.asm", ["bracket.prt"] * 3 + ["new.prt"])
        self.scanner.documents += [solid("new.prt"), {"file_name": "new.drw", "complete": True,
                                  "drawing_models": ["new.prt"], "occurrences": []}]
        (self.directory / "new.prt").write_bytes(b"#UGC:2 PART\nnative fixture")
        (self.directory / "new.drw").write_bytes(b"#UGC:2 DRAWING\nnative fixture")
        with self.service.repo.get_conn() as conn:
            before = [tuple(r) for r in conn.execute("SELECT * FROM item_usages")]
            old_member = conn.execute("SELECT id FROM cad_document_members WHERE parent_cad_document_id=?", (self.root,)).fetchone()[0]
        result = self.scan()
        self.assertEqual(result["plan"]["problems"], [])
        self.apply(result)
        with self.service.repo.get_conn() as conn:
            self.assertEqual(before, [tuple(r) for r in conn.execute("SELECT * FROM item_usages")])
            self.assertEqual(conn.execute("SELECT quantity FROM cad_document_members WHERE id=?", (old_member,)).fetchone()[0], 3)
            new = conn.execute("SELECT * FROM cad_documents WHERE file_name='new.prt'").fetchone()
            drawing = conn.execute("SELECT * FROM cad_documents WHERE file_name='new.drw'").fetchone()
            self.assertEqual(new["revision"], "B")
            self.assertEqual(drawing["drawing_owner_cad_document_id"], new["id"])
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.service.assert_current(self.root)
        with self.assertRaisesRegex(ValueError, "already been applied"):
            self.apply(result)

    def test_removal_keeps_ebom_and_detaches_build_link(self):
        self.scanner.documents = [solid("machine.asm")]
        result = self.scan()
        self.apply(result)
        with self.service.repo.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM cad_document_members WHERE parent_cad_document_id=?", (self.root,)).fetchone()[0], 0)
            usage = conn.execute("SELECT quantity,cad_member_id FROM item_usages WHERE parent_item_id=1").fetchone()
            self.assertEqual(tuple(usage), (2, None))

    def test_partial_scan_and_suppression_cannot_remove_members(self):
        self.scanner.complete = False
        self.scanner.documents = [solid("machine.asm")]
        result = self.scan()
        with self.assertRaisesRegex(ValueError, "conflicts"):
            self.apply(result)
        self.scanner.complete = True
        self.scanner.documents = [solid("machine.asm", ["bracket.prt"]), solid("bracket.prt")]
        self.scanner.documents[0]["occurrences"][0]["status"] = "SUPPRESSED"
        result = self.scan()
        with self.assertRaisesRegex(ValueError, "Suppressed"):
            self.apply(result)
        with self.service.repo.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM cad_document_members WHERE parent_cad_document_id=?", (self.root,)).fetchone()[0], 1)

    def test_stale_files_and_concurrent_database_changes_block_apply(self):
        result = self.scan()
        (self.directory / "bracket.prt").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "files changed"):
            self.apply(result)
        result = self.scan()
        with self.service.repo.get_conn() as conn:
            conn.execute("UPDATE cad_documents SET checked_out_by=2 WHERE id=?", (self.root,))
        with self.assertRaisesRegex(ValueError, "Nexus changed"):
            self.apply(result)
        result = self.scan()
        with self.assertRaisesRegex(ValueError, "another user"):
            self.apply(result)

    def test_controlled_version_never_falls_forward_and_no_recursive_import(self):
        (self.directory / "machine.asm.99").write_bytes(b"unapproved")
        (self.directory / "branches").mkdir()
        (self.directory / "branches" / "secret.prt").write_bytes(b"branch")
        with self.service.repo.get_conn() as conn:
            conn.execute("UPDATE cad_document_iterations SET primary_path='machine.asm.2' WHERE cad_document_id=?", (self.root,))
        with self.assertRaisesRegex(ValueError, "exact controlled file"):
            self.scan()
        _, _, sources, _ = self.service.inventory(7)
        self.assertNotIn("secret.prt", sources)

    def test_cache_requires_matching_inputs_and_build_guard_rejects_changed_native(self):
        result = self.scan()
        self.scan()
        self.assertEqual(self.scanner.calls, 1)
        self.apply(result)
        (self.directory / "machine.asm").write_bytes(b"updated CAD")
        with self.assertRaisesRegex(ValueError, "Native CAD changed"):
            self.service.assert_current(self.root)
        self.scan()
        self.assertEqual(self.scanner.calls, 2)

    def test_ambiguous_drawing_readonly_project_and_permission_are_blocked(self):
        (self.directory / "drawing.drw").write_bytes(b"#UGC:2 DRAWING\nfixture")
        self.scanner.documents.append({"file_name": "drawing.drw", "complete": True,
                                      "drawing_models": ["machine.asm", "bracket.prt"]})
        result = self.scan()
        with self.assertRaisesRegex(ValueError, "multi-model"):
            self.apply(result)
        with self.assertRaises(PermissionError):
            self.service.apply(result["id"], 1, can_manage=False)
        with self.service.repo.get_conn() as conn:
            conn.execute("UPDATE projects SET is_readonly=1")
        with self.assertRaisesRegex(ValueError, "read-only"):
            self.apply(result)

    def test_sketchup_payload_with_drawing_extension_is_ignored(self):
        invalid = self.directory / "cover_tetra_marked_logo.drw.3"
        invalid.write_bytes(b"SketchUp STL tmpjlBlnA")

        _, _, sources, warnings = self.service.inventory(7)
        self.assertNotIn("cover_tetra_marked_logo.drw", sources)
        self.assertEqual(warnings, [
            "Ignored non-Creo file with CAD extension: cover_tetra_marked_logo.drw.3"
        ])

        result = self.scan()
        self.assertNotIn("cover_tetra_marked_logo.drw", result["snapshot"]["sources"])
        self.assertTrue(any("cover_tetra_marked_logo.drw.3" in warning
                            for warning in result["plan"]["warnings"]))
        self.assertFalse(any(change["parent"] == "cover_tetra_marked_logo.drw"
                             for change in result["plan"]["changes"]))

    def test_creo_relationships_wait_for_approval_then_place_model_and_drawing(self):
        repo = self.service.repo
        assembly_id = repo.create_cad_document(
            7, "new_mount.asm", "New mount", "new_mount.asm", category="ASSEMBLY"
        )
        with repo.get_conn() as conn:
            conn.execute("UPDATE cad_documents SET checked_out_by=1 WHERE id=?", (assembly_id,))
        drawing_id = repo.create_cad_document(
            7, "new_mount.drw", "New mount drawing", "new_mount.drw",
            category="DRAWING", drawing_owner_cad_document_id=assembly_id,
        )
        bracket = repo.get_cad_document_by_file(7, "bracket.prt")
        payload = {
            "schema": 1,
            "members": [{
                "parent_file_name": "new_mount.asm",
                "child_file_name": "bracket.prt",
                "feature_id": 41,
                "status": "ACTIVE",
            }],
            "drawings": [{
                "drawing_file_name": "new_mount.drw",
                "model_file_name": "new_mount.asm",
            }],
        }
        self.service.stage_pending_commit(
            "pending-assembly", 7, 1, [assembly_id, drawing_id], payload
        )
        with repo.get_conn() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM cad_document_members WHERE parent_cad_document_id=?",
                (assembly_id,),
            ).fetchone()[0], 0)
        validation = self.service.validate_pending_commit(
            "pending-assembly", 7, [assembly_id, drawing_id]
        )
        self.assertTrue(validation["validated"])
        with repo.get_conn() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM cad_document_members WHERE parent_cad_document_id=?",
                (assembly_id,),
            ).fetchone()[0], 0)
        result = self.service.apply_pending_commit(
            "pending-assembly", 7, [assembly_id, drawing_id]
        )
        self.assertTrue(result["applied"])
        member = repo.list_cad_members(assembly_id)
        self.assertEqual(len(member), 1)
        self.assertEqual(int(member[0]["child_cad_document_id"]), int(bracket["id"]))
        self.assertEqual(member[0]["component_path"], "41")

    def test_creo_structure_requires_changed_parent_in_same_pending_commit(self):
        repo = self.service.repo
        assembly_id = repo.create_cad_document(
            7, "new_frame.asm", "New frame", "new_frame.asm", category="ASSEMBLY"
        )
        with repo.get_conn() as conn:
            conn.execute("UPDATE cad_documents SET checked_out_by=1 WHERE id=?", (assembly_id,))
        bracket = repo.get_cad_document_by_file(7, "bracket.prt")
        payload = {
            "schema": 1,
            "members": [{
                "parent_file_name": "new_frame.asm",
                "child_file_name": "bracket.prt",
                "feature_id": 3,
                "status": "ACTIVE",
            }],
            "drawings": [],
        }
        with self.assertRaisesRegex(ValueError, "containing assembly in the same Pending commit"):
            self.service.stage_pending_commit(
                "pending-missing-parent", 7, 1, [int(bracket["id"])], payload
            )

    def test_complete_pending_assembly_snapshot_replaces_all_existing_links(self):
        repo = self.service.repo
        with repo.get_conn() as conn:
            conn.execute("UPDATE cad_documents SET checked_out_by=1 WHERE id=?", (self.root,))
            member = conn.execute(
                "SELECT id FROM cad_document_members WHERE parent_cad_document_id=? LIMIT 1",
                (self.root,),
            ).fetchone()
            before = conn.execute(
                "SELECT COUNT(*) FROM cad_document_members WHERE parent_cad_document_id=?",
                (self.root,),
            ).fetchone()[0]
            conn.execute("UPDATE item_usages SET cad_member_id=? WHERE id=(SELECT id FROM item_usages LIMIT 1)",
                         (int(member["id"]),))
        self.assertIsNotNone(member)
        self.assertGreater(before, 0)
        self.service.stage_pending_commit(
            "pending-complete-empty-assembly", 7, 1, [self.root],
            {"schema": 1, "members": [], "drawings": [],
             "complete_assemblies": ["machine.asm"]},
        )
        with repo.get_conn() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM cad_document_members WHERE parent_cad_document_id=?",
                (self.root,),
            ).fetchone()[0], before)
        self.service.apply_pending_commit(
            "pending-complete-empty-assembly", 7, [self.root]
        )
        with repo.get_conn() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM cad_document_members WHERE parent_cad_document_id=?",
                (self.root,),
            ).fetchone()[0], 0)
            self.assertIsNone(conn.execute(
                "SELECT cad_member_id FROM item_usages LIMIT 1"
            ).fetchone()[0])

    def test_later_complete_snapshot_replaces_earlier_staged_snapshot(self):
        repo = self.service.repo
        with repo.get_conn() as conn:
            conn.execute("UPDATE cad_documents SET checked_out_by=1 WHERE id=?", (self.root,))
        self.service.stage_pending_commit(
            "pending-refresh-snapshot", 7, 1, [self.root],
            {"schema": 1, "members": [{
                "parent_file_name": "machine.asm", "child_file_name": "bracket.prt",
                "feature_id": 10, "status": "ACTIVE",
            }], "drawings": [], "complete_assemblies": ["machine.asm"]},
        )
        self.service.stage_pending_commit(
            "pending-refresh-snapshot", 7, 1, [self.root],
            {"schema": 1, "members": [{
                "parent_file_name": "machine.asm", "child_file_name": "bracket_sim.prt",
                "feature_id": 20, "status": "ACTIVE",
            }], "drawings": [], "complete_assemblies": ["machine.asm"]},
        )
        with repo.get_conn() as conn:
            payload = conn.execute(
                "SELECT payload_json FROM cad_pending_structure_changes WHERE commit_id=?",
                ("pending-refresh-snapshot",),
            ).fetchone()[0]
        self.assertEqual(
            [edge["child_file_name"] for edge in json.loads(payload)["members"]],
            ["bracket_sim.prt"],
        )

    def test_approval_rejects_dependency_that_changed_after_structure_staging(self):
        repo = self.service.repo
        assembly_id = repo.create_cad_document(
            7, "dependency_test.asm", "Dependency test", "dependency_test.asm",
            category="ASSEMBLY",
        )
        with repo.get_conn() as conn:
            conn.execute("UPDATE cad_documents SET checked_out_by=1 WHERE id=?", (assembly_id,))
        payload = {"schema": 1, "members": [{
            "parent_file_name": "dependency_test.asm",
            "child_file_name": "bracket.prt", "feature_id": 4, "status": "ACTIVE",
        }], "drawings": []}
        review = self.service.review_pending_structure(7, 1, [assembly_id], payload)
        payload["dependency_baselines"] = review["dependencies"]
        self.service.stage_pending_commit(
            "pending-stale-dependency", 7, 1, [assembly_id], payload,
        )
        with repo.get_conn() as conn:
            conn.execute(
                "UPDATE cad_documents SET iteration=iteration+1 WHERE file_name='bracket.prt'"
            )
        with self.assertRaisesRegex(ValueError, "dependency changed since review or staging"):
            self.service.validate_pending_commit(
                "pending-stale-dependency", 7, [assembly_id]
            )

    def test_structure_stage_rejects_dependency_changed_after_user_review(self):
        repo = self.service.repo
        assembly_id = repo.create_cad_document(
            7, "review_race.asm", "Review race", "review_race.asm", category="ASSEMBLY"
        )
        with repo.get_conn() as conn:
            conn.execute("UPDATE cad_documents SET checked_out_by=1 WHERE id=?", (assembly_id,))
        payload = {"schema": 1, "members": [{
            "parent_file_name": "review_race.asm", "child_file_name": "bracket.prt",
            "feature_id": 6, "status": "ACTIVE",
        }], "drawings": []}
        review = self.service.review_pending_structure(7, 1, [assembly_id], payload)
        payload["dependency_baselines"] = review["dependencies"]
        with repo.get_conn() as conn:
            conn.execute(
                "UPDATE cad_documents SET iteration=iteration+1 WHERE file_name='bracket.prt'"
            )
        with self.assertRaisesRegex(ValueError, "dependency changed since review or staging"):
            self.service.stage_pending_commit(
                "pending-review-race", 7, 1, [assembly_id], payload
            )

    def test_structure_review_returns_server_diff_and_pinned_dependency_revision(self):
        repo = self.service.repo
        with repo.get_conn() as conn:
            conn.execute("UPDATE cad_documents SET checked_out_by=1 WHERE id=?", (self.root,))
        result = self.service.review_pending_structure(
            7, 1, [self.root],
            {"schema": 1, "members": [], "drawings": [],
             "complete_assemblies": ["machine.asm"]},
        )
        self.assertTrue(any(
            row["action"] == "REMOVE" and row["parent_file_name"] == "machine.asm"
            for row in result["changes"]
        ))

        result = self.service.review_pending_structure(
            7, 1, [self.root],
            {"schema": 1, "members": [{
                "parent_file_name": "machine.asm", "child_file_name": "bracket.prt",
                "feature_id": 22, "status": "ACTIVE",
            }], "drawings": [], "complete_assemblies": ["machine.asm"]},
        )
        dependency = next(row for row in result["dependencies"]
                          if row["file_name"] == "bracket.prt")
        with repo.get_conn() as conn:
            current = conn.execute(
                "SELECT revision,iteration FROM cad_documents WHERE file_name='bracket.prt'"
            ).fetchone()
        self.assertEqual(dependency["revision"], current["revision"])
        self.assertEqual(dependency["iteration"], current["iteration"])


if __name__ == "__main__":
    unittest.main()
