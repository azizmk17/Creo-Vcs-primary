import sqlite3
from typing import List, Optional
from core.models.lock_model import Locks
from core.models.lock_logs_model import Lock_logs
from config import DB_NAME
from datetime import datetime


class LockRepository:
    def __init__(self, db_name=DB_NAME):
        self.db_name = db_name

    def get_conn(self):
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        return conn

    # -------------------------------
    def checkin(self, part_id, user_id, signature) -> bool:
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM locks WHERE part_id = ?", (part_id,))
            cur.execute("""
                INSERT INTO lock_logs (part_id, user_id, action, timestamp, signature)
                VALUES (?, ?, ?, ?, ?)
            """, (
                part_id, user_id, "checkin", sqlite3.datetime.datetime.now().isoformat(), signature
            ))
            return True

    def undo_checkout(self, part_id, user_id, signature) -> bool:
        """Release a checkout without recording a check-in event."""
        with self.get_conn() as conn:
            conn.execute("DELETE FROM locks WHERE part_id = ?", (int(part_id),))
            conn.execute(
                """
                INSERT INTO lock_logs (part_id, user_id, action, timestamp, signature)
                VALUES (?, ?, 'undo_checkout', ?, ?)
                """,
                (int(part_id), int(user_id), sqlite3.datetime.datetime.now().isoformat(), signature),
            )
            return True
        
    def checkout(self, part_id, user_id, signature) -> bool:
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO locks (part_id, user_id)
                VALUES (?, ?)
            """, (
                part_id, user_id
            ))
            cur.execute("""
                INSERT INTO lock_logs (part_id, user_id, action, timestamp, signature)
                VALUES (?, ?, ?, ?, ?)
            """, (
                part_id, user_id, "checkout", sqlite3.datetime.datetime.now().isoformat(), signature
            ))
            return cur.lastrowid > 0
        
    
        
    # -------------------------------
    # READ / GET
    # -------------------------------
    def get_by_part(self, part_id: int) -> Optional[Locks]:
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM locks WHERE part_id=?", (part_id,))
            row = cur.fetchone()
            if row:
                return Locks(**row)
            return None
        
    def get_by_user(self, user_id: int) -> Optional[Locks]:
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM locks WHERE user_id=?", (user_id,))
            row = cur.fetchone()
            if row:
                return Locks(**row)
            return None

    def get_all(self) -> List[Locks]:
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM locks")
            rows = cur.fetchall()
            return [Locks(**row) for row in rows]
        
    def get_history_by_part(self, part_id: int) -> List[Lock_logs]:
        with self.get_conn() as conn:
            cur = conn.cursor()
            # cur.execute("SELECT * FROM lock_logs WHERE part_id=?", (part_id,))
            cur.execute("""
            SELECT 
                ll.*,
                u.username as user_name
            FROM lock_logs ll
            LEFT JOIN users u ON ll.user_id = u.id
            WHERE ll.part_id=?
            ORDER BY ll.timestamp DESC
        """, (part_id,))
            rows = cur.fetchall()
            #print rows results for debugging
            for row in rows:
                print(dict(row)) 
            return [Lock_logs(**row) for row in rows]
        
    def get_lock_by_part(self, part_id: int) -> Optional[Locks]:
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM locks WHERE part_id=?", (part_id,))
            row = cur.fetchone()
            if row:
                return Locks(**row)
            return None

    def get_lock_owners_for_project(self, project_id: int) -> dict[int, str]:
        """Return mapping: bom.part_id -> username for parts currently in locks.

        locks table has no project_id; join through bom.
        """
        with self.get_conn() as conn:
            cur = conn.cursor()
            try:
                cur.execute(
                    """
                    SELECT l.part_id, u.username
                    FROM locks l
                    JOIN bom b ON b.id = l.part_id
                    LEFT JOIN users u ON u.id = l.user_id
                    WHERE b.project_id = ?
                    """,
                    (int(project_id),),
                )
                rows = cur.fetchall()
                out: dict[int, str] = {}
                for r in rows:
                    try:
                        pid = int(r[0])
                        uname = str(r[1] or "").strip()
                    except Exception:
                        continue
                    if uname:
                        out[pid] = uname
                return out
            except Exception:
                return {}
