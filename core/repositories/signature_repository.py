import sqlite3
from typing import List, Optional
from core.models.signature_model import Signature
from config import DB_NAME
from datetime import datetime

class SignatureRepository:
    def __init__(self, db_name=DB_NAME):
        self.db_name = db_name

    def get_conn(self):
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        return conn

    # -------------------------------
    # CREATE / INSERT
    # -------------------------------
    def add_signature(self, action, user_id, note=None) -> int:
        """
        Add a new signature and return the inserted ID
        
        Args:
            action: The action being signed
            user_id: ID of the user creating the signature
            note: Optional note for the signature
        
        Returns:
            int: The ID of the newly inserted signature, or -1 if failed
        """
        try:
            with self.get_conn() as conn:
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO signature (action, user_id, note, timestamp)
                    VALUES (?, ?, ?, ?)
                """, (
                    action, user_id, note, sqlite3.datetime.datetime.now().isoformat()
                ))
                return cur.lastrowid  
        except sqlite3.Error as e:
            print(f"Database error: {e}")
            return -1
        except Exception as e:
            print(f"Unexpected error: {e}")
            return -1
        

    # -------------------------------
    # READ / SELECT
    # -------------------------------
    def get_signature_by_id(self, signature_id: int) -> Optional[Signature]:
        """
        Retrieve a signature by its ID
        
        Args:
            signature_id: The ID of the signature to retrieve
        
        Returns:
            Signature or None if not found
        """
        try:
            with self.get_conn() as conn:
                cur = conn.cursor()
                cur.execute("SELECT * FROM signature WHERE id = ?", (signature_id,))
                row = cur.fetchone()
                if row:
                    return Signature(**row)
                return None
        except sqlite3.Error as e:
            print(f"Database error: {e}")
            return None
        except Exception as e:
            print(f"Unexpected error: {e}")
            return None
        
    def get_all_history_by_part(self, part_id) -> List[Signature]:
        """
        Retrieve all signatures from the database
        
        Returns:
            List of Signature objects
        """
        try:
            with self.get_conn() as conn:
                cur = conn.cursor()
                cur.execute(
                """
                    SELECT 
                        sig.action,
                        sig.note,
                        u.username AS username,
                        sig.timestamp
                    FROM signature sig
                    LEFT JOIN commits com ON sig.id = com.signature
                    LEFT JOIN lock_logs ll ON sig.id = ll.signature
                    LEFT JOIN users u ON sig.user_id = u.id
                    WHERE com.part_id = ? OR ll.part_id = ?
                    ORDER BY sig.timestamp DESC
                """, (part_id,part_id))
                rows = cur.fetchall()
                return [Signature(**row) for row in rows]
        except sqlite3.Error as e:
            print(f"Database error: {e}")
            return []
        except Exception as e:
            print(f"Unexpected error: {e}")
            return []