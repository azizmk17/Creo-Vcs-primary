import sqlite3
from typing import List
from core.models.bom_children_model import BomChild
from config import DB_NAME

class BomChildrenRepository:
    def __init__(self, db_name=DB_NAME):
        self.db_name = db_name
        self._ensure_sort_order_column()

    def get_conn(self):
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_sort_order_column(self):
        try:
            with self.get_conn() as conn:
                cols = [r[1] for r in conn.execute("PRAGMA table_info(bom_children)").fetchall()]
                if "sort_order" not in cols:
                    conn.execute("ALTER TABLE bom_children ADD COLUMN sort_order INTEGER DEFAULT 0")
                    conn.execute(
                        """
                        UPDATE bom_children
                        SET sort_order = id
                        WHERE sort_order IS NULL OR sort_order = 0
                        """
                    )
        except Exception:
            pass

    def _next_sort_order(self, conn, parent_id: int) -> int:
        row = conn.execute(
            "SELECT COALESCE(MAX(sort_order), 0) + 10 FROM bom_children WHERE parent_id = ?",
            (int(parent_id),),
        ).fetchone()
        try:
            return int(row[0] or 10)
        except Exception:
            return 10

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
                    INSERT INTO bom_children (parent_id, child_id, quantity, sort_order)
                    VALUES (?, ?, ?, ?)
                """, (parent_id, child_id, quantity, self._next_sort_order(conn, int(parent_id))))
                conn.commit()
                return cur.lastrowid


    # -------------------------------
    # READ / GET
    # -------------------------------
    def get_children(self, parent_id: int) -> List[BomChild]:
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT * FROM bom_children
                WHERE parent_id=?
                ORDER BY COALESCE(sort_order, id), id
                """,
                (parent_id,),
            )
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
            cur.execute("SELECT * FROM bom_children ORDER BY parent_id, COALESCE(sort_order, id), id")
            rows = cur.fetchall()
            return [BomChild(**row) for row in rows]

    def get_all_for_project(self, project_id: int) -> List[BomChild]:
        """Return only child relationships whose parent belongs to the given project."""
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT bc.id, bc.parent_id, bc.child_id, bc.quantity, COALESCE(bc.sort_order, bc.id) AS sort_order
                FROM bom_children bc
                JOIN bom b ON b.id = bc.parent_id
                WHERE b.project_id = ?
                ORDER BY bc.parent_id, COALESCE(bc.sort_order, bc.id), bc.id
                """,
                (int(project_id),),
            )
            rows = cur.fetchall()
            return [BomChild(**dict(r)) for r in rows]

    def set_child_order(self, parent_id: int, ordered_child_ids: list[int]) -> bool:
        ordered = []
        seen = set()
        for child_id in ordered_child_ids or []:
            try:
                cid = int(child_id)
            except Exception:
                continue
            if cid in seen:
                continue
            ordered.append(cid)
            seen.add(cid)
        if not ordered:
            return False
        with self.get_conn() as conn:
            cur = conn.cursor()
            existing = cur.execute(
                """
                SELECT child_id
                FROM bom_children
                WHERE parent_id = ?
                ORDER BY COALESCE(sort_order, id), id
                """,
                (int(parent_id),),
            ).fetchall()
            existing_ids = [int(r["child_id"]) for r in existing]
            existing_set = set(existing_ids)
            final_order = [cid for cid in ordered if cid in existing_set]
            final_order.extend(cid for cid in existing_ids if cid not in set(final_order))
            for idx, child_id in enumerate(final_order):
                cur.execute(
                    """
                    UPDATE bom_children
                    SET sort_order = ?
                    WHERE parent_id = ? AND child_id = ?
                    """,
                    ((idx + 1) * 10, int(parent_id), int(child_id)),
                )
            conn.commit()
            return True

    def ordered_child_ids(self, parent_id: int) -> list[int]:
        with self.get_conn() as conn:
            rows = conn.execute(
                """
                SELECT child_id
                FROM bom_children
                WHERE parent_id = ?
                ORDER BY COALESCE(sort_order, id), id
                """,
                (int(parent_id),),
            ).fetchall()
            return [int(r["child_id"]) for r in rows]

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
