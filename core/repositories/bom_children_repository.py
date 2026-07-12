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

    def get_structure_rows(self, project_id: int) -> list[dict]:
        """Return relation IDs only; this is intentionally cheaper than BOM rows."""
        with self.get_conn() as conn:
            rows = conn.execute(
                """
                SELECT bc.parent_id, bc.child_id,
                       COALESCE(bc.sort_order, bc.id) AS sort_order
                FROM bom_children bc
                JOIN bom parent ON parent.id=bc.parent_id
                JOIN bom child ON child.id=bc.child_id
                WHERE parent.project_id=? AND child.project_id=?
                ORDER BY bc.parent_id, COALESCE(bc.sort_order, bc.id), bc.id
                """,
                (int(project_id), int(project_id)),
            ).fetchall()
            return [dict(row) for row in rows]

    def search_occurrences(self, project_id: int, query: str = "", limit: int | None = None) -> list[dict]:
        """Return searchable direct BOM occurrences, including roots."""
        value = str(query or "").strip()
        sql = """
                SELECT b.id AS child_id,
                       b.name,
                       b.aes_number,
                       b.part_number,
                       b.type,
                       bc.parent_id,
                       parent.name AS parent_name,
                       parent.aes_number AS parent_aes_number,
                       COALESCE(bc.quantity, 1) AS quantity
                FROM bom b
                LEFT JOIN bom_children bc ON bc.child_id=b.id
                LEFT JOIN bom parent ON parent.id=bc.parent_id
                WHERE b.project_id=?
                  AND (parent.id IS NULL OR parent.project_id=?)
                  AND (
                    instr(lower(COALESCE(b.name, '')), lower(?)) > 0 OR
                    instr(lower(COALESCE(b.aes_number, '')), lower(?)) > 0 OR
                    instr(lower(COALESCE(b.part_number, '')), lower(?)) > 0 OR
                    instr(lower(COALESCE(parent.name, '')), lower(?)) > 0 OR
                    instr(lower(COALESCE(parent.aes_number, '')), lower(?)) > 0
                  )
                ORDER BY lower(COALESCE(b.aes_number, '')), lower(b.name),
                         lower(COALESCE(parent.aes_number, '')), bc.id
              """
        params = [int(project_id), int(project_id), value, value, value, value, value]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(max(1, int(limit)))
        with self.get_conn() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(row) for row in rows]

    def apply_child_relations(self, target_parent_id: int, selections, mode: str) -> dict:
        """Atomically copy or move direct occurrences to a target parent."""
        action = str(mode or "").strip().lower()
        if action not in {"copy", "move"}:
            raise ValueError("Relation action must be Copy or Move.")

        normalized = []
        seen = set()
        for selection in selections or []:
            child_id = int(selection.get("child_id"))
            source_value = selection.get("source_parent_id")
            source_parent_id = int(source_value) if source_value is not None else None
            key = (child_id, source_parent_id)
            if key in seen:
                continue
            seen.add(key)
            normalized.append((child_id, source_parent_id))
        if not normalized:
            raise ValueError("Select at least one child occurrence.")

        affected_sources = set()
        changed_children = set()
        skipped = []
        with self.get_conn() as conn:
            for child_id, source_parent_id in normalized:
                if action == "move" and source_parent_id == int(target_parent_id):
                    skipped.append(child_id)
                    continue

                quantity = 1
                if source_parent_id is not None:
                    source = conn.execute(
                        """
                        SELECT quantity FROM bom_children
                        WHERE parent_id=? AND child_id=?
                        """,
                        (int(source_parent_id), int(child_id)),
                    ).fetchone()
                    if not source:
                        raise ValueError(f"The selected source relation for item {child_id} no longer exists.")
                    quantity = max(1, int(source["quantity"] or 1))

                target = conn.execute(
                    """
                    SELECT id, quantity FROM bom_children
                    WHERE parent_id=? AND child_id=?
                    """,
                    (int(target_parent_id), int(child_id)),
                ).fetchone()
                if target:
                    conn.execute(
                        "UPDATE bom_children SET quantity=? WHERE id=?",
                        (int(target["quantity"] or 0) + quantity, int(target["id"])),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO bom_children(parent_id, child_id, quantity, sort_order)
                        VALUES(?,?,?,?)
                        """,
                        (
                            int(target_parent_id), int(child_id), quantity,
                            self._next_sort_order(conn, int(target_parent_id)),
                        ),
                    )

                if action == "move" and source_parent_id is not None:
                    conn.execute(
                        "DELETE FROM bom_children WHERE parent_id=? AND child_id=?",
                        (int(source_parent_id), int(child_id)),
                    )
                    affected_sources.add(int(source_parent_id))
                changed_children.add(int(child_id))

        return {
            "mode": action,
            "target_parent_id": int(target_parent_id),
            "child_ids": sorted(changed_children),
            "source_parent_ids": sorted(affected_sources),
            "skipped_child_ids": skipped,
            "had_root_sources": any(source is None for _child, source in normalized),
        }

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

    def remove_children_from_parent(self, project_id: int, parent_id: int, child_ids) -> dict:
        """Remove selected direct relations and report children that become roots."""
        ids = []
        seen = set()
        for value in child_ids or []:
            child_id = int(value)
            if child_id not in seen:
                seen.add(child_id)
                ids.append(child_id)
        if not ids:
            raise ValueError("Select at least one child relation to remove.")

        placeholders = ",".join("?" for _ in ids)
        with self.get_conn() as conn:
            parent = conn.execute(
                "SELECT id FROM bom WHERE id=? AND project_id=?",
                (int(parent_id), int(project_id)),
            ).fetchone()
            if not parent:
                raise ValueError("The parent assembly was not found in the current project.")
            relations = conn.execute(
                f"""
                SELECT bc.child_id
                FROM bom_children bc
                JOIN bom child ON child.id=bc.child_id
                WHERE bc.parent_id=? AND bc.child_id IN ({placeholders})
                  AND child.project_id=?
                """,
                [int(parent_id), *ids, int(project_id)],
            ).fetchall()
            existing_ids = {int(row["child_id"]) for row in relations}
            missing = [child_id for child_id in ids if child_id not in existing_ids]
            if missing:
                raise ValueError("One or more selected items are no longer direct children of this parent.")

            conn.execute(
                f"DELETE FROM bom_children WHERE parent_id=? AND child_id IN ({placeholders})",
                [int(parent_id), *ids],
            )
            moved_to_root = []
            for child_id in ids:
                remaining = conn.execute(
                    """
                    SELECT 1
                    FROM bom_children bc
                    JOIN bom parent ON parent.id=bc.parent_id
                    WHERE bc.child_id=? AND parent.project_id=?
                    LIMIT 1
                    """,
                    (int(child_id), int(project_id)),
                ).fetchone()
                if not remaining:
                    moved_to_root.append(int(child_id))
        return {
            "parent_id": int(parent_id),
            "removed_child_ids": ids,
            "moved_to_root_ids": moved_to_root,
        }

    def delete_by_parent(self, part_id: int):
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM bom_children WHERE parent_id=?", (part_id,))

    def delete_by_child(self, part_id: int):
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM bom_children WHERE child_id=?", (part_id,))
