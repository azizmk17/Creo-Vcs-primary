"""
face_lifecycle.py
=================
Query the commit database to build a **lifecycle timeline** for a
geometric face identified by its fingerprint hash.

This module is designed to be used *inside the viewer subprocess*, so
it opens the SQLite database directly (no session / service layer).
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def get_face_lifecycle(
    fingerprint: str,
    part_id: int,
    project_id: int,
    db_path: str,
) -> list[dict[str, Any]]:
    """Return a chronologically‑sorted list of lifecycle events for *fingerprint*.

    Each event dict contains:
        ``commit_id``, ``message``, ``committed_at``, ``status``,
        ``designer``, ``event`` (``"added"`` | ``"removed"`` | ``"modified"``
        | ``"present"`` | ``"baseline"``), ``details`` (optional extra info).
    """
    if not db_path or not os.path.exists(db_path):
        return []

    conn = _connect(db_path)
    try:
        # Resolve family (root_project_id) so lifecycle spans all revisions
        family_ids = _resolve_family_project_ids(conn, project_id)

        # Fetch all commits for this part (across family) ordered chronologically
        placeholders = ",".join("?" for _ in family_ids)
        rows = conn.execute(
            f"""
            SELECT id, commit_id, message, committed_at, status, designer,
                   step_file_path, step_prev_file_path,
                   step_diff_path, step_diff_status, step_diff_summary
            FROM commits
            WHERE part_id = ?
              AND project_id IN ({placeholders})
            ORDER BY id ASC
            """,
            [int(part_id)] + [int(pid) for pid in family_ids],
        ).fetchall()

        # Resolve designer usernames
        user_cache: dict[int, str] = {}

        events: list[dict[str, Any]] = []
        for row in rows:
            row_d = dict(row)
            diff_path = (row_d.get("step_diff_path") or "").strip()
            diff_status = (row_d.get("step_diff_status") or "").strip().upper()

            commit_id = row_d.get("commit_id", "")
            message = row_d.get("message", "")
            committed_at = row_d.get("committed_at", "")
            status = row_d.get("status", "")
            designer_id = row_d.get("designer")
            designer_name = _username(conn, designer_id, user_cache)

            base_info: dict[str, Any] = {
                "commit_id": commit_id,
                "message": message,
                "committed_at": committed_at,
                "status": status,
                "designer": designer_name,
            }

            if diff_status == "BASELINE":
                # First commit — check if the face fingerprint is present in the
                # STEP file (we can't know without re-parsing; we note it as baseline)
                events.append({**base_info, "event": "baseline", "details": "First STEP commit (baseline)"})
                continue

            if diff_status != "COMPARED" or not diff_path or not os.path.exists(diff_path):
                continue

            # Load the diff JSON and search for the fingerprint
            try:
                with open(diff_path, "r", encoding="utf-8") as fh:
                    diff_data: dict[str, Any] = json.load(fh)
            except Exception:
                continue

            face_events = _search_diff_for_fingerprint(diff_data, fingerprint)
            for fe in face_events:
                events.append({**base_info, **fe})

        return events
    finally:
        conn.close()


# ---------------------------------------------------------------------------
#  Internal helpers
# ---------------------------------------------------------------------------

def _resolve_family_project_ids(conn: sqlite3.Connection, project_id: int) -> list[int]:
    """Return all project IDs sharing the same root_project_id, or just [project_id]."""
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(projects)").fetchall()]
        if "root_project_id" not in cols:
            return [int(project_id)]
        row = conn.execute(
            "SELECT root_project_id FROM projects WHERE id = ?", (int(project_id),)
        ).fetchone()
        root_id = int(row[0]) if row and row[0] is not None else int(project_id)
        rows = conn.execute(
            "SELECT id FROM projects WHERE root_project_id = ?", (int(root_id),)
        ).fetchall()
        ids = [int(r[0]) for r in rows]
        return ids if ids else [int(project_id)]
    except Exception:
        return [int(project_id)]


def _username(conn: sqlite3.Connection, user_id, cache: dict[int, str]) -> str:
    if user_id is None:
        return "Unknown"
    uid = int(user_id)
    if uid in cache:
        return cache[uid]
    try:
        row = conn.execute("SELECT username FROM users WHERE id = ?", (uid,)).fetchone()
        name = row[0] if row else f"User#{uid}"
    except Exception:
        name = f"User#{uid}"
    cache[uid] = name
    return name


def _search_diff_for_fingerprint(
    diff_data: dict[str, Any],
    fingerprint: str,
) -> list[dict[str, Any]]:
    """Look for *fingerprint* inside a diff JSON and return event dicts."""
    results: list[dict[str, Any]] = []

    # Added surfaces — list of dicts with "fingerprint" key
    for item in diff_data.get("added_surfaces", []):
        fp = item.get("fingerprint", "")
        if fp == fingerprint:
            stype = item.get("surface_type", "")
            area = item.get("area", "")
            results.append({
                "event": "added",
                "details": f"Face added (type={stype}, area={area})",
            })

    # Removed surfaces
    for item in diff_data.get("removed_surfaces", []):
        fp = item.get("fingerprint", "")
        if fp == fingerprint:
            stype = item.get("surface_type", "")
            area = item.get("area", "")
            results.append({
                "event": "removed",
                "details": f"Face removed (type={stype}, area={area})",
            })

    # Modified surfaces — check both before_fingerprint and after_fingerprint
    for item in diff_data.get("modified_surfaces", []):
        before_fp = item.get("before_fingerprint", "")
        after_fp = item.get("after_fingerprint", "")
        if before_fp == fingerprint:
            results.append({
                "event": "modified (before)",
                "details": f"Face geometry changed in this commit",
            })
        elif after_fp == fingerprint:
            results.append({
                "event": "modified (after)",
                "details": f"Face geometry was updated to this state",
            })

    return results
