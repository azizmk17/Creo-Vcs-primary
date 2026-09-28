"""Durable state for resumable, all-or-nothing CAD submission approval."""

import json
import sqlite3
import uuid
from contextlib import contextmanager, nullcontext


_PHASES = {
    "PREPARING": 0,
    "FILES_READY": 1,
    "STRUCTURE_APPLIED": 2,
    "RECORDS_FINALIZED": 3,
    "CHECKINS_COMPLETED": 4,
    "COMPLETED": 5,
}


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


class ApprovalJournalService:
    def __init__(self, db_name):
        self.db_name = db_name

    @contextmanager
    def _connection(self):
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def get(self, project_id, commit_id, snapshot_sha256):
        with self._connection() as conn:
            row = conn.execute("""
                SELECT * FROM cad_submission_approvals
                WHERE project_id=? AND commit_id=? AND snapshot_sha256=?
            """, (int(project_id), str(commit_id), str(snapshot_sha256))).fetchone()
        return dict(row) if row else None

    def latest(self, project_id, commit_id):
        with self._connection() as conn:
            row = conn.execute("""
                SELECT * FROM cad_submission_approvals
                WHERE project_id=? AND commit_id=?
                ORDER BY rowid DESC LIMIT 1
            """, (int(project_id), str(commit_id))).fetchone()
        return dict(row) if row else None

    def get_by_id(self, approval_id):
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM cad_submission_approvals WHERE approval_id=?",
                (str(approval_id),),
            ).fetchone()
        return dict(row) if row else None

    def begin(self, project_id, commit_id, snapshot_sha256, approver_id,
              message, merge_id=None):
        """Create one approval identity, or return its existing retry record."""
        approval_id = uuid.uuid4().hex
        stable_merge_id = merge_id or f"merge_{uuid.uuid4().hex[:12]}"
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("""
                SELECT * FROM cad_submission_approvals
                WHERE project_id=? AND commit_id=? AND snapshot_sha256=?
            """, (int(project_id), str(commit_id), str(snapshot_sha256))).fetchone()
            if row:
                return dict(row)
            conn.execute("""
                INSERT INTO cad_submission_approvals(
                    approval_id,project_id,commit_id,snapshot_sha256,
                    approver_id,merge_id,message,status
                ) VALUES(?,?,?,?,?,?,?,'PREPARING')
            """, (approval_id, int(project_id), str(commit_id),
                  str(snapshot_sha256), int(approver_id), stable_merge_id,
                  str(message or "")))
            return dict(conn.execute(
                "SELECT * FROM cad_submission_approvals WHERE approval_id=?",
                (approval_id,),
            ).fetchone())

    def save_plan(self, approval_id, plan):
        encoded = _canonical(plan)
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT plan_json FROM cad_submission_approvals WHERE approval_id=?",
                (str(approval_id),),
            ).fetchone()
            if not row:
                raise ValueError("Approval journal entry was not found.")
            current = str(row["plan_json"] or "{}")
            if current not in {"", "{}", encoded}:
                raise ValueError("The saved approval plan differs from this retry.")
            conn.execute("""
                UPDATE cad_submission_approvals
                SET plan_json=?,last_error=NULL,updated_at=datetime('now')
                WHERE approval_id=?
            """, (encoded, str(approval_id)))
        return json.loads(encoded)

    def plan(self, record):
        try:
            value = json.loads(record.get("plan_json") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            raise ValueError("The saved approval plan is invalid.")
        if not isinstance(value, dict):
            raise ValueError("The saved approval plan is invalid.")
        return value

    def advance(self, approval_id, status, *, conn=None):
        if status not in _PHASES:
            raise ValueError("Unknown approval journal state.")
        if conn is None:
            context = self._connection()
        else:
            context = nullcontext(conn)
        with context as active_conn:
            if conn is None:
                active_conn.execute("BEGIN IMMEDIATE")
            row = active_conn.execute(
                "SELECT status FROM cad_submission_approvals WHERE approval_id=?",
                (str(approval_id),),
            ).fetchone()
            if not row:
                raise ValueError("Approval journal entry was not found.")
            current = str(row["status"] or "PREPARING")
            if _PHASES.get(current, -1) > _PHASES[status]:
                return current
            active_conn.execute("""
                UPDATE cad_submission_approvals
                SET status=?,last_error=NULL,updated_at=datetime('now'),
                    completed_at=CASE WHEN ?='COMPLETED' THEN datetime('now') ELSE completed_at END
                WHERE approval_id=?
            """, (status, status, str(approval_id)))
        return status

    def record_error(self, approval_id, error):
        with self._connection() as conn:
            conn.execute("""
                UPDATE cad_submission_approvals
                SET last_error=?,updated_at=datetime('now')
                WHERE approval_id=?
            """, (str(error)[:2000], str(approval_id)))

