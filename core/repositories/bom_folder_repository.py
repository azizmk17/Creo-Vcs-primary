import sqlite3

from config import DB_NAME


class BomFolderRepository:
    """Persistence for visual BOM folders that never alter engineering relations."""

    def __init__(self, db_name=DB_NAME):
        self.db_name = db_name
        self._ensure_schema()

    def get_conn(self):
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self):
        try:
            with self.get_conn() as conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS bom_folders (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        project_id INTEGER NOT NULL,
                        name TEXT NOT NULL,
                        parent_bom_id INTEGER,
                        parent_folder_id INTEGER,
                        sort_order INTEGER NOT NULL DEFAULT 0,
                        created_by INTEGER,
                        created_at TEXT NOT NULL DEFAULT (datetime('now'))
                    );
                    CREATE TABLE IF NOT EXISTS bom_folder_items (
                        folder_id INTEGER NOT NULL,
                        bom_id INTEGER NOT NULL,
                        assigned_by INTEGER,
                        assigned_at TEXT NOT NULL DEFAULT (datetime('now')),
                        PRIMARY KEY (folder_id, bom_id)
                    );
                    CREATE INDEX IF NOT EXISTS idx_bom_folders_project_parent
                        ON bom_folders(project_id, parent_bom_id, parent_folder_id, sort_order, id);
                    CREATE INDEX IF NOT EXISTS idx_bom_folder_items_folder
                        ON bom_folder_items(folder_id);
                    CREATE INDEX IF NOT EXISTS idx_bom_folder_items_bom
                        ON bom_folder_items(bom_id);
                    """
                )
        except Exception:
            pass

    @staticmethod
    def _clean_name(name: str) -> str:
        value = " ".join(str(name or "").split())
        if not value:
            raise ValueError("Folder name is required.")
        if len(value) > 100:
            raise ValueError("Folder name must be 100 characters or fewer.")
        return value

    def _folder_row(self, conn, project_id: int, folder_id: int):
        return conn.execute(
            "SELECT * FROM bom_folders WHERE id=? AND project_id=?",
            (int(folder_id), int(project_id)),
        ).fetchone()

    def effective_parent_bom_id(self, conn, project_id: int, folder_id: int):
        seen = set()
        current_id = int(folder_id)
        while current_id not in seen:
            seen.add(current_id)
            row = self._folder_row(conn, project_id, current_id)
            if not row:
                raise ValueError("Folder was not found in the current project.")
            if row["parent_bom_id"] is not None:
                return int(row["parent_bom_id"])
            if row["parent_folder_id"] is None:
                return None
            current_id = int(row["parent_folder_id"])
        raise ValueError("Circular folder hierarchy detected.")

    def list_for_project(self, project_id: int) -> list[dict]:
        with self.get_conn() as conn:
            folders = [
                dict(row) for row in conn.execute(
                    """
                    SELECT * FROM bom_folders
                    WHERE project_id=?
                    ORDER BY COALESCE(parent_bom_id, -1), COALESCE(parent_folder_id, -1), sort_order, id
                    """,
                    (int(project_id),),
                ).fetchall()
            ]
            memberships = conn.execute(
                """
                SELECT fi.folder_id, fi.bom_id
                FROM bom_folder_items fi
                JOIN bom_folders f ON f.id=fi.folder_id
                WHERE f.project_id=?
                ORDER BY fi.folder_id, fi.bom_id
                """,
                (int(project_id),),
            ).fetchall()
            items_by_folder = {}
            for row in memberships:
                items_by_folder.setdefault(int(row["folder_id"]), []).append(int(row["bom_id"]))
            for folder in folders:
                folder["item_ids"] = items_by_folder.get(int(folder["id"]), [])
                folder["effective_parent_bom_id"] = self.effective_parent_bom_id(
                    conn, int(project_id), int(folder["id"])
                )
            return folders

    def create(self, project_id: int, name: str, created_by=None, parent_bom_id=None, parent_folder_id=None) -> dict:
        clean_name = self._clean_name(name)
        with self.get_conn() as conn:
            if parent_bom_id is not None:
                parent = conn.execute(
                    "SELECT id FROM bom WHERE id=? AND project_id=?",
                    (int(parent_bom_id), int(project_id)),
                ).fetchone()
                if not parent:
                    raise ValueError("Parent BOM item was not found in the current project.")
            if parent_folder_id is not None:
                if not self._folder_row(conn, int(project_id), int(parent_folder_id)):
                    raise ValueError("Parent folder was not found in the current project.")
            if parent_bom_id is not None and parent_folder_id is not None:
                raise ValueError("A folder cannot have both a BOM parent and a folder parent.")
            duplicate = conn.execute(
                """
                SELECT id FROM bom_folders
                WHERE project_id=? AND name=? COLLATE NOCASE
                  AND parent_bom_id IS ? AND parent_folder_id IS ?
                """,
                (int(project_id), clean_name, parent_bom_id, parent_folder_id),
            ).fetchone()
            if duplicate:
                raise ValueError("A folder with this name already exists at that location.")
            order_row = conn.execute(
                """
                SELECT COALESCE(MAX(sort_order), 0) + 10
                FROM bom_folders
                WHERE project_id=? AND parent_bom_id IS ? AND parent_folder_id IS ?
                """,
                (int(project_id), parent_bom_id, parent_folder_id),
            ).fetchone()
            cursor = conn.execute(
                """
                INSERT INTO bom_folders(
                    project_id, name, parent_bom_id, parent_folder_id, sort_order, created_by
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    int(project_id), clean_name,
                    int(parent_bom_id) if parent_bom_id is not None else None,
                    int(parent_folder_id) if parent_folder_id is not None else None,
                    int(order_row[0] or 10), created_by,
                ),
            )
            folder_id = int(cursor.lastrowid)
        return next(row for row in self.list_for_project(project_id) if int(row["id"]) == folder_id)

    def rename(self, project_id: int, folder_id: int, name: str) -> dict:
        clean_name = self._clean_name(name)
        with self.get_conn() as conn:
            folder = self._folder_row(conn, project_id, folder_id)
            if not folder:
                raise ValueError("Folder was not found in the current project.")
            duplicate = conn.execute(
                """
                SELECT id FROM bom_folders
                WHERE project_id=? AND id<>? AND name=? COLLATE NOCASE
                  AND parent_bom_id IS ? AND parent_folder_id IS ?
                """,
                (
                    int(project_id), int(folder_id), clean_name,
                    folder["parent_bom_id"], folder["parent_folder_id"],
                ),
            ).fetchone()
            if duplicate:
                raise ValueError("A folder with this name already exists at that location.")
            conn.execute("UPDATE bom_folders SET name=? WHERE id=?", (clean_name, int(folder_id)))
        return next(row for row in self.list_for_project(project_id) if int(row["id"]) == int(folder_id))

    def eligible_items(self, project_id: int, folder_id: int) -> list[dict]:
        with self.get_conn() as conn:
            parent_bom_id = self.effective_parent_bom_id(conn, project_id, folder_id)
            if parent_bom_id is None:
                rows = conn.execute(
                    """
                    SELECT b.* FROM bom b
                    WHERE b.project_id=? AND NOT EXISTS(
                        SELECT 1 FROM bom_children bc WHERE bc.child_id=b.id
                    )
                    ORDER BY lower(b.name), b.id
                    """,
                    (int(project_id),),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT b.* FROM bom_children bc
                    JOIN bom b ON b.id=bc.child_id
                    WHERE bc.parent_id=? AND b.project_id=?
                    ORDER BY COALESCE(bc.sort_order, bc.id), bc.id
                    """,
                    (int(parent_bom_id), int(project_id)),
                ).fetchall()
            assigned = {
                int(row["bom_id"]) for row in conn.execute(
                    "SELECT bom_id FROM bom_folder_items WHERE folder_id=?", (int(folder_id),)
                ).fetchall()
            }
            result = []
            for row in rows:
                item = dict(row)
                item["assigned"] = int(item["id"]) in assigned
                result.append(item)
            return result

    def set_items(self, project_id: int, folder_id: int, bom_ids, assigned_by=None) -> list[int]:
        selected = sorted({int(value) for value in (bom_ids or [])})
        with self.get_conn() as conn:
            folder = self._folder_row(conn, project_id, folder_id)
            if not folder:
                raise ValueError("Folder was not found in the current project.")
            context_parent = self.effective_parent_bom_id(conn, project_id, folder_id)
            all_folders = conn.execute(
                "SELECT id FROM bom_folders WHERE project_id=?", (int(project_id),)
            ).fetchall()
            same_context_ids = []
            for row in all_folders:
                candidate_id = int(row["id"])
                if self.effective_parent_bom_id(conn, project_id, candidate_id) == context_parent:
                    same_context_ids.append(candidate_id)
            if same_context_ids and selected:
                placeholders_folders = ",".join("?" for _ in same_context_ids)
                placeholders_items = ",".join("?" for _ in selected)
                conn.execute(
                    f"DELETE FROM bom_folder_items WHERE folder_id IN ({placeholders_folders}) "
                    f"AND bom_id IN ({placeholders_items})",
                    (*same_context_ids, *selected),
                )
            conn.execute("DELETE FROM bom_folder_items WHERE folder_id=?", (int(folder_id),))
            if context_parent is None:
                eligible_rows = conn.execute(
                    """
                    SELECT b.id FROM bom b
                    WHERE b.project_id=? AND NOT EXISTS(
                        SELECT 1 FROM bom_children bc WHERE bc.child_id=b.id
                    )
                    """,
                    (int(project_id),),
                ).fetchall()
            else:
                eligible_rows = conn.execute(
                    """
                    SELECT b.id FROM bom_children bc
                    JOIN bom b ON b.id=bc.child_id
                    WHERE bc.parent_id=? AND b.project_id=?
                    """,
                    (int(context_parent), int(project_id)),
                ).fetchall()
            eligible = {int(row["id"]) for row in eligible_rows}
            invalid = [item_id for item_id in selected if item_id not in eligible]
            if invalid:
                raise ValueError("One or more selected items are not direct members of this folder location.")
            for bom_id in selected:
                conn.execute(
                    "INSERT INTO bom_folder_items(folder_id, bom_id, assigned_by) VALUES(?,?,?)",
                    (int(folder_id), int(bom_id), assigned_by),
                )
        return selected

    def delete(self, project_id: int, folder_id: int) -> list[int]:
        with self.get_conn() as conn:
            folder = self._folder_row(conn, project_id, folder_id)
            if not folder:
                raise ValueError("Folder was not found in the current project.")
            descendants = []

            def collect(current_id: int):
                descendants.append(int(current_id))
                rows = conn.execute(
                    "SELECT id FROM bom_folders WHERE project_id=? AND parent_folder_id=?",
                    (int(project_id), int(current_id)),
                ).fetchall()
                for row in rows:
                    collect(int(row["id"]))

            collect(int(folder_id))
            placeholders = ",".join("?" for _ in descendants)
            conn.execute(f"DELETE FROM bom_folder_items WHERE folder_id IN ({placeholders})", descendants)
            conn.execute(f"DELETE FROM bom_folders WHERE id IN ({placeholders})", descendants)
            return descendants

    def unassign_from_context(self, project_id: int, parent_bom_id: int, bom_id: int) -> None:
        with self.get_conn() as conn:
            folder_rows = conn.execute(
                "SELECT id FROM bom_folders WHERE project_id=?", (int(project_id),)
            ).fetchall()
            matching_folder_ids = [
                int(row["id"])
                for row in folder_rows
                if self.effective_parent_bom_id(conn, project_id, int(row["id"])) == int(parent_bom_id)
            ]
            if not matching_folder_ids:
                return
            placeholders = ",".join("?" for _ in matching_folder_ids)
            conn.execute(
                f"DELETE FROM bom_folder_items WHERE folder_id IN ({placeholders}) AND bom_id=?",
                (*matching_folder_ids, int(bom_id)),
            )
