import os
import hashlib
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from types import SimpleNamespace

from core.repositories.signature_repository import SignatureRepository
from core.repositories.merge_repository import MergeRepository
from core.repositories.project_event_repository import ProjectEventRepository
from core.services.approval_journal_service import ApprovalJournalService
from core.services.merge_service import MergeService
from setup.migrations import _migration_46, _migration_47, _migration_48


class ApprovalJournalServiceTests(unittest.TestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("CREATE TABLE signature(id INTEGER PRIMARY KEY, action TEXT, user_id INTEGER, note TEXT, timestamp TEXT)")
            _migration_46(conn)
            _migration_47(conn)
            _migration_48(conn)
        self.service = ApprovalJournalService(self.db_path)

    def tearDown(self):
        try:
            os.remove(self.db_path)
        except OSError:
            pass

    def test_approval_plan_and_phase_are_stable_across_retry(self):
        first = self.service.begin(2, "batch-9", "snapshot-hash", 7, "approve")
        plan = {"schema": 1, "version": "part.prt.4"}
        self.service.save_plan(first["approval_id"], plan)

        retried = self.service.begin(2, "batch-9", "snapshot-hash", 7, "changed message")
        self.assertEqual(retried["approval_id"], first["approval_id"])
        self.assertEqual(retried["merge_id"], first["merge_id"])
        self.assertEqual(retried["message"], "approve")
        self.assertEqual(self.service.plan(retried), plan)

        self.service.advance(first["approval_id"], "FILES_READY")
        self.service.advance(first["approval_id"], "STRUCTURE_APPLIED")
        self.service.advance(first["approval_id"], "FILES_READY")
        self.assertEqual(
            self.service.get_by_id(first["approval_id"])["status"],
            "STRUCTURE_APPLIED",
        )
        events = self.service.events(first["approval_id"])
        self.assertEqual(
            [event["to_status"] for event in events],
            ["PREPARING", "FILES_READY", "STRUCTURE_APPLIED"],
        )
        self.assertEqual(
            [event["from_status"] for event in events],
            ["NONE", "PREPARING", "FILES_READY"],
        )

    def test_withdrawn_or_rejected_approval_journal_is_terminal(self):
        for status, logical_id in (("WITHDRAWN", "withdrawn"), ("REJECTED", "rejected")):
            journal = self.service.begin(2, "batch-" + logical_id, "hash", 7, "approve")
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "UPDATE cad_submission_approvals SET status=? WHERE approval_id=?",
                    (status, journal["approval_id"]),
                )
            self.assertEqual(
                self.service.advance(journal["approval_id"], "FILES_READY"), status
            )
            self.assertEqual(
                self.service.get_by_id(journal["approval_id"])["status"], status
            )

    def test_signature_idempotency_does_not_update_audit_rows(self):
        signature_repo = SignatureRepository(self.db_path)
        first_id = signature_repo.add_signature(
            "Merge", 7, "approved", idempotency_key="approval-1:commit-1"
        )
        second_id = signature_repo.add_signature(
            "Merge", 7, "approved", idempotency_key="approval-1:commit-1"
        )
        self.assertEqual(first_id, second_id)
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM signature").fetchone()[0], 1)

    def test_approval_event_is_transactional_and_idempotent(self):
        repo = ProjectEventRepository(self.db_path)
        with self.assertRaisesRegex(RuntimeError, "rollback event"):
            with repo.get_conn() as conn:
                conn.execute("BEGIN IMMEDIATE")
                repo.emit(
                    2, 7, "cad.checkin", entity_type="CAD_DOCUMENT", entity_id=4,
                    payload={"cad_document_ids": [4]}, conn=conn,
                    event_key="approval:1:cad:4",
                )
                raise RuntimeError("rollback event")

        self.assertEqual(repo.current_id(2), 0)
        first = repo.emit(
            2, 7, "cad.checkin", entity_type="CAD_DOCUMENT", entity_id=4,
            payload={"cad_document_ids": [4]}, event_key="approval:1:cad:4",
        )
        second = repo.emit(
            2, 7, "cad.checkin", entity_type="CAD_DOCUMENT", entity_id=4,
            payload={"cad_document_ids": [4]}, event_key="approval:1:cad:4",
        )
        self.assertEqual(first, second)
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM project_events").fetchone()[0], 1)

    def test_commit_approval_update_is_idempotent_for_same_merge(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE commits(
                    id INTEGER PRIMARY KEY,status TEXT,merged_by INTEGER,merge_id TEXT,
                    merged_at TEXT,merge_message TEXT,approved_version TEXT,pr_path TEXT
                )
            """)
            conn.execute("INSERT INTO commits(id,status) VALUES(8,'Validated')")
        repo = MergeRepository(self.db_path)

        self.assertTrue(repo.merge_commit(8, 7, "merge-1", "approved", "4", "master/part.prt.4"))
        self.assertTrue(repo.merge_commit(8, 7, "merge-1", "approved", "4", "master/part.prt.4"))
        with self.assertRaisesRegex(ValueError, "different operation"):
            repo.merge_commit(8, 9, "merge-2", "approved", "5", "master/part.prt.5")
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM commits").fetchone()[0], 1)

    def test_approval_publication_rolls_back_as_one_transaction(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE commits(
                    id INTEGER PRIMARY KEY,status TEXT,merged_by INTEGER,merge_id TEXT,
                    merged_at TEXT,merge_message TEXT,approved_version TEXT,pr_path TEXT
                )
            """)
            conn.execute("INSERT INTO commits(id,status) VALUES(8,'Validated')")
            conn.execute("CREATE TABLE published_structure(value TEXT)")

        journal = self.service.begin(2, "batch-9", "snapshot-hash", 7, "approve")
        self.service.advance(journal["approval_id"], "FILES_READY")
        merge_repo = MergeRepository(self.db_path)
        signature_repo = SignatureRepository(self.db_path)

        with self.assertRaisesRegex(RuntimeError, "injected publish failure"):
            with merge_repo.get_conn() as conn:
                conn.execute("BEGIN IMMEDIATE")
                merge_repo.merge_commit(
                    8, 7, "merge-1", "approved", "4", "master/part.prt.4", conn=conn
                )
                signature_repo.add_signature(
                    "Merge", 7, "approved", idempotency_key="approval:8", conn=conn
                )
                conn.execute("INSERT INTO published_structure VALUES('assembly-link')")
                self.service.advance(
                    journal["approval_id"], "RECORDS_FINALIZED", conn=conn
                )
                raise RuntimeError("injected publish failure")

        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(conn.execute("SELECT status FROM commits WHERE id=8").fetchone()[0], "Validated")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM signature").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM published_structure").fetchone()[0], 0)
        self.assertEqual(
            self.service.get_by_id(journal["approval_id"])["status"], "FILES_READY"
        )

        with merge_repo.get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            merge_repo.merge_commit(
                8, 7, "merge-1", "approved", "4", "master/part.prt.4", conn=conn
            )
            signature_repo.add_signature(
                "Merge", 7, "approved", idempotency_key="approval:8", conn=conn
            )
            conn.execute("INSERT INTO published_structure VALUES('assembly-link')")
            self.service.advance(journal["approval_id"], "RECORDS_FINALIZED", conn=conn)

        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(conn.execute("SELECT status FROM commits WHERE id=8").fetchone()[0], "Approved")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM signature").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM published_structure").fetchone()[0], 1)
        self.assertEqual(
            self.service.get_by_id(journal["approval_id"])["status"], "RECORDS_FINALIZED"
        )

    def test_item_checkin_failure_rolls_back_publication_then_retry_commits_once(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE commits(
                    id INTEGER PRIMARY KEY,status TEXT,merged_by INTEGER,merge_id TEXT,
                    merged_at TEXT,merge_message TEXT,approved_version TEXT,pr_path TEXT
                )
            """)
            conn.execute("INSERT INTO commits(id,status) VALUES(8,'Validated')")
            conn.execute("CREATE TABLE cad_pointer_probe(cad_id INTEGER,iteration INTEGER)")

        journal = self.service.begin(2, "batch-9", "snapshot-hash", 7, "approve")
        self.service.advance(journal["approval_id"], "FILES_READY")
        merge_repo = MergeRepository(self.db_path)
        cad_repo = Mock()
        cad_repo.get_cad_document.side_effect = [
            {
                "checked_out_by": 7,
                "checkout_workspace_id": "workspace-1",
                "category": "COMPONENT",
            },
            {
                "checked_out_by": 7,
                "checkout_workspace_id": "workspace-1",
                "category": "COMPONENT",
            },
            {"checked_out_by": None},
        ]
        cad_repo.list_related_drawings.return_value = []

        def record_cad_checkin(*_args, connection, **_kwargs):
            self.assertTrue(connection.in_transaction)
            connection.execute(
                "INSERT INTO cad_pointer_probe VALUES(4,2)"
            )
            return {"checked_out_by": None, "iteration_id": 2, "checkout_item_ids": [9]}

        cad_repo.checkin_cad_document.side_effect = record_cad_checkin
        bom_service = SimpleNamespace(
            pdm_service=SimpleNamespace(
                db_name=self.db_path,
                repo=cad_repo,
                checkout_target_item_ids=Mock(return_value=[9]),
            ),
            checkin_pdm_cad_document=Mock(),
            emit_project_event=Mock(),
            checked_out_cad_for_item=Mock(return_value=[]),
            checkin_by_part_id=Mock(side_effect=[
                RuntimeError("injected Item check-in failure"), True
            ]),
        )
        service = object.__new__(MergeService)
        service.bom_service = bom_service
        service.merge_repository = merge_repo
        service.bom_repo = SimpleNamespace(get_by_id=Mock(return_value=None))
        service.signature_repo = SignatureRepository(self.db_path)
        service.lock_repo = SimpleNamespace(
            get_by_part=Mock(return_value=SimpleNamespace(user_id=7))
        )
        service.managed_file_service = SimpleNamespace(capture_current_iteration=Mock())
        entry = {
            "item_id": 9,
            "cad_document_id": 4,
            "commit_id": 8,
            "source_commit_id": "batch-9",
            "project_id": 2,
            "part_type": "Cad",
            "new_filename": "part.prt.4",
            "new_version": "4",
            "pr_path": "master/part.prt.4",
            "source_file_name": "part.prt.3",
            "creo_file_version": 3,
        }

        class StructureService:
            def __init__(self, _db_name):
                pass

            def apply_pending_commit(self, *_args, **_kwargs):
                return {"applied": True}

        class WorkspaceService:
            def process_pending_approval_releases(
                self, db_name, *, approval_key=None
            ):
                with sqlite3.connect(db_name) as conn:
                    cur = conn.execute(
                        """
                        UPDATE cad_workspace_release_queue
                        SET completed_at=datetime('now')
                        WHERE approval_key=? AND completed_at IS NULL
                        """,
                        (approval_key,),
                    )
                    return {"completed": cur.rowcount, "failed_ids": []}

        with patch(
            "core.services.cad_structure_sync_service.CadStructureSyncService",
            StructureService,
        ), patch(
            "core.services.cad_workspace_service.CadWorkspaceService",
            WorkspaceService,
        ):
            with self.assertRaisesRegex(RuntimeError, "Item check-in failure"):
                service.finalize_merge(
                    [entry], 7, journal["merge_id"], "approve",
                    approval_id=journal["approval_id"],
                )
            self.assertEqual(
                self.service.get_by_id(journal["approval_id"])["status"],
                "FILES_READY",
            )
            with sqlite3.connect(self.db_path) as conn:
                self.assertEqual(conn.execute("SELECT status FROM commits WHERE id=8").fetchone()[0], "Validated")
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM signature").fetchone()[0], 0)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM cad_pointer_probe").fetchone()[0], 0)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM project_events").fetchone()[0], 0)
            service.finalize_merge(
                [entry], 7, journal["merge_id"], "approve",
                approval_id=journal["approval_id"],
            )

        self.assertEqual(cad_repo.checkin_cad_document.call_count, 2)
        bom_service.checkin_pdm_cad_document.assert_not_called()
        self.assertEqual(bom_service.checkin_by_part_id.call_count, 2)
        self.assertEqual(
            self.service.get_by_id(journal["approval_id"])["status"],
            "CHECKINS_COMPLETED",
        )
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM signature").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM commits WHERE status='Approved'").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM cad_pointer_probe").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM project_events").fetchone()[0], 1)
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM cad_workspace_release_queue WHERE completed_at IS NOT NULL"
                ).fetchone()[0],
                1,
            )

    def test_file_plan_uses_stable_targets_and_retries_idempotently(self):
        root = tempfile.mkdtemp()
        try:
            commits = os.path.join(root, "commits")
            working = os.path.join(root, "working")
            pr = os.path.join(root, "pr")
            os.makedirs(os.path.join(commits, "_snapshots", "objects"))
            os.makedirs(working)
            os.makedirs(pr)
            blob = os.path.join(commits, "_snapshots", "objects", "content.prt")
            with open(blob, "wb") as stream:
                stream.write(b"same frozen bytes")
            digest = hashlib.sha256(b"same frozen bytes").hexdigest()

            service = object.__new__(MergeService)
            service.commits_dir = commits
            service.working_dir = working
            service.pr_dir = pr
            journal = {"approval_id": "approval-1", "merge_id": "merge-1"}
            rows = [
                SimpleNamespace(id=1, filename="alpha.prt.3"),
                SimpleNamespace(id=2, filename="beta.prt.8"),
            ]
            snapshots = {
                1: {"path": "_snapshots/objects/content.prt", "sha256": digest},
                2: {"path": "_snapshots/objects/content.prt", "sha256": digest},
            }
            plan = service._build_approval_plan(journal, rows, snapshots)

            self.assertEqual(len(plan["results_by_path"]), 2)
            service._stage_approval_files(plan)
            service._stage_approval_files(plan)
            self.assertEqual(sorted(os.listdir(working)), ["alpha.prt.1", "beta.prt.1"])
        finally:
            import shutil
            shutil.rmtree(root)


if __name__ == "__main__":
    unittest.main()
