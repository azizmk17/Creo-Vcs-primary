"""Snapshot-based Nexus branches and release packages.

All edits live in branch workspaces until an explicit, reviewed master merge.
Release packages are independent, immutable snapshots, never branch origins.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid
import zipfile

from config import DB_NAME
from core.repositories.nexus_history_repository import NexusHistoryRepository
from core.repositories.permission_repository import PermissionRepository
from core.services.master_work_service import MasterWorkService


ITEM_FIELDS = ("name", "part_number", "drawing_number", "aes_number", "material", "weight", "notes")
NATIVE = re.compile(r"^(.+\.(?:prt|asm|drw))(?:\.(\d+))?$", re.I)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def logical_name(filename):
    name = Path(str(filename)).name
    match = NATIVE.fullmatch(name)
    return match[1].casefold() if match else name.casefold()


def local_timestamp(value, *, naive_is_utc=False):
    """Normalize timestamps to local time, honoring the source's legacy convention."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None and naive_is_utc:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone()
    except (TypeError, ValueError, OverflowError):
        return None


class NexusHistoryService:
    def __init__(self, db_name=DB_NAME, *, user_id, project_id):
        self.repo = NexusHistoryRepository(db_name)
        self.user_id, self.project_id = int(user_id), int(project_id)

    def can(self, permission):
        return PermissionRepository(self.repo.db_name).user_has_permission(
            self.user_id, permission, self.project_id
        )

    def _require(self, permission):
        if not self.can(permission):
            raise PermissionError("You do not have permission to " + permission.replace("_", " ") + ".")
        with self.repo.connection() as conn:
            if self._project(conn).get("is_readonly"):
                raise ValueError("The selected project version is read-only.")

    def _project(self, conn):
        row = conn.execute("SELECT * FROM projects WHERE id=?", (self.project_id,)).fetchone()
        if not row:
            raise ValueError("The active project was not found.")
        if "user_projects" in self.repo.tables(conn) and not conn.execute(
            "SELECT 1 FROM user_projects WHERE user_id=? AND project_id=?",
            (self.user_id, self.project_id),
        ).fetchone() and not self.can("admin_panel"):
            raise PermissionError("The selected project is not assigned to this user.")
        return dict(row)

    def _root(self, conn):
        project = self._project(conn)
        value = str(project.get("working_directory") or "").strip()
        if not value:
            raise ValueError("Configure the project's working directory first.")
        root = Path(value).expanduser().resolve()
        if not root.is_dir():
            raise ValueError("The project working directory is unavailable.")
        return root

    def _item(self, conn, item_id):
        row = conn.execute("SELECT * FROM bom WHERE id=? AND project_id=?",
                           (int(item_id), self.project_id)).fetchone()
        if not row:
            raise ValueError("The item was not found in the selected project.")
        return dict(row)

    def _branch_access(self, conn, branch_id, *, editable=False):
        self._project(conn)
        branch = self.repo.branch(conn, branch_id, self.project_id)
        if editable:
            self._require("commit")
            if branch["state"] != "Active":
                raise ValueError("Resume the branch before checking in changes.")
        return branch

    @staticmethod
    def _safe_path(root, relative):
        path = (root / str(relative)).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError:
            raise ValueError("A snapshot path points outside the project storage.")
        return path

    @contextmanager
    def _branch_lock(self, root, branch_id):
        folder = root / ".nexus" / "branches" / str(branch_id)
        folder.mkdir(parents=True, exist_ok=True)
        lock = folder / ".operation.lock"
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise ValueError("Another operation is updating this branch. Retry when it finishes.")
        os.close(descriptor)
        try:
            yield
        finally:
            lock.unlink(missing_ok=True)

    @staticmethod
    def _verified_manifest(row):
        encoded = str(row["manifest_json"])
        if hashlib.sha256(encoded.encode("utf-8")).hexdigest() != row["manifest_sha256"]:
            raise ValueError("The frozen snapshot manifest failed its integrity check.")
        return json.loads(encoded)

    def _snapshot(self, conn, snapshot_id):
        row = conn.execute("""
            SELECT s.* FROM nexus_branch_snapshots s JOIN nexus_branches b ON b.id=s.branch_id
            WHERE s.id=? AND b.project_id=?
        """, (str(snapshot_id), self.project_id)).fetchone()
        if not row:
            raise ValueError("The branch snapshot was not found in this project.")
        return self._verified_manifest(row)

    def _freeze(self, root, entry):
        source = Path(entry["path"])
        if not source.is_file():
            raise ValueError("Snapshot source is missing: " + str(source))
        digest = file_hash(source)
        expected = str(entry.get("sha256") or "")
        if expected and expected != digest:
            raise ValueError("Snapshot source has changed: " + source.name)
        filename = re.sub(r"[^A-Za-z0-9._ -]", "_", source.name) or "file"
        relative = Path(".nexus/history/blobs") / digest[:2] / digest / filename
        destination = self._safe_path(root, relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            fd, temporary = tempfile.mkstemp(prefix=".freeze-", dir=destination.parent)
            os.close(fd)
            try:
                shutil.copyfile(source, temporary)
                if file_hash(temporary) != digest:
                    raise ValueError("A file changed while the snapshot was being created.")
                os.replace(temporary, destination)
            finally:
                Path(temporary).unlink(missing_ok=True)
        if file_hash(destination) != digest:
            raise ValueError("A frozen file failed its integrity check: " + filename)
        result = {k: v for k, v in entry.items() if k != "path"}
        result.update(blob_path=relative.as_posix(), sha256=digest, size_bytes=source.stat().st_size)
        return result

    def _blob(self, root, entry):
        path = self._safe_path(root, entry["blob_path"])
        if not path.is_file() or file_hash(path) != entry["sha256"]:
            raise ValueError("A frozen file is missing or has changed: " + entry["filename"])
        return path

    @staticmethod
    def _next_native_iteration(entry):
        """Return the next Creo iteration name after the current master file."""
        filename = Path(str(entry.get("filename") or "")).name
        match = NATIVE.fullmatch(filename)
        if not match:
            return filename
        return f"{match[1]}.{int(match[2] or 0) + 1}"

    @staticmethod
    def _archive_merged_branch(root, branch_id, branch_name):
        """Move the disposable branch workspace to a user-managed archive folder."""
        source = root / ".nexus" / "branches" / str(branch_id)
        if not source.exists():
            return ""
        safe_name = re.sub(r"[^A-Za-z0-9._ -]", "_", str(branch_name)).strip(" .") or "branch"
        archive_root = root / "Merged Branch Archives"
        archive_root.mkdir(parents=True, exist_ok=True)
        destination = archive_root / f"{safe_name}__{str(branch_id)[:8]}"
        if destination.exists():
            destination = archive_root / f"{safe_name}__{str(branch_id)[:8]}_{uuid.uuid4().hex[:6]}"
        source.rename(destination)
        return str(destination)

    def _scope(self, conn, item_ids, include_children):
        tables = self.repo.tables(conn)
        ids, frontier = set(), set(map(int, item_ids))
        while frontier:
            for item_id in frontier:
                self._item(conn, item_id)
            ids.update(frontier)
            marks = ",".join("?" for _ in frontier)
            related = set()
            # One native CAD file may drive several Items. They must share one
            # branch base and one atomic merge rather than losing associations.
            if "cad_item_associations" in tables:
                related.update(int(row[0]) for row in conn.execute(f"""
                    SELECT DISTINCT b.item_id FROM cad_item_associations a
                    JOIN cad_item_associations b ON b.cad_document_id=a.cad_document_id
                    WHERE a.item_id IN ({marks}) AND a.active=1 AND b.active=1 AND b.project_id=?
                """, (*frontier, self.project_id)))
            related.update(int(row[0]) for row in conn.execute(f"""
                SELECT DISTINCT b.id FROM bom a JOIN bom b
                ON (lower(b.filename)=lower(a.filename) AND trim(COALESCE(a.filename,''))<>'')
                OR (lower(b.base_file_name)=lower(a.base_file_name)
                    AND trim(COALESCE(a.base_file_name,''))<>'' AND lower(b.type)=lower(a.type))
                WHERE a.id IN ({marks}) AND b.project_id=?
            """, (*frontier, self.project_id)))
            if include_children and "bom_children" in tables:
                related.update(int(row[0]) for row in conn.execute(
                    f"SELECT child_id FROM bom_children WHERE parent_id IN ({marks})", tuple(frontier)
                ))
            if include_children and "item_usages" in tables:
                related.update(int(row[0]) for row in conn.execute(
                    f"SELECT child_item_id FROM item_usages WHERE parent_item_id IN ({marks})",
                    tuple(frontier),
                ))
            if include_children and {"cad_item_associations", "cad_document_members"} <= tables:
                related.update(int(row[0]) for row in conn.execute(f"""
                    SELECT DISTINCT child.item_id FROM cad_item_associations owner
                    JOIN cad_document_members m ON m.parent_cad_document_id=owner.cad_document_id
                    JOIN cad_item_associations child ON child.cad_document_id=m.child_cad_document_id
                    WHERE owner.item_id IN ({marks}) AND owner.active=1 AND child.active=1
                    AND child.project_id=?
                """, (*frontier, self.project_id)))
            frontier = related - ids
        if not ids:
            raise ValueError("Select at least one item.")
        return sorted(ids)

    def _source_path(self, conn, root, entry):
        relative = str(entry.get("vault_rel_path") or "")
        if relative:
            # Old projects may store the exact file in the project family root.
            roots = [root]
            if entry.get("root_project_id"):
                rows = conn.execute(
                    "SELECT working_directory FROM projects WHERE id=? OR "
                    "(root_project_id=? AND version_label=?)",
                    (entry["root_project_id"], entry["root_project_id"],
                     entry.get("project_version_label") or ""),
                ).fetchall()
                roots.extend(Path(row[0]).resolve() for row in rows if row[0])
            for candidate_root in roots:
                path = self._safe_path(candidate_root, relative)
                if path.is_file():
                    return path
            raise ValueError("A referenced file is missing: " + str(root / relative))
        filename = str(entry.get("filename") or "")
        path = self._safe_path(root, filename)
        if not path.is_file():
            raise ValueError("A referenced file is missing: " + filename)
        return path

    def _capture(self, conn, item_ids, *, freeze=False):
        root, tables = self._root(conn), self.repo.tables(conn)
        project = self._project(conn)
        master = MasterWorkService.read_manifest(root).get("files", {})
        result = {"schema": 1, "project_id": self.project_id,
                  "cad_revision": str(project.get("version_label") or ""),
                  "items": {}, "files": {}}
        for item_id in sorted(set(map(int, item_ids))):
            item = self._item(conn, item_id)
            attrs = {name: item.get(name) for name in ITEM_FIELDS if name in item}
            structure = {}
            if "bom_children" in tables:
                for row in conn.execute("SELECT * FROM bom_children WHERE parent_id=?", (item_id,)):
                    values = dict(row)
                    structure[str(values["id"])] = {
                        key: values[key] for key in
                        ("id", "child_id", "quantity", "sort_order", "ebom_behavior") if key in values
                    }
            if "item_usages" in tables:
                for row in conn.execute("SELECT * FROM item_usages WHERE parent_item_id=?", (item_id,)):
                    usage = dict(row)
                    legacy = str(usage.get("legacy_usage_id") or "")
                    if legacy in structure:
                        structure[legacy].update(item_usage_id=usage["id"], source=usage.get("source"))
                    else:
                        structure["pdm:" + str(usage["id"])] = {
                            "id": usage["id"], "child_id": usage["child_item_id"], "quantity": usage["quantity"],
                            "sort_order": usage.get("sort_order") or 0, "item_usage_id": usage["id"],
                            "source": usage.get("source") or "MANUAL",
                        }
            result["items"][str(item_id)] = {"attributes": attrs, "structure": structure,
                "revision": item.get("revision"), "iteration_id": item.get("current_iteration_id")}
            entries = []
            iteration_id = item.get("current_iteration_id")
            if iteration_id and "bom_iteration_files" in tables:
                entries = [dict(row) for row in conn.execute(
                    "SELECT * FROM bom_iteration_files WHERE iteration_id=?", (iteration_id,)
                )]
            # Resolve active document versions without changing any PDF/STEP acknowledgements.
            if {"part_files", "part_file_versions"} <= tables:
                versions = conn.execute("""
                    SELECT v.*,p.id AS part_file_id,p.file_type,p.file_role
                    FROM part_files p JOIN part_file_versions v ON v.id=p.active_version_id
                    WHERE p.part_id=?
                """, (item_id,)).fetchall()
                entries = [e for e in entries if not e.get("part_file_id")]
                for row in versions:
                    value = dict(row)
                    if value.get("deleted_at"):
                        continue
                    entries.append({**value, "part_file_version_id": value["id"],
                        "filename": value["original_filename"], "file_revision": value.get("revision") or "",
                        "file_role": value.get("file_role") or "document"})
            for field, role in (("filename", "native_cad"), ("drawing", "drawing")):
                if item.get(field) and not any(e.get("file_role") == role for e in entries):
                    entries.append({"filename": item[field], "file_role": role})
            if {"cad_item_associations", "cad_documents"} <= tables:
                for row in conn.execute("""
                    SELECT d.*,a.association_type FROM cad_item_associations a
                    JOIN cad_documents d ON d.id=a.cad_document_id
                    WHERE a.item_id=? AND a.active=1 AND d.project_id=?
                    ORDER BY CASE WHEN a.association_type='OWNER' THEN 0 ELSE 1 END,d.id
                """, (item_id, self.project_id)):
                    document = dict(row)
                    key = logical_name(document["file_name"])
                    if any(logical_name(e.get("filename") or "") == key for e in entries):
                        continue
                    filename = document.get("latest_creo_file_name") or document["file_name"]
                    if not (root / filename).is_file() and (root / (
                        document["file_name"] + "." + str(document.get("latest_creo_file_version") or document.get("iteration") or 1)
                    )).is_file():
                        filename = document["file_name"] + "." + str(
                            document.get("latest_creo_file_version") or document.get("iteration") or 1)
                    if document["association_type"] == "OWNER" and not key.endswith(".drw"):
                        role = "native_cad"
                    elif key.endswith(".drw") and not any(e.get("file_role") == "drawing" for e in entries):
                        role = "drawing"
                    else:
                        role = "cad_dependency"
                    entries.append({"filename": filename, "cad_document_id": document["id"], "file_role": role})
            for field, kind in (("pdf_path", "PDF"), ("step_path", "STEP")):
                if item.get(field) and not any(str(e.get("file_type")).upper() == kind for e in entries):
                    value = str(item[field])
                    path = Path(value) if Path(value).is_absolute() else root / value
                    entries.append({"filename": path.name, "path": str(path), "file_type": kind,
                                    "file_role": "generated_" + kind.lower()})
            for raw in entries:
                name = Path(str(raw.get("filename") or "")).name
                if not name:
                    continue
                native = bool(NATIVE.fullmatch(name))
                key = logical_name(name) if native else str(item_id) + "/" + str(
                    raw.get("part_file_id") or raw.get("binding_key") or name.casefold()
                )
                record = master.get(logical_name(name)) if native else None
                if record:
                    source = self._safe_path(root, "master/" + logical_name(name))
                    expected = str(record.get("sha256") or "")
                    name = Path(record.get("source_filename") or name).name
                    if logical_name(name) != key:
                        raise ValueError("The master manifest contains an inconsistent native filename.")
                else:
                    source = Path(raw["path"]) if raw.get("path") else self._source_path(conn, root, raw)
                    expected = str(raw.get("sha256") or "")
                if not source.is_file():
                    raise ValueError("A snapshot source is missing: " + str(source))
                digest = file_hash(source)
                if expected and expected != digest:
                    raise ValueError("A referenced file failed its hash check: " + name)
                prior = result["files"].get(key)
                if prior:
                    if prior["sha256"] != digest:
                        raise ValueError("Different files use the same Creo name: " + name)
                    prior["item_ids"] = sorted(set(prior["item_ids"] + [item_id]))
                    continue
                cad_id = record.get("cad_document_id") if record else raw.get("cad_document_id")
                if native and cad_id is None and {"cad_item_associations", "cad_documents"} <= tables:
                    row = conn.execute("""
                        SELECT d.id FROM cad_item_associations a JOIN cad_documents d ON d.id=a.cad_document_id
                        WHERE a.item_id=? AND a.active=1 AND lower(d.file_name)=? LIMIT 1
                    """, (item_id, logical_name(name))).fetchone()
                    cad_id = row[0] if row else None
                entry = {"key": key, "filename": name, "role": raw.get("file_role") or "document",
                    "file_type": raw.get("file_type") or Path(logical_name(name)).suffix.lstrip(".").upper(),
                    "file_revision": str(raw.get("file_revision") or ""), "item_ids": [item_id],
                    "cad_document_id": cad_id, "path": str(source), "sha256": digest,
                    "size_bytes": source.stat().st_size, "source_commit_id": (record or raw).get("source_commit_id"),
                    "part_file_version_id": raw.get("part_file_version_id"),
                    "part_file_id": raw.get("part_file_id")}
                result["files"][key] = self._freeze(root, entry) if freeze else entry
        # Capture registered CAD-only dependencies too. They are usable in Creo,
        # but require Item associations before they may be changed in a branch.
        if {"cad_document_members", "cad_documents"} <= tables:
            seen = {int(e["cad_document_id"]) for e in result["files"].values() if e.get("cad_document_id")}
            frontier = set(seen)
            while frontier:
                marks = ",".join("?" for _ in frontier)
                children = [dict(row) for row in conn.execute(f"""
                    SELECT DISTINCT d.* FROM cad_document_members m
                    JOIN cad_documents d ON d.id=m.child_cad_document_id
                    WHERE m.parent_cad_document_id IN ({marks}) AND d.project_id=?
                """, (*frontier, self.project_id))]
                frontier = set()
                for document in children:
                    if document["id"] in seen:
                        continue
                    seen.add(document["id"])
                    frontier.add(document["id"])
                    key = logical_name(document["file_name"])
                    if key in result["files"]:
                        continue
                    record = master.get(key)
                    filename = document.get("latest_creo_file_name") or document["file_name"]
                    if record:
                        source = self._safe_path(root, "master/" + key)
                    else:
                        source = self._safe_path(root, filename)
                        if not source.is_file():
                            filename = document["file_name"] + "." + str(
                                document.get("latest_creo_file_version") or document.get("iteration") or 1)
                            source = self._safe_path(root, filename)
                    if not source.is_file():
                        raise ValueError("A referenced CAD dependency is missing: " + document["file_name"])
                    actual = file_hash(source)
                    if record and record.get("sha256") and actual != record["sha256"]:
                        raise ValueError("A referenced CAD dependency failed its hash check.")
                    if record:
                        filename = Path(record.get("source_filename") or filename).name
                    entry = {"key": key, "filename": filename, "role": "cad_dependency",
                        "file_type": Path(key).suffix.lstrip(".").upper(), "file_revision": "",
                        "item_ids": [], "cad_document_id": document["id"], "readonly_dependency": True,
                        "path": str(source), "sha256": actual,
                        "size_bytes": source.stat().st_size}
                    result["files"][key] = self._freeze(root, entry) if freeze else entry
        return result

    @staticmethod
    def _master_token(manifest):
        return fingerprint({"items": manifest["items"],
                            "files": {k: v["sha256"] for k, v in manifest["files"].items()}})

    def _save_snapshot(self, conn, branch_id, parent_id, manifest, note, stamp):
        snapshot_id, encoded = uuid.uuid4().hex, canonical(manifest)
        conn.execute("""
            INSERT INTO nexus_branch_snapshots(
                id,branch_id,parent_id,manifest_json,manifest_sha256,note,actor_id,created_at
            ) VALUES(?,?,?,?,?,?,?,?)
        """, (snapshot_id, branch_id, parent_id, encoded,
              hashlib.sha256(encoded.encode("utf-8")).hexdigest(), note, self.user_id, stamp))
        conn.execute("UPDATE nexus_branches SET head_snapshot_id=?,updated_at=? WHERE id=?",
                     (snapshot_id, stamp, branch_id))
        return snapshot_id

    def create_branch(self, item_ids, name, note="", include_children=True):
        self._require("commit")
        name = str(name).strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._ -]{0,79}", name) or name.casefold() == "master":
            raise ValueError("Use a branch name of 1–80 letters, digits, spaces, dots, hyphens or underscores.")
        with self.repo.connection() as conn:
            ids = self._scope(conn, item_ids, include_children)
            manifest = self._capture(conn, ids, freeze=True)
            if not any(NATIVE.fullmatch(f["filename"]) for f in manifest["files"].values()):
                raise ValueError("Associate a native CAD file before creating a development branch.")
            branch_id, stamp = uuid.uuid4().hex, now()
            # Recheck the exact base under the DB and master-publication locks.
            root = self._root(conn)
            root.joinpath("master").mkdir(exist_ok=True)
            lock = root / "master/.publish.lock"
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                raise ValueError("Master is being published. Retry branch creation after it finishes.")
            os.close(fd)
            try:
                conn.execute("BEGIN IMMEDIATE")
                if self._master_token(self._capture(conn, ids)) != self._master_token(manifest):
                    raise ValueError("Master changed while the branch base was captured. Retry.")
                conn.execute("""
                    INSERT INTO nexus_branches(id,project_id,name,owner_id,created_at,updated_at)
                    VALUES(?,?,?,?,?,?)
                """, (branch_id, self.project_id, name, self.user_id, stamp, stamp))
                conn.executemany("INSERT INTO nexus_branch_items(branch_id,bom_id) VALUES(?,?)",
                                 [(branch_id, item_id) for item_id in ids])
                snapshot_id = self._save_snapshot(conn, branch_id, None, manifest, note, stamp)
                self.repo.event(conn, self.project_id, ids, self.user_id, stamp, "BRANCH_CREATED",
                    "Branch created · " + name, note, branch_id, snapshot_id,
                    payload={"base_master_token": self._master_token(manifest)})
                conn.commit()
            finally:
                lock.unlink(missing_ok=True)
            return branch_id

    def branch_details(self, branch_id):
        with self.repo.connection() as conn:
            branch = self._branch_access(conn, branch_id)
            branch["manifest"] = self._snapshot(conn, branch["head_snapshot_id"])
            return branch

    def active_branch_map(self, item_ids=None):
        """Return this user's active branch name and ID by item; absent means Master."""
        with self.repo.connection() as conn:
            self._project(conn)
            params = [self.user_id, self.project_id]
            filter_sql = ""
            ids = sorted({int(value) for value in (item_ids or [])})
            if ids:
                filter_sql = " AND a.bom_id IN (" + ",".join("?" for _ in ids) + ")"
                params.extend(ids)
            return {int(row["bom_id"]): {
                "branch_id": str(row["branch_id"]), "name": str(row["name"]),
                "state": str(row["state"]),
            } for row in conn.execute("""
                SELECT a.bom_id,a.branch_id,b.name,b.state FROM nexus_active_branches a
                JOIN nexus_branches b ON b.id=a.branch_id
                WHERE a.user_id=? AND a.project_id=?
            """ + filter_sql, params)}

    def branch_choices(self, item_ids=None):
        """Active branches available for the given item set; empty means all project branches."""
        with self.repo.connection() as conn:
            self._project(conn)
            ids = sorted({int(value) for value in (item_ids or [])})
            if ids:
                marks = ",".join("?" for _ in ids)
                rows = conn.execute(f"""
                    SELECT b.id,b.name,b.state,b.owner_id,COUNT(DISTINCT i.bom_id) AS matched
                    FROM nexus_branches b JOIN nexus_branch_items i ON i.branch_id=b.id
                    WHERE b.project_id=? AND b.state='Active' AND i.bom_id IN ({marks})
                    GROUP BY b.id HAVING matched=? ORDER BY b.name COLLATE NOCASE
                """, (self.project_id, *ids, len(ids))).fetchall()
            else:
                rows = conn.execute("""
                    SELECT b.id,b.name,b.state,b.owner_id FROM nexus_branches b
                    WHERE b.project_id=? AND b.state='Active' ORDER BY b.name COLLATE NOCASE
                """, (self.project_id,)).fetchall()
            return [dict(row) for row in rows]

    def activate_branch(self, item_ids, branch_id=None):
        """Set a per-user active branch for every selected Item; None selects Master."""
        self._require("commit")
        ids = sorted({int(value) for value in item_ids})
        if not ids:
            raise ValueError("Select one or more BOM items first.")
        with self.repo.connection() as conn:
            for item_id in ids:
                self._item(conn, item_id)
            if branch_id:
                branch = self._branch_access(conn, branch_id)
                if branch["state"] != "Active":
                    raise ValueError("Resume the branch before activating it.")
                marks = ",".join("?" for _ in ids)
                found = {int(row[0]) for row in conn.execute(
                    f"SELECT bom_id FROM nexus_branch_items WHERE branch_id=? AND bom_id IN ({marks})",
                    (str(branch_id), *ids),
                )}
                if found != set(ids):
                    raise ValueError("All selected Items must belong to the chosen branch.")
            conn.execute("BEGIN IMMEDIATE")
            if branch_id:
                current = self._branch_access(conn, branch_id)
                if current["state"] != "Active":
                    raise ValueError("Resume the branch before activating it.")
            for item_id in ids:
                if branch_id:
                    conn.execute("""
                        INSERT INTO nexus_active_branches(user_id,project_id,bom_id,branch_id,updated_at)
                        VALUES(?,?,?,?,?) ON CONFLICT(user_id,project_id,bom_id)
                        DO UPDATE SET branch_id=excluded.branch_id,updated_at=excluded.updated_at
                    """, (self.user_id, self.project_id, item_id, str(branch_id), now()))
                else:
                    conn.execute("DELETE FROM nexus_active_branches WHERE user_id=? AND project_id=? AND bom_id=?",
                                 (self.user_id, self.project_id, item_id))

    def check_in_from_commit(self, branch_id, expected_head, note, files, source_commit_id):
        """Copy a verified submission into this user's branch workspace and snapshot it."""
        details = self.branch_details(branch_id)
        if details["head_snapshot_id"] != expected_head:
            raise ValueError("The branch advanced. Refresh before pushing this commit.")
        folder = Path(self.workspace(branch_id))
        known = list(details["manifest"]["files"].values())
        plans, targets_seen = [], set()
        for source_entry in files:
            source = Path(source_entry["path"]).resolve()
            filename = Path(str(source_entry.get("filename") or source.name)).name
            if not source.is_file() or file_hash(source) != source_entry.get("sha256"):
                raise ValueError("A checked-in file is missing or changed: " + filename)
            logical = logical_name(filename)
            matches = [entry for entry in known if logical_name(entry["filename"]) == logical]
            if not matches:
                raise ValueError("This commit contains a file not associated with the branch: " + filename)
            targets = {str(self._workspace_target(folder, entry)) for entry in matches}
            if len(targets) != 1:
                raise ValueError("The file maps to multiple branch Items: " + filename)
            target = Path(next(iter(targets)))
            native_match = NATIVE.fullmatch(filename)
            if native_match:
                existing_iterations = [int((NATIVE.fullmatch(entry["filename"])[2] or 0))
                                       for entry in matches if NATIVE.fullmatch(entry["filename"])]
                # A branch push creates a new iteration from the branch head;
                # the incoming filename's master iteration does not set its number.
                iteration = max(existing_iterations, default=0) + 1
                branch_filename = native_match[1] + (f".{iteration}" if iteration else "")
                target = folder / branch_filename
            normalized_target = os.path.normcase(str(target.resolve()))
            if normalized_target in targets_seen:
                raise ValueError("The commit contains multiple files for the same branch destination: " + filename)
            targets_seen.add(normalized_target)
            plans.append((source, filename, target, source_entry["sha256"]))
        installed = []
        for source, filename, target, expected_hash in plans:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
            try:
                shutil.copyfile(source, temporary)
                if file_hash(temporary) != expected_hash:
                    raise ValueError("The commit file changed while being copied: " + filename)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
            installed.append(filename)
        if not installed:
            raise ValueError("The commit contains no files for this branch.")
        snapshot_id = self.check_in(branch_id, expected_head, str(note).strip(),
                                    source_commit_id=str(source_commit_id),
                                    source_commit_title=str(note).strip())
        # Keep one current Creo file per logical name in this user's branch
        # workspace. The snapshot retains the exact iteration filename and bytes.
        native_targets = {logical_name(target.name): target for _, filename, target, _ in plans
                          if NATIVE.fullmatch(filename)}
        for path in folder.iterdir():
            if (path.is_file() and NATIVE.fullmatch(path.name)
                    and logical_name(path.name) in native_targets
                    and path.resolve() != native_targets[logical_name(path.name)].resolve()):
                path.unlink(missing_ok=True)
        return snapshot_id

    def workspace(self, branch_id, *, refresh=False):
        with self.repo.connection() as conn:
            branch = self._branch_access(conn, branch_id, editable=True)
            root = self._root(conn)
            manifest = self._snapshot(conn, branch["head_snapshot_id"])
            folder = root / ".nexus/branches" / branch_id / "workspaces" / str(self.user_id)
            with self._branch_lock(root, branch_id):
                marker = folder / ".nexus-workspace.json"
                if folder.exists() and any(folder.iterdir()):
                    if refresh:
                        saved = folder.with_name(folder.name + "-saved-" + uuid.uuid4().hex[:8])
                        folder.rename(saved)
                        folder.mkdir()
                    else:
                        if not marker.is_file():
                            raise ValueError("The branch folder contains untracked work. Refresh its workspace to preserve those files and open a clean copy.")
                        data = json.loads(marker.read_text(encoding="utf-8"))
                        if data.get("head_snapshot_id") != branch["head_snapshot_id"]:
                            raise ValueError("The branch advanced. Refresh its workspace to preserve your files and open the latest snapshot.")
                        return str(folder)
                folder.mkdir(parents=True, exist_ok=True)
                try:
                    for entry in manifest["files"].values():
                        target = self._workspace_target(folder, entry)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(self._blob(root, entry), target)
                    marker.write_text(canonical({"branch_id": branch_id,
                        "head_snapshot_id": branch["head_snapshot_id"]}), encoding="utf-8")
                except Exception as exc:
                    raise ValueError("The partial workspace has been preserved. Use Refresh Workspace "
                                     "to keep those files and prepare a clean copy. " + str(exc)) from exc
            return str(folder)

    @staticmethod
    def _workspace_target(folder, entry):
        if NATIVE.fullmatch(entry["filename"]):
            return folder / logical_name(entry["filename"])
        safe = re.sub(r"[^A-Za-z0-9._ -]", "_", entry["filename"])
        return folder / "documents" / hashlib.sha256(entry["key"].encode()).hexdigest()[:12] / safe

    def check_in(self, branch_id, expected_head, note, *, attributes=None, quantities=None,
                 source_commit_id=None, source_commit_title=None):
        if not str(note).strip():
            raise ValueError("Describe the branch changes before checking in.")
        with self.repo.connection() as conn:
            branch = self._branch_access(conn, branch_id, editable=True)
            root = self._root(conn)
            with self._branch_lock(root, branch_id):
                if branch["head_snapshot_id"] != expected_head:
                    raise ValueError("The branch advanced. Refresh before checking in.")
                manifest = self._snapshot(conn, expected_head)
                old = canonical(manifest)
                folder = root / ".nexus/branches" / branch_id / "workspaces" / str(self.user_id)
                marker = folder / ".nexus-workspace.json"
                if not marker.is_file() or json.loads(marker.read_text()).get("head_snapshot_id") != expected_head:
                    raise ValueError("Open the current branch workspace before checking in.")
                names = list(folder.iterdir())
                known = {logical_name(e["filename"]) for e in manifest["files"].values() if NATIVE.fullmatch(e["filename"])}
                unknown = [p.name for p in names if p.is_file() and NATIVE.fullmatch(p.name) and logical_name(p.name) not in known]
                if unknown:
                    raise ValueError("New CAD names need item associations before merging: " + ", ".join(unknown))
                for key, entry in list(manifest["files"].items()):
                    target = self._workspace_target(folder, entry)
                    if NATIVE.fullmatch(entry["filename"]):
                        candidates = [p for p in names if p.is_file() and NATIVE.fullmatch(p.name)
                                      and logical_name(p.name) == logical_name(entry["filename"])]
                        target = max(candidates, key=lambda p: int((NATIVE.fullmatch(p.name)[2] or 0)),
                                     default=target)
                    if not target.is_file():
                        raise ValueError("A workspace file is missing; check-in will not silently delete it: " + target.name)
                    if file_hash(target) != entry["sha256"]:
                        if entry.get("readonly_dependency"):
                            raise ValueError("This CAD dependency needs an Item association before branch editing: " + target.name)
                        manifest["files"][key] = self._freeze(root, {
                            **entry, "filename": target.name, "path": str(target), "sha256": "",
                        })
                for item_id, changes in (attributes or {}).items():
                    if str(item_id) not in manifest["items"]:
                        raise ValueError("Cannot edit an item outside this branch.")
                    if set(changes) - set(ITEM_FIELDS):
                        raise ValueError("Unsupported branch item attribute.")
                    if "name" in changes and not str(changes["name"]).strip():
                        raise ValueError("The branch item name cannot be empty.")
                    manifest["items"][str(item_id)]["attributes"].update(changes)
                for item_id, changes in (quantities or {}).items():
                    structure = manifest["items"][str(item_id)]["structure"]
                    for usage_id, quantity in changes.items():
                        value = float(quantity)
                        if str(usage_id) not in structure or not math.isfinite(value) or value < 1 or not value.is_integer():
                            raise ValueError("Branch quantities must be positive whole numbers for existing usages.")
                        usage = structure[str(usage_id)]
                        if str(usage.get("source") or "").upper() == "CAD" and value != usage["quantity"]:
                            raise ValueError("CAD-driven quantities must be changed through the CAD structure workflow.")
                        usage["quantity"] = int(value)
                if canonical(manifest) == old and not source_commit_id:
                    raise ValueError("No changes were detected in the branch workspace or item parameters.")
                stamp = now()
                conn.execute("BEGIN IMMEDIATE")
                latest = self._branch_access(conn, branch_id, editable=True)
                if latest["head_snapshot_id"] != expected_head:
                    raise ValueError("The branch advanced during check-in. Refresh and retry.")
                snapshot_id = self._save_snapshot(conn, branch_id, expected_head, manifest, str(note).strip(), stamp)
                event_type = "BRANCH_COMMIT" if source_commit_id else "BRANCH_CHECKIN"
                title = ("Commit · " + str(source_commit_title or branch["name"])) if source_commit_id else "Branch check-in · " + branch["name"]
                self.repo.event(conn, self.project_id, manifest["items"], self.user_id, stamp,
                    event_type, title, str(note).strip(), branch_id, snapshot_id,
                    payload={"source_commit_id": str(source_commit_id)} if source_commit_id else None)
                if source_commit_id:
                    if "nexus_branch_id" not in self.repo.columns(conn, "commits"):
                        raise ValueError("Database migration 51 is required before pushing commits to a branch.")
                    rows = conn.execute("SELECT id,status FROM commits WHERE project_id=? AND commit_id=?",
                                        (self.project_id, str(source_commit_id))).fetchall()
                    if not rows or any(str(row["status"]).casefold() not in {"pending", "validated"} for row in rows):
                        raise ValueError("Only a complete Pending or Validated commit can be pushed to a branch.")
                    conn.execute("UPDATE commits SET status='Branched',nexus_branch_id=? "
                                 "WHERE project_id=? AND commit_id=?",
                                 (str(branch_id), self.project_id, str(source_commit_id)))
                conn.commit()
                marker.write_text(canonical({"branch_id": branch_id, "head_snapshot_id": snapshot_id}), encoding="utf-8")
                return snapshot_id

    def set_branch_state(self, branch_id, state):
        if state not in {"Active", "Paused"}:
            raise ValueError("Choose Active or Paused.")
        with self.repo.connection() as conn:
            branch = self._branch_access(conn, branch_id)
            self._require("commit")
            if branch["owner_id"] != self.user_id and not self.can("merge"):
                raise PermissionError("Only the branch owner or Master/Admin can pause or resume it.")
            root = self._root(conn)
            with self._branch_lock(root, branch_id):
                conn.execute("BEGIN IMMEDIATE")
                branch = self._branch_access(conn, branch_id)
                if branch["state"] not in {"Active", "Paused"}:
                    raise ValueError("A merged branch cannot be resumed.")
                if state == branch["state"]:
                    return
                stamp = now()
                conn.execute("UPDATE nexus_branches SET state=?,updated_at=? WHERE id=?", (state, stamp, branch_id))
                ids = [r[0] for r in conn.execute("SELECT bom_id FROM nexus_branch_items WHERE branch_id=?", (branch_id,))]
                self.repo.event(conn, self.project_id, ids, self.user_id, stamp,
                    "BRANCH_RESUMED" if state == "Active" else "BRANCH_PAUSED",
                    ("Branch resumed · " if state == "Active" else "Branch paused · ") + branch["name"],
                    "The last checked-in snapshot is preserved. Unchecked local work is not published.",
                    branch_id, branch["head_snapshot_id"])

    def release_sources(self, item_id, branch_id=None):
        self._require("release_files")
        with self.repo.connection() as conn:
            if branch_id:
                branch = self._branch_access(conn, branch_id)
                if branch["state"] != "Active":
                    raise ValueError("Only an active branch can be used for a new release package.")
                manifest = self._snapshot(conn, branch["head_snapshot_id"])
                if str(item_id) not in manifest["items"]:
                    raise ValueError("The selected item is not part of this branch.")
                manifest["source_branch_id"] = branch_id
                manifest["source_snapshot_id"] = branch["head_snapshot_id"]
                manifest["files"] = {key: value for key, value in manifest["files"].items()
                                     if int(item_id) in value["item_ids"] or value.get("readonly_dependency")}
                manifest["items"] = {str(item_id): manifest["items"][str(item_id)]}
                return manifest
            return self._capture(conn, [item_id])

    def freeze_release(self, item_id, document_revision, note, *, expected=None, selected_keys=None,
                       source_snapshot_id=None):
        self._require("release_files")
        document_revision = str(document_revision).strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", document_revision):
            raise ValueError("Enter a document revision such as A010.")
        if not str(note).strip():
            raise ValueError("Enter the release purpose or note.")
        with self.repo.connection() as conn:
            branch_id = None
            if source_snapshot_id:
                current = self._snapshot(conn, source_snapshot_id)
                row = conn.execute("SELECT branch_id FROM nexus_branch_snapshots WHERE id=?", (source_snapshot_id,)).fetchone()
                branch_id = row[0]
                branch = self._branch_access(conn, branch_id)
                if branch["state"] != "Active":
                    raise ValueError("Only an active branch can be used for a new release package.")
                if str(item_id) not in current["items"]:
                    raise ValueError("The selected item is not in this branch snapshot.")
                current["items"] = {str(item_id): current["items"][str(item_id)]}
                current["files"] = {key: value for key, value in current["files"].items()
                                     if int(item_id) in value["item_ids"] or value.get("readonly_dependency")}
                current["source_branch_id"], current["source_snapshot_id"] = branch_id, source_snapshot_id
            else:
                current = self._capture(conn, [item_id])
            original_token = self._master_token(current)
            if expected and self._master_token(current) != expected:
                raise ValueError("The selected file versions changed. Review the release sources again.")
            keys = set(current["files"]) if selected_keys is None else set(selected_keys)
            if not keys or keys - set(current["files"]):
                raise ValueError("Select the exact available files for this release package.")
            root = self._root(conn)
            if source_snapshot_id:
                current["files"] = {k: v for k, v in current["files"].items() if k in keys}
                for entry in current["files"].values():
                    self._blob(root, entry)
            else:
                current["files"] = {k: self._freeze(root, v) for k, v in current["files"].items() if k in keys}
            release_id, stamp, encoded = uuid.uuid4().hex, now(), canonical(current)
            conn.execute("BEGIN IMMEDIATE")
            # File sources and metadata must still match after the potentially long copies.
            check = self._capture(conn, [item_id]) if not source_snapshot_id else None
            if check is not None and self._master_token(check) != original_token:
                raise ValueError("The item changed while the release package was being frozen. Retry.")
            conn.execute("""
                INSERT INTO nexus_releases(id,project_id,bom_id,cad_revision,document_revision,
                    note,manifest_json,manifest_sha256,actor_id,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)
            """, (release_id, self.project_id, int(item_id), current["cad_revision"], document_revision,
                  str(note).strip(), encoded, hashlib.sha256(encoded.encode()).hexdigest(), self.user_id, stamp))
            self.repo.event(conn, self.project_id, [item_id], self.user_id, stamp, "RELEASE_FROZEN",
                "Release · " + current["cad_revision"] + " / " + document_revision, str(note).strip(),
                branch_id=branch_id, snapshot_id=source_snapshot_id, release_id=release_id,
                payload={"cad_revision": current["cad_revision"],
                                              "document_revision": document_revision})
            return release_id

    def export_release(self, release_id, destination):
        with self.repo.connection() as conn:
            row = conn.execute("SELECT * FROM nexus_releases WHERE id=? AND project_id=?",
                               (release_id, self.project_id)).fetchone()
            if not row:
                raise ValueError("The release package was not found in this project.")
            manifest, root = self._verified_manifest(row), self._root(conn)
            destination = Path(destination).resolve()
            if destination.suffix.casefold() != ".zip":
                raise ValueError("Export a release package to a .zip file.")
            for protected in (".nexus", "master", "commits", "vault"):
                if destination.is_relative_to((root / protected).resolve()):
                    raise ValueError("Choose an export location outside managed project storage.")
            destination.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=".release-", suffix=".zip", dir=destination.parent)
            os.close(fd)
            try:
                export_manifest = json.loads(json.dumps(manifest))
                used_paths = {}
                package_paths = {}
                role_folders = {
                    "native_cad": "Native CAD", "drawing": "Drawings",
                    "pdf": "PDF", "step": "STEP",
                    "stp": "STEP", "cad_dependency": "CAD Dependencies",
                }
                for key, entry in export_manifest["files"].items():
                    role = str(entry.get("role") or "").strip().casefold()
                    if role in {"", "document", "attachment", "file"}:
                        file_type = str(entry.get("file_type") or
                                        Path(str(entry.get("filename") or "")).suffix.lstrip(".")).casefold()
                        role = file_type if file_type in {"pdf", "step", "stp"} else role
                    folder = role_folders.get(role, "Other Documents")
                    raw_name = Path(str(entry.get("filename") or "file").replace("\\", "/")).name
                    filename = re.sub(r'[<>:"/\\|?*]', "_", raw_name).rstrip(" .") or "file"
                    relative = f"files/{folder}/{filename}"
                    previous_hash = used_paths.get(relative.casefold())
                    if previous_hash and previous_hash != entry["sha256"]:
                        path = Path(filename)
                        relative = f"files/{folder}/{path.stem} ({fingerprint(key)[:8]}){path.suffix}"
                    used_paths[relative.casefold()] = entry["sha256"]
                    package_paths[key] = relative
                    entry["package_path"] = relative
                    entry.pop("blob_path", None)
                    entry.pop("path", None)
                with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as package:
                    package.writestr("release.json", json.dumps({**dict(row), "manifest": export_manifest,
                        "manifest_json": None}, indent=2))
                    written = set()
                    for key, entry in manifest["files"].items():
                        relative = package_paths[key]
                        if relative in written:
                            continue
                        source = self._blob(root, entry)
                        digest = hashlib.sha256()
                        with source.open("rb") as content, package.open(
                            relative, "w"
                        ) as archived:
                            for chunk in iter(lambda: content.read(1024 * 1024), b""):
                                digest.update(chunk)
                                archived.write(chunk)
                        if digest.hexdigest() != entry["sha256"]:
                            raise ValueError("A frozen file changed during package export.")
                        written.add(relative)
                os.replace(temporary, destination)
            finally:
                Path(temporary).unlink(missing_ok=True)
            return str(destination)

    def preview_merge(self, branch_id):
        self._require("merge")
        with self.repo.connection() as conn:
            branch = self._branch_access(conn, branch_id)
            if branch["state"] != "Active":
                raise ValueError("Only an active branch can be merged.")
            first = conn.execute("SELECT * FROM nexus_branch_snapshots WHERE branch_id=? "
                                 "AND parent_id IS NULL", (branch_id,)).fetchone()
            base, head = self._verified_manifest(first), self._snapshot(conn, branch["head_snapshot_id"])
            master = self._capture(conn, head["items"])
            changes = []
            for key, entry in head["files"].items():
                before = base["files"].get(key, {}).get("sha256")
                after = entry["sha256"]
                latest = master["files"].get(key, {}).get("sha256")
                if before == after or latest == after:
                    continue
                changes.append({"id": "file:" + key, "kind": "file", "key": key,
                    "label": entry["filename"], "conflict": latest != before,
                    "base": before, "master": latest, "branch": after,
                    "native": bool(NATIVE.fullmatch(entry["filename"]))})
            for item_id, value in head["items"].items():
                for field, after in value["attributes"].items():
                    before = base["items"][item_id]["attributes"].get(field)
                    latest = master["items"][item_id]["attributes"].get(field)
                    if after != before and after != latest:
                        changes.append({"id": f"attribute:{item_id}:{field}", "kind": "attribute",
                            "item_id": int(item_id), "key": field, "label": f"Item {item_id} · {field}",
                            "conflict": latest != before, "base": before, "master": latest, "branch": after})
                for usage_id, usage in value["structure"].items():
                    before = base["items"][item_id]["structure"].get(usage_id)
                    latest = master["items"][item_id]["structure"].get(usage_id)
                    if usage != before and usage != latest:
                        changes.append({"id": f"usage:{item_id}:{usage_id}", "kind": "usage",
                            "item_id": int(item_id), "key": usage_id, "label": f"Item {item_id} · usage {usage_id}",
                            "conflict": latest != before, "base": before, "master": latest, "branch": usage})
            return {"branch_id": branch_id, "branch_name": branch["name"],
                    "head": branch["head_snapshot_id"], "master_token": self._master_token(master),
                    "changes": changes, "plan_token": fingerprint(changes)}

    def merge(self, preview, decisions, note):
        """Recheck a three-way review under the master lock, then publish atomically."""
        self._require("merge")
        if not str(note).strip():
            raise ValueError("Enter a merge note.")
        branch_id = str(preview["branch_id"])
        # Initialize established schema adapters before holding the write transaction.
        from core.repositories.commit_repository import CommitRepository
        from core.repositories.bom_revision_repository import BomRevisionRepository
        from core.repositories.managed_file_repository import ManagedFileRepository
        from core.services.commit_snapshot_service import CommitSnapshotService
        CommitRepository(self.repo.db_name)
        revisions = BomRevisionRepository(self.repo.db_name)
        managed = ManagedFileRepository(self.repo.db_name)
        current_preview = self.preview_merge(branch_id)
        if any(current_preview[key] != preview[key] for key in ("head", "master_token", "plan_token")):
            raise ValueError("Master or branch changed after review. Open a new merge preview.")
        chosen = []
        for change in current_preview["changes"]:
            decision = decisions.get(change["id"], "branch" if not change["conflict"] else "")
            if decision not in {"master", "branch"}:
                raise ValueError("Resolve every conflict by choosing branch or master.")
            if decision == "branch":
                chosen.append(change)
        if not chosen:
            # A user may explicitly close a branch that already matches master,
            # or choose Keep master for every reviewed change. Keep its history,
            # close its active state, and archive only the disposable workspace.
            with self.repo.connection() as conn:
                branch = self._branch_access(conn, branch_id)
                root = self._root(conn)
                with self._branch_lock(root, branch_id):
                    conn.execute("BEGIN IMMEDIATE")
                    fresh = self._branch_access(conn, branch_id, editable=True)
                    if fresh["head_snapshot_id"] != preview["head"]:
                        raise ValueError("The branch changed after merge review.")
                    if self._master_token(self._capture(conn, self._snapshot(conn, fresh["head_snapshot_id"])["items"])) != preview["master_token"]:
                        raise ValueError("Master changed after merge review. Review the differences again.")
                    item_ids = sorted(int(value) for value in
                                      self._snapshot(conn, fresh["head_snapshot_id"])["items"])
                    stamp = now()
                    conn.execute("UPDATE nexus_branches SET state='Merged',merge_commit_id=NULL,updated_at=? WHERE id=?",
                                 (stamp, branch_id))
                    conn.execute("DELETE FROM nexus_active_branches WHERE project_id=? AND branch_id=?",
                                 (self.project_id, branch_id))
                    self.repo.event(conn, self.project_id,
                        self._snapshot(conn, fresh["head_snapshot_id"])["items"], self.user_id, stamp,
                        "BRANCH_MERGED", "Branch closed · " + branch["name"], str(note).strip(),
                        branch_id, fresh["head_snapshot_id"],
                        payload={"commit_id": None, "no_changes_published": True,
                                 "decisions": decisions, "base_master_token": preview["master_token"]})
                    conn.commit()
                archive_path = self._archive_merged_branch(root, branch_id, branch["name"])
            return {"branch_id": branch_id, "commit_id": None,
                    "item_ids": item_ids, "archive_path": archive_path}
        with self.repo.connection() as conn:
            branch = self._branch_access(conn, branch_id)
            root = self._root(conn)
            with self._branch_lock(root, branch_id):
                head = self._snapshot(conn, branch["head_snapshot_id"])
                native_changes = [c for c in chosen if c["kind"] == "file" and c["native"]]
                affected = set()
                branch_document_entries = {}
                for change in chosen:
                    if change["kind"] == "file":
                        if change["native"]:
                            affected.update(head["files"][change["key"]]["item_ids"])
                        else:
                            branch_document_entries[change["key"]] = head["files"][change["key"]]
                            affected.update(head["files"][change["key"]]["item_ids"])
                    else:
                        affected.add(change["item_id"])
                if not affected:
                    raise ValueError("The merge has no selected CAD, deliverable, or item changes.")
                latest_master = self._capture(conn, head["items"])
                publication_entries = {}
                for change in native_changes:
                    branch_entry = head["files"][change["key"]]
                    current_entry = latest_master["files"].get(change["key"]) or branch_entry
                    publication_entries[change["key"]] = {
                        **branch_entry,
                        "filename": self._next_native_iteration(current_entry),
                    }
                # Metadata-only changes still use a sealed submission containing
                # the current native bytes, so the established validation UI works.
                covered = {item_id for entry in publication_entries.values() for item_id in entry["item_ids"]}
                for item_id in sorted(affected - covered):
                    source = next((entry for entry in latest_master["files"].values()
                                   if item_id in entry["item_ids"] and entry["role"] == "native_cad"), None)
                    if source is None:
                        raise ValueError("Associate native CAD before merging this branch's item changes.")
                    entry = self._freeze(root, source)
                    entry["filename"] = self._next_native_iteration(source)
                    publication_entries[entry["key"]] = entry
                for entry in publication_entries.values():
                    affected.update(entry["item_ids"])
                # Check in changed children before their parents capture bindings.
                item_order, visiting, visited = [], set(), set()
                def order_item(item_id):
                    if item_id in visiting:
                        raise ValueError("The branch assembly contains a cycle.")
                    if item_id in visited:
                        return
                    visiting.add(item_id)
                    for usage in head["items"][str(item_id)]["structure"].values():
                        child = int(usage["child_id"])
                        if child in affected:
                            order_item(child)
                    visiting.remove(item_id)
                    visited.add(item_id)
                    item_order.append(item_id)
                for item_id in sorted(affected):
                    order_item(item_id)
                # Native files remain frozen in the branch and are also sealed using
                # the existing submission store so normal validation still works.
                commit_id, stamp = "NB-" + uuid.uuid4().hex[:20], now()
                items, sealed_files = [], []
                for entry in publication_entries.values():
                    source = self._blob(root, entry)
                    filename = Path(entry["filename"]).name
                    relative = Path("_snapshots/objects") / entry["sha256"][:2] / (
                        entry["sha256"] + Path(filename).suffix.lower()
                    )
                    target = root / "commits" / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if not target.exists():
                        temporary = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
                        try:
                            shutil.copyfile(source, temporary)
                            if file_hash(temporary) != entry["sha256"]:
                                raise ValueError("The sealed merge file failed its integrity check.")
                            os.replace(temporary, target)
                        finally:
                            try:
                                temporary.unlink(missing_ok=True)
                            except OSError:
                                pass
                    if file_hash(target) != entry["sha256"]:
                        raise ValueError("The sealed merge file failed its integrity check.")
                    items.append({"filename": filename, "dest_path": str(source),
                        "snapshot_sha256": entry["sha256"], "part_ids": entry["item_ids"],
                        "cad_document_id": entry.get("cad_document_id"), "source_order": 0})
                    baseline = {}
                    cad_id = entry.get("cad_document_id")
                    if cad_id and "cad_documents" in self.repo.tables(conn):
                        row = conn.execute("SELECT revision,iteration FROM cad_documents WHERE id=?", (cad_id,)).fetchone()
                        baseline = dict(row) if row else {}
                    sealed_files.append({"filename": filename, "blob_path": relative.as_posix(),
                        "cad_document_id": cad_id, "sha256": entry["sha256"], "size_bytes": entry["size_bytes"],
                        "base_revision": str(baseline.get("revision") or ""),
                        "base_iteration": int(baseline.get("iteration") or 0), "base_sha256": None})

                def preflight():
                    conn.execute("BEGIN IMMEDIATE")
                    fresh = self._branch_access(conn, branch_id, editable=True)
                    if fresh["head_snapshot_id"] != preview["head"]:
                        raise ValueError("The branch changed after merge review.")
                    if self._master_token(self._capture(conn, head["items"])) != preview["master_token"]:
                        raise ValueError("Master changed after merge review. Review the differences again.")
                    # A new Item may have been associated with the same native CAD
                    # document after branch creation. Never replace its associations
                    # with the older frozen branch scope during publication.
                    current_scope = set(self._scope(conn, head["items"], False))
                    if current_scope != {int(value) for value in head["items"]}:
                        raise ValueError("The item's CAD associations changed since this branch was created. Review and create a fresh branch before merging.")
                    latest_scope = self._capture(conn, head["items"])
                    for entry in latest_scope["files"].values():
                        if entry["role"] == "native_cad" and not set(entry["item_ids"]).issubset(
                            {int(value) for value in head["items"]}
                        ):
                            raise ValueError("The master native file now has additional Item associations. Review and create a fresh branch before merging.")
                    for item_id in affected:
                        item = self._item(conn, item_id)
                        if str(item.get("lifecycle_state") or "").casefold() in {"released", "obsolete"}:
                            if not item.get("pending_revision_code"):
                                raise ValueError("Create or check out a new revision in master before merging changes to a Released/Obsolete item.")
                        if "locks" in self.repo.tables(conn):
                            lock_columns = self.repo.columns(conn, "locks")
                            field = "part_id" if "part_id" in lock_columns else None
                            if field and conn.execute("SELECT 1 FROM locks WHERE part_id=?", (item_id,)).fetchone():
                                raise ValueError("An item is checked out in master. Finish that checkout before merging.")
                    if "cad_documents" in self.repo.tables(conn):
                        for entry in publication_entries.values():
                            if entry.get("cad_document_id") and conn.execute(
                                "SELECT 1 FROM cad_documents WHERE id=? AND checked_out_by IS NOT NULL",
                                (entry["cad_document_id"],),
                            ).fetchone():
                                raise ValueError("Finish the CAD checkout in master before merging this branch.")
                    order = int(conn.execute("SELECT COALESCE(MAX(id),0) FROM commits").fetchone()[0])
                    for publish_item in items:
                        order += len(set(publish_item["part_ids"]) & affected)
                        publish_item["source_order"] = order

                def publish_records():
                    for change in chosen:
                        if change["kind"] == "attribute":
                            conn.execute(f"UPDATE bom SET {change['key']}=? WHERE id=? AND project_id=?",
                                         (change["branch"], change["item_id"], self.project_id))
                        elif change["kind"] == "usage":
                            usage = change["branch"]
                            if not str(change["key"]).startswith("pdm:"):
                                conn.execute("UPDATE bom_children SET quantity=? WHERE id=? AND parent_id=?",
                                             (usage["quantity"], int(change["key"]), change["item_id"]))
                            if usage.get("item_usage_id"):
                                conn.execute("UPDATE item_usages SET quantity=?,modified_at=datetime('now') "
                                             "WHERE id=? AND parent_item_id=?",
                                             (usage["quantity"], usage["item_usage_id"], change["item_id"]))
                    contexts = {}
                    for item_id in item_order:
                        contexts[item_id] = revisions.record_checkin(
                            item_id, self.user_id, "Nexus branch merge: " + str(note).strip(),
                            source_commit_id=commit_id, connection=conn,
                        )
                    for publish_item, sealed in zip(items, sealed_files):
                        row_ids = []
                        entry = next(e for e in publication_entries.values() if e["filename"] == sealed["filename"])
                        for item_id in entry["item_ids"]:
                            if item_id not in contexts:
                                continue
                            iteration_id = contexts[item_id]["current_iteration_id"]
                            cursor = conn.execute("""
                                INSERT INTO commits(part_id,type,filename,file_path,base_file_name,
                                    designer,committed_by,message,status,committed_at,project_id,title,
                                    commit_id,object_iteration_id,cad_document_id)
                                VALUES(?,?,?,?,?,?,?,?, 'Integrated',?,?,?,?,?,?)
                            """, (item_id, Path(logical_name(entry["filename"])).suffix.lstrip("."),
                                  entry["filename"], str(root / "commits" / sealed["blob_path"]),
                                  logical_name(entry["filename"]).rsplit(".", 1)[0], branch["owner_id"],
                                  self.user_id, str(note).strip(), stamp, self.project_id,
                                  "Nexus Branch " + branch["name"], commit_id, iteration_id,
                                  entry.get("cad_document_id")))
                            row_ids.append(int(cursor.lastrowid))
                        publish_item["source_order"] = max(row_ids, default=0)
                    # Bind all the new iteration's files, keeping previous validated
                    # exports attached until the normal export/validation workflow.
                    document_version_cache = {}
                    for item_id in item_order:
                        current = self._capture(conn, [item_id])
                        iteration_id = contexts[item_id]["current_iteration_id"]
                        for key, entry in head["files"].items():
                            if item_id not in entry["item_ids"]:
                                continue
                            picked = publication_entries.get(key)
                            branch_document = branch_document_entries.get(key)
                            if branch_document and item_id in branch_document["item_ids"]:
                                cache_key = (key, item_id)
                                picked = document_version_cache.get(cache_key)
                                if picked is None:
                                    picked = dict(branch_document)
                                    part_file_id = picked.get("part_file_id")
                                    if part_file_id and "part_files" in self.repo.tables(conn) and "part_file_versions" in self.repo.tables(conn):
                                        file_row = conn.execute(
                                            "SELECT * FROM part_files WHERE id=? AND part_id=? AND deleted_at IS NULL",
                                            (int(part_file_id), int(item_id)),
                                        ).fetchone()
                                        if file_row:
                                            digest = picked["sha256"]
                                            filename = Path(picked["filename"]).name
                                            vault_rel = (Path(".nexus/vault/blobs") / digest[:2] / digest / filename).as_posix()
                                            vault_path = self._safe_path(root, vault_rel)
                                            if not vault_path.is_file():
                                                vault_path.parent.mkdir(parents=True, exist_ok=True)
                                                shutil.copyfile(self._blob(root, picked), vault_path)
                                            if file_hash(vault_path) != digest:
                                                raise ValueError("A branch deliverable failed its vault integrity check: " + filename)
                                            version_no = int(conn.execute(
                                                "SELECT COALESCE(MAX(version_no),0)+1 FROM part_file_versions WHERE file_id=?",
                                                (int(part_file_id),),
                                            ).fetchone()[0])
                                            version_columns = self.repo.columns(conn, "part_file_versions")
                                            version_values = {
                                                "file_id": int(part_file_id), "version_no": version_no,
                                                "original_filename": filename, "vault_rel_path": vault_rel,
                                                "sha256": digest, "size_bytes": int(picked.get("size_bytes") or 0),
                                                "note": "Nexus branch merge: " + str(note).strip(),
                                                "revision": str(picked.get("file_revision") or ""),
                                                "created_by": self.user_id, "lifecycle_state": "WIP",
                                                "root_project_id": self._project(conn).get("root_project_id"),
                                                "project_version_label": self._project(conn).get("version_label"),
                                                "object_iteration_id": iteration_id,
                                                "storage_scheme": "managed_blob", "source_kind": "nexus_branch",
                                                "source_commit_id": commit_id, "integrity_status": "Verified",
                                                "derived_from_version_id": picked.get("part_file_version_id"),
                                            }
                                            columns = [name for name in version_values if name in version_columns]
                                            cursor = conn.execute(
                                                f"INSERT INTO part_file_versions ({', '.join(columns)}) "
                                                f"VALUES ({', '.join('?' for _ in columns)})",
                                                [version_values[name] for name in columns],
                                            )
                                            new_version_id = int(cursor.lastrowid)
                                            conn.execute("UPDATE part_files SET active_version_id=? WHERE id=?",
                                                         (new_version_id, int(part_file_id)))
                                            picked.update(part_file_version_id=new_version_id,
                                                          vault_rel_path=vault_rel,
                                                          blob_path=vault_rel)
                                    document_version_cache[cache_key] = picked
                            if picked:
                                entry = picked
                            if not picked:
                                source = current["files"].get(key)
                                if not source:
                                    continue
                                entry = self._freeze(root, source)
                            managed.upsert({
                                "bom_id": item_id, "iteration_id": iteration_id,
                                "binding_key": key, "file_role": entry["role"],
                                "file_type": entry["file_type"], "source_kind": "nexus_branch",
                                "part_file_id": entry.get("part_file_id"),
                                "part_file_version_id": entry.get("part_file_version_id"),
                                "filename": entry["filename"], "file_revision": entry.get("file_revision") or "",
                                "storage_scheme": "managed_blob", "vault_rel_path": entry["blob_path"],
                                "sha256": entry["sha256"], "size_bytes": entry["size_bytes"],
                                "integrity_status": "Verified", "lifecycle_state": "In Work",
                                "source_commit_id": commit_id if picked else entry.get("source_commit_id"),
                                "created_by": self.user_id,
                            }, connection=conn)
                    if sealed_files:
                        CommitSnapshotService._insert_generation(conn, self.project_id, commit_id, 1,
                            {"schema": 1, "project_id": self.project_id, "commit_id": commit_id,
                             "files": sorted(sealed_files, key=lambda e: e["filename"].casefold())},
                            branch["owner_id"])
                    conn.execute("UPDATE nexus_branches SET state='Merged',merge_commit_id=?,updated_at=? WHERE id=?",
                                 (commit_id, stamp, branch_id))
                    conn.execute("DELETE FROM nexus_active_branches WHERE project_id=? AND branch_id=?",
                                 (self.project_id, branch_id))
                    self.repo.event(conn, self.project_id, head["items"], self.user_id, stamp,
                        "BRANCH_MERGED", "Merged to master · " + branch["name"], str(note).strip(),
                        branch_id, branch["head_snapshot_id"],
                        payload={"commit_id": commit_id, "native_state": "Under development",
                                 "decisions": decisions, "base_master_token": preview["master_token"]})
                    conn.commit()

                # One publisher lock protects both stale-review detection and bytes.
                # If the DB callback raises, MasterWorkService restores master files.
                MasterWorkService.publish(root, commit_id, items, actor_user_id=self.user_id,
                    before_publish=preflight, on_published=publish_records)
            archive_path = self._archive_merged_branch(root, branch_id, branch["name"])
            return {"branch_id": branch_id, "commit_id": commit_id,
                    "item_ids": sorted(affected), "archive_path": archive_path}

    def timeline(self, item_id, legacy_history=None, limit=200):
        """Read only selected-item metadata; never scan or hash the CAD directory."""
        with self.repo.connection() as conn:
            project, item = self._project(conn), self._item(conn, item_id)
            branches = [dict(row) for row in conn.execute("""
                SELECT b.* FROM nexus_branches b JOIN nexus_branch_items i ON i.branch_id=b.id
                WHERE b.project_id=? AND i.bom_id=? ORDER BY b.created_at,b.id
            """, (self.project_id, int(item_id)))]
            rows = [dict(row) for row in conn.execute("""
                SELECT e.*,u.username AS actor FROM nexus_item_events e
                LEFT JOIN users u ON u.id=e.actor_id
                WHERE e.project_id=? AND e.bom_id=? ORDER BY e.created_at DESC,e.id DESC LIMIT ?
            """, (self.project_id, int(item_id), int(limit)))]
            for row in rows:
                row["payload"] = json.loads(row.pop("payload_json") or "{}")
            legacy = list(legacy_history or [])
            commits = [event for event in legacy if str(event.get("event") or "").upper() == "COMMIT"]
            branch_commit_ids = {
                str((event.get("payload") or {}).get("source_commit_id") or "")
                for event in rows if event.get("event_type") == "BRANCH_COMMIT"
                and (event.get("payload") or {}).get("source_commit_id")
            }
            suppress_checkins = set()
            for index, event in enumerate(legacy):
                if str(event.get("event") or "").upper() != "CHECKIN":
                    continue
                for commit_index, commit in enumerate(commits):
                    if event.get("part_id") is not None and commit.get("part_id") is not None:
                        if int(event["part_id"]) != int(commit["part_id"]):
                            continue
                    if event.get("project_id") and commit.get("project_id"):
                        if int(event["project_id"]) != int(commit["project_id"]):
                            continue
                    checkin_iteration = event.get("object_iteration_id")
                    commit_iteration = commit.get("object_iteration_id")
                    if checkin_iteration is not None and commit_iteration is not None:
                        if int(checkin_iteration) == int(commit_iteration):
                            suppress_checkins.add(index)
                            break
                        continue
                    checkin_rev = str(event.get("object_version") or "").strip()
                    commit_rev = str(commit.get("object_version") or "").strip()
                    checkin_time = local_timestamp(
                        event.get("timestamp"),
                        naive_is_utc=event.get("timestamp_timezone") == "UTC",
                    )
                    commit_time = local_timestamp(
                        commit.get("timestamp"),
                        naive_is_utc=commit.get("timestamp_timezone") == "UTC",
                    )
                    if (checkin_rev and checkin_rev == commit_rev and checkin_time and commit_time
                            and abs((checkin_time - commit_time).total_seconds()) <= 300):
                        suppress_checkins.add(index)
                        break
            for index, event in enumerate(legacy):
                kind = str(event.get("event") or "ACTION")
                normalized_kind = kind.upper()
                if normalized_kind in {"CHECKOUT", "UNDO_CHECKOUT"} or index in suppress_checkins:
                    continue
                if normalized_kind == "COMMIT" and str(event.get("commit_id") or "") in branch_commit_ids:
                    continue
                rows.append({"id": "legacy-" + str(index), "event_type": kind,
                    "created_at": str(event.get("timestamp") or ""), "actor": event.get("user") or "",
                    "timestamp_timezone": event.get("timestamp_timezone") or "local",
                    "title": kind.replace("_", " ").title(), "note": str(event.get("details") or ""),
                    "payload": {"cad_revision": event.get("version") or "",
                                "object_version": event.get("object_version") or ""},
                    "branch_id": None, "legacy": True})
            def event_order(row):
                parsed = local_timestamp(
                    row.get("created_at"),
                    naive_is_utc=row.get("timestamp_timezone") == "UTC",
                )
                return (parsed.timestamp() if parsed else float("-inf"), str(row.get("id") or ""))
            rows.sort(key=event_order, reverse=True)
            releases = [dict(row) for row in conn.execute("""
                SELECT id,cad_revision,document_revision,note,created_at FROM nexus_releases
                WHERE project_id=? AND bom_id=? ORDER BY created_at DESC
            """, (self.project_id, int(item_id)))]
            active_row = conn.execute("""
                SELECT b.id AS branch_id,b.name,b.state FROM nexus_active_branches a
                JOIN nexus_branches b ON b.id=a.branch_id
                WHERE a.user_id=? AND a.project_id=? AND a.bom_id=?
            """, (self.user_id, self.project_id, int(item_id))).fetchone()
            state, source_id = "", ""
            if project.get("working_directory"):
                manifest = MasterWorkService.read_manifest(project["working_directory"])
                records = [r for r in manifest.get("files", {}).values()
                           if int(item_id) in [int(v) for v in r.get("part_ids", []) if v is not None]]
                if records:
                    source_id = str(max(records, key=lambda r: r.get("published_at") or "").get("source_commit_id") or "")
                    if source_id and "commits" in self.repo.tables(conn):
                        statuses = {str(r[0]).casefold() for r in conn.execute(
                            "SELECT status FROM commits WHERE project_id=? AND commit_id=?",
                            (self.project_id, source_id))}
                        state = "Validated" if statuses and statuses <= {"validated", "approved"} else "Under development"
            return {"item": item, "cad_revision": str(project.get("version_label") or ""),
                    "master_state": state or "Not published", "master_source": source_id,
                    "branches": branches, "events": rows[:limit], "releases": releases,
                    "active_branch": dict(active_row) if active_row else None,
                    "can_branch": self.can("commit") and not project.get("is_readonly"),
                    "can_merge": self.can("merge") and not project.get("is_readonly"),
                    "can_release": self.can("release_files") and not project.get("is_readonly")}
