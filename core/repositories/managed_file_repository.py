import sqlite3
from typing import Iterable

from config import DB_NAME


class ManagedFileRepository:
    """Persistence for immutable file manifests bound to BOM object iterations."""

    def __init__(self, db_name=DB_NAME):
        self.db_name = db_name
        self._ensure_schema()

    def get_conn(self):
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self):
        with self.get_conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS bom_iteration_files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bom_id INTEGER NOT NULL,
                    iteration_id INTEGER NOT NULL,
                    binding_key TEXT NOT NULL,
                    file_role TEXT NOT NULL,
                    file_type TEXT NOT NULL DEFAULT '',
                    source_kind TEXT NOT NULL DEFAULT 'legacy',
                    part_file_id INTEGER,
                    part_file_version_id INTEGER,
                    filename TEXT NOT NULL,
                    file_revision TEXT NOT NULL DEFAULT '',
                    creo_iteration INTEGER,
                    storage_scheme TEXT NOT NULL DEFAULT 'legacy_reference',
                    vault_rel_path TEXT NOT NULL DEFAULT '',
                    sha256 TEXT,
                    size_bytes INTEGER,
                    integrity_status TEXT NOT NULL DEFAULT 'Unknown',
                    lifecycle_state TEXT NOT NULL DEFAULT 'In Work',
                    source_commit_id TEXT,
                    created_by INTEGER,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    UNIQUE(iteration_id, binding_key)
                );
                CREATE INDEX IF NOT EXISTS idx_bom_iteration_files_iteration
                    ON bom_iteration_files(iteration_id, file_role);
                CREATE INDEX IF NOT EXISTS idx_bom_iteration_files_bom
                    ON bom_iteration_files(bom_id, iteration_id);
                CREATE INDEX IF NOT EXISTS idx_bom_iteration_files_version
                    ON bom_iteration_files(part_file_version_id);
                """
            )
            columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(bom_iteration_files)").fetchall()
            }
            if "file_revision" not in columns:
                conn.execute(
                    "ALTER TABLE bom_iteration_files "
                    "ADD COLUMN file_revision TEXT NOT NULL DEFAULT ''"
                )

    def upsert(self, entry: dict) -> int:
        entry = dict(entry)
        entry["binding_key"] = (
            f"file:{int(entry['part_file_id'])}"
            if entry.get("part_file_id") is not None
            else str(entry.get("file_role") or "content")
        )
        columns = (
            "bom_id", "iteration_id", "binding_key", "file_role", "file_type", "source_kind",
            "part_file_id", "part_file_version_id", "filename", "file_revision", "creo_iteration",
            "storage_scheme", "vault_rel_path", "sha256", "size_bytes",
            "integrity_status", "lifecycle_state", "source_commit_id", "created_by",
        )
        values = [entry.get(column) for column in columns]
        with self.get_conn() as conn:
            if entry.get("part_file_id") is not None:
                conn.execute(
                    """
                    DELETE FROM bom_iteration_files
                    WHERE iteration_id=? AND part_file_id=?
                      AND (file_role<>? OR filename<>?)
                    """,
                    (
                        int(entry["iteration_id"]), int(entry["part_file_id"]),
                        str(entry["file_role"]), str(entry["filename"]),
                    ),
                )
            elif str(entry.get("file_role") or "") in {"native_cad", "drawing"}:
                conn.execute(
                    """
                    DELETE FROM bom_iteration_files
                    WHERE iteration_id=? AND file_role=? AND part_file_id IS NULL
                      AND filename<>?
                    """,
                    (
                        int(entry["iteration_id"]), str(entry["file_role"]),
                        str(entry["filename"]),
                    ),
                )
            conn.execute(
                f"""
                INSERT INTO bom_iteration_files ({', '.join(columns)})
                VALUES ({', '.join('?' for _ in columns)})
                ON CONFLICT(iteration_id, binding_key) DO UPDATE SET
                    file_role=excluded.file_role,
                    filename=excluded.filename,
                    file_revision=excluded.file_revision,
                    file_type=excluded.file_type,
                    source_kind=excluded.source_kind,
                    part_file_id=excluded.part_file_id,
                    part_file_version_id=excluded.part_file_version_id,
                    creo_iteration=excluded.creo_iteration,
                    storage_scheme=excluded.storage_scheme,
                    vault_rel_path=excluded.vault_rel_path,
                    sha256=excluded.sha256,
                    size_bytes=excluded.size_bytes,
                    integrity_status=excluded.integrity_status,
                    lifecycle_state=excluded.lifecycle_state,
                    source_commit_id=COALESCE(excluded.source_commit_id, source_commit_id),
                    created_by=COALESCE(excluded.created_by, created_by)
                """,
                values,
            )
            row = conn.execute(
                """
                SELECT id FROM bom_iteration_files
                WHERE iteration_id=? AND binding_key=?
                """,
                (
                    int(entry["iteration_id"]),
                    str(entry["binding_key"]),
                ),
            ).fetchone()
            return int(row["id"])

    def list_for_iteration(self, iteration_id: int) -> list[dict]:
        with self.get_conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM bom_iteration_files
                WHERE iteration_id=?
                ORDER BY CASE file_role
                    WHEN 'native_cad' THEN 0
                    WHEN 'drawing' THEN 1
                    WHEN 'generated_pdf' THEN 2
                    WHEN 'generated_step' THEN 3
                    ELSE 4 END,
                    filename
                """,
                (int(iteration_id),),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_for_iterations(self, iteration_ids: Iterable[int]) -> dict[int, list[dict]]:
        ids = sorted({int(value) for value in iteration_ids if value is not None})
        if not ids:
            return {}
        result = {iteration_id: [] for iteration_id in ids}
        with self.get_conn() as conn:
            for offset in range(0, len(ids), 800):
                chunk = ids[offset:offset + 800]
                placeholders = ",".join("?" for _ in chunk)
                rows = conn.execute(
                    f"""
                    SELECT * FROM bom_iteration_files
                    WHERE iteration_id IN ({placeholders})
                    ORDER BY iteration_id DESC, id
                    """,
                    chunk,
                ).fetchall()
                for row in rows:
                    item = dict(row)
                    result[int(item["iteration_id"])].append(item)
        return result

    def inherit_iteration_files(
        self,
        bom_id: int,
        source_iteration_id: int,
        target_iteration_id: int,
        lifecycle_state: str = "In Work",
    ) -> None:
        """Bind the exact prior file manifest to a new object iteration."""
        with self.get_conn() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO bom_iteration_files(
                    bom_id, iteration_id, binding_key, file_role, file_type, source_kind,
                    part_file_id, part_file_version_id, filename, file_revision, creo_iteration,
                    storage_scheme, vault_rel_path, sha256, size_bytes, integrity_status,
                    lifecycle_state, source_commit_id, created_by
                )
                SELECT ?, ?, binding_key, file_role, file_type, source_kind,
                       part_file_id, part_file_version_id, filename, file_revision, creo_iteration,
                       storage_scheme, vault_rel_path, sha256, size_bytes, integrity_status,
                       ?, source_commit_id, created_by
                FROM bom_iteration_files
                WHERE bom_id=? AND iteration_id=?
                """,
                (
                    int(bom_id), int(target_iteration_id), str(lifecycle_state or "In Work"),
                    int(bom_id), int(source_iteration_id),
                ),
            )

    def version_is_referenced(self, version_id: int) -> bool:
        with self.get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM bom_iteration_files WHERE part_file_version_id=? LIMIT 1",
                (int(version_id),),
            ).fetchone()
            return bool(row)

    def iteration_labels_for_version(self, version_id: int) -> list[str]:
        with self.get_conn() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT r.revision_code || '.' || i.iteration_number AS version_label
                FROM bom_iteration_files f
                JOIN bom_iterations i ON i.id=f.iteration_id
                JOIN bom_revisions r ON r.id=i.revision_id
                WHERE f.part_file_version_id=?
                ORDER BY i.id DESC
                """,
                (int(version_id),),
            ).fetchall()
            return [str(row["version_label"] or "") for row in rows]

    def set_iteration_lifecycle(self, iteration_id: int, state: str) -> None:
        with self.get_conn() as conn:
            conn.execute(
                "UPDATE bom_iteration_files SET lifecycle_state=? WHERE iteration_id=?",
                (str(state or "In Work"), int(iteration_id)),
            )

    def prune_iteration_documents(self, iteration_id: int, active_file_ids) -> None:
        ids = sorted({int(value) for value in (active_file_ids or []) if value is not None})
        with self.get_conn() as conn:
            if not ids:
                conn.execute(
                    "DELETE FROM bom_iteration_files WHERE iteration_id=? AND part_file_id IS NOT NULL",
                    (int(iteration_id),),
                )
                return
            placeholders = ",".join("?" for _ in ids)
            conn.execute(
                f"""
                DELETE FROM bom_iteration_files
                WHERE iteration_id=? AND part_file_id IS NOT NULL
                  AND part_file_id NOT IN ({placeholders})
                """,
                (int(iteration_id), *ids),
            )
