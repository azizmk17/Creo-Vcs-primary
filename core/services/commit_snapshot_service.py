"""Immutable file and Creo metadata snapshots for pending submissions."""

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(path):
    result = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def _files_fingerprint(files):
    stable = [
        {"filename": item["filename"], "sha256": item["sha256"],
         "size_bytes": int(item["size_bytes"])}
        for item in sorted(files, key=lambda value: value["filename"].casefold())
    ]
    return hashlib.sha256(_canonical(stable).encode("utf-8")).hexdigest()


class CommitSnapshotService:
    def __init__(self, db_name):
        self.db_name = db_name

    @contextmanager
    def _connection(self):
        conn = sqlite3.connect(self.db_name)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _next_generation(conn, project_id, commit_id):
        row = conn.execute("""
            SELECT COALESCE(MAX(generation), 0) + 1
            FROM cad_commit_snapshots WHERE project_id=? AND commit_id=?
        """, (int(project_id), str(commit_id))).fetchone()
        return int(row[0])

    @staticmethod
    def _insert_generation(conn, project_id, commit_id, generation, manifest, created_by):
        encoded = _canonical(manifest)
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        conn.execute("""
            INSERT INTO cad_commit_snapshots(
                project_id,commit_id,generation,manifest_json,manifest_sha256,created_by
            ) VALUES(?,?,?,?,?,?)
        """, (int(project_id), str(commit_id), int(generation), encoded, digest,
              int(created_by) if created_by is not None else None))
        return {"generation": int(generation), "manifest_sha256": digest}

    def snapshot_identity(self, project_id, commit_id):
        """Return the integrity-checked latest manifest hash without baseline checks."""
        with self._connection() as conn:
            row = conn.execute("""
                SELECT manifest_json,manifest_sha256 FROM cad_commit_snapshots
                WHERE project_id=? AND commit_id=? ORDER BY generation DESC LIMIT 1
            """, (int(project_id), str(commit_id))).fetchone()
        if not row:
            raise ValueError("The frozen submission manifest is missing.")
        encoded = str(row["manifest_json"] or "")
        actual = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        if actual != str(row["manifest_sha256"] or ""):
            raise ValueError("The frozen submission manifest failed its integrity check.")
        return actual

    def seal_pending_commit(self, commits_root, project_id, commit_id, created_by,
                            baseline_by_cad_id=None):
        """Copy every currently pending file into content-addressed immutable storage."""
        root = os.path.realpath(commits_root)
        with self._connection() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT c.filename,c.cad_document_id,c.status,c.title,u.username
                FROM commits c LEFT JOIN users u ON u.id=c.designer
                WHERE c.project_id=? AND c.commit_id=? ORDER BY c.id
            """, (int(project_id), str(commit_id))).fetchall()
            if not rows:
                raise ValueError("Cannot seal a submission with no commit rows.")
            if any(str(row["status"] or "").casefold() != "pending" for row in rows):
                raise ValueError("Only a wholly Pending submission can be sealed.")

            cad_ids = {int(row["cad_document_id"]) for row in rows
                       if row["cad_document_id"] is not None}
            cad_documents = {}
            if cad_ids:
                marks = ",".join("?" for _ in cad_ids)
                has_cad_iterations = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cad_document_iterations'"
                ).fetchone() is not None
                iteration_join = (
                    "LEFT JOIN cad_document_iterations i ON i.cad_document_id=d.id "
                    "AND i.revision=d.revision AND i.iteration=d.iteration"
                    if has_cad_iterations else ""
                )
                iteration_hash = "i.sha256" if has_cad_iterations else "NULL AS sha256"
                cad_documents = {
                    int(row["id"]): dict(row) for row in conn.execute(
                        f"SELECT d.id,d.revision,d.iteration,{iteration_hash} "
                        f"FROM cad_documents d {iteration_join} WHERE d.id IN ({marks})",
                        tuple(sorted(cad_ids)),
                    )
                }

            files_by_name = {}
            for row in rows:
                filename = os.path.basename(str(row["filename"] or ""))
                if not filename:
                    raise ValueError("A pending submission contains a file without a name.")
                key = filename.casefold()
                cad_id = int(row["cad_document_id"]) if row["cad_document_id"] is not None else None
                existing = files_by_name.get(key)
                if existing and existing["cad_document_id"] != cad_id:
                    raise ValueError("One staged filename resolves to multiple CAD Documents: " + filename)
                if existing:
                    continue
                folder = f"{str(row['title'] or '')}_{commit_id}"
                source = os.path.realpath(os.path.join(root, str(row["username"] or ""), folder, filename))
                try:
                    inside_root = os.path.commonpath((root, source)) == root
                except ValueError:
                    inside_root = False
                if not inside_root or not os.path.isfile(source):
                    raise ValueError("Pending submission file is missing or outside its commit folder: " + filename)
                digest = _digest(source)
                extension = os.path.splitext(filename)[1].lower()
                relative = os.path.join("_snapshots", "objects", digest[:2], digest + extension)
                target = os.path.join(root, relative)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                if os.path.exists(target):
                    if _digest(target) != digest:
                        raise ValueError("A content-addressed snapshot file failed its hash check.")
                else:
                    fd, temporary = tempfile.mkstemp(prefix=".snapshot-", dir=os.path.dirname(target))
                    os.close(fd)
                    try:
                        shutil.copyfile(source, temporary)
                        if _digest(temporary) != digest:
                            raise ValueError("The staged file changed while its snapshot was being created.")
                        try:
                            os.link(temporary, target)
                        except FileExistsError:
                            if _digest(target) != digest:
                                raise ValueError("A concurrent snapshot failed its content hash check.")
                        except OSError:
                            if not os.path.exists(target):
                                os.replace(temporary, target)
                        if os.path.exists(target) and _digest(target) != digest:
                            raise ValueError("The saved snapshot failed its content hash check.")
                    finally:
                        if os.path.exists(temporary):
                            os.remove(temporary)

                baseline = (baseline_by_cad_id or {}).get(cad_id, {}) if cad_id is not None else {}
                document = cad_documents.get(cad_id, {}) if cad_id is not None else {}
                files_by_name[key] = {
                    "filename": filename,
                    "cad_document_id": cad_id,
                    "blob_path": relative.replace("\\", "/"),
                    "sha256": digest,
                    "size_bytes": os.path.getsize(target),
                    "base_revision": str(baseline.get("revision") or document.get("revision") or ""),
                    "base_iteration": int(baseline.get("iteration") or document.get("iteration") or 0),
                    "base_sha256": str(baseline.get("sha256") or document.get("sha256") or "") or None,
                    "workspace_baseline_sha256": str(baseline.get("workspace_sha256") or "") or None,
                }

            manifest = {
                "schema": 1,
                "project_id": int(project_id),
                "commit_id": str(commit_id),
                "files": sorted(files_by_name.values(), key=lambda item: item["filename"].casefold()),
            }
            conn.execute("BEGIN IMMEDIATE")
            latest = conn.execute("""
                SELECT manifest_json FROM cad_commit_snapshots
                WHERE project_id=? AND commit_id=? ORDER BY generation DESC LIMIT 1
            """, (int(project_id), str(commit_id))).fetchone()
            prior_manifest = json.loads(latest["manifest_json"]) if latest else {}
            prior_structure = prior_manifest.get("structure")
            if prior_structure is not None:
                manifest["structure"] = prior_structure
            generation = self._next_generation(conn, project_id, commit_id)
            result = self._insert_generation(
                conn, project_id, commit_id, generation, manifest, created_by
            )
        return result

    def record_pending_structure(self, project_id, commit_id, created_by):
        """Seal the exact accumulated Creo relationship payload into a new generation."""
        with self._connection() as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("""
                SELECT payload_json,required_cad_document_ids_json
                FROM cad_pending_structure_changes
                WHERE project_id=? AND commit_id=? AND status='PENDING'
            """, (int(project_id), str(commit_id))).fetchone()
            if not row:
                raise ValueError("Pending Creo structure metadata was not saved.")
            latest = conn.execute("""
                SELECT manifest_json FROM cad_commit_snapshots
                WHERE project_id=? AND commit_id=? ORDER BY generation DESC LIMIT 1
            """, (int(project_id), str(commit_id))).fetchone()
            if not latest:
                raise ValueError("The submission has no frozen file snapshot.")
            manifest = json.loads(latest["manifest_json"])
            payload = json.loads(row["payload_json"])
            structure = {
                "payload": payload,
                "payload_sha256": hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest(),
                "files_sha256": _files_fingerprint(manifest.get("files") or []),
                "required_cad_document_ids": sorted(int(value) for value in
                    json.loads(row["required_cad_document_ids_json"])),
            }
            manifest["structure"] = structure
            generation = self._next_generation(conn, project_id, commit_id)
            return self._insert_generation(
                conn, project_id, commit_id, generation, manifest, created_by
            )

    def verify_pending_snapshot(self, commits_root, project_id, commit_id, commit_rows):
        """Return immutable file paths after verifying manifest, rows, blobs, and baselines."""
        root = os.path.realpath(commits_root)
        with self._connection() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("""
                SELECT manifest_json,manifest_sha256 FROM cad_commit_snapshots
                WHERE project_id=? AND commit_id=? ORDER BY generation DESC LIMIT 1
            """, (int(project_id), str(commit_id))).fetchone()
            if not row:
                raise ValueError(
                    f"Submission {commit_id} predates frozen snapshots. Re-stage it before approval."
                )
            encoded = str(row["manifest_json"] or "")
            digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
            if digest != str(row["manifest_sha256"] or ""):
                raise ValueError("The saved submission manifest failed its integrity check.")
            manifest = json.loads(encoded)
            if int(manifest.get("project_id") or 0) != int(project_id) or str(manifest.get("commit_id") or "") != str(commit_id):
                raise ValueError("The submission snapshot belongs to a different project or commit.")
            expected = {}
            for item in manifest.get("files") or []:
                name = os.path.basename(str(item.get("filename") or ""))
                if not name or name.casefold() in expected:
                    raise ValueError("The submission manifest has a missing or duplicate filename.")
                expected[name.casefold()] = item
            row_names = {os.path.basename(str(item.get("filename") or "")).casefold()
                         for item in commit_rows}
            if row_names != set(expected):
                raise ValueError("Pending file rows no longer match the frozen submission snapshot.")

            result = {}
            current_cad_ids = {int(item["cad_document_id"]) for item in commit_rows
                               if item.get("cad_document_id") is not None}
            snapshot_cad_ids = {int(item["cad_document_id"]) for item in expected.values()
                                if item.get("cad_document_id") is not None}
            if current_cad_ids != snapshot_cad_ids:
                raise ValueError("Pending CAD identities no longer match the frozen submission snapshot.")
            for key, item in expected.items():
                relative = str(item.get("blob_path") or "")
                path = os.path.realpath(os.path.join(root, relative))
                try:
                    inside_root = os.path.commonpath((root, path)) == root
                except ValueError:
                    inside_root = False
                if not inside_root or not os.path.isfile(path):
                    raise ValueError("A frozen file is missing from its snapshot: " + item["filename"])
                if os.path.getsize(path) != int(item.get("size_bytes") or -1) or _digest(path) != str(item.get("sha256") or ""):
                    raise ValueError("A frozen file changed after submission: " + item["filename"])
                cad_id = item.get("cad_document_id")
                if cad_id is not None:
                    has_cad_iterations = conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cad_document_iterations'"
                    ).fetchone() is not None
                    iteration_join = (
                        "LEFT JOIN cad_document_iterations i ON i.cad_document_id=d.id "
                        "AND i.revision=d.revision AND i.iteration=d.iteration"
                        if has_cad_iterations else ""
                    )
                    iteration_hash = "i.sha256" if has_cad_iterations else "NULL AS sha256"
                    current = conn.execute(
                        f"SELECT d.revision,d.iteration,{iteration_hash} "
                        f"FROM cad_documents d {iteration_join} WHERE d.id=? AND d.project_id=?",
                        (int(cad_id), int(project_id)),
                    ).fetchone()
                    if not current:
                        raise ValueError("A CAD Document in the frozen submission no longer exists.")
                    if (str(current["revision"] or "") != str(item.get("base_revision") or "")
                            or int(current["iteration"] or 0) != int(item.get("base_iteration") or 0)):
                        raise ValueError("CAD changed after the submission snapshot was created; refresh and resubmit.")
                    base_sha256 = str(item.get("base_sha256") or "").strip().casefold()
                    current_sha256 = str(current["sha256"] or "").strip().casefold()
                    if base_sha256 and current_sha256 and base_sha256 != current_sha256:
                        raise ValueError("Approved CAD bytes changed after the submission snapshot was created; refresh and resubmit.")
                result[key] = {"path": relative, "sha256": str(item.get("sha256") or "")}

            result["_snapshot_sha256"] = digest

            pending_structure = conn.execute("""
                SELECT payload_json,required_cad_document_ids_json
                FROM cad_pending_structure_changes WHERE project_id=? AND commit_id=? AND status='PENDING'
            """, (int(project_id), str(commit_id))).fetchone()
            frozen_structure = manifest.get("structure")
            if pending_structure:
                payload = json.loads(pending_structure["payload_json"])
                payload_hash = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
                required_ids = sorted(int(value) for value in json.loads(
                    pending_structure["required_cad_document_ids_json"]
                ))
                if (not frozen_structure
                        or frozen_structure.get("payload_sha256") != payload_hash
                        or frozen_structure.get("files_sha256") != _files_fingerprint(
                            manifest.get("files") or []
                        )
                        or frozen_structure.get("required_cad_document_ids") != required_ids):
                    raise ValueError("Creo structure metadata does not match the reviewed file snapshot.")
            elif frozen_structure:
                raise ValueError("Frozen Creo structure metadata is missing from its pending review record.")
        return result
