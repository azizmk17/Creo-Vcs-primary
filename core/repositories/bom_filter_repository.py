import json
import sqlite3

from config import DB_NAME


class BomFilterRepository:
    """Persistence for private and project-shared BOM filter definitions."""

    def __init__(self, db_name=DB_NAME):
        self.db_name = db_name
        self._ensure_schema()

    def get_conn(self):
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        try:
            with self.get_conn() as conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS bom_saved_filters (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        project_id INTEGER NOT NULL,
                        owner_user_id INTEGER NOT NULL,
                        name TEXT NOT NULL COLLATE NOCASE,
                        definition_json TEXT NOT NULL,
                        is_shared INTEGER NOT NULL DEFAULT 0,
                        sort_order INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL DEFAULT (datetime('now')),
                        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                        UNIQUE(project_id, owner_user_id, name)
                    );
                    CREATE INDEX IF NOT EXISTS idx_bom_saved_filters_visible
                        ON bom_saved_filters(project_id, is_shared, owner_user_id, sort_order);
                    """
                )
        except Exception:
            pass

    @staticmethod
    def _clean_name(name: str) -> str:
        value = " ".join(str(name or "").split())
        if not value:
            raise ValueError("Filter name is required.")
        if len(value) > 100:
            raise ValueError("Filter name must be 100 characters or fewer.")
        return value

    @staticmethod
    def _encode_definition(definition: dict) -> str:
        if not isinstance(definition, dict):
            raise ValueError("Filter definition must be an object.")
        return json.dumps(definition, ensure_ascii=True, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _decode_row(row) -> dict:
        result = dict(row)
        try:
            result["definition"] = json.loads(str(result.pop("definition_json") or "{}"))
        except Exception:
            result["definition"] = {}
            result.pop("definition_json", None)
        result["is_shared"] = bool(result.get("is_shared"))
        return result

    def list_visible(self, project_id: int, user_id: int) -> list[dict]:
        with self.get_conn() as conn:
            rows = conn.execute(
                """
                SELECT f.*, COALESCE(u.username, 'Unknown user') AS owner_name
                FROM bom_saved_filters f
                LEFT JOIN users u ON u.id=f.owner_user_id
                WHERE f.project_id=? AND (f.owner_user_id=? OR f.is_shared=1)
                ORDER BY CASE WHEN f.owner_user_id=? THEN 0 ELSE 1 END,
                         f.sort_order, lower(f.name), f.id
                """,
                (int(project_id), int(user_id), int(user_id)),
            ).fetchall()
        return [self._decode_row(row) for row in rows]

    def get_visible(self, project_id: int, user_id: int, filter_id: int) -> dict:
        with self.get_conn() as conn:
            row = conn.execute(
                """
                SELECT f.*, COALESCE(u.username, 'Unknown user') AS owner_name
                FROM bom_saved_filters f
                LEFT JOIN users u ON u.id=f.owner_user_id
                WHERE f.id=? AND f.project_id=?
                  AND (f.owner_user_id=? OR f.is_shared=1)
                """,
                (int(filter_id), int(project_id), int(user_id)),
            ).fetchone()
        if not row:
            raise ValueError("Saved filter was not found or is no longer shared.")
        return self._decode_row(row)

    def create(
        self,
        project_id: int,
        owner_user_id: int,
        name: str,
        definition: dict,
        is_shared: bool = False,
    ) -> dict:
        clean_name = self._clean_name(name)
        encoded = self._encode_definition(definition)
        with self.get_conn() as conn:
            next_order = conn.execute(
                """
                SELECT COALESCE(MAX(sort_order), 0) + 10
                FROM bom_saved_filters WHERE project_id=? AND owner_user_id=?
                """,
                (int(project_id), int(owner_user_id)),
            ).fetchone()[0]
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO bom_saved_filters(
                        project_id, owner_user_id, name, definition_json, is_shared, sort_order
                    ) VALUES(?,?,?,?,?,?)
                    """,
                    (
                        int(project_id), int(owner_user_id), clean_name, encoded,
                        1 if is_shared else 0, int(next_order or 10),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("You already have a saved filter with this name.") from exc
            filter_id = int(cursor.lastrowid)
        return self.get_visible(project_id, owner_user_id, filter_id)

    def update_owned(
        self,
        project_id: int,
        owner_user_id: int,
        filter_id: int,
        *,
        name=None,
        definition=None,
        is_shared=None,
    ) -> dict:
        assignments = []
        values = []
        if name is not None:
            assignments.append("name=?")
            values.append(self._clean_name(name))
        if definition is not None:
            assignments.append("definition_json=?")
            values.append(self._encode_definition(definition))
        if is_shared is not None:
            assignments.append("is_shared=?")
            values.append(1 if is_shared else 0)
        if not assignments:
            return self.get_visible(project_id, owner_user_id, filter_id)
        assignments.append("updated_at=datetime('now')")
        with self.get_conn() as conn:
            try:
                cursor = conn.execute(
                    f"""
                    UPDATE bom_saved_filters SET {', '.join(assignments)}
                    WHERE id=? AND project_id=? AND owner_user_id=?
                    """,
                    [*values, int(filter_id), int(project_id), int(owner_user_id)],
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("You already have a saved filter with this name.") from exc
            if cursor.rowcount != 1:
                raise PermissionError("Only the filter owner can modify this saved filter.")
        return self.get_visible(project_id, owner_user_id, filter_id)

    def delete_owned(self, project_id: int, owner_user_id: int, filter_id: int) -> None:
        with self.get_conn() as conn:
            cursor = conn.execute(
                """
                DELETE FROM bom_saved_filters
                WHERE id=? AND project_id=? AND owner_user_id=?
                """,
                (int(filter_id), int(project_id), int(owner_user_id)),
            )
            if cursor.rowcount != 1:
                raise PermissionError("Only the filter owner can delete this saved filter.")

    def move_owned(self, project_id: int, owner_user_id: int, filter_id: int, direction: int) -> None:
        step = -1 if int(direction) < 0 else 1
        with self.get_conn() as conn:
            rows = conn.execute(
                """
                SELECT id, sort_order FROM bom_saved_filters
                WHERE project_id=? AND owner_user_id=?
                ORDER BY sort_order, lower(name), id
                """,
                (int(project_id), int(owner_user_id)),
            ).fetchall()
            ids = [int(row["id"]) for row in rows]
            if int(filter_id) not in ids:
                raise PermissionError("Only the filter owner can reorder this saved filter.")
            index = ids.index(int(filter_id))
            other_index = index + step
            if other_index < 0 or other_index >= len(rows):
                return
            current = rows[index]
            other = rows[other_index]
            conn.execute(
                "UPDATE bom_saved_filters SET sort_order=? WHERE id=?",
                (int(other["sort_order"]), int(current["id"])),
            )
            conn.execute(
                "UPDATE bom_saved_filters SET sort_order=? WHERE id=?",
                (int(current["sort_order"]), int(other["id"])),
            )
