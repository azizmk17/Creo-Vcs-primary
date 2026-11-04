import json
from datetime import datetime
from config import DB_NAME # adjust to your ORM or query interface
import sqlite3
from contextlib import closing

class SnapshotRepository:
    def __init__(self, db_name=DB_NAME):
        self.db_name = db_name

    def get_conn(self):
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        return conn

    def create_snapshot(self, project_id, name, description, data, created_by):
        with closing(self.get_conn()) as conn, conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO snapshots (project_id, snapshot_name, description, snapshot_data, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (project_id, name, description, json.dumps(data), created_by, datetime.now()))
            conn.commit()
            return cur.lastrowid

    def get_all(self, project_id):
        with closing(self.get_conn()) as conn, conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM snapshots WHERE project_id = ? ORDER BY created_at DESC", (project_id,))
            rows = cur.fetchall()
            return rows

    def get_by_id(self, snapshot_id):
        with closing(self.get_conn()) as conn, conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM snapshots WHERE id = ?", (snapshot_id,))
            return cur.fetchone()

    def delete(self, snapshot_id):
        with closing(self.get_conn()) as conn, conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM snapshots WHERE id = ?", (snapshot_id,))
            conn.commit()

    def update_data(self, snapshot_id, data):
        with closing(self.get_conn()) as conn, conn:
            conn.execute("UPDATE snapshots SET snapshot_data=? WHERE id=?", (json.dumps(data), int(snapshot_id)))

    def get_last_snapshot_id(self, project_id):
        with closing(self.get_conn()) as conn, conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM snapshots WHERE project_id = ? ORDER BY id DESC LIMIT 1",(project_id,))
            row = cur.fetchone()
            return int(row[0]) if row else 0
        
    def get_last_snapshot(self, project_id):
        with closing(self.get_conn()) as conn, conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM snapshots WHERE project_id = ? ORDER BY id DESC LIMIT 1", (project_id,))
            return cur.fetchone()
            
