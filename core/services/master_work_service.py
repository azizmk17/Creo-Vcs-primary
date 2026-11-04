"""Publish submitted native CAD independently of approval and release exports."""

import hashlib
import json
import os
import re
import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timezone
from utils import long_path, safe_open


def _file_digest(path):
    digest = hashlib.sha256()
    with safe_open(str(path), "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class MasterWorkService:
    @staticmethod
    def manifest_path(working_directory):
        return Path(working_directory) / ".nexus" / "master-manifest.json"

    @staticmethod
    def lock_path(working_directory):
        return Path(working_directory) / ".nexus" / "master-publish.lock"

    @staticmethod
    def native_path(working_directory, record):
        root = Path(working_directory).resolve()
        name = str(record.get("path") or record.get("source_filename") or "")
        if not re.fullmatch(r"[^/\\]+\.(?:prt|asm|drw)(?:\.\d+)?", name, re.I):
            raise ValueError("Invalid root Master native filename.")
        path = root / name
        if path.resolve().parent != root:
            raise ValueError("A Master native file points outside the project root.")
        return path

    @staticmethod
    def latest_native_files(working_directory):
        latest = {}
        for path in Path(long_path(str(working_directory))).iterdir():
            match = re.fullmatch(r"(.+\.(?:prt|asm|drw))(?:\.(\d+))?", path.name, re.I)
            if match and path.is_file():
                key, number = match[1].lower(), int(match[2] or 0)
                if key not in latest or number > latest[key][0]:
                    latest[key] = (number, path.name)
        return latest

    @staticmethod
    @contextmanager
    def staging_directory(root):
        # Normal mkdir inherits the share ACL; TemporaryDirectory can create
        # private ACLs which survive a rename into the shared working directory.
        path = Path(root) / (".publish-" + uuid.uuid4().hex)
        path.mkdir()
        try:
            yield path
        finally:
            shutil.rmtree(path)

    @staticmethod
    def publish(working_directory, commit_id, items, *, actor_user_id=None, preserve_newer=False,
                on_published=None, before_publish=None, deliverables=None,
                always_new=False, is_validated_commit=None):
        if not working_directory:
            raise ValueError("A project working directory is required to publish master work.")
        root = Path(long_path(str(working_directory)))
        root.mkdir(parents=True, exist_ok=True)
        lock = MasterWorkService.lock_path(root)
        lock.parent.mkdir(parents=True, exist_ok=True)
        if (root / "master" / "manifest.json").exists():
            raise ValueError("Open the project to convert its legacy Master storage before publishing.")
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise ValueError("Another master publication is active; retry after it completes.")
        os.close(descriptor)
        try:
            if before_publish is not None:
                before_publish()
            with MasterWorkService.staging_directory(lock.parent) as staging:
                index = MasterWorkService.manifest_path(root)
                manifest = json.loads(index.read_text(encoding="utf-8")) if index.exists() else {}
                files = dict(manifest.get("files", {}))
                validated_files = dict(manifest.get("validated_files", {}))
                high_water = dict(manifest.get("iteration_high_water", {}))
                replacements = {}
                published_at = datetime.now(timezone.utc).isoformat()
                # Root dependencies already exist; publication must never duplicate
                # them or manufacture provenance for unsubmitted files.
                latest = MasterWorkService.latest_native_files(root)
                native_destinations = set()
                for item in items:
                    name = re.sub(r"\.\d+$", "", Path(item["filename"]).name).lower()
                    if not re.fullmatch(r"[^/\\]+\.(prt|asm|drw)", name):
                        raise ValueError(f"Invalid master native filename: {name}")
                    existing = files.get(name) or {}
                    if (name not in validated_files and existing.get("source_commit_id")
                            and is_validated_commit is not None
                            and is_validated_commit(existing["source_commit_id"]) is True):
                        validated_files[name] = dict(existing)
                    if preserve_newer and int(existing.get("source_order") or 0) > int(item.get("source_order") or 0):
                        continue
                    source = Path(long_path(str(item["dest_path"])))
                    staged = Path(staging) / name
                    shutil.copyfile(source, staged)
                    digest = _file_digest(staged)
                    expected = str(item.get("snapshot_sha256") or "")
                    if expected and digest != expected:
                        raise ValueError(f"Frozen native file changed while publishing master: {name}")
                    # Approval already installed an exact numbered root file.
                    # Branch merges reserve their filename before writing DB rows.
                    exact = item.get("root_filename")
                    if exact:
                        destination = MasterWorkService.native_path(root, {"path": exact})
                        if re.sub(r"\.\d+$", "", destination.name).lower() != name:
                            raise ValueError("The reserved Master filename belongs to a different CAD file.")
                        if destination.exists() and _file_digest(destination) != digest:
                            raise ValueError("The root CAD iteration changed; review and retry: " + str(exact))
                    else:
                        number, filename = latest.get(name, (0, ""))
                        candidate = root / filename if filename else None
                        if not always_new and candidate and _file_digest(candidate) == digest:
                            destination = candidate
                        else:
                            number = max(number, int(high_water.get(name, 0))) + 1
                            destination = root / f"{name}.{number}"
                            latest[name] = (number, destination.name)
                    if destination.name.casefold() in native_destinations:
                        raise ValueError("Multiple native files use the same Master destination.")
                    native_destinations.add(destination.name.casefold())
                    item["published_filename"] = destination.name
                    iteration = re.search(r"\.(\d+)$", destination.name)
                    if iteration:
                        high_water[name] = max(int(high_water.get(name, 0)), int(iteration[1]))
                    files[name] = {
                        "source_commit_id": None if item.get("seed") else str(commit_id),
                        "source_filename": destination.name,
                        "path": destination.name,
                        "sha256": digest,
                        "published_at": published_at,
                        "published_by": actor_user_id,
                        "source_order": int(item.get("source_order") or 0),
                        "part_ids": item.get("part_ids") or [item.get("part_id")],
                        "cad_document_id": item.get("cad_document_id"),
                    }
                    if item.get("validated"):
                        validated_files[name] = dict(files[name])
                    if not destination.exists():
                        replacements[destination] = staged
                documents = dict(manifest.get("deliverables", {}))
                seen_paths = set()
                for number, document in enumerate(deliverables or []):
                    relative = Path(str(document["relative_path"]))
                    destination = (root / relative).resolve()
                    if (relative.is_absolute() or relative.parts[:1] != ("deliverables",)
                            or destination == (root / "deliverables").resolve()
                            or not destination.is_relative_to((root / "deliverables").resolve())):
                        raise ValueError("A Master deliverable path points outside managed deliverable storage.")
                    path_key = os.path.normcase(str(destination))
                    if path_key in seen_paths:
                        raise ValueError("Multiple deliverables use the same Master destination.")
                    seen_paths.add(path_key)
                    staged = Path(staging) / f"deliverable-{number}"
                    shutil.copyfile(long_path(str(document["dest_path"])), staged)
                    digest = _file_digest(staged)
                    if digest != str(document["sha256"]):
                        raise ValueError("A frozen deliverable changed while publishing Master.")
                    documents[str(document["key"])] = {
                        "path": relative.as_posix(), "filename": document["filename"],
                        "file_type": document["file_type"], "file_revision": document.get("file_revision") or "",
                        "sha256": digest, "part_ids": document["part_ids"],
                        "source_commit_id": document.get("source_commit_id") or str(commit_id),
                        "published_at": published_at, "published_by": actor_user_id,
                    }
                    replacements[destination] = staged
                staged_index = Path(staging) / "manifest.json"
                next_manifest = {**manifest, "schema": 2, "files": files,
                                 "validated_files": validated_files, "iteration_high_water": high_water}
                if documents:
                    next_manifest["deliverables"] = documents
                staged_index.write_text(json.dumps(next_manifest, indent=2), encoding="utf-8")
                replacements[index] = staged_index
                backups = {}
                try:
                    for number, (destination, staged) in enumerate(replacements.items()):
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        if destination.parent == root:
                            # Never overwrite an iteration created concurrently by Creo.
                            with destination.open("xb") as output:
                                backups[destination] = None
                                with staged.open("rb") as source:
                                    shutil.copyfileobj(source, output)
                            continue
                        backup = Path(staging) / f"backup-{number}"
                        if destination.exists():
                            shutil.copyfile(destination, backup)
                            backups[destination] = backup
                        else:
                            backups[destination] = None
                        os.replace(staged, destination)
                    if on_published is not None:
                        on_published()
                except Exception:
                    for destination, backup in reversed(list(backups.items())):
                        if backup is not None:
                            os.replace(backup, destination)
                        elif destination.exists():
                            destination.unlink()
                    raise
        finally:
            lock.unlink()
        return str(working_directory)

    @staticmethod
    def read_manifest(working_directory):
        if not working_directory:
            return {}
        path = MasterWorkService.manifest_path(long_path(str(working_directory)))
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def master_commit_for_part(commits, manifest, part_id):
    """Unpushed submissions cannot change the state or exports shown for master."""
    records = [record for record in (manifest.get("files") or {}).values()
               if int(part_id) in [int(value) for value in record.get("part_ids", []) if value is not None]
               and record.get("source_commit_id")]
    if records:
        record = max(records, key=lambda value: (str(value.get("published_at") or ""),
                                                int(value.get("source_order") or 0)))
        return next((commit for commit in commits if str(commit.commit_id) == str(record["source_commit_id"])), None)
    approved = [commit for commit in commits
                if str(getattr(commit, "status", "")).lower() in {"approved", "released", "pushed"}]
    return max(approved, key=lambda commit: (str(getattr(commit, "committed_at", "") or ""),
                                            int(getattr(commit, "id", 0) or 0)), default=None)


def native_work_state(commit):
    if commit is None:
        return ""
    status = str(getattr(commit, "status", "") or "").lower()
    return "Validated" if status in {"validated", "approved"} else "Under development"


def deliverable_provenance(doc_type, version, latest_commit, source_commit):
    """No timestamp or filename can prove that an export matches native CAD."""
    source_id = str(getattr(version, "source_commit_id", "") or "")
    latest_id = str(getattr(latest_commit, "commit_id", "") or "")
    source_status = str(getattr(source_commit, "status", "") or "").lower()
    details = {
        "source_commit_id": source_id,
        "native_commit_id": latest_id,
        "native_state": native_work_state(latest_commit),
    }
    source_validated = source_status in {"validated", "approved"} or (
        source_status == "branched" and getattr(source_commit, "checked_by", None) is not None
    )
    if source_id and source_commit and source_validated:
        current = source_id == latest_id
        state = "ok" if current else "previous"
        text = "current validated native" if current else "previous validated native; master has newer work"
        return dict(details, state=state, tooltip=f"{doc_type}: {text}. Source: {source_id}; master: {latest_id or 'unknown'}")
    return dict(details, state="unverified", tooltip=(
        f"{doc_type}: available, validation/provenance unverified. "
        f"Source: {source_id or 'unknown'}; master: {latest_id or 'unknown'}"
    ))
