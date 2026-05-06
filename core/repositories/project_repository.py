import uuid
from config import DB_NAME
import sqlite3
from datetime import datetime
import os


def _alpha_to_int(label: str) -> int:
    """Excel-style A=1..Z=26, AA=27... Returns 0 for invalid."""
    s = (label or "").strip().upper()
    if not s.isalpha():
        return 0
    n = 0
    for ch in s:
        n = n * 26 + (ord(ch) - ord('A') + 1)
    return n


def _int_to_alpha(n: int) -> str:
    if n <= 0:
        return "A"
    out = []
    while n > 0:
        n -= 1
        out.append(chr(ord('A') + (n % 26)))
        n //= 26
    return "".join(reversed(out))

class ProjectRepository:
    def __init__(self, db_name=DB_NAME):
        self.db_name = db_name

    def get_conn(self):
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        return conn

    def get_all(self):
        with self.get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, name, description, working_directory, created_at, root_project_id, version_label, version_state FROM projects")
            rows = cursor.fetchall()
            out = []
            for r in rows:
                d = dict(r)
                out.append(d)
            return out
        
    def get_projects_for_user(self, user_id):
        with self.get_conn() as conn:
            cursor = conn.cursor()
            # Best-effort: support both legacy schema (no versioning) and new schema.
            cols = set(self._table_columns(conn, "projects"))
            if "version_label" in cols and "root_project_id" in cols:
                cursor.execute(
                    """
                    SELECT
                        p.id,
                        p.name,
                        p.working_directory,
                        p.root_project_id,
                        p.version_label,
                        p.version_state,
                        root.name AS root_name,
                        up.is_current
                    FROM projects p
                    JOIN user_projects up ON p.id = up.project_id
                    LEFT JOIN projects root ON root.id = p.root_project_id
                    WHERE up.user_id = ?
                    ORDER BY COALESCE(root.name, p.name) ASC, p.version_label ASC
                    """,
                    (user_id,),
                )
            else:
                cursor.execute(
                    """
                    SELECT p.id, p.name, p.working_directory, up.is_current
                    FROM projects p
                    JOIN user_projects up ON p.id = up.project_id
                    WHERE up.user_id = ?
                    ORDER BY p.name ASC
                    """,
                    (user_id,),
                )
            rows = cursor.fetchall()
            out = []
            for r in rows:
                d = dict(r)
                out.append(d)
            return out
    
    def get_project_by_id(self, project_id):
        with self.get_conn() as conn:
            cursor = conn.cursor()
            cols = set(self._table_columns(conn, "projects"))
            if "version_label" in cols:
                cursor.execute(
                    "SELECT id, name, working_directory, description, created_at, root_project_id, version_label, version_state, created_from_project_id, is_readonly FROM projects WHERE id = ?",
                    (project_id,),
                )
            else:
                cursor.execute("SELECT id, name, working_directory FROM projects WHERE id = ?", (project_id,))
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None
        
    def get_project_id(self, project_name):
        with self.get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM projects WHERE name = ?", (project_name,))
            row = cursor.fetchone()
            if row:
                return row[0]
            return None


    def add_user_to_project(self, user_id, project_id):
        with self.get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO user_projects (user_id, project_id) VALUES (?, ?)",
                (user_id, project_id),
            )
            conn.commit()

    def remove_user_from_project(self, user_id, project_id):
        with self.get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM user_projects WHERE user_id = ? AND project_id = ?",
                (user_id, project_id),
            )
            conn.commit()

    def get_users_for_project(self, project_id: int):
        with self.get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT u.id, u.username, u.email
                FROM users u
                JOIN user_projects up ON up.user_id = u.id
                WHERE up.project_id = ?
                ORDER BY u.username ASC
                """,
                (project_id,),
            )
            rows = cursor.fetchall()
            return [dict(r) for r in rows]

    def create_project(self, name, working_directory, description=""):
        """Create a new project and return its ID"""
        with self.get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO projects (name, description, working_directory, created_at) VALUES (?, ?, ?, datetime('now'))",
                (name, description, working_directory),
            )
            conn.commit()
            new_id = cursor.lastrowid

            # If versioning columns exist, make this the root (A)
            cols = set(self._table_columns(conn, "projects"))
            if "root_project_id" in cols and "version_label" in cols:
                conn.execute(
                    "UPDATE projects SET root_project_id = COALESCE(root_project_id, id), version_label = COALESCE(version_label, 'A'), version_state = COALESCE(version_state, 'WIP'), is_readonly = COALESCE(is_readonly, 0) WHERE id = ?",
                    (new_id,),
                )
                conn.commit()

            return new_id

    def delete_project(self, project_id: int) -> bool:
        """Delete a project (best-effort)."""
        with self.get_conn() as conn:
            cur = conn.cursor()
            # Best-effort cleanup of user mapping and BOM rows
            try:
                cur.execute("DELETE FROM user_projects WHERE project_id = ?", (project_id,))
            except Exception:
                pass

            # Best-effort cleanup of vault metadata linked to BOM parts
            try:
                tables = set(self._list_tables(conn))
                if "bom" in tables and "part_files" in tables and "part_file_versions" in tables:
                    part_ids = [
                        int(r[0])
                        for r in conn.execute("SELECT id FROM bom WHERE project_id = ?", (project_id,)).fetchall()
                    ]
                    if part_ids:
                        placeholders = ",".join(["?"] * len(part_ids))
                        file_ids = [
                            int(r[0])
                            for r in conn.execute(
                                f"SELECT id FROM part_files WHERE part_id IN ({placeholders})",
                                tuple(part_ids),
                            ).fetchall()
                        ]
                        if file_ids:
                            fp = ",".join(["?"] * len(file_ids))
                            conn.execute(
                                f"DELETE FROM part_file_versions WHERE file_id IN ({fp})",
                                tuple(file_ids),
                            )
                        conn.execute(
                            f"DELETE FROM part_files WHERE part_id IN ({placeholders})",
                            tuple(part_ids),
                        )
            except Exception:
                pass
            try:
                cur.execute("DELETE FROM bom WHERE project_id = ?", (project_id,))
            except Exception:
                pass
            cur.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            conn.commit()
            return cur.rowcount > 0

    def update_project(self, project_id: int, name: str, working_directory: str, description: str = "") -> bool:
        """Update editable project fields. Returns True if a row was updated."""

        if not project_id:
            raise ValueError("project_id is required")
        if name is None or not str(name).strip():
            raise ValueError("name is required")

        name = str(name).strip()
        working_directory = str(working_directory or "")
        description = str(description or "")

        with self.get_conn() as conn:
            cols = set(self._table_columns(conn, "projects"))

            # If the schema supports readonly, refuse edits.
            if "is_readonly" in cols:
                row = conn.execute("SELECT is_readonly FROM projects WHERE id = ?", (int(project_id),)).fetchone()
                if row and int(dict(row).get("is_readonly") or 0) == 1:
                    raise ValueError("This project is read-only")

            cur = conn.execute(
                "UPDATE projects SET name = ?, description = ?, working_directory = ? WHERE id = ?",
                (name, description, working_directory, int(project_id)),
            )
            conn.commit()
            return cur.rowcount > 0

    def _next_project_version_label(self, conn, root_project_id: int) -> str:
        rows = conn.execute(
            "SELECT version_label FROM projects WHERE root_project_id = ?",
            (root_project_id,),
        ).fetchall()
        max_n = 0
        for r in rows:
            try:
                lbl = (r[0] if not isinstance(r, dict) else r.get("version_label"))
            except Exception:
                lbl = None
            max_n = max(max_n, _alpha_to_int(lbl))
        return _int_to_alpha(max_n + 1)

    def create_project_version(
        self,
        source_project_id: int,
        user_id: int,
        new_working_directory: str,
        version_label: str | None = None,
        new_project_name: str | None = None,
        description: str | None = None,
        progress_cb=None,
        cancel_cb=None,
    ) -> int:
        """Create a new project version: deep-copy BOM + relations; do NOT copy commits."""

        if not source_project_id:
            raise ValueError("source_project_id is required")
        if not new_working_directory:
            raise ValueError("new_working_directory is required")

        with self.get_conn() as conn:
            conn.execute("BEGIN")
            try:
                if callable(progress_cb):
                    try:
                        progress_cb(0, "Preparing new version...")
                    except Exception:
                        pass

                src = conn.execute("SELECT * FROM projects WHERE id = ?", (source_project_id,)).fetchone()
                if not src:
                    raise ValueError("Source project not found")
                srcd = dict(src)

                cols = set(self._table_columns(conn, "projects"))
                root_id = int(srcd.get("root_project_id") or srcd.get("id")) if "root_project_id" in cols else int(srcd.get("id"))

                root = conn.execute("SELECT * FROM projects WHERE id = ?", (root_id,)).fetchone()
                root_name = (dict(root).get("name") if root else None) or srcd.get("name") or str(root_id)

                # Normalize and choose an available version label (robust against collisions).
                supports_versioning = "version_label" in cols and "root_project_id" in cols
                explicit_name = bool(new_project_name and str(new_project_name).strip())
                explicit_desc = description is not None

                existing_labels: set[str] = set()
                if supports_versioning:
                    rows = conn.execute(
                        "SELECT version_label FROM projects WHERE root_project_id = ?",
                        (root_id,),
                    ).fetchall()
                    for r in rows:
                        try:
                            lbl = r[0]
                        except Exception:
                            lbl = None
                        lbl = (lbl or "").strip().upper()
                        if lbl:
                            existing_labels.add(lbl)

                def _label_is_valid(lbl: str) -> bool:
                    s = (lbl or "").strip().upper()
                    return bool(s) and s.isalpha()

                def _next_free_label(start_n: int) -> str:
                    n = max(1, int(start_n))
                    while True:
                        cand = _int_to_alpha(n)
                        if cand not in existing_labels:
                            return cand
                        n += 1

                # Determine initial candidate label
                if not supports_versioning:
                    version_label = "A"
                else:
                    if version_label is None or not str(version_label).strip():
                        max_n = 0
                        for lbl in existing_labels:
                            max_n = max(max_n, _alpha_to_int(lbl))
                        version_label = _next_free_label(max_n + 1)
                    else:
                        candidate = str(version_label).strip().upper()
                        if not _label_is_valid(candidate):
                            raise ValueError("Version label must be alphabetic (A..Z..AA..)")
                        n0 = _alpha_to_int(candidate)
                        version_label = candidate if candidate not in existing_labels else _next_free_label(n0)

                if not _label_is_valid(version_label):
                    raise ValueError("Version label must be alphabetic (A..Z..AA..)")

                insert_cols = set(self._table_columns(conn, "projects"))

                # Insert new project with retry on uniqueness collisions
                last_err: Exception | None = None
                for _attempt in range(30):
                    # Build name/description for this label
                    if not explicit_name:
                        candidate_name = f"{root_name}__{version_label}"
                    else:
                        candidate_name = str(new_project_name).strip()

                    # Ensure unique name
                    if conn.execute("SELECT 1 FROM projects WHERE name = ? LIMIT 1", (candidate_name,)).fetchone():
                        candidate_name = f"{candidate_name}__{uuid.uuid4().hex[:6]}"

                    if not explicit_desc:
                        candidate_desc = f"Version {version_label} from project {source_project_id}"
                    else:
                        candidate_desc = description

                    try:
                        if "version_label" in insert_cols and "root_project_id" in insert_cols:
                            cur = conn.execute(
                                """
                                INSERT INTO projects (
                                    name, description, working_directory, created_at,
                                    root_project_id, version_label, version_state,
                                    created_from_project_id, is_readonly
                                ) VALUES (?, ?, ?, datetime('now'), ?, ?, 'WIP', ?, 0)
                                """,
                                (candidate_name, candidate_desc, new_working_directory, root_id, version_label, source_project_id),
                            )
                        else:
                            cur = conn.execute(
                                "INSERT INTO projects (name, description, working_directory, created_at) VALUES (?, ?, ?, datetime('now'))",
                                (candidate_name, candidate_desc, new_working_directory),
                            )
                        new_project_id = cur.lastrowid
                        if supports_versioning:
                            existing_labels.add(version_label)
                        break
                    except sqlite3.IntegrityError as e:
                        last_err = e
                        msg = str(e)
                        # If version label is taken, pick next label and retry.
                        if supports_versioning and ("projects.root_project_id" in msg and "projects.version_label" in msg):
                            n = _alpha_to_int(version_label)
                            version_label = _next_free_label(n + 1)
                            continue
                        # Name collision: just retry with a different suffix.
                        if "projects.name" in msg or "UNIQUE constraint failed: projects.name" in msg:
                            continue
                        raise
                else:
                    raise last_err or RuntimeError("Failed to create project version")

                if callable(progress_cb):
                    try:
                        progress_cb(5, "Project version created. Copying BOM...")
                    except Exception:
                        pass

                # Link user to new project
                try:
                    conn.execute(
                        "INSERT INTO user_projects (user_id, project_id) VALUES (?, ?)",
                        (user_id, new_project_id),
                    )
                except Exception:
                    pass

                # Duplicate BOM rows
                bom_cols = self._table_columns(conn, "bom")
                if "project_id" not in bom_cols:
                    raise ValueError("BOM table missing project_id")

                src_boms = conn.execute(
                    "SELECT * FROM bom WHERE project_id = ?",
                    (source_project_id,),
                ).fetchall()

                total_boms = len(src_boms)

                bom_id_map = {}
                for idx, r in enumerate(src_boms, start=1):
                    if callable(cancel_cb) and cancel_cb():
                        raise RuntimeError("Cancelled")
                    rdict = dict(r)
                    old_id = rdict.get("id")

                    # Fix inherited absolute attachment paths when the working directory changes.
                    # If pdf_path/step_path are absolute under the source wd, remap to new wd.
                    try:
                        src_wd = (rdict.get("__src_wd") or None)
                    except Exception:
                        src_wd = None
                    # Source working directory is available earlier in this method as src, but
                    # to keep changes localized we recompute it here safely.
                    try:
                        src_row = conn.execute("SELECT working_directory FROM projects WHERE id = ?", (source_project_id,)).fetchone()
                        src_wd = (src_row[0] if src_row else "") or ""
                    except Exception:
                        src_wd = ""

                    def _remap_path(p: str | None) -> str | None:
                        if not p:
                            return p
                        try:
                            if os.path.isabs(p) and src_wd and os.path.commonpath([os.path.normpath(p), os.path.normpath(src_wd)]) == os.path.normpath(src_wd):
                                rel = os.path.relpath(os.path.normpath(p), os.path.normpath(src_wd))
                                return os.path.normpath(os.path.join(new_working_directory, rel))
                        except Exception:
                            return p
                        return p

                    if "pdf_path" in rdict:
                        rdict["pdf_path"] = _remap_path(rdict.get("pdf_path"))
                    if "step_path" in rdict:
                        rdict["step_path"] = _remap_path(rdict.get("step_path"))

                    new_id = self._insert_row_from_row(
                        conn,
                        "bom",
                        bom_cols,
                        rdict,
                        overrides={"project_id": new_project_id},
                        id_col="id",
                    )
                    if old_id is not None:
                        bom_id_map[int(old_id)] = int(new_id)

                    if callable(progress_cb) and total_boms:
                        try:
                            # 5..60
                            pct = 5 + int((idx / total_boms) * 55)
                            progress_cb(pct, f"Copying BOM... ({idx}/{total_boms})")
                        except Exception:
                            pass

                # Duplicate BOM relations (only those between duplicated parts)
                if bom_id_map:
                    rel_cols = self._table_columns(conn, "bom_children")
                    rel_rows = conn.execute("SELECT * FROM bom_children").fetchall()
                    old_ids = set(bom_id_map.keys())
                    total_rels = len(rel_rows)
                    for idx, rr in enumerate(rel_rows, start=1):
                        if callable(cancel_cb) and cancel_cb():
                            raise RuntimeError("Cancelled")
                        d = dict(rr)
                        p = d.get("parent_id")
                        c = d.get("child_id")
                        if p in old_ids and c in old_ids:
                            overrides = {"parent_id": bom_id_map[int(p)], "child_id": bom_id_map[int(c)]}
                            self._insert_row_from_row(conn, "bom_children", rel_cols, d, overrides=overrides, id_col="id")

                        if callable(progress_cb) and total_rels:
                            try:
                                # 60..70
                                pct = 60 + int((idx / total_rels) * 10)
                                progress_cb(pct, "Copying BOM relations...")
                            except Exception:
                                pass

                # Duplicate vault metadata (attachments) so the new revision keeps file history.
                tables = set(self._list_tables(conn))
                if bom_id_map and "part_files" in tables and "part_file_versions" in tables:
                    pf_cols = self._table_columns(conn, "part_files")
                    pv_cols = self._table_columns(conn, "part_file_versions")

                    # Pre-count files for smoother progress
                    try:
                        old_part_ids = [int(x) for x in bom_id_map.keys()]
                        placeholders = ",".join(["?"] * len(old_part_ids))
                        pf_total = int(
                            conn.execute(
                                f"SELECT COUNT(*) AS c FROM part_files WHERE part_id IN ({placeholders})",
                                tuple(old_part_ids),
                            ).fetchone()["c"]
                        )
                    except Exception:
                        pf_total = 0
                    pf_done = 0

                    for old_part_id, new_part_id in bom_id_map.items():
                        old_files = conn.execute(
                            "SELECT * FROM part_files WHERE part_id = ?",
                            (int(old_part_id),),
                        ).fetchall()
                        for pf_row in old_files:
                            if callable(cancel_cb) and cancel_cb():
                                raise RuntimeError("Cancelled")
                            pf = dict(pf_row)
                            old_file_id = int(pf.get("id"))
                            old_active_vid = pf.get("active_version_id")

                            new_file_id = int(
                                self._insert_row_from_row(
                                    conn,
                                    "part_files",
                                    pf_cols,
                                    pf,
                                    overrides={"part_id": int(new_part_id), "active_version_id": None},
                                    id_col="id",
                                )
                            )

                            # Copy versions and map old->new version ids
                            old_versions = conn.execute(
                                "SELECT * FROM part_file_versions WHERE file_id = ? ORDER BY version_no ASC",
                                (old_file_id,),
                            ).fetchall()
                            old_to_new_vid: dict[int, int] = {}
                            for v_row in old_versions:
                                v = dict(v_row)
                                old_vid = int(v.get("id"))
                                new_vid = int(
                                    self._insert_row_from_row(
                                        conn,
                                        "part_file_versions",
                                        pv_cols,
                                        v,
                                        overrides={"file_id": new_file_id},
                                        id_col="id",
                                    )
                                )
                                old_to_new_vid[old_vid] = new_vid

                            # Restore active version id to the equivalent new version id.
                            try:
                                if old_active_vid is not None:
                                    mapped = old_to_new_vid.get(int(old_active_vid))
                                    if mapped is not None:
                                        conn.execute(
                                            "UPDATE part_files SET active_version_id = ? WHERE id = ?",
                                            (int(mapped), int(new_file_id)),
                                        )
                            except Exception:
                                pass

                            pf_done += 1
                            if callable(progress_cb):
                                try:
                                    # 70..90
                                    if pf_total:
                                        pct = 70 + int((pf_done / max(1, pf_total)) * 20)
                                    else:
                                        pct = 80
                                    progress_cb(pct, f"Copying attachments... ({pf_done}/{pf_total or '?'})")
                                except Exception:
                                    pass

                if callable(progress_cb):
                    try:
                        progress_cb(95, "Finalizing...")
                    except Exception:
                        pass

                conn.commit()
                if callable(progress_cb):
                    try:
                        progress_cb(100, "Done")
                    except Exception:
                        pass
                return int(new_project_id)
            except Exception:
                conn.rollback()
                raise

    def _list_tables(self, conn):
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        return [r[0] for r in rows]

    def _table_columns(self, conn, table_name):
        # basic identifier safety
        if not all(ch.isalnum() or ch == "_" for ch in table_name):
            return []
        info = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return [r[1] for r in info]  # second column is name

    def _insert_row_from_row(self, conn, table_name, cols, row_dict, overrides=None, id_col="id"):
        """
        Insert a copy of row_dict into table_name, excluding id_col.
        Returns lastrowid.
        """
        overrides = overrides or {}

        insert_cols = [c for c in cols if c != id_col]
        data = {c: row_dict.get(c) for c in insert_cols}
        for k, v in overrides.items():
            if k in data:
                data[k] = v

        placeholders = ", ".join(["?"] * len(insert_cols))
        col_list = ", ".join(insert_cols)
        sql = f"INSERT INTO {table_name} ({col_list}) VALUES ({placeholders})"
        cur = conn.execute(sql, tuple(data[c] for c in insert_cols))
        return cur.lastrowid

    def duplicate_project(self, source_project_id, new_project_name, user_id):
        """
        Duplicate: project + user link + BOM + BOM relations + commits (best-effort, schema-driven).
        """
        source_project = self.get_project_by_id(source_project_id)
        if not source_project:
            raise ValueError("Source project not found")

        with self.get_conn() as conn:
            conn.execute("BEGIN")
            try:
                # 1) Create new project (same working_directory)
                cur = conn.execute(
                    "INSERT INTO projects (name, description, working_directory, created_at) "
                    "VALUES (?, ?, ?, datetime('now'))",
                    (
                        new_project_name,
                        f"Duplicate of {source_project.get('name','')}",
                        source_project.get("working_directory", ""),
                    ),
                )
                new_project_id = cur.lastrowid

                # 2) Link user to new project
                conn.execute(
                    "INSERT INTO user_projects (user_id, project_id) VALUES (?, ?)",
                    (user_id, new_project_id),
                )

                tables = self._list_tables(conn)

                def is_bom_table(t):
                    cols = set(self._table_columns(conn, t))
                    return (
                        "project_id" in cols
                        and "id" in cols
                        and (
                            "part_number" in cols
                            or "drawing_number" in cols
                            or "aes_number" in cols
                            or "filename" in cols
                        )
                    )

                def is_relation_table(t):
                    cols = set(self._table_columns(conn, t))
                    return "parent_id" in cols and "child_id" in cols

                def is_commit_table(t):
                    cols = set(self._table_columns(conn, t))
                    return (
                        "project_id" in cols
                        and "id" in cols
                        and ("message" in cols or "commit_id" in cols or "signature" in cols)
                    )

                bom_tables = [t for t in tables if is_bom_table(t)]
                relation_tables = [t for t in tables if is_relation_table(t)]
                commit_tables = [t for t in tables if is_commit_table(t)]

                bom_id_map = {}  # old_bom_id -> new_bom_id

                # 3) Duplicate BOM rows (first BOM table found)
                if bom_tables:
                    bom_table = bom_tables[0]
                    bom_cols = self._table_columns(conn, bom_table)

                    src_boms = conn.execute(
                        f"SELECT * FROM {bom_table} WHERE project_id = ?",
                        (source_project_id,),
                    ).fetchall()

                    for r in src_boms:
                        rdict = dict(r)
                        old_id = rdict.get("id")
                        new_id = self._insert_row_from_row(
                            conn,
                            bom_table,
                            bom_cols,
                            rdict,
                            overrides={"project_id": new_project_id},
                            id_col="id",
                        )
                        if old_id is not None:
                            bom_id_map[old_id] = new_id

                # 4) Duplicate BOM relations
                if bom_id_map and relation_tables:
                    old_ids = set(bom_id_map.keys())
                    for rel_table in relation_tables:
                        rel_cols = self._table_columns(conn, rel_table)
                        has_project_id = "project_id" in rel_cols

                        rel_rows = conn.execute(f"SELECT * FROM {rel_table}").fetchall()
                        for rr in rel_rows:
                            rrd = dict(rr)
                            p = rrd.get("parent_id")
                            c = rrd.get("child_id")
                            if p in old_ids and c in old_ids:
                                overrides = {
                                    "parent_id": bom_id_map[p],
                                    "child_id": bom_id_map[c],
                                }
                                if has_project_id:
                                    overrides["project_id"] = new_project_id

                                self._insert_row_from_row(
                                    conn, rel_table, rel_cols, rrd, overrides=overrides, id_col="id"
                                )

                # 5) Duplicate commits (remap part_id if present)
                if commit_tables:
                    for commit_table in commit_tables:
                        commit_cols = self._table_columns(conn, commit_table)
                        has_part_id = "part_id" in commit_cols
                        has_signature = "signature" in commit_cols
                        has_commit_id = "commit_id" in commit_cols

                        src_commits = conn.execute(
                            f"SELECT * FROM {commit_table} WHERE project_id = ?",
                            (source_project_id,),
                        ).fetchall()

                        for idx, cr in enumerate(src_commits, start=1):
                            crd = dict(cr)
                            overrides = {"project_id": new_project_id}

                            if has_part_id:
                                old_part_id = crd.get("part_id")
                                if old_part_id is None or old_part_id not in bom_id_map:
                                    continue
                                overrides["part_id"] = bom_id_map[old_part_id]

                            # ALWAYS make signature unique (handles "" too)
                            if has_signature:
                                base_sig = crd.get("signature")
                                if base_sig is None:
                                    base_sig = ""
                                overrides["signature"] = f"{base_sig}|dup:{new_project_id}:{idx}:{uuid.uuid4().hex}"

                            # Often unique too; rewrite to avoid collisions
                            if has_commit_id and crd.get("commit_id") is not None:
                                overrides["commit_id"] = f"{crd['commit_id']}|dup:{new_project_id}:{idx}"

                            self._insert_row_from_row(
                                conn, commit_table, commit_cols, crd, overrides=overrides, id_col="id"
                            )

                conn.commit()
                return new_project_id

            except Exception:
                conn.rollback()
                raise

    def get_project_by_root_and_label(self, root_project_id: int, version_label: str):
        if not root_project_id or not version_label:
            return None
        with self.get_conn() as conn:
            cols = set(self._table_columns(conn, "projects"))
            if "root_project_id" not in cols or "version_label" not in cols:
                return None
            row = conn.execute(
                """
                SELECT id, name, working_directory, description, created_at, root_project_id, version_label, version_state, created_from_project_id, is_readonly
                FROM projects
                WHERE root_project_id = ? AND UPPER(TRIM(version_label)) = UPPER(TRIM(?))
                LIMIT 1
                """,
                (root_project_id, version_label),
            ).fetchone()
            return dict(row) if row else None

