"""Operational health checks for the shared-folder PDM deployment."""

from __future__ import annotations

import os
import socket
import sqlite3
import tempfile
from pathlib import Path

from config import DB_NAME


class PdmOperationsHealthService:
    """Inspect operational dependencies without changing PDM business data."""

    def __init__(self, db_name=DB_NAME, *, machine_id: str | None = None):
        self.db_name = db_name
        self.machine_id = str(machine_id or socket.gethostname()).strip()

    def inspect(self, project_id: int | None) -> list[dict]:
        rows = []
        db_path = Path(os.fspath(self.db_name)).expanduser()
        if not db_path.is_file():
            return [self._row("Database", "ERROR", f"Database file is missing: {db_path}")]

        try:
            with sqlite3.connect(str(db_path), timeout=0.15, isolation_level=None) as conn:
                conn.execute("PRAGMA busy_timeout=150")
                check_rows = conn.execute("PRAGMA quick_check").fetchall()
                check = "; ".join(str(row[0] or "") for row in check_rows)
                journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0] or "unknown")
                db_size = db_path.stat().st_size
                lock_status = self._probe_writer_lock(conn)
            db_status = "OK" if check.casefold() == "ok" else "ERROR"
            rows.append(self._row(
                "Database",
                db_status,
                f"{db_path} | {db_size:,} bytes | SQLite quick_check: {check} | journal: {journal_mode}",
            ))
            rows.append(self._row(
                "Shared-folder write lock",
                lock_status[0],
                lock_status[1],
            ))
        except sqlite3.Error as exc:
            return [
                self._row("Database", "ERROR", f"Cannot inspect {db_path}: {exc}"),
                self._row(
                    "Shared-folder write lock",
                    "ERROR",
                    "Lock availability could not be checked because the database is unavailable.",
                ),
            ]
        except OSError as exc:
            rows.append(self._row("Database", "ERROR", f"Cannot access {db_path}: {exc}"))

        rows.extend(self._inspect_project_store(project_id))
        rows.extend(self._inspect_approval_journals(project_id))
        rows.append(self._inspect_release_queue(project_id))
        return rows

    @staticmethod
    def _probe_writer_lock(conn: sqlite3.Connection) -> tuple[str, str]:
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("ROLLBACK")
            return "OK", "Short non-writing reservation succeeded and was immediately released."
        except sqlite3.OperationalError as exc:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            message = str(exc).casefold()
            if "locked" in message or "busy" in message:
                return "WARNING", "Another writer currently holds the shared database lock; retry after it finishes."
            return "ERROR", f"SQLite could not acquire and release a short writer reservation: {exc}"

    def _inspect_project_store(self, project_id: int | None) -> list[dict]:
        if project_id is None:
            return [self._row("Project file store", "WARNING", "Select a project to inspect its shared file folders.")]
        try:
            with sqlite3.connect(str(self.db_name), timeout=2) as conn:
                conn.row_factory = sqlite3.Row
                project = conn.execute(
                    "SELECT name, working_directory FROM projects WHERE id=?",
                    (int(project_id),),
                ).fetchone()
                if not project:
                    return [self._row("Project file store", "ERROR", "The active project is missing from the database.")]
                snapshot_count = 0
                if self._table_exists(conn, "cad_commit_snapshots"):
                    snapshot_count = int(conn.execute(
                        "SELECT COUNT(*) FROM cad_commit_snapshots WHERE project_id=?",
                        (int(project_id),),
                    ).fetchone()[0])
        except sqlite3.Error as exc:
            return [self._row("Project file store", "ERROR", f"Cannot read project storage settings: {exc}")]

        root = Path(str(project["working_directory"] or "")).expanduser()
        if not str(project["working_directory"] or "").strip():
            return [self._row("Project file store", "ERROR", "The project has no shared working-directory path.")]

        commits = root / "commits"
        snapshot_objects = commits / "_snapshots" / "objects"
        checks = [
            ("Project root", root, True),
            ("Commit file store", commits, True),
        ]
        if snapshot_count or snapshot_objects.exists():
            checks.append(("Frozen snapshot store", snapshot_objects, snapshot_count > 0))

        results = []
        for label, path, required in checks:
            if not path.is_dir():
                state = "ERROR" if required else "WARNING"
                detail = f"Required folder is missing: {path}" if required else f"Folder has not been created yet: {path}"
                results.append(self._row(label, state, detail))
                continue
            try:
                self._write_probe(path)
                results.append(self._row(label, "OK", f"Folder exists and a temporary write/delete probe succeeded: {path}"))
            except OSError as exc:
                results.append(self._row(label, "ERROR", f"Folder is not writable: {path} ({exc})"))
        return results

    def _inspect_approval_journals(self, project_id: int | None) -> list[dict]:
        if project_id is None:
            return []
        try:
            with sqlite3.connect(str(self.db_name), timeout=2) as conn:
                conn.row_factory = sqlite3.Row
                if not self._table_exists(conn, "cad_submission_approvals"):
                    return [self._row("Approval recovery", "WARNING", "Approval journal table is not installed; run Nexus database migrations.")]
                records = conn.execute(
                    """SELECT approval_id, commit_id, approver_id, status, updated_at, last_error
                       FROM cad_submission_approvals
                       WHERE project_id=? AND status NOT IN ('COMPLETED','WITHDRAWN','REJECTED')
                       ORDER BY updated_at LIMIT 50""",
                    (int(project_id),),
                ).fetchall()
                total = int(conn.execute(
                    """SELECT COUNT(*) FROM cad_submission_approvals
                       WHERE project_id=? AND status NOT IN ('COMPLETED','WITHDRAWN','REJECTED')""",
                    (int(project_id),),
                ).fetchone()[0])
        except sqlite3.Error as exc:
            return [self._row("Approval recovery", "ERROR", f"Could not inspect approval journals: {exc}")]

        if not total:
            return [self._row("Approval recovery", "OK", "No interrupted approval publications are recorded for this project.")]

        rows = []
        for record in records:
            error = str(record["last_error"] or "").strip()
            detail = (
                f"Commit {record['commit_id']} | phase {record['status']} | "
                f"updated {record['updated_at']} | original approver ID {record['approver_id']}"
            )
            if error:
                detail += f" | last error: {error}"
            detail += ". Retry approval from Commit using the original approver; verify the snapshot and workspace before retrying."
            rows.append(self._row("Approval recovery", "WARNING", detail, f"approval:{record['approval_id']}"))
        if total > len(records):
            rows.append(self._row("Approval recovery", "WARNING", f"Showing {len(records)} of {total} incomplete approval journals."))
        return rows

    def _inspect_release_queue(self, project_id: int | None) -> dict:
        try:
            with sqlite3.connect(str(self.db_name), timeout=2) as conn:
                conn.row_factory = sqlite3.Row
                if not self._table_exists(conn, "cad_workspace_release_queue"):
                    return self._row("Local workspace cleanup", "WARNING", "Recovery queue is not installed; run Nexus database migrations.")
                local = conn.execute(
                    """SELECT COUNT(*) AS count, MAX(last_error) AS last_error
                       FROM cad_workspace_release_queue
                       WHERE completed_at IS NULL AND machine_id=?
                       AND (? IS NULL OR project_id=?)""",
                    (self.machine_id, project_id, project_id),
                ).fetchone()
                elsewhere = int(conn.execute(
                    """SELECT COUNT(*) FROM cad_workspace_release_queue
                       WHERE completed_at IS NULL AND machine_id<>?
                       AND (? IS NULL OR project_id=?)""",
                    (self.machine_id, project_id, project_id),
                ).fetchone()[0])
                unassigned = int(conn.execute(
                    """SELECT COUNT(*) FROM cad_workspace_release_queue
                       WHERE completed_at IS NULL AND trim(machine_id)=''
                       AND (? IS NULL OR project_id=?)""",
                    (project_id, project_id),
                ).fetchone()[0])
        except sqlite3.Error as exc:
            return self._row("Local workspace cleanup", "ERROR", f"Could not inspect recovery queue: {exc}")

        count = int(local["count"] or 0)
        if count:
            detail = f"{count} pending cleanup task(s) for this machine. Use Retry Local Cleanup to release approved CAD copies."
            if local["last_error"]:
                detail += f" Last error: {local['last_error']}"
            if elsewhere:
                detail += f" {elsewhere} task(s) belong to other machines and can only be retried there."
            if unassigned:
                detail += f" {unassigned} legacy task(s) have no machine assignment and require administrator review."
            return self._row("Local workspace cleanup", "WARNING", detail, "retry:local")
        detail = "No pending cleanup tasks for this machine."
        if elsewhere:
            detail += f" {elsewhere} task(s) belong to other machines and can only be retried there."
        if unassigned:
            detail += f" {unassigned} legacy task(s) have no machine assignment and require administrator review."
            return self._row("Local workspace cleanup", "WARNING", detail)
        return self._row("Local workspace cleanup", "OK", detail)

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (str(table),),
        ).fetchone() is not None

    @staticmethod
    def _write_probe(directory: Path) -> None:
        descriptor, path = tempfile.mkstemp(prefix=".nexus-health-", dir=str(directory))
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(b"nexus operational health probe\n")
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    @staticmethod
    def _row(area: str, status: str, details: str, key: str = "") -> dict:
        return {"area": area, "status": status, "details": details, "key": key}
