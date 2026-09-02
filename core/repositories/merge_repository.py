from core.models.merge_model import Merge
from datetime import datetime
from config import DB_NAME
import sqlite3

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
        
    def get_commit_ids_by_commitid(self, commit_id: str) -> list[Merge]:
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT c.id, c.type, c.part_id, c.cad_document_id, c.creo_file_version, c.status,
                       c.filename, c.title, c.commit_id, c.project_id,
                       u.username AS designer_username
                FROM commits c
                JOIN users u ON u.id = c.designer
                WHERE c.status = 'Validated' AND c.commit_id=?
            """, (commit_id,))
            rows = cur.fetchall()
            return [self._row_to_merge(r) for r in rows]
            

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
            'designer_username', 'status', 'title', 'commit_id', 'project_id'
        ]
        filtered = {k: data.get(k) for k in keys}
        return Merge(**filtered)
    
    def merge_commit(self, id, merge_user_id, merge_id,  message, approved_version, pr_path):
        """Set status=Approved and attach merge message for given part IDs."""
        with self.get_conn() as conn:
            cur = conn.cursor()
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
            conn.commit()

    


