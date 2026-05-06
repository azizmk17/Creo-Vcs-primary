import os
from datetime import datetime
from core.models.commit_model import Commit 
from config import DB_NAME # adjust to your ORM or query interface
import sqlite3


class DiagRepository:
    def __init__(self, db_name=DB_NAME):
        self.db_name = db_name

        self.commits_dir = "commits_dir"
        self.working_dir = "working_dir"

    def get_conn(self):
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        return conn
    

    # --- Database ---
    def get_all_commits(self, project_id: int):
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM commits WHERE project_id=? ORDER BY committed_at DESC", (project_id,))
            rows = cur.fetchall()
            return [self._row_to_commit(r) for r in rows]

    
    
    def _row_to_commit(self, row: sqlite3.Row) -> Commit:
        """Convert a sqlite3.Row into a Commit dataclass, filtering unexpected columns.

        This avoids passing database-specific column names that don't match the
        Commit constructor.
        """
        if row is None:
            return None
        data = dict(row)
        # Fields expected by Commit dataclass
        keys = [
            'id','part_id','filename','file_path','base_file_name','designer',
            'message','committed_by','checked_by','status','committed_at','signature','title','commit_id','username'
        ]
        filtered = {k: data.get(k) for k in keys}
        return Commit(**filtered)
