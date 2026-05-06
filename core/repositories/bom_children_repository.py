import sqlite3
from typing import List
from core.models.bom_children_model import BomChild
from config import DB_NAME

class BomChildrenRepository:
    def __init__(self, db_name=DB_NAME):
        self.db_name = db_name

    def get_conn(self):
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        return conn

    # -------------------------------
    # CREATE / INSERT
    # -------------------------------
    def insert(self, parent_id: int, child_id: int, quantity: int = 1) -> int:
        with self.get_conn() as conn:
            cur = conn.cursor()

            # Check if this parent-child pair already exists
            cur.execute("""
                SELECT id, quantity FROM bom_children
                WHERE parent_id = ? AND child_id = ?
            """, (parent_id, child_id))
            row = cur.fetchone()

            if row:
                # If exists → update quantity (+1 or +N)
                new_qty = (row["quantity"] if isinstance(row, dict) else row[1]) + quantity
                cur.execute("""
                    UPDATE bom_children
                    SET quantity = ?
                    WHERE parent_id = ? AND child_id = ?
                """, (new_qty, parent_id, child_id))
                conn.commit()
                return row["id"] if isinstance(row, dict) else row[0]
            else:
                # If not exists → insert new relation
                cur.execute("""
                    INSERT INTO bom_children (parent_id, child_id, quantity)
                    VALUES (?, ?, ?)
                """, (parent_id, child_id, quantity))
                conn.commit()
                return cur.lastrowid


    # -------------------------------
    # READ / GET
    # -------------------------------
    def get_children(self, parent_id: int) -> List[BomChild]:
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM bom_children WHERE parent_id=?", (parent_id,))
            rows = cur.fetchall()
            return [BomChild(**row) for row in rows]

    def get_parents(self, child_id: int) -> List[BomChild]:
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM bom_children WHERE child_id=?", (child_id,))
            rows = cur.fetchall()
            return [BomChild(**row) for row in rows]

    def get_all(self) -> List[BomChild]:
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM bom_children")
            rows = cur.fetchall()
            return [BomChild(**row) for row in rows]

    def get_all_for_project(self, project_id: int) -> List[BomChild]:
        """Return only child relationships whose parent belongs to the given project."""
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT bc.id, bc.parent_id, bc.child_id, bc.quantity
                FROM bom_children bc
                JOIN bom b ON b.id = bc.parent_id
                WHERE b.project_id = ?
                """,
                (int(project_id),),
            )
            rows = cur.fetchall()
            return [BomChild(**dict(r)) for r in rows]

    # -------------------------------
    # DELETE
    # -------------------------------
    def delete(self, parent_id: int, child_id: int):
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                DELETE FROM bom_children
                WHERE parent_id=? AND child_id=?
            """, (parent_id, child_id))

    def delete_by_parent(self, part_id: int):
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM bom_children WHERE parent_id=?", (part_id,))

    def delete_by_child(self, part_id: int):
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM bom_children WHERE child_id=?", (part_id,))
