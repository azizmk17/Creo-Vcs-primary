import sqlite3
from typing import List, Optional

from config import DB_NAME
from core.models.baseline_model import Baseline


class BaselineRepository:
    def __init__(self, db_name: str = DB_NAME):
        self.db_name = db_name

    def get_conn(self):
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        return conn

    def create(self, project_id: int, name: str, created_by: Optional[int], include_children: int, part_ids_json: Optional[str]) -> int:
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO baselines(project_id, name, created_by, include_children, part_ids_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (project_id, name, created_by, int(include_children), part_ids_json),
            )
            return int(cur.lastrowid)

    def get_by_id(self, baseline_id: int) -> Optional[Baseline]:
        with self.get_conn() as conn:
            row = conn.execute("SELECT * FROM baselines WHERE id = ?", (baseline_id,)).fetchone()
            return Baseline(**dict(row)) if row else None

    def list_for_project(self, project_id: int) -> List[Baseline]:
        with self.get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM baselines WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
            return [Baseline(**dict(r)) for r in rows]
