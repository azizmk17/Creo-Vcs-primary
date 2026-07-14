import json
import re
import sqlite3

from config import DB_NAME


class BomRevisionRepository:
    """Object-level revisions, iterations, and exact assembly configurations."""

    _REVISION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")
    _SNAPSHOT_FIELDS = (
        "type", "name", "part_number", "drawing_number", "aes_number",
        "filename", "drawing", "base_file_name", "base_drw_name", "material",
        "weight", "notes", "pdf_path", "step_path",
    )
    _CHECKIN_METADATA_FIELDS = (
        "type", "name", "part_number", "drawing_number", "aes_number",
        "material", "weight", "notes",
    )
    _CHECKIN_METADATA_LABELS = {
        "type": "Type",
        "name": "Name",
        "part_number": "Part number",
        "drawing_number": "Drawing number",
        "aes_number": "AES number",
        "material": "Material",
        "weight": "Weight",
        "notes": "Technical note",
    }

    def __init__(self, db_name=DB_NAME):
        self.db_name = db_name
        self._ensure_schema()

    def get_conn(self):
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        return conn

    @classmethod
    def normalize_revision_code(cls, value: str) -> str:
        code = str(value or "").strip().upper()
        if not code:
            raise ValueError("Revision code is required.")
        if not cls._REVISION_PATTERN.fullmatch(code):
            raise ValueError(
                "Revision code must start with a letter or number and may contain letters, "
                "numbers, dots, underscores, or hyphens (for example A, A1, or A010)."
            )
        return code

    @classmethod
    def suggest_next_revision_code(cls, value: str) -> str:
        """Suggest a revision while allowing the user to override local conventions."""
        code = cls.normalize_revision_code(value or "A")
        if code.isalpha():
            digits = [ord(char) - ord("A") for char in code]
            carry = 1
            for index in range(len(digits) - 1, -1, -1):
                digits[index] += carry
                if digits[index] >= 26:
                    digits[index] = 0
                else:
                    carry = 0
                    break
            if carry:
                digits.insert(0, 0)
            return "".join(chr(value + ord("A")) for value in digits)

        match = re.fullmatch(r"(.*?)(\d+)", code)
        if match:
            prefix, raw_number = match.groups()
            number = int(raw_number)
            step = 10 if len(raw_number) >= 2 and number and number % 10 == 0 else 1
            return f"{prefix}{number + step:0{len(raw_number)}d}"
        return f"{code}1"

    def _ensure_schema(self) -> None:
        with self.get_conn() as conn:
            tables = {
                str(row[0])
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            if "bom" not in tables:
                return
            columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(bom)").fetchall()}
            if "current_revision_id" not in columns:
                conn.execute("ALTER TABLE bom ADD COLUMN current_revision_id INTEGER")
            if "current_iteration_id" not in columns:
                conn.execute("ALTER TABLE bom ADD COLUMN current_iteration_id INTEGER")
            if "pending_revision_code" not in columns:
                conn.execute("ALTER TABLE bom ADD COLUMN pending_revision_code TEXT")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS bom_revisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bom_id INTEGER NOT NULL,
                    revision_code TEXT NOT NULL COLLATE NOCASE,
                    state TEXT NOT NULL DEFAULT 'In Work',
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    created_by INTEGER,
                    released_at TEXT,
                    released_by INTEGER,
                    release_note TEXT,
                    UNIQUE(bom_id, revision_code)
                );
                CREATE TABLE IF NOT EXISTS bom_iterations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    revision_id INTEGER NOT NULL,
                    iteration_number INTEGER NOT NULL,
                    checkin_note TEXT,
                    source_commit_id TEXT,
                    object_data_json TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    created_by INTEGER,
                    UNIQUE(revision_id, iteration_number)
                );
                CREATE TABLE IF NOT EXISTS bom_iteration_bindings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    parent_iteration_id INTEGER NOT NULL,
                    usage_id INTEGER,
                    child_bom_id INTEGER NOT NULL,
                    child_revision_id INTEGER NOT NULL,
                    child_iteration_id INTEGER NOT NULL,
                    quantity INTEGER NOT NULL DEFAULT 1,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    UNIQUE(parent_iteration_id, usage_id)
                );
                CREATE TABLE IF NOT EXISTS bom_working_bindings (
                    parent_bom_id INTEGER NOT NULL,
                    usage_id INTEGER NOT NULL,
                    child_bom_id INTEGER NOT NULL,
                    child_revision_id INTEGER NOT NULL,
                    child_iteration_id INTEGER NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_by INTEGER,
                    PRIMARY KEY(parent_bom_id, usage_id)
                );
                CREATE INDEX IF NOT EXISTS idx_bom_revisions_bom
                    ON bom_revisions(bom_id, id);
                CREATE INDEX IF NOT EXISTS idx_bom_iterations_revision
                    ON bom_iterations(revision_id, iteration_number);
                CREATE INDEX IF NOT EXISTS idx_bom_iteration_bindings_parent
                    ON bom_iteration_bindings(parent_iteration_id, sort_order, id);
                CREATE INDEX IF NOT EXISTS idx_bom_iteration_bindings_child
                    ON bom_iteration_bindings(child_bom_id, child_iteration_id);
                CREATE INDEX IF NOT EXISTS idx_bom_working_bindings_parent
                    ON bom_working_bindings(parent_bom_id, usage_id);
                """
            )
            iteration_columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(bom_iterations)").fetchall()
            }
            if "source_commit_id" not in iteration_columns:
                conn.execute("ALTER TABLE bom_iterations ADD COLUMN source_commit_id TEXT")
            if "object_data_json" not in iteration_columns:
                conn.execute("ALTER TABLE bom_iterations ADD COLUMN object_data_json TEXT")
            iteration_columns = self._iteration_columns_conn(conn)
            if "commit_id" in iteration_columns:
                conn.execute(
                    """
                    UPDATE bom_iterations
                    SET source_commit_id=CAST(commit_id AS TEXT)
                    WHERE source_commit_id IS NULL AND commit_id IS NOT NULL
                    """
                )
            self._backfill_missing(conn)

    @staticmethod
    def _iteration_columns_conn(conn) -> set:
        return {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(bom_iterations)").fetchall()
        }

    def _insert_iteration_conn(
        self,
        conn,
        revision_id: int,
        iteration_number: int,
        *,
        checkin_note=None,
        source_commit_id=None,
        object_data_json=None,
        created_by=None,
        ignore_existing: bool = False,
    ) -> int:
        columns = self._iteration_columns_conn(conn)
        values = {
            "revision_id": int(revision_id),
            "iteration_number": int(iteration_number),
            "checkin_note": checkin_note,
            "source_commit_id": source_commit_id,
            "object_data_json": object_data_json,
            "created_by": created_by,
        }
        # The pre-v22 PLM prototype required this column. Keep it populated so
        # existing databases can be upgraded without rebuilding their history.
        if "folder_path" in columns:
            values["folder_path"] = ""
        names = [name for name in values if name in columns]
        placeholders = ",".join("?" for _ in names)
        insert_verb = "INSERT OR IGNORE" if ignore_existing else "INSERT"
        cursor = conn.execute(
            f"{insert_verb} INTO bom_iterations({','.join(names)}) VALUES({placeholders})",
            tuple(values[name] for name in names),
        )
        if cursor.lastrowid:
            return int(cursor.lastrowid)
        row = conn.execute(
            """
            SELECT id FROM bom_iterations
            WHERE revision_id=? AND iteration_number=?
            """,
            (int(revision_id), int(iteration_number)),
        ).fetchone()
        if not row:
            raise RuntimeError(
                f"Could not create iteration {iteration_number} for revision {revision_id}."
            )
        return int(row[0])

    def _object_snapshot_conn(self, conn, bom_id: int) -> str:
        columns = ", ".join(self._SNAPSHOT_FIELDS)
        row = conn.execute(f"SELECT {columns} FROM bom WHERE id=?", (int(bom_id),)).fetchone()
        if not row:
            raise ValueError("BOM item was not found.")
        return json.dumps(dict(row), ensure_ascii=True, sort_keys=True, separators=(",", ":"))

    def _ensure_bom_conn(self, conn, bom_id: int, created_by=None) -> dict:
        row = conn.execute(
            """
            SELECT id, revision, lifecycle_state, status,
                   current_revision_id, current_iteration_id
            FROM bom WHERE id=?
            """,
            (int(bom_id),),
        ).fetchone()
        if not row:
            raise ValueError("BOM item was not found.")

        current = self._current_context_conn(conn, int(bom_id), allow_missing=True)
        if current:
            return current

        code = str(row["revision"] or "A").strip().upper() or "A"
        lifecycle = str(row["lifecycle_state"] or row["status"] or "").lower()
        state = "Released" if "release" in lifecycle else "In Work"
        conn.execute(
            """
            INSERT OR IGNORE INTO bom_revisions(bom_id, revision_code, state, created_by)
            VALUES(?,?,?,?)
            """,
            (int(bom_id), code, state, created_by),
        )
        revision_id = int(conn.execute(
            "SELECT id FROM bom_revisions WHERE bom_id=? AND revision_code=? COLLATE NOCASE",
            (int(bom_id), code),
        ).fetchone()[0])
        self._insert_iteration_conn(
            conn,
            revision_id,
            1,
            object_data_json=self._object_snapshot_conn(conn, int(bom_id)),
            created_by=created_by,
            ignore_existing=True,
        )
        iteration_id = int(conn.execute(
            "SELECT id FROM bom_iterations WHERE revision_id=? AND iteration_number=1",
            (revision_id,),
        ).fetchone()[0])
        conn.execute(
            """
            UPDATE bom_iterations
            SET object_data_json=COALESCE(object_data_json, ?)
            WHERE id=?
            """,
            (self._object_snapshot_conn(conn, int(bom_id)), iteration_id),
        )
        conn.execute(
            "UPDATE bom SET current_revision_id=?, current_iteration_id=? WHERE id=?",
            (revision_id, iteration_id, int(bom_id)),
        )
        return self._current_context_conn(conn, int(bom_id))

    def _backfill_missing(self, conn) -> None:
        rows = conn.execute(
            """
            SELECT b.id
            FROM bom b
            LEFT JOIN bom_revisions r ON r.id=b.current_revision_id AND r.bom_id=b.id
            LEFT JOIN bom_iterations i ON i.id=b.current_iteration_id AND i.revision_id=r.id
            WHERE b.current_revision_id IS NULL OR b.current_iteration_id IS NULL
               OR r.id IS NULL OR i.id IS NULL
            ORDER BY b.id
            """
        ).fetchall()
        initialized_ids = []
        for row in rows:
            bom_id = int(row["id"])
            self._ensure_bom_conn(conn, bom_id)
            initialized_ids.append(bom_id)
        if not initialized_ids:
            return
        placeholders = ",".join("?" for _ in initialized_ids)
        for row in conn.execute(
            f"""
            SELECT bc.id AS usage_id, bc.parent_id, bc.child_id,
                   COALESCE(bc.quantity, 1) AS quantity,
                   COALESCE(bc.sort_order, bc.id) AS sort_order,
                   parent.current_iteration_id AS parent_iteration_id,
                   child.current_revision_id AS child_revision_id,
                   child.current_iteration_id AS child_iteration_id
            FROM bom_children bc
            JOIN bom parent ON parent.id=bc.parent_id
            JOIN bom child ON child.id=bc.child_id
            WHERE bc.parent_id IN ({placeholders})
            """,
            initialized_ids,
        ).fetchall():
            if not row["parent_iteration_id"] or not row["child_iteration_id"]:
                continue
            conn.execute(
                """
                INSERT OR IGNORE INTO bom_iteration_bindings(
                    parent_iteration_id, usage_id, child_bom_id, child_revision_id,
                    child_iteration_id, quantity, sort_order
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    int(row["parent_iteration_id"]), int(row["usage_id"]), int(row["child_id"]),
                    int(row["child_revision_id"]), int(row["child_iteration_id"]),
                    max(1, int(row["quantity"] or 1)), int(row["sort_order"] or 0),
                ),
            )

    def ensure_bom(self, bom_id: int, created_by=None) -> dict:
        with self.get_conn() as conn:
            return self._ensure_bom_conn(conn, int(bom_id), created_by=created_by)

    def _current_context_conn(self, conn, bom_id: int, allow_missing: bool = False) -> dict:
        row = conn.execute(
            """
            SELECT b.id AS bom_id, b.current_revision_id, b.current_iteration_id,
                   b.pending_revision_code,
                   r.revision_code, r.state, r.created_at AS revision_created_at,
                   r.created_by AS revision_created_by, r.released_at, r.released_by,
                   r.release_note, i.iteration_number, i.checkin_note,
                   i.source_commit_id, i.object_data_json,
                   i.created_at AS iteration_created_at,
                   i.created_by AS iteration_created_by
            FROM bom b
            LEFT JOIN bom_revisions r ON r.id=b.current_revision_id AND r.bom_id=b.id
            LEFT JOIN bom_iterations i ON i.id=b.current_iteration_id AND i.revision_id=r.id
            WHERE b.id=?
            """,
            (int(bom_id),),
        ).fetchone()
        if (
            not row
            or row["current_revision_id"] is None
            or row["current_iteration_id"] is None
            or row["revision_code"] is None
            or row["iteration_number"] is None
        ):
            if allow_missing:
                return {}
            raise ValueError("BOM item has no current revision/iteration.")
        result = dict(row)
        result["version_label"] = f"{result['revision_code']}.{int(result['iteration_number'])}"
        return result

    def get_current_context(self, bom_id: int) -> dict:
        with self.get_conn() as conn:
            self._ensure_bom_conn(conn, int(bom_id))
            return self._current_context_conn(conn, int(bom_id))

    def analyze_working_object(self, bom_id: int) -> dict:
        """Compare the checked-in object snapshot with its current working state."""
        with self.get_conn() as conn:
            current = self._ensure_bom_conn(conn, int(bom_id))
            try:
                baseline_object = json.loads(str(current.get("object_data_json") or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                baseline_object = {}

            columns = ", ".join(self._SNAPSHOT_FIELDS)
            row = conn.execute(
                f"SELECT {columns} FROM bom WHERE id=?", (int(bom_id),)
            ).fetchone()
            working_object = dict(row) if row else {}

            def comparable(value):
                if value is None:
                    return ""
                if isinstance(value, float) and value.is_integer():
                    return str(int(value))
                return str(value).strip()

            metadata_changes = []
            for field in self._CHECKIN_METADATA_FIELDS:
                before = baseline_object.get(field)
                after = working_object.get(field)
                if comparable(before) == comparable(after):
                    continue
                metadata_changes.append({
                    "field": field,
                    "label": self._CHECKIN_METADATA_LABELS.get(field, field.replace("_", " ").title()),
                    "before": before,
                    "after": after,
                })

            baseline_rows = conn.execute(
                """
                SELECT ib.usage_id, ib.child_bom_id AS child_id,
                       COALESCE(ib.quantity, 1) AS quantity,
                       COALESCE(ib.sort_order, ib.id) AS sort_order,
                       ib.child_iteration_id,
                       child.name, child.aes_number,
                       child_rev.revision_code || '.' || child_it.iteration_number AS version_label
                FROM bom_iteration_bindings ib
                JOIN bom child ON child.id=ib.child_bom_id
                JOIN bom_revisions child_rev ON child_rev.id=ib.child_revision_id
                JOIN bom_iterations child_it ON child_it.id=ib.child_iteration_id
                WHERE ib.parent_iteration_id=?
                ORDER BY COALESCE(ib.sort_order, ib.id), ib.id
                """,
                (int(current["current_iteration_id"]),),
            ).fetchall()
            working_rows = conn.execute(
                """
                SELECT bc.id AS usage_id, bc.child_id,
                       COALESCE(bc.quantity, 1) AS quantity,
                       COALESCE(bc.sort_order, bc.id) AS sort_order,
                       COALESCE(wb.child_iteration_id, child.current_iteration_id) AS child_iteration_id,
                       child.name, child.aes_number,
                       child_rev.revision_code || '.' || child_it.iteration_number AS version_label
                FROM bom_children bc
                JOIN bom child ON child.id=bc.child_id
                LEFT JOIN bom_working_bindings wb
                    ON wb.parent_bom_id=bc.parent_id AND wb.usage_id=bc.id
                LEFT JOIN bom_revisions child_rev
                    ON child_rev.id=COALESCE(wb.child_revision_id, child.current_revision_id)
                LEFT JOIN bom_iterations child_it
                    ON child_it.id=COALESCE(wb.child_iteration_id, child.current_iteration_id)
                WHERE bc.parent_id=?
                ORDER BY COALESCE(bc.sort_order, bc.id), bc.id
                """,
                (int(bom_id),),
            ).fetchall()

            baseline_by_usage = {
                int(item["usage_id"]): dict(item)
                for item in baseline_rows
                if item["usage_id"] is not None
            }
            working_by_usage = {
                int(item["usage_id"]): dict(item)
                for item in working_rows
                if item["usage_id"] is not None
            }

            def item_label(item):
                aes = str(item.get("aes_number") or "").strip()
                name = str(item.get("name") or item.get("child_id") or "Item").strip()
                return f"{aes} {name}".strip()

            structure_changes = []
            for usage_id in sorted(set(working_by_usage) - set(baseline_by_usage)):
                item = working_by_usage[usage_id]
                structure_changes.append({
                    "kind": "added",
                    "usage_id": usage_id,
                    "child_id": int(item["child_id"]),
                    "text": f"Added: {item_label(item)}",
                })
            for usage_id in sorted(set(baseline_by_usage) - set(working_by_usage)):
                item = baseline_by_usage[usage_id]
                structure_changes.append({
                    "kind": "removed",
                    "usage_id": usage_id,
                    "child_id": int(item["child_id"]),
                    "text": f"Removed: {item_label(item)}",
                })
            for usage_id in sorted(set(baseline_by_usage) & set(working_by_usage)):
                before = baseline_by_usage[usage_id]
                after = working_by_usage[usage_id]
                label = item_label(after)
                if int(before["child_id"]) != int(after["child_id"]):
                    structure_changes.append({
                        "kind": "replaced",
                        "usage_id": usage_id,
                        "child_id": int(after["child_id"]),
                        "text": f"Replaced: {item_label(before)} -> {label}",
                    })
                    continue
                if int(before["quantity"] or 1) != int(after["quantity"] or 1):
                    structure_changes.append({
                        "kind": "quantity",
                        "usage_id": usage_id,
                        "child_id": int(after["child_id"]),
                        "text": (
                            f"Quantity changed: {label}, "
                            f"{int(before['quantity'] or 1)} -> {int(after['quantity'] or 1)}"
                        ),
                    })
                if int(before["child_iteration_id"] or 0) != int(after["child_iteration_id"] or 0):
                    structure_changes.append({
                        "kind": "binding",
                        "usage_id": usage_id,
                        "child_id": int(after["child_id"]),
                        "text": (
                            f"Child version changed: {label}, "
                            f"{before.get('version_label') or '?'} -> {after.get('version_label') or '?'}"
                        ),
                    })
                if int(before["sort_order"] or 0) != int(after["sort_order"] or 0):
                    structure_changes.append({
                        "kind": "reordered",
                        "usage_id": usage_id,
                        "child_id": int(after["child_id"]),
                        "text": f"Reordered: {label}",
                    })

            return {
                "context": dict(current),
                "baseline_object": baseline_object,
                "working_object": working_object,
                "metadata_changes": metadata_changes,
                "structure_changes": structure_changes,
            }

    def get_current_contexts_for_project(self, project_id: int) -> dict[int, dict]:
        with self.get_conn() as conn:
            rows = conn.execute("SELECT id FROM bom WHERE project_id=?", (int(project_id),)).fetchall()
        return self.get_current_contexts(int(row["id"]) for row in rows)

    def get_current_contexts(self, bom_ids) -> dict[int, dict]:
        ids = sorted({int(value) for value in (bom_ids or []) if value is not None})
        if not ids:
            return {}
        with self.get_conn() as conn:
            result = {}
            for offset in range(0, len(ids), 800):
                chunk = ids[offset:offset + 800]
                placeholders = ",".join("?" for _ in chunk)
                rows = conn.execute(
                    f"""
                    SELECT b.id AS bom_id, b.current_revision_id, b.current_iteration_id,
                           b.pending_revision_code,
                           r.revision_code, r.state, r.created_at AS revision_created_at,
                           r.created_by AS revision_created_by, r.released_at, r.released_by,
                           r.release_note, i.iteration_number, i.checkin_note,
                           i.source_commit_id, i.object_data_json,
                           i.created_at AS iteration_created_at,
                           i.created_by AS iteration_created_by
                    FROM bom b
                    LEFT JOIN bom_revisions r ON r.id=b.current_revision_id AND r.bom_id=b.id
                    LEFT JOIN bom_iterations i ON i.id=b.current_iteration_id AND i.revision_id=r.id
                    WHERE b.id IN ({placeholders})
                    """,
                    chunk,
                ).fetchall()
                for row in rows:
                    bom_id = int(row["bom_id"])
                    if row["revision_code"] is None or row["iteration_number"] is None:
                        continue
                    context = dict(row)
                    context["version_label"] = (
                        f"{context['revision_code']}.{int(context['iteration_number'])}"
                    )
                    result[bom_id] = context
            for bom_id in ids:
                if bom_id not in result:
                    result[bom_id] = self._ensure_bom_conn(conn, bom_id)
            return result

    def list_revisions(self, bom_id: int) -> list[dict]:
        with self.get_conn() as conn:
            self._ensure_bom_conn(conn, int(bom_id))
            rows = conn.execute(
                """
                SELECT r.*, COUNT(i.id) AS iteration_count,
                       MAX(i.iteration_number) AS latest_iteration_number
                FROM bom_revisions r
                LEFT JOIN bom_iterations i ON i.revision_id=r.id
                WHERE r.bom_id=?
                GROUP BY r.id ORDER BY r.id
                """,
                (int(bom_id),),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_iterations(self, bom_id: int, revision_id=None) -> list[dict]:
        with self.get_conn() as conn:
            self._ensure_bom_conn(conn, int(bom_id))
            sql = """
                SELECT i.*, r.bom_id, r.revision_code, r.state,
                       r.released_at, r.released_by,
                       r.revision_code || '.' || i.iteration_number AS version_label
                FROM bom_iterations i
                JOIN bom_revisions r ON r.id=i.revision_id
                WHERE r.bom_id=?
            """
            params = [int(bom_id)]
            if revision_id is not None:
                sql += " AND r.id=?"
                params.append(int(revision_id))
            sql += " ORDER BY r.id DESC, i.iteration_number DESC"
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def get_iteration_context(self, iteration_id: int) -> dict:
        with self.get_conn() as conn:
            row = conn.execute(
                """
                SELECT i.*, r.bom_id, r.revision_code, r.state,
                       r.revision_code || '.' || i.iteration_number AS version_label
                FROM bom_iterations i
                JOIN bom_revisions r ON r.id=i.revision_id
                WHERE i.id=?
                """,
                (int(iteration_id),),
            ).fetchone()
            return dict(row) if row else {}

    def get_iteration_object_contexts(self, iteration_ids) -> dict[int, dict]:
        """Load immutable object metadata for many selected iterations at once."""
        ids = sorted({int(value) for value in (iteration_ids or []) if value is not None})
        if not ids:
            return {}
        result = {}
        with self.get_conn() as conn:
            for offset in range(0, len(ids), 800):
                chunk = ids[offset:offset + 800]
                placeholders = ",".join("?" for _ in chunk)
                rows = conn.execute(
                    f"""
                    SELECT i.id AS iteration_id, i.revision_id, i.iteration_number,
                           i.object_data_json, i.created_at, i.created_by, i.checkin_note,
                           r.bom_id, r.revision_code, r.state, b.project_id,
                           b.type AS current_type, b.name AS current_name,
                           b.aes_number AS current_aes_number,
                           b.part_number AS current_part_number,
                           b.drawing_number AS current_drawing_number,
                           b.filename AS current_filename,
                           b.drawing AS current_drawing
                    FROM bom_iterations i
                    JOIN bom_revisions r ON r.id=i.revision_id
                    JOIN bom b ON b.id=r.bom_id
                    WHERE i.id IN ({placeholders})
                    """,
                    chunk,
                ).fetchall()
                for row in rows:
                    item = dict(row)
                    snapshot = self._snapshot_object_data(
                        item.pop("object_data_json", None)
                    )
                    fallback_fields = {
                        "type": item.pop("current_type", ""),
                        "name": item.pop("current_name", ""),
                        "aes_number": item.pop("current_aes_number", ""),
                        "part_number": item.pop("current_part_number", ""),
                        "drawing_number": item.pop("current_drawing_number", ""),
                        "filename": item.pop("current_filename", ""),
                        "drawing": item.pop("current_drawing", ""),
                    }
                    for field, fallback in fallback_fields.items():
                        item[field] = str(snapshot.get(field, fallback) or "").strip()
                    item["version_label"] = (
                        f"{item['revision_code']}.{int(item['iteration_number'])}"
                    )
                    result[int(item["iteration_id"])] = item
        return result

    def get_iteration_cad_files(self, iteration_id: int) -> dict:
        context = self.get_iteration_context(int(iteration_id))
        if not context:
            return {}
        try:
            snapshot = json.loads(str(context.get("object_data_json") or "{}"))
        except Exception:
            snapshot = {}
        return {
            "iteration_id": int(iteration_id),
            "bom_id": context.get("bom_id"),
            "version_label": context.get("version_label") or "",
            "state": context.get("state") or "",
            "filename": str(snapshot.get("filename") or "").strip(),
            "drawing": str(snapshot.get("drawing") or "").strip(),
            "base_file_name": str(snapshot.get("base_file_name") or "").strip(),
            "base_drw_name": str(snapshot.get("base_drw_name") or "").strip(),
            "created_at": context.get("created_at"),
        }

    @staticmethod
    def _snapshot_object_data(raw_snapshot) -> dict:
        try:
            snapshot = json.loads(str(raw_snapshot or "{}"))
        except Exception:
            snapshot = {}
        return snapshot if isinstance(snapshot, dict) else {}

    def _iteration_bindings_conn(self, conn, iteration_id: int) -> list[dict]:
        rows = conn.execute(
            """
            SELECT ib.id AS binding_id, ib.usage_id, ib.child_bom_id,
                   ib.child_revision_id, ib.child_iteration_id,
                   COALESCE(ib.quantity,1) AS quantity,
                   COALESCE(ib.sort_order,ib.id) AS sort_order,
                   child.name, child.aes_number, child.part_number, child.type,
                   child_rev.revision_code AS child_revision,
                   child_it.iteration_number AS child_iteration,
                   child_it.object_data_json AS child_object_data_json
            FROM bom_iteration_bindings ib
            LEFT JOIN bom child ON child.id=ib.child_bom_id
            JOIN bom_revisions child_rev ON child_rev.id=ib.child_revision_id
            JOIN bom_iterations child_it ON child_it.id=ib.child_iteration_id
            WHERE ib.parent_iteration_id=?
            ORDER BY COALESCE(ib.sort_order,ib.id), ib.id
            """,
            (int(iteration_id),),
        ).fetchall()
        fallback_occurrences = {}
        result = []
        for position, row in enumerate(rows, start=1):
            item = dict(row)
            usage_id = item.get("usage_id")
            child_id = int(item["child_bom_id"])
            if usage_id is not None:
                occurrence_key = f"usage:{int(usage_id)}"
            else:
                occurrence_number = fallback_occurrences.get(child_id, 0) + 1
                fallback_occurrences[child_id] = occurrence_number
                occurrence_key = f"legacy:{child_id}:{occurrence_number}"
            snapshot = self._snapshot_object_data(item.pop("child_object_data_json", None))
            for field in ("name", "aes_number", "part_number", "type"):
                if field in snapshot:
                    item[field] = str(snapshot.get(field) or "").strip()
            filename = str(snapshot.get("filename") or "").strip()
            drawing = str(snapshot.get("drawing") or "").strip()
            item.update({
                "occurrence_key": occurrence_key,
                "position": position,
                "child_version": (
                    f"{item['child_revision']}.{int(item['child_iteration'])}"
                ),
                "filename": filename,
                "drawing": drawing,
            })
            result.append(item)
        return result

    def compare_assembly_iterations(
        self, bom_id: int, left_iteration_id: int, right_iteration_id: int
    ) -> dict:
        with self.get_conn() as conn:
            self._ensure_bom_conn(conn, int(bom_id))
            assembly = conn.execute(
                "SELECT id, name, aes_number, part_number, type FROM bom WHERE id=?",
                (int(bom_id),),
            ).fetchone()
            if not assembly:
                raise ValueError("Assembly was not found.")
            if str(assembly["type"] or "").strip().lower() not in {"asm", "assembly"}:
                raise ValueError("Assembly iteration comparison is available only for assemblies.")

            def iteration_context(iteration_id: int) -> dict:
                row = conn.execute(
                    """
                    SELECT i.id, i.revision_id, i.iteration_number, i.created_at,
                           i.created_by, i.checkin_note, i.source_commit_id,
                           r.bom_id, r.revision_code, r.state,
                           r.revision_code || '.' || i.iteration_number AS version_label
                    FROM bom_iterations i
                    JOIN bom_revisions r ON r.id=i.revision_id
                    WHERE i.id=?
                    """,
                    (int(iteration_id),),
                ).fetchone()
                if not row or int(row["bom_id"]) != int(bom_id):
                    raise ValueError("Both selected iterations must belong to this assembly.")
                return dict(row)

            left_context = iteration_context(int(left_iteration_id))
            right_context = iteration_context(int(right_iteration_id))
            left_rows = self._iteration_bindings_conn(conn, int(left_iteration_id))
            right_rows = self._iteration_bindings_conn(conn, int(right_iteration_id))

        left_by_key = {row["occurrence_key"]: row for row in left_rows}
        right_by_key = {row["occurrence_key"]: row for row in right_rows}
        ordered_keys = [row["occurrence_key"] for row in left_rows]
        ordered_keys.extend(
            row["occurrence_key"] for row in right_rows
            if row["occurrence_key"] not in left_by_key
        )

        compared = []
        summary = {
            "total": 0,
            "changed": 0,
            "unchanged": 0,
            "added": 0,
            "removed": 0,
            "version_changed": 0,
            "quantity_changed": 0,
            "order_changed": 0,
            "component_changed": 0,
        }
        for key in ordered_keys:
            left = left_by_key.get(key)
            right = right_by_key.get(key)
            change_types = []
            if left is None:
                change_types.append("added")
                change_label = "Added"
            elif right is None:
                change_types.append("removed")
                change_label = "Removed"
            else:
                labels = []
                component_changed = int(left["child_bom_id"]) != int(right["child_bom_id"])
                if component_changed:
                    change_types.append("component_changed")
                    labels.append("Component")
                if (
                    not component_changed
                    and int(left["child_iteration_id"]) != int(right["child_iteration_id"])
                ):
                    change_types.append("version_changed")
                    labels.append("Child version")
                if int(left["quantity"] or 1) != int(right["quantity"] or 1):
                    change_types.append("quantity_changed")
                    labels.append("Quantity")
                if int(left["position"] or 0) != int(right["position"] or 0):
                    change_types.append("order_changed")
                    labels.append("Order")
                change_label = ", ".join(labels) if labels else "Unchanged"

            summary["total"] += 1
            if change_types:
                summary["changed"] += 1
                for change_type in change_types:
                    summary[change_type] += 1
            else:
                summary["unchanged"] += 1
            display = right or left or {}
            compared.append({
                "occurrence_key": key,
                "usage_id": display.get("usage_id"),
                "change": change_label,
                "change_types": change_types,
                "name": display.get("name") or "",
                "aes_number": display.get("aes_number") or "",
                "part_number": display.get("part_number") or "",
                "type": display.get("type") or "",
                "left": left,
                "right": right,
            })

        return {
            "assembly": dict(assembly),
            "left": left_context,
            "right": right_context,
            "rows": compared,
            "summary": summary,
            "left_binding_count": len(left_rows),
            "right_binding_count": len(right_rows),
        }

    def get_iteration_structure_snapshot(self, bom_id: int, iteration_id: int) -> dict:
        """Return one exact recursive assembly configuration from immutable bindings."""
        with self.get_conn() as conn:
            self._ensure_bom_conn(conn, int(bom_id))
            context_cache = {}
            binding_cache = {}

            def iteration_context(selected_iteration_id: int) -> dict:
                selected_iteration_id = int(selected_iteration_id)
                if selected_iteration_id in context_cache:
                    return dict(context_cache[selected_iteration_id])
                row = conn.execute(
                    """
                    SELECT i.id AS iteration_id, i.revision_id, i.iteration_number,
                           i.object_data_json, i.checkin_note, i.created_at,
                           r.bom_id, r.revision_code, r.state,
                           b.project_id, b.type AS current_type, b.name AS current_name,
                           b.aes_number AS current_aes_number,
                           b.part_number AS current_part_number,
                           b.drawing_number AS current_drawing_number
                    FROM bom_iterations i
                    JOIN bom_revisions r ON r.id=i.revision_id
                    LEFT JOIN bom b ON b.id=r.bom_id
                    WHERE i.id=?
                    """,
                    (selected_iteration_id,),
                ).fetchone()
                if not row:
                    raise ValueError(f"Iteration {selected_iteration_id} was not found.")
                result = dict(row)
                snapshot = self._snapshot_object_data(result.pop("object_data_json", None))
                if not snapshot:
                    raise ValueError(
                        f"Iteration {selected_iteration_id} has no immutable object snapshot."
                    )
                fallback_fields = {
                    "type": result.pop("current_type", ""),
                    "name": result.pop("current_name", ""),
                    "aes_number": result.pop("current_aes_number", ""),
                    "part_number": result.pop("current_part_number", ""),
                    "drawing_number": result.pop("current_drawing_number", ""),
                }
                for field, fallback in fallback_fields.items():
                    result[field] = str(snapshot.get(field, fallback) or "").strip()
                for field in (
                    "filename", "drawing", "base_file_name", "base_drw_name",
                    "material", "weight", "notes", "pdf_path", "step_path",
                ):
                    result[field] = str(snapshot.get(field) or "").strip()
                result["version_label"] = (
                    f"{result['revision_code']}.{int(result['iteration_number'])}"
                )
                context_cache[selected_iteration_id] = dict(result)
                return result

            root = iteration_context(int(iteration_id))
            if int(root["bom_id"]) != int(bom_id):
                raise ValueError("The selected iteration does not belong to this assembly.")
            if str(root.get("type") or "").lower() not in {"asm", "assembly"}:
                raise ValueError("A frozen configuration must start from an assembly iteration.")
            project_id = root.get("project_id")
            if project_id is None:
                raise ValueError("The selected assembly is not assigned to a project.")

            members = []

            def child_bindings(parent_iteration_id: int) -> list[dict]:
                parent_iteration_id = int(parent_iteration_id)
                if parent_iteration_id not in binding_cache:
                    binding_cache[parent_iteration_id] = self._iteration_bindings_conn(
                        conn, parent_iteration_id
                    )
                return [dict(row) for row in binding_cache[parent_iteration_id]]

            def walk(
                context: dict,
                occurrence_path: str,
                parent_occurrence_path,
                usage_id,
                quantity: int,
                position: int,
                sort_order: int,
                ancestors: tuple[int, ...],
            ) -> None:
                selected_iteration_id = int(context["iteration_id"])
                if selected_iteration_id in ancestors:
                    raise ValueError(
                        f"Circular immutable structure detected at {context.get('name') or occurrence_path}."
                    )
                if int(context.get("project_id") or 0) != int(project_id):
                    raise ValueError("A configuration cannot contain BOM objects from another project.")
                members.append({
                    "occurrence_path": occurrence_path,
                    "parent_occurrence_path": parent_occurrence_path,
                    "usage_id": int(usage_id) if usage_id is not None else None,
                    "bom_id": int(context["bom_id"]),
                    "revision_id": int(context["revision_id"]),
                    "iteration_id": selected_iteration_id,
                    "revision_code": str(context.get("revision_code") or ""),
                    "iteration_number": int(context.get("iteration_number") or 0),
                    "version_label": str(context.get("version_label") or ""),
                    "quantity": max(1, int(quantity or 1)),
                    "position": int(position or 0),
                    "sort_order": int(sort_order or 0),
                    "type": str(context.get("type") or ""),
                    "name": str(context.get("name") or ""),
                    "aes_number": str(context.get("aes_number") or ""),
                    "part_number": str(context.get("part_number") or ""),
                    "drawing_number": str(context.get("drawing_number") or ""),
                    "filename": str(context.get("filename") or ""),
                    "drawing": str(context.get("drawing") or ""),
                    "is_root": occurrence_path == "root",
                })
                if str(context.get("type") or "").lower() not in {"asm", "assembly"}:
                    return
                next_ancestors = (*ancestors, selected_iteration_id)
                if len(next_ancestors) > 100:
                    raise ValueError("The assembly structure exceeds the supported depth of 100 levels.")
                bindings = child_bindings(selected_iteration_id)
                if not bindings:
                    current_child_count = int(conn.execute(
                        "SELECT COUNT(*) FROM bom_children WHERE parent_id=?",
                        (int(context["bom_id"]),),
                    ).fetchone()[0])
                    if current_child_count:
                        raise ValueError(
                            f"{context.get('name') or context.get('bom_id')} "
                            f"{context.get('version_label') or ''} has no captured child bindings "
                            "and cannot be frozen exactly."
                        )
                for binding in bindings:
                    child = iteration_context(int(binding["child_iteration_id"]))
                    if int(child["bom_id"]) != int(binding["child_bom_id"]):
                        raise ValueError("An immutable child binding points to the wrong BOM object.")
                    segment = str(binding.get("occurrence_key") or "occurrence").replace(":", "_")
                    walk(
                        child,
                        f"{occurrence_path}/{segment}",
                        occurrence_path,
                        binding.get("usage_id"),
                        int(binding.get("quantity") or 1),
                        int(binding.get("position") or 0),
                        int(binding.get("sort_order") or 0),
                        next_ancestors,
                    )

            walk(root, "root", None, None, 1, 0, 0, ())
            return {
                "project_id": int(project_id),
                "root_bom_id": int(bom_id),
                "root_iteration_id": int(iteration_id),
                "root_version": str(root.get("version_label") or ""),
                "root_name": str(root.get("name") or ""),
                "root_aes_number": str(root.get("aes_number") or ""),
                "members": members,
            }

    def validate_released_checkout(self, bom_id: int, revision_code: str) -> dict:
        code = self.normalize_revision_code(revision_code)
        with self.get_conn() as conn:
            current = self._ensure_bom_conn(conn, int(bom_id))
            if str(current.get("state") or "").strip().lower() != "released":
                raise ValueError(f"{current['version_label']} is not Released.")
            exists = conn.execute(
                "SELECT 1 FROM bom_revisions WHERE bom_id=? AND revision_code=? COLLATE NOCASE",
                (int(bom_id), code),
            ).fetchone()
            if exists:
                raise ValueError(f"Revision {code} already exists for this item.")
            current["pending_revision_code"] = code
            return current

    def prepare_released_checkout(self, bom_id: int, revision_code: str) -> dict:
        code = self.normalize_revision_code(revision_code)
        with self.get_conn() as conn:
            current = self._ensure_bom_conn(conn, int(bom_id))
            if str(current.get("state") or "").strip().lower() != "released":
                raise ValueError(f"{current['version_label']} is not Released.")
            exists = conn.execute(
                "SELECT 1 FROM bom_revisions WHERE bom_id=? AND revision_code=? COLLATE NOCASE",
                (int(bom_id), code),
            ).fetchone()
            if exists:
                raise ValueError(f"Revision {code} already exists for this item.")
            conn.execute(
                "UPDATE bom SET pending_revision_code=?, modified=datetime('now') WHERE id=?",
                (code, int(bom_id)),
            )
            current["pending_revision_code"] = code
            return current

    def assert_mutable(self, bom_id: int) -> dict:
        context = self.get_current_context(int(bom_id))
        if str(context.get("state") or "").strip().lower() == "released":
            raise ValueError(
                f"{context['version_label']} is released and immutable. Create a new revision before modifying it."
            )
        if str(context.get("state") or "").strip().lower() == "obsolete":
            raise ValueError("Obsolete revisions cannot be modified.")
        return context

    def assert_checkout_mutable(self, bom_id: int) -> dict:
        context = self.get_current_context(int(bom_id))
        state = str(context.get("state") or "").strip().lower()
        if state == "obsolete":
            raise ValueError("Obsolete revisions cannot be modified.")
        if state == "released" and not str(context.get("pending_revision_code") or "").strip():
            raise ValueError(
                f"{context['version_label']} is Released. Start a new-revision checkout before modifying it."
            )
        return context

    @staticmethod
    def _valid_child_binding_conn(conn, child_bom_id: int, revision_id, iteration_id):
        if revision_id is None or iteration_id is None:
            return None
        row = conn.execute(
            """
            SELECT r.id AS revision_id, i.id AS iteration_id
            FROM bom_revisions r
            JOIN bom_iterations i ON i.revision_id=r.id
            WHERE r.bom_id=? AND r.id=? AND i.id=?
            """,
            (int(child_bom_id), int(revision_id), int(iteration_id)),
        ).fetchone()
        return (int(row["revision_id"]), int(row["iteration_id"])) if row else None

    def _resolve_binding_conn(self, conn, parent_bom_id: int, usage_id: int, child_bom_id: int, preferred=None):
        row = conn.execute(
            """
            SELECT child_revision_id, child_iteration_id
            FROM bom_working_bindings WHERE parent_bom_id=? AND usage_id=?
            """,
            (int(parent_bom_id), int(usage_id)),
        ).fetchone()
        if row:
            valid = self._valid_child_binding_conn(
                conn, child_bom_id, row["child_revision_id"], row["child_iteration_id"]
            )
            if valid:
                return valid

        if preferred:
            valid = self._valid_child_binding_conn(conn, child_bom_id, preferred[0], preferred[1])
            if valid:
                return valid

        parent = self._current_context_conn(conn, int(parent_bom_id))
        row = conn.execute(
            """
            SELECT child_revision_id, child_iteration_id
            FROM bom_iteration_bindings
            WHERE parent_iteration_id=? AND usage_id=?
            """,
            (int(parent["current_iteration_id"]), int(usage_id)),
        ).fetchone()
        if not row:
            row = conn.execute(
                """
                SELECT child_revision_id, child_iteration_id
                FROM bom_iteration_bindings
                WHERE parent_iteration_id=? AND child_bom_id=?
                ORDER BY id LIMIT 1
                """,
                (int(parent["current_iteration_id"]), int(child_bom_id)),
            ).fetchone()
        if row:
            valid = self._valid_child_binding_conn(
                conn, child_bom_id, row["child_revision_id"], row["child_iteration_id"]
            )
            if valid:
                return valid

        child = self._ensure_bom_conn(conn, int(child_bom_id))
        return int(child["current_revision_id"]), int(child["current_iteration_id"])

    def initialize_checkout(self, bom_id: int, user_id: int) -> None:
        with self.get_conn() as conn:
            self._ensure_bom_conn(conn, int(bom_id), created_by=user_id)
            current = self._current_context_conn(conn, int(bom_id))
            state = str(current["state"]).lower()
            if state == "obsolete":
                raise ValueError(
                    f"{current['version_label']} is {current['state']} and cannot be checked out."
                )
            if state == "released" and not str(current.get("pending_revision_code") or "").strip():
                raise ValueError(
                    f"{current['version_label']} is Released. A target revision is required for checkout."
                )
            conn.execute("DELETE FROM bom_working_bindings WHERE parent_bom_id=?", (int(bom_id),))
            rows = conn.execute(
                "SELECT id, child_id FROM bom_children WHERE parent_id=? ORDER BY COALESCE(sort_order,id), id",
                (int(bom_id),),
            ).fetchall()
            for row in rows:
                revision_id, iteration_id = self._resolve_binding_conn(
                    conn, int(bom_id), int(row["id"]), int(row["child_id"])
                )
                conn.execute(
                    """
                    INSERT INTO bom_working_bindings(
                        parent_bom_id, usage_id, child_bom_id, child_revision_id,
                        child_iteration_id, updated_by
                    ) VALUES(?,?,?,?,?,?)
                    """,
                    (
                        int(bom_id), int(row["id"]), int(row["child_id"]),
                        revision_id, iteration_id, int(user_id),
                    ),
                )

    def discard_checkout(self, bom_id: int) -> None:
        with self.get_conn() as conn:
            conn.execute("DELETE FROM bom_working_bindings WHERE parent_bom_id=?", (int(bom_id),))
            conn.execute(
                "UPDATE bom SET pending_revision_code=NULL WHERE id=?", (int(bom_id),)
            )

    def restore_checked_in_state(self, bom_id: int) -> dict:
        """Restore object attributes and direct structure from the current immutable iteration."""
        with self.get_conn() as conn:
            current = self._ensure_bom_conn(conn, int(bom_id))
            raw_snapshot = current.get("object_data_json")
            try:
                snapshot = json.loads(str(raw_snapshot or "{}"))
            except Exception:
                snapshot = {}
            assignments = []
            values = []
            for field in self._SNAPSHOT_FIELDS:
                if field in snapshot:
                    assignments.append(f"{field}=?")
                    values.append(snapshot.get(field))
            assignments.append("pending_revision_code=NULL")
            conn.execute(
                f"UPDATE bom SET {', '.join(assignments)}, modified=datetime('now') WHERE id=?",
                [*values, int(bom_id)],
            )

            bindings = conn.execute(
                """
                SELECT usage_id, child_bom_id, quantity, sort_order
                FROM bom_iteration_bindings
                WHERE parent_iteration_id=?
                ORDER BY sort_order, id
                """,
                (int(current["current_iteration_id"]),),
            ).fetchall()
            conn.execute("DELETE FROM bom_children WHERE parent_id=?", (int(bom_id),))
            for binding in bindings:
                usage_id = binding["usage_id"]
                inserted = False
                if usage_id is not None:
                    try:
                        conn.execute(
                            """
                            INSERT INTO bom_children(id, parent_id, child_id, quantity, sort_order)
                            VALUES(?,?,?,?,?)
                            """,
                            (
                                int(usage_id), int(bom_id), int(binding["child_bom_id"]),
                                max(1, int(binding["quantity"] or 1)), int(binding["sort_order"] or 0),
                            ),
                        )
                        inserted = True
                    except sqlite3.IntegrityError:
                        inserted = False
                if not inserted:
                    conn.execute(
                        """
                        INSERT INTO bom_children(parent_id, child_id, quantity, sort_order)
                        VALUES(?,?,?,?)
                        """,
                        (
                            int(bom_id), int(binding["child_bom_id"]),
                            max(1, int(binding["quantity"] or 1)), int(binding["sort_order"] or 0),
                        ),
                    )
            conn.execute("DELETE FROM bom_working_bindings WHERE parent_bom_id=?", (int(bom_id),))
            return self._current_context_conn(conn, int(bom_id))

    def get_effective_child_binding(self, parent_bom_id: int, child_bom_id: int):
        with self.get_conn() as conn:
            relation = conn.execute(
                "SELECT id FROM bom_children WHERE parent_id=? AND child_id=?",
                (int(parent_bom_id), int(child_bom_id)),
            ).fetchone()
            if not relation:
                return None
            return self._resolve_binding_conn(
                conn, int(parent_bom_id), int(relation["id"]), int(child_bom_id)
            )

    def sync_working_bindings(self, parent_bom_id: int, user_id: int, preferred_by_child=None) -> None:
        preferred_by_child = preferred_by_child or {}
        with self.get_conn() as conn:
            self._ensure_bom_conn(conn, int(parent_bom_id), created_by=user_id)
            current_usage_ids = []
            rows = conn.execute(
                "SELECT id, child_id FROM bom_children WHERE parent_id=? ORDER BY COALESCE(sort_order,id), id",
                (int(parent_bom_id),),
            ).fetchall()
            for row in rows:
                usage_id = int(row["id"])
                child_bom_id = int(row["child_id"])
                current_usage_ids.append(usage_id)
                revision_id, iteration_id = self._resolve_binding_conn(
                    conn,
                    int(parent_bom_id),
                    usage_id,
                    child_bom_id,
                    preferred=preferred_by_child.get(child_bom_id),
                )
                conn.execute(
                    """
                    INSERT INTO bom_working_bindings(
                        parent_bom_id, usage_id, child_bom_id, child_revision_id,
                        child_iteration_id, updated_by
                    ) VALUES(?,?,?,?,?,?)
                    ON CONFLICT(parent_bom_id, usage_id) DO UPDATE SET
                        child_bom_id=excluded.child_bom_id,
                        child_revision_id=excluded.child_revision_id,
                        child_iteration_id=excluded.child_iteration_id,
                        updated_at=datetime('now'), updated_by=excluded.updated_by
                    """,
                    (
                        int(parent_bom_id), usage_id, child_bom_id,
                        revision_id, iteration_id, int(user_id),
                    ),
                )
            if current_usage_ids:
                placeholders = ",".join("?" for _ in current_usage_ids)
                conn.execute(
                    f"DELETE FROM bom_working_bindings WHERE parent_bom_id=? AND usage_id NOT IN ({placeholders})",
                    [int(parent_bom_id), *current_usage_ids],
                )
            else:
                conn.execute("DELETE FROM bom_working_bindings WHERE parent_bom_id=?", (int(parent_bom_id),))

    def update_children_to_latest(self, parent_bom_id: int, child_bom_ids, user_id: int) -> list[int]:
        wanted = sorted({int(value) for value in (child_bom_ids or [])})
        if not wanted:
            raise ValueError("Select at least one direct child to update.")
        with self.get_conn() as conn:
            self._ensure_bom_conn(conn, int(parent_bom_id), created_by=user_id)
            changed = []
            for child_bom_id in wanted:
                child = self._ensure_bom_conn(conn, child_bom_id, created_by=user_id)
                relations = conn.execute(
                    "SELECT id FROM bom_children WHERE parent_id=? AND child_id=?",
                    (int(parent_bom_id), child_bom_id),
                ).fetchall()
                if not relations:
                    raise ValueError(f"Item {child_bom_id} is not a direct child of this assembly.")
                for relation in relations:
                    conn.execute(
                        """
                        INSERT INTO bom_working_bindings(
                            parent_bom_id, usage_id, child_bom_id, child_revision_id,
                            child_iteration_id, updated_by
                        ) VALUES(?,?,?,?,?,?)
                        ON CONFLICT(parent_bom_id, usage_id) DO UPDATE SET
                            child_revision_id=excluded.child_revision_id,
                            child_iteration_id=excluded.child_iteration_id,
                            updated_at=datetime('now'), updated_by=excluded.updated_by
                        """,
                        (
                            int(parent_bom_id), int(relation["id"]), child_bom_id,
                            int(child["current_revision_id"]), int(child["current_iteration_id"]),
                            int(user_id),
                        ),
                    )
                changed.append(child_bom_id)
            return changed

    def _capture_iteration_bindings_conn(self, conn, bom_id: int, iteration_id: int) -> None:
        relations = conn.execute(
            """
            SELECT id, child_id, COALESCE(quantity,1) AS quantity,
                   COALESCE(sort_order,id) AS sort_order
            FROM bom_children WHERE parent_id=?
            ORDER BY COALESCE(sort_order,id), id
            """,
            (int(bom_id),),
        ).fetchall()
        for relation in relations:
            revision_id, child_iteration_id = self._resolve_binding_conn(
                conn, int(bom_id), int(relation["id"]), int(relation["child_id"])
            )
            conn.execute(
                """
                INSERT INTO bom_iteration_bindings(
                    parent_iteration_id, usage_id, child_bom_id, child_revision_id,
                    child_iteration_id, quantity, sort_order
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    int(iteration_id), int(relation["id"]), int(relation["child_id"]),
                    revision_id, child_iteration_id,
                    max(1, int(relation["quantity"] or 1)), int(relation["sort_order"] or 0),
                ),
            )

    def record_checkin(self, bom_id: int, user_id: int, note: str = "", source_commit_id=None) -> dict:
        with self.get_conn() as conn:
            current = self._ensure_bom_conn(conn, int(bom_id), created_by=user_id)
            state = str(current["state"]).strip().lower()
            pending_code = str(current.get("pending_revision_code") or "").strip()
            if state == "obsolete":
                raise ValueError(f"{current['version_label']} is immutable and cannot be checked in.")
            if state == "released" and not pending_code:
                raise ValueError(
                    f"{current['version_label']} is Released and has no new-revision checkout."
                )

            revision_id = int(current["current_revision_id"])
            next_number = 1
            if state == "released":
                pending_code = self.normalize_revision_code(pending_code)
                exists = conn.execute(
                    "SELECT 1 FROM bom_revisions WHERE bom_id=? AND revision_code=? COLLATE NOCASE",
                    (int(bom_id), pending_code),
                ).fetchone()
                if exists:
                    raise ValueError(f"Revision {pending_code} already exists for this item.")
                revision_id = int(conn.execute(
                    """
                    INSERT INTO bom_revisions(bom_id, revision_code, state, created_by)
                    VALUES(?,?, 'In Work', ?)
                    """,
                    (int(bom_id), pending_code, int(user_id)),
                ).lastrowid)
            else:
                next_number = int(conn.execute(
                    "SELECT COALESCE(MAX(iteration_number),0)+1 FROM bom_iterations WHERE revision_id=?",
                    (revision_id,),
                ).fetchone()[0])

            iteration_id = self._insert_iteration_conn(
                conn,
                revision_id,
                next_number,
                checkin_note=str(note or "").strip() or (
                    f"Created from Released {current['version_label']}" if state == "released" else None
                ),
                source_commit_id=str(source_commit_id or "").strip() or None,
                object_data_json=self._object_snapshot_conn(conn, int(bom_id)),
                created_by=int(user_id),
            )
            self._capture_iteration_bindings_conn(conn, int(bom_id), iteration_id)
            if state == "released":
                conn.execute(
                    """
                    UPDATE bom
                    SET revision=?, lifecycle_state='WIP', status='Design',
                        released_at=NULL, released_by=NULL,
                        current_revision_id=?, current_iteration_id=?,
                        pending_revision_code=NULL, modified=datetime('now')
                    WHERE id=?
                    """,
                    (pending_code, revision_id, iteration_id, int(bom_id)),
                )
            else:
                conn.execute(
                    """
                    UPDATE bom SET current_iteration_id=?, pending_revision_code=NULL,
                                   modified=datetime('now')
                    WHERE id=?
                    """,
                    (iteration_id, int(bom_id)),
                )
            conn.execute("DELETE FROM bom_working_bindings WHERE parent_bom_id=?", (int(bom_id),))
            return self._current_context_conn(conn, int(bom_id))

    def create_revision(self, bom_id: int, revision_code: str, user_id: int, note: str = "") -> dict:
        code = self.normalize_revision_code(revision_code)
        with self.get_conn() as conn:
            current = self._ensure_bom_conn(conn, int(bom_id), created_by=user_id)
            if str(current["state"]).strip().lower() != "released":
                raise ValueError(
                    f"Current version {current['version_label']} must be released before creating a new revision."
                )
            exists = conn.execute(
                "SELECT 1 FROM bom_revisions WHERE bom_id=? AND revision_code=? COLLATE NOCASE",
                (int(bom_id), code),
            ).fetchone()
            if exists:
                raise ValueError(f"Revision {code} already exists for this item.")
            revision_id = int(conn.execute(
                """
                INSERT INTO bom_revisions(bom_id, revision_code, state, created_by)
                VALUES(?,?, 'In Work', ?)
                """,
                (int(bom_id), code, int(user_id)),
            ).lastrowid)
            iteration_id = self._insert_iteration_conn(
                conn,
                revision_id,
                1,
                checkin_note=str(note or "").strip() or "Created from released revision",
                object_data_json=self._object_snapshot_conn(conn, int(bom_id)),
                created_by=int(user_id),
            )
            source_bindings = conn.execute(
                """
                SELECT usage_id, child_bom_id, child_revision_id, child_iteration_id,
                       quantity, sort_order
                FROM bom_iteration_bindings
                WHERE parent_iteration_id=? ORDER BY sort_order, id
                """,
                (int(current["current_iteration_id"]),),
            ).fetchall()
            for binding in source_bindings:
                conn.execute(
                    """
                    INSERT INTO bom_iteration_bindings(
                        parent_iteration_id, usage_id, child_bom_id, child_revision_id,
                        child_iteration_id, quantity, sort_order
                    ) VALUES(?,?,?,?,?,?,?)
                    """,
                    (
                        iteration_id, binding["usage_id"], int(binding["child_bom_id"]),
                        int(binding["child_revision_id"]), int(binding["child_iteration_id"]),
                        int(binding["quantity"] or 1), int(binding["sort_order"] or 0),
                    ),
                )
            conn.execute(
                """
                UPDATE bom
                SET revision=?, lifecycle_state='WIP', status='Design',
                    released_at=NULL, released_by=NULL,
                    current_revision_id=?, current_iteration_id=?,
                    pending_revision_code=NULL, modified=datetime('now')
                WHERE id=?
                """,
                (code, revision_id, iteration_id, int(bom_id)),
            )
            return self._current_context_conn(conn, int(bom_id))

    def release_current_revision(self, bom_id: int, user_id: int, note: str = "") -> dict:
        with self.get_conn() as conn:
            current = self._ensure_bom_conn(conn, int(bom_id), created_by=user_id)
            if str(current["state"]).strip().lower() == "released":
                raise ValueError(f"Revision {current['revision_code']} is already released.")
            conn.execute(
                """
                UPDATE bom_revisions
                SET state='Released', released_at=datetime('now'), released_by=?, release_note=?
                WHERE id=?
                """,
                (int(user_id), str(note or "").strip() or None, int(current["current_revision_id"])),
            )
            conn.execute(
                """
                UPDATE bom
                SET revision=?, lifecycle_state='Released', status='Released',
                    released_at=datetime('now'), released_by=?,
                    pending_revision_code=NULL, modified=datetime('now')
                WHERE id=?
                """,
                (str(current["revision_code"]), int(user_id), int(bom_id)),
            )
            conn.execute("DELETE FROM bom_working_bindings WHERE parent_bom_id=?", (int(bom_id),))
            return self._current_context_conn(conn, int(bom_id))

    def list_child_version_status(self, parent_bom_id: int) -> list[dict]:
        with self.get_conn() as conn:
            self._ensure_bom_conn(conn, int(parent_bom_id))
            for child in conn.execute(
                "SELECT child_id FROM bom_children WHERE parent_id=?",
                (int(parent_bom_id),),
            ).fetchall():
                self._ensure_bom_conn(conn, int(child["child_id"]))
            rows = conn.execute(
                """
                SELECT bc.id AS usage_id, bc.parent_id, bc.child_id AS child_bom_id,
                       COALESCE(bc.quantity,1) AS quantity,
                       COALESCE(bc.sort_order,bc.id) AS sort_order,
                       child.name, child.aes_number, child.part_number, child.type,
                       COALESCE(wb.child_revision_id, ib.child_revision_id, child.current_revision_id) AS bound_revision_id,
                       COALESCE(wb.child_iteration_id, ib.child_iteration_id, child.current_iteration_id) AS bound_iteration_id,
                       bound_rev.revision_code AS bound_revision,
                       bound_it.iteration_number AS bound_iteration,
                       child.current_revision_id AS latest_revision_id,
                       child.current_iteration_id AS latest_iteration_id,
                       latest_rev.revision_code AS latest_revision,
                       latest_it.iteration_number AS latest_iteration,
                       CASE WHEN wb.usage_id IS NOT NULL THEN 'Working' ELSE 'Checked In' END AS binding_source
                FROM bom_children bc
                JOIN bom parent ON parent.id=bc.parent_id
                JOIN bom child ON child.id=bc.child_id
                LEFT JOIN bom_working_bindings wb
                    ON wb.parent_bom_id=bc.parent_id AND wb.usage_id=bc.id
                LEFT JOIN bom_iteration_bindings ib
                    ON ib.parent_iteration_id=parent.current_iteration_id AND ib.usage_id=bc.id
                LEFT JOIN bom_revisions bound_rev
                    ON bound_rev.id=COALESCE(wb.child_revision_id, ib.child_revision_id, child.current_revision_id)
                LEFT JOIN bom_iterations bound_it
                    ON bound_it.id=COALESCE(wb.child_iteration_id, ib.child_iteration_id, child.current_iteration_id)
                LEFT JOIN bom_revisions latest_rev ON latest_rev.id=child.current_revision_id
                LEFT JOIN bom_iterations latest_it ON latest_it.id=child.current_iteration_id
                WHERE bc.parent_id=?
                ORDER BY COALESCE(bc.sort_order,bc.id), bc.id
                """,
                (int(parent_bom_id),),
            ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["bound_version"] = (
                    f"{item['bound_revision']}.{int(item['bound_iteration'])}"
                    if item.get("bound_revision") is not None and item.get("bound_iteration") is not None else ""
                )
                item["latest_version"] = (
                    f"{item['latest_revision']}.{int(item['latest_iteration'])}"
                    if item.get("latest_revision") is not None and item.get("latest_iteration") is not None else ""
                )
                item["is_latest"] = (
                    item.get("bound_iteration_id") is not None
                    and int(item["bound_iteration_id"]) == int(item.get("latest_iteration_id") or 0)
                )
                result.append(item)
            return result

    def get_project_binding_status(self, project_id: int) -> dict[int, dict]:
        with self.get_conn() as conn:
            rows = conn.execute(
                """
                SELECT bc.id AS usage_id, bc.parent_id, bc.child_id AS child_bom_id,
                       COALESCE(bc.quantity,1) AS quantity,
                       COALESCE(bc.sort_order,bc.id) AS sort_order,
                       COALESCE(wb.child_iteration_id, ib.child_iteration_id, child.current_iteration_id) AS bound_iteration_id,
                       bound_rev.revision_code AS bound_revision,
                       bound_it.iteration_number AS bound_iteration,
                       child.current_iteration_id AS latest_iteration_id,
                       latest_rev.revision_code AS latest_revision,
                       latest_it.iteration_number AS latest_iteration,
                       CASE WHEN wb.usage_id IS NOT NULL THEN 'Working' ELSE 'Checked In' END AS binding_source
                FROM bom_children bc
                JOIN bom parent ON parent.id=bc.parent_id
                JOIN bom child ON child.id=bc.child_id
                LEFT JOIN bom_working_bindings wb
                    ON wb.parent_bom_id=bc.parent_id AND wb.usage_id=bc.id
                LEFT JOIN bom_iteration_bindings ib
                    ON ib.parent_iteration_id=parent.current_iteration_id AND ib.usage_id=bc.id
                LEFT JOIN bom_revisions bound_rev
                    ON bound_rev.id=COALESCE(wb.child_revision_id, ib.child_revision_id, child.current_revision_id)
                LEFT JOIN bom_iterations bound_it
                    ON bound_it.id=COALESCE(wb.child_iteration_id, ib.child_iteration_id, child.current_iteration_id)
                LEFT JOIN bom_revisions latest_rev ON latest_rev.id=child.current_revision_id
                LEFT JOIN bom_iterations latest_it ON latest_it.id=child.current_iteration_id
                WHERE parent.project_id=? AND child.project_id=?
                ORDER BY bc.parent_id, COALESCE(bc.sort_order,bc.id), bc.id
                """,
                (int(project_id), int(project_id)),
            ).fetchall()
        result = {}
        for row in rows:
            item = dict(row)
            item["bound_version"] = (
                f"{item['bound_revision']}.{int(item['bound_iteration'])}"
                if item.get("bound_revision") is not None and item.get("bound_iteration") is not None else ""
            )
            item["latest_version"] = (
                f"{item['latest_revision']}.{int(item['latest_iteration'])}"
                if item.get("latest_revision") is not None and item.get("latest_iteration") is not None else ""
            )
            item["is_latest"] = (
                item.get("bound_iteration_id") is not None
                and int(item["bound_iteration_id"]) == int(item.get("latest_iteration_id") or 0)
            )
            result[int(item["usage_id"])] = item
        return result

    def get_parent_binding_update_counts(self, project_id: int) -> dict[int, int]:
        counts = {}
        for item in self.get_project_binding_status(int(project_id)).values():
            if item.get("is_latest"):
                continue
            parent_id = int(item["parent_id"])
            counts[parent_id] = counts.get(parent_id, 0) + 1
        return counts

    def count_parent_binding_updates(self, parent_bom_id: int) -> int:
        return sum(
            1 for item in self.list_child_version_status(int(parent_bom_id))
            if not item.get("is_latest")
        )

    def project_configuration_snapshot(self, project_id: int) -> dict:
        """Return the checked-in object and exact-binding configuration for snapshots."""
        contexts = self.get_current_contexts_for_project(int(project_id))
        with self.get_conn() as conn:
            object_rows = conn.execute(
                """
                SELECT id, aes_number, part_number, name, type
                FROM bom WHERE project_id=? ORDER BY id
                """,
                (int(project_id),),
            ).fetchall()
            objects = []
            for row in object_rows:
                item = dict(row)
                context = contexts.get(int(item["id"]), {})
                item.update({
                    "revision_id": context.get("current_revision_id"),
                    "iteration_id": context.get("current_iteration_id"),
                    "version": context.get("version_label"),
                    "state": context.get("state"),
                })
                objects.append(item)
            binding_rows = conn.execute(
                """
                SELECT parent.id AS parent_bom_id, parent.aes_number AS parent_aes_number,
                       parent.current_iteration_id AS parent_iteration_id,
                       ib.usage_id, ib.child_bom_id, child.aes_number AS child_aes_number,
                       ib.child_revision_id, ib.child_iteration_id,
                       child_rev.revision_code AS child_revision,
                       child_it.iteration_number AS child_iteration,
                       ib.quantity, ib.sort_order
                FROM bom parent
                JOIN bom_iteration_bindings ib
                    ON ib.parent_iteration_id=parent.current_iteration_id
                JOIN bom child ON child.id=ib.child_bom_id
                JOIN bom_revisions child_rev ON child_rev.id=ib.child_revision_id
                JOIN bom_iterations child_it ON child_it.id=ib.child_iteration_id
                WHERE parent.project_id=?
                ORDER BY parent.id, ib.sort_order, ib.id
                """,
                (int(project_id),),
            ).fetchall()
            bindings = []
            for row in binding_rows:
                item = dict(row)
                item["child_version"] = (
                    f"{item['child_revision']}.{int(item['child_iteration'])}"
                )
                bindings.append(item)
        return {"objects": objects, "bindings": bindings}
