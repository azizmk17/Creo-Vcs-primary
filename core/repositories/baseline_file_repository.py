import sqlite3
from typing import List, Optional

from config import DB_NAME
from core.models.baseline_file_model import BaselineFile


class BaselineFileRepository:
    def __init__(self, db_name: str = DB_NAME):
        self.db_name = db_name

    def get_conn(self):
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        return conn

    def add(self, baseline_id: int, part_id: int, file_type: str, file_id: Optional[int], version_id: Optional[int]) -> int:
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO baseline_files(baseline_id, part_id, file_type, file_id, version_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (baseline_id, part_id, file_type, file_id, version_id),
            )
            return int(cur.lastrowid)

    def list_for_baseline(self, baseline_id: int) -> List[BaselineFile]:
        with self.get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM baseline_files WHERE baseline_id = ? ORDER BY part_id, file_type",
                (baseline_id,),
            ).fetchall()
            return [BaselineFile(**dict(r)) for r in rows]
