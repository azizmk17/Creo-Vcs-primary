"""One-time conversion of app-owned Master storage to the project root.

Unvalidated native bytes in the legacy folder are discarded, never promoted.
Validated files already in the root are reused; missing validated bytes are
preserved once in the root before their old copy is deleted.
"""
import json
import os
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

from utils import long_path
from core.services.master_work_service import MasterWorkService, _file_digest


def _copy_new_verified(source, target, expected):
    created = False
    try:
        with target.open("xb") as output:
            created = True
            with source.open("rb") as input_stream:
                shutil.copyfileobj(input_stream, output)
        if _file_digest(target) != expected:
            raise ValueError("A migrated file failed verification: " + target.name)
    except Exception:
        if created:
            target.unlink(missing_ok=True)
        raise


def migrate_master_layout(working_directory, db_name, project_id):
    if not working_directory:
        return
    root = Path(long_path(str(Path(working_directory).resolve())))
    legacy = root / "master"
    if not legacy.exists():
        return
    # Never recurse through a junction/symlink or remove an unrelated folder.
    if legacy.resolve().parent != root.resolve() or legacy.is_symlink() or (
        os.name == "nt" and legacy.lstat().st_file_attributes & 0x400
    ):
        raise ValueError("Legacy Master is a link or junction; conversion cannot delete it safely.")
    index = legacy / "manifest.json"
    if not index.is_file():
        raise ValueError("The master folder has no Nexus manifest; it was left untouched.")
    manifest = json.loads(index.read_text(encoding="utf-8"))
    if not isinstance(manifest.get("files"), dict):
        raise ValueError("The legacy Master manifest is invalid; its folder was left untouched.")
    lock = MasterWorkService.lock_path(root)
    lock.parent.mkdir(parents=True, exist_ok=True)
    acquired = []
    try:
        for path in (lock, legacy / ".publish.lock"):
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                raise ValueError("Master is busy. Close other project sessions and retry conversion.")
            os.close(fd)
            acquired.append(path)
        latest = MasterWorkService.latest_native_files(root)
        converted = MasterWorkService.read_manifest(root)
        files = dict(converted.get("files", {}))
        migrated_validated = {}
        discarded = []
        with closing(sqlite3.connect(db_name)) as conn, conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            project = conn.execute("SELECT working_directory FROM projects WHERE id=?", (project_id,)).fetchone()
            if not project or Path(long_path(str(project[0]))).resolve() != root.resolve():
                raise ValueError("The conversion database does not match this project directory.")
            for name, record in manifest["files"].items():
                candidate = latest.get(name.lower())
                source_id = record.get("source_commit_id")
                approved = not source_id
                if source_id and "commits" in tables:
                    approved = conn.execute("SELECT 1 FROM commits WHERE project_id=? AND commit_id=? "
                        "AND lower(status) IN ('approved','validated') LIMIT 1",
                        (project_id, source_id)).fetchone() is not None
                if approved and source_id and (not candidate or
                        _file_digest(root / candidate[1]) != record.get("sha256")):
                    # Earlier branch merges did not always install root CAD.
                    # Preserve verified approved bytes, never a development copy.
                    source = MasterWorkService.native_path(legacy, {"path": name})
                    if not source.is_file() or _file_digest(source) != record.get("sha256"):
                        raise ValueError("A validated legacy CAD file is missing or changed: " + name)
                    number = (candidate[0] if candidate else 0) + 1
                    filename = f"{name}.{number}"
                    target = MasterWorkService.native_path(root, {"path": filename})
                    _copy_new_verified(source, target, record["sha256"])
                    candidate = (number, filename)
                    latest[name.lower()] = candidate
                if candidate and approved and _file_digest(root / candidate[1]) == record.get("sha256"):
                    files[name] = {**record, "path": candidate[1], "source_filename": candidate[1]}
                    if source_id:
                        migrated_validated[name] = dict(files[name])
                    field = "drawing" if name.lower().endswith(".drw") else "filename"
                    for item_id in record.get("part_ids", []):
                        if item_id is not None and "bom" in tables:
                            conn.execute(f"UPDATE bom SET {field}=? WHERE id=? AND project_id=?",
                                         (candidate[1], item_id, project_id))
                    if record.get("cad_document_id") and "cad_documents" in tables:
                        conn.execute("UPDATE cad_documents SET latest_creo_file_name=?,latest_creo_file_version=? "
                                     "WHERE id=? AND project_id=?",
                                     (candidate[1], candidate[0], record["cad_document_id"], project_id))
                else:
                    discarded.append(name)

            # Preserve document paths without changing their acknowledgement,
            # validation, revision, source commit, or managed-version identity.
            documents = dict(converted.get("deliverables", {}))
            mapping = {}
            for key, record in manifest.get("deliverables", {}).items():
                relative = Path(str(record.get("path") or ""))
                source = legacy / relative
                if (relative.is_absolute() or relative.parts[:1] != ("deliverables",)
                        or not source.resolve().is_relative_to((legacy / "deliverables").resolve())):
                    raise ValueError("Invalid legacy deliverable path; conversion stopped.")
                destination = root / relative
                if destination.exists() and _file_digest(destination) != record["sha256"]:
                    relative = Path("deliverables") / ("legacy-" + record["sha256"][:16]) / source.name
                    destination = root / relative
                if not destination.resolve().is_relative_to(root.resolve()):
                    raise ValueError("A deliverable destination leaves the project root.")
                destination.parent.mkdir(parents=True, exist_ok=True)
                if not destination.exists():
                    if not source.is_file() or _file_digest(source) != record.get("sha256"):
                        raise ValueError("A legacy deliverable is missing or changed: " + str(relative))
                    _copy_new_verified(source, destination, record["sha256"])
                if _file_digest(destination) != record["sha256"]:
                    raise ValueError("A migrated deliverable failed verification.")
                mapping[record["path"]] = relative.as_posix()
                documents[key] = {**record, "path": relative.as_posix()}

            # These are live path references. Immutable snapshots already use
            # content-addressed blobs and must not be rewritten.
            targets = [
                ("bom", "pdf_path", "project_id=?"),
                ("bom", "step_path", "project_id=?"),
                ("bom_iteration_files", "vault_rel_path", "bom_id IN (SELECT id FROM bom WHERE project_id=?)"),
                ("part_file_versions", "vault_rel_path", "file_id IN (SELECT f.id FROM part_files f JOIN bom b ON b.id=f.part_id WHERE b.project_id=?)"),
            ]
            plain_root = Path(working_directory).resolve()
            for table, column, scope in targets:
                if table not in tables or column not in {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}:
                    continue
                for old, new in mapping.items():
                    for previous, replacement in (
                        (str(plain_root / "master" / old), str(plain_root / new)),
                        (str(Path("master") / old), str(Path(new))),
                        ((Path("master") / old).as_posix(), Path(new).as_posix()),
                        ((plain_root / "master" / old).as_posix(), (plain_root / new).as_posix()),
                    ):
                        conn.execute(f'UPDATE "{table}" SET "{column}"=? WHERE "{column}"=? AND {scope}',
                                     (replacement, previous, project_id))
            validated_files = dict(converted.get("validated_files", {}))
            validated_files.update(migrated_validated)
            converted = {**converted, "schema": 2, "files": files, "validated_files": validated_files,
                         "deliverables": documents,
                         "legacy_conversion": {"discarded_native_records": discarded}}
            # Publish references before committing the database; on retry the
            # conversion is idempotent and the legacy sources still exist.
            with MasterWorkService.staging_directory(lock.parent) as staging:
                staged = staging / "manifest.json"
                staged.write_text(json.dumps(converted, indent=2), encoding="utf-8")
                os.replace(staged, MasterWorkService.manifest_path(root))
        # Confirm the resolved deletion target again, after all persistence.
        if legacy.resolve() != root.resolve() / "master":
            raise ValueError("Legacy Master changed location during conversion; cleanup stopped.")
        # Refuse nested reparse points too: cleanup must stay within this folder.
        for folder, dirs, names in os.walk(legacy, followlinks=False):
            for name in dirs + names:
                path = Path(folder) / name
                if path.is_symlink() or (os.name == "nt" and path.lstat().st_file_attributes & 0x400):
                    raise ValueError("Legacy Master contains a link; cleanup stopped.")
        # Leave the identifying manifest until last, so interrupted cleanup can
        # be retried without treating the remaining folder as unrelated data.
        for path in legacy.iterdir():
            if path.name in {"manifest.json", ".publish.lock"}:
                continue
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        (legacy / ".publish.lock").unlink()
        index.unlink()
        legacy.rmdir()
    finally:
        for path in reversed(acquired):
            path.unlink(missing_ok=True)
