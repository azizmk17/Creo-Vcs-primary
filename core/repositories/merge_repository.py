from core.models.merge_model import Merge
from datetime import datetime
from config import DB_NAME
import sqlite3
from contextlib import nullcontext

class MergeRepository:
    def __init__(self, db_name=DB_NAME):
        self.db_name = db_name

    def get_conn(self):
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        return conn
    
    def get_pending_commits_grouped(self):
        """Return {designer: [parts]} for pending commits."""
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT c.id, c.part_id, c.status, c.filename, u.username
                FROM commits c
                JOIN users u ON u.id = c.designer
                WHERE c.status = 'Pending'
            """)
            rows = cur.fetchall()

        commits = {}
        for row in rows:
            designer = row["username"]
            part_entry = {
                "id": row["id"],
                "part_id": row["part_id"],
                "filename": row["filename"],
                "status": row["status"]
            }
            commits.setdefault(designer, []).append(part_entry)
        return commits
    
    def get_ready_to_merge_by_id(self, id: int) -> Merge:
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT c.id, c.type, c.part_id, c.cad_document_id, c.creo_file_version, c.status,
                       c.designer, c.committed_by,
                       c.filename, c.title, c.commit_id, c.project_id,
                       u.username AS designer_username
                FROM commits c
                JOIN users u ON u.id = c.designer
                WHERE c.status = 'Validated' AND c.id=?
            """, (id,))
            row = cur.fetchone()
            if row:
                return self._row_to_merge(row)
            return None
        
    def get_commit_ids_by_commitid(
        self, commit_id: str, project_id: int | None = None, *, include_approved=False
    ) -> list[Merge]:
        with self.get_conn() as conn:
            status_clause = "c.status IN ('Validated','Approved')" if include_approved else "c.status='Validated'"
            project_clause = " AND c.project_id=?" if project_id is not None else ""
            params = [str(commit_id)]
            if project_id is not None:
                params.append(int(project_id))
            rows = conn.execute(f"""
                SELECT c.id, c.type, c.part_id, c.cad_document_id, c.creo_file_version, c.status,
                       c.designer, c.committed_by,
                       c.filename, c.title, c.commit_id, c.project_id,
                       u.username AS designer_username
                FROM commits c
                JOIN users u ON u.id = c.designer
                WHERE {status_clause} AND c.commit_id=? {project_clause}
                ORDER BY c.id
            """, tuple(params)).fetchall()
            return [self._row_to_merge(row) for row in rows]
            

    def _row_to_merge(self, row: sqlite3.Row) -> Merge:
        """Convert a sqlite3.Row into a Commit dataclass, filtering unexpected columns.

        This avoids passing database-specific column names that don't match the
        Commit constructor.
        """
        if row is None:
            return None
        data = dict(row)
        # Fields expected by Commit dataclass
        keys = [
            'id', 'part_id', 'cad_document_id', 'creo_file_version', 'type', 'filename',
            'designer_username', 'status', 'title', 'commit_id', 'project_id',
            'designer', 'committed_by',
        ]
        filtered = {k: data.get(k) for k in keys}
        return Merge(**filtered)
    
    def merge_commit(self, id, merge_user_id, merge_id, message, approved_version, pr_path, *, conn=None):
        """Set status=Approved, treating an identical retry as successful."""
        context = self.get_conn() if conn is None else nullcontext(conn)
        with context as active_conn:
            cur = active_conn.cursor()
            current = cur.execute(
                "SELECT status,merge_id,approved_version,pr_path FROM commits WHERE id=?",
                (int(id),),
            ).fetchone()
            if not current:
                raise ValueError(f"Commit row {id} no longer exists.")
            if str(current["status"] or "").casefold() == "approved":
                if (str(current["merge_id"] or "") == str(merge_id)
                        and str(current["approved_version"] or "") == str(approved_version)
                        and str(current["pr_path"] or "") == str(pr_path)):
                    return True
                raise ValueError(f"Commit row {id} was approved by a different operation.")
            if str(current["status"] or "").casefold() != "validated":
                raise ValueError(f"Commit row {id} is no longer awaiting approval.")
            cur.execute("""
                UPDATE commits
                SET status = 'Approved',
                    merged_by = ?,
                    merge_id = ?,
                    merged_at = CURRENT_TIMESTAMP,
                    merge_message = ?,
                    approved_version =?,
                    pr_path = ?
                    
                WHERE id = ?
                AND status = 'Validated'
            """, (merge_user_id, merge_id, message, approved_version, pr_path, id))
            return cur.rowcount == 1

    


