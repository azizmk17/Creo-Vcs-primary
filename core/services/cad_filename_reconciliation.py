"""Adopt Creo filenames renamed by a user, without modifying CAD bytes or history."""
import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone

from core.repositories.nexus_history_repository import NexusHistoryRepository
from core.services.master_work_service import MasterWorkService, _file_digest
from utils import long_path

logger = logging.getLogger(__name__)


def root_key(path):
    value = os.fspath(path)
    if os.name == "nt" and value.lower().startswith("\\\\?\\unc\\"):
        value = "\\\\" + value[8:]
    elif os.name == "nt" and value.startswith("\\\\?\\"):
        value = value[4:]
    return os.path.normcase(os.path.abspath(value)).casefold()


def _logical(name):
    return re.sub(r"\.\d+$", "", str(name or "")).casefold()


def _new_name(value, extension):
    name = str(value or "").strip()
    if not name or name in {".", ".."} or re.search(r'[<>:"/\\|?*\x00-\x1f]', name):
        raise ValueError("Enter a valid Creo filename without a path.")
    if name.endswith((".", " ")):
        raise ValueError("The filename cannot end with a dot or space.")
    name = re.sub(r"\.\d+$", "", name)
    if not name.casefold().endswith(extension):
        if re.search(r"\.(prt|asm|drw)$", name, re.I):
            raise ValueError("The new filename must keep the same CAD file type.")
        name += extension
    if re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", name):
        raise ValueError("The filename is reserved by Windows.")
    return name


def reconcile_filenames(*, db_name, project_id, actor_id, cad_id, names):
    """Commit name changes only for matching, already renamed root files.

    Frozen records keep their original names. Each reconciliation and its exact
    root file mappings are committed in the same SQLite transaction as the
    current CAD/BOM metadata. Master manifest readers apply the mappings.
    """
    from core.repositories.permission_repository import PermissionRepository

    if not PermissionRepository(db_name).user_has_permission(actor_id, "manage_parts", project_id):
        raise PermissionError("You do not have permission to manage CAD Documents.")
    conn = sqlite3.connect(db_name, timeout=30)
    conn.row_factory = sqlite3.Row
    lock_path = None
    result = None
    try:
        project = conn.execute("SELECT * FROM projects WHERE id=?", (int(project_id),)).fetchone()
        if not project or not str(project["working_directory"] or "").strip():
            raise ValueError("The project working directory is not configured.")
        if "is_readonly" in project.keys() and bool(project["is_readonly"]):
            raise ValueError("This project version is read-only.")
        root = os.path.abspath(str(project["working_directory"]))
        if not os.path.isdir(long_path(root)):
            raise ValueError("The project working directory is unavailable.")
        lock = MasterWorkService.lock_path(root)
        os.makedirs(long_path(str(lock.parent)), exist_ok=True)
        try:
            descriptor = os.open(long_path(str(lock)), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise ValueError("Another Master publication or reconciliation is active. Try again after it completes.") from exc
        os.close(descriptor)
        lock_path = str(lock)
        conn.execute("BEGIN IMMEDIATE")
        fresh = conn.execute("SELECT working_directory FROM projects WHERE id=?", (int(project_id),)).fetchone()
        if not fresh or root_key(fresh[0]) != root_key(root):
            raise ValueError("The project working directory changed. Select the project again and retry.")
        key = root_key(root)
        rows = conn.execute(
            "SELECT * FROM cad_documents WHERE project_id=? AND (id=? OR drawing_owner_cad_document_id=?)",
            (int(project_id), int(cad_id), int(cad_id)),
        ).fetchall()
        documents = {int(row["id"]): dict(row) for row in rows}
        main = documents.get(int(cad_id))
        if not main or str(main.get("category") or "").upper() not in {"ASSEMBLY", "COMPONENT"}:
            raise ValueError("Select a managed PRT or ASM CAD Document.")
        requested = {int(k): str(v) for k, v in names.items() if str(v or "").strip()}
        if any(k not in documents for k in requested):
            raise ValueError("Choose the model and its related drawings from this project.")
        normalized = {}
        for document_id, value in requested.items():
            document = documents[document_id]
            old_name = str(document["file_name"])
            extension = os.path.splitext(old_name)[1].casefold()
            if extension not in {".prt", ".asm", ".drw"}:
                raise ValueError("Only Creo PRT, ASM and related DRW files are supported.")
            new_name = _new_name(value, extension)
            if new_name.casefold() != old_name.casefold():
                normalized[document_id] = new_name
        requested = normalized
        if not requested:
            raise ValueError("No CAD or drawing filenames were changed.")
        affected = [documents[k] for k in requested]
        ids = [int(row["id"]) for row in affected]
        marks = ",".join("?" for _ in ids)
        if conn.execute("SELECT 1 FROM cad_documents WHERE project_id=? AND checked_out_by IS NOT NULL LIMIT 1", (int(project_id),)).fetchone():
            raise ValueError("Finish CAD checkouts in this project before reconciling filenames.")
        item_ids = sorted({int(row[0]) for row in conn.execute(
            f"SELECT DISTINCT item_id FROM cad_item_associations WHERE active=1 AND cad_document_id IN ({marks})", ids
        )})
        if conn.execute("SELECT 1 FROM locks l JOIN bom b ON b.id=l.part_id WHERE b.project_id=? LIMIT 1", (int(project_id),)).fetchone():
            raise ValueError("Finish Item checkouts in this project before reconciling filenames.")
        if conn.execute("SELECT 1 FROM nexus_branches WHERE project_id=? AND state IN ('Active','Paused') LIMIT 1", (int(project_id),)).fetchone():
            raise ValueError("This project has an active or paused Nexus branch. Merge or abandon it before reconciling filenames.")
        if conn.execute("SELECT 1 FROM commits WHERE project_id=? AND lower(status) IN ('pending','validated') LIMIT 1", (int(project_id),)).fetchone():
            raise ValueError("Finish pending and validated commits in this project before reconciling filenames.")

        # Read without taking another SQLite connection while the write lock is held.
        manifest = MasterWorkService.read_manifest(root, db_name=db_name, conn=conn)
        with os.scandir(long_path(root)) as scan:
            inventory = {entry.name.casefold(): entry.name for entry in scan if entry.is_file(follow_symlinks=False)}
        changed = []
        verified_files = {}

        def verify_file(name, expected):
            path = os.path.join(root, name)
            before = os.stat(long_path(path))
            digest = _file_digest(path)
            after = os.stat(long_path(path))
            signature = (after.st_size, after.st_mtime_ns, after.st_ino)
            if (before.st_size, before.st_mtime_ns, before.st_ino) != signature:
                raise ValueError(f"{name} changed while Nexus was checking it. Retry when the file is stable.")
            if digest.casefold() != str(expected).casefold():
                return False
            verified_files[path] = signature
            return True
        for document in affected:
            old = str(document["file_name"])
            new = requested[int(document["id"])]
            for other in conn.execute("SELECT d.id,d.project_id,p.working_directory FROM cad_documents d JOIN projects p ON p.id=d.project_id WHERE d.project_id<>? AND (lower(d.file_name)=lower(?) OR lower(d.file_name)=lower(?))", (int(project_id), old, new)):
                if other["working_directory"] and root_key(other["working_directory"]) == key:
                    raise ValueError(f"{old} or {new} is managed by another project using this directory. Resolve the shared-file conflict first.")
            if conn.execute("SELECT 1 FROM cad_documents WHERE project_id=? AND id<>? AND (lower(file_name)=lower(?) OR lower(number)=lower(?)) LIMIT 1",
                            (int(project_id), int(document["id"]), new, new)).fetchone():
                raise ValueError(f"{new} already belongs to another CAD Document.")
            # Old names must remain reserved so history can be resolved unambiguously.
            if conn.execute("SELECT 1 FROM cad_filename_reconciliations WHERE project_id=? AND (lower(old_name)=lower(?) OR lower(new_name)=lower(?)) LIMIT 1",
                            (int(project_id), new, new)).fetchone():
                raise ValueError(f"{new} is already part of another CAD rename history.")
            old_pattern = re.compile(r"^" + re.escape(old) + r"(?:\.(\d+))?$", re.I)
            new_pattern = re.compile(r"^" + re.escape(new) + r"(?:\.(\d+))?$", re.I)
            if any(old_pattern.fullmatch(name) for name in inventory.values()):
                raise ValueError(f"{old} still exists in the project root. Complete the rename in Creo first.")
            new_files = [name for name in inventory.values() if new_pattern.fullmatch(name)]
            if not new_files:
                raise ValueError(f"No renamed {new} files were found in the project root.")
            mapping = {}
            for name in new_files:
                suffix = name[len(new):]
                mapping[old + suffix] = name
            evidence = False
            current_manifest_file = ""
            for section in ("files", "validated_files"):
                record = (manifest.get(section) or {}).get(old.casefold())
                if not record:
                    continue
                old_file = str(record.get("path") or record.get("source_filename") or "")
                if section == "files":
                    current_manifest_file = old_file
                mapped = next((v for k, v in mapping.items() if k.casefold() == old_file.casefold()), None)
                expected = str(record.get("sha256") or "").strip()
                if not mapped or not expected or not verify_file(mapped, expected):
                    raise ValueError(f"Cannot verify {old_file} against {new}; the file is missing or its contents changed. Restore the recorded CAD file or reconcile the changed content through the normal CAD workflow.")
                evidence = True
            current = conn.execute("SELECT * FROM cad_document_iterations WHERE cad_document_id=? AND revision=? AND iteration=?",
                                   (int(document["id"]), document["revision"], document["iteration"])).fetchone()
            if not evidence and current and current["sha256"]:
                old_file = os.path.basename(str(current["primary_path"] or document.get("latest_creo_file_name") or ""))
                mapped = next((v for k, v in mapping.items() if k.casefold() == old_file.casefold()), None)
                if mapped and verify_file(mapped, current["sha256"]):
                    evidence = True
            if not evidence:
                raise ValueError(f"No recorded hash can verify the renamed file for {old}. Reconcile it through a CAD check-in instead.")
            latest_old = str(document.get("latest_creo_file_name") or "")
            latest_new = next((v for k, v in mapping.items() if k.casefold() == latest_old.casefold()), "")
            if latest_old and not latest_new:
                raise ValueError(f"The recorded current iteration {latest_old} was not found under the new name.")
            if latest_old and current_manifest_file and latest_old.casefold() != current_manifest_file.casefold():
                raise ValueError(f"The current CAD iteration {latest_old} differs from the recorded Master file {current_manifest_file}. Resolve the mismatch before reconciling.")
            changed.append((document, old, new, mapping, current, latest_new))

        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for document, old, new, mapping, current, latest_new in changed:
            doc_id = int(document["id"])
            conn.execute("INSERT INTO cad_filename_reconciliations(project_id,cad_document_id,root_key,old_name,new_name,file_mapping_json,actor_user_id,created_at) VALUES(?,?,?,?,?,?,?,?)",
                         (int(project_id), doc_id, key, old, new, json.dumps(mapping, sort_keys=True), int(actor_id), stamp))
            label = str(document.get("name") or "")
            if label.casefold() in {old.casefold(), os.path.splitext(old)[0].casefold()}:
                label = new if label.casefold() == old.casefold() else os.path.splitext(new)[0]
            conn.execute("UPDATE cad_documents SET number=?,name=?,file_name=?,base_file_name=?,latest_creo_file_name=?,modified_at=datetime('now') WHERE id=? AND project_id=?",
                         (new, label, new, os.path.splitext(new)[0].casefold(), latest_new or None, doc_id, int(project_id)))
            if current:
                path = str(current["primary_path"] or "")
                old_file = os.path.basename(path.replace("\\", "/"))
                mapped = next((v for k, v in mapping.items() if k.casefold() == old_file.casefold()), None)
                if mapped and (not os.path.isabs(path) or root_key(os.path.dirname(path)) == key):
                    replacement = os.path.join(root, mapped) if os.path.isabs(path) else mapped
                    conn.execute("UPDATE cad_document_iterations SET primary_path=? WHERE id=?", (replacement, int(current["id"])))
            for column, base_column in (("filename", "base_file_name"), ("drawing", "base_drw_name")):
                bom_rows = conn.execute(f"SELECT id,{column} FROM bom WHERE project_id=? AND {column} IS NOT NULL", (int(project_id),)).fetchall()
                for row in bom_rows:
                    val = str(row[column] or "")
                    if _logical(os.path.basename(val.replace("\\", "/"))) != old.casefold():
                        continue
                    mapped = next((v for k, v in mapping.items() if k.casefold() == os.path.basename(val.replace("\\", "/")).casefold()), None)
                    if not mapped and os.path.basename(val.replace("\\", "/")).casefold() == old.casefold():
                        master = (manifest.get("files") or {}).get(old.casefold()) or {}
                        master_file = str(master.get("path") or master.get("source_filename") or "")
                        mapped = latest_new or next((v for k, v in mapping.items() if k.casefold() == master_file.casefold()), None)
                    if mapped:
                        conn.execute(f"UPDATE bom SET {column}=?,{base_column}=? WHERE id=?", (mapped, new, int(row["id"])))
            if item_ids:
                NexusHistoryRepository.event(conn, project_id, item_ids, actor_id, stamp,
                    "CAD_RENAMED", "CAD filename reconciled", old + " -> " + new,
                    payload={"cad_document_id": doc_id, "old_name": old, "new_name": new, "files": mapping})
        for path, signature in verified_files.items():
            stat = os.stat(long_path(path))
            if (stat.st_size, stat.st_mtime_ns, stat.st_ino) != signature:
                raise ValueError(f"{os.path.basename(path)} changed during reconciliation. Retry when the file is stable.")
        conn.commit()
        result = {"cad_document_ids": ids, "item_ids": item_ids, "renamed": [{"old": old, "new": new} for _, old, new, _, _, _ in changed]}
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
        if lock_path:
            try:
                os.unlink(long_path(lock_path))
            except FileNotFoundError:
                pass
            except OSError:
                logger.exception("Could not remove CAD reconciliation lock %s", lock_path)
                if result is not None:
                    result["warning"] = "The rename was saved, but the Master lock could not be removed: " + lock_path
