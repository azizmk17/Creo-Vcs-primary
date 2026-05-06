import sqlite3
from typing import Optional, Dict

from config import DB_NAME


class PartDocAckRepository:
    def __init__(self, db_name: str = DB_NAME):
        self.db_name = db_name

    def get_conn(self):
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        return conn

    def get_ack(self, part_id: int, doc_type: str) -> Optional[Dict]:
        with self.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM part_doc_ack WHERE part_id = ? AND doc_type = ?",
                (int(part_id), str(doc_type).upper()),
            ).fetchone()
            return dict(row) if row else None

    def upsert_ack(self, part_id: int, doc_type: str, acknowledged_against: str, acknowledged_by: Optional[int] = None):
        with self.get_conn() as conn:
            conn.execute(
                """
                INSERT INTO part_doc_ack (part_id, doc_type, acknowledged_against, acknowledged_by)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(part_id, doc_type)
                DO UPDATE SET
                    acknowledged_against = excluded.acknowledged_against,
                    acknowledged_by = excluded.acknowledged_by,
                    acknowledged_at = datetime('now')
                """,
                (int(part_id), str(doc_type).upper(), str(acknowledged_against), acknowledged_by),
            )
            conn.commit()
