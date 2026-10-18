"""Publish submitted native CAD independently of approval and release exports."""

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from datetime import datetime, timezone


class MasterWorkService:
    @staticmethod
    def publish(working_directory, commit_id, items, *, actor_user_id=None, preserve_newer=False, on_published=None):
        if not working_directory:
            raise ValueError("A project working directory is required to publish master work.")
        root = Path(working_directory) / "master"
        root.mkdir(parents=True, exist_ok=True)
        lock = root / ".publish.lock"
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise ValueError("Another master publication is active; retry after it completes.")
        os.close(descriptor)
        try:
            with tempfile.TemporaryDirectory(prefix=".publish-", dir=root) as staging:
                index = root / "manifest.json"
                manifest = json.loads(index.read_text(encoding="utf-8")) if index.exists() else {}
                files = dict(manifest.get("files", {}))
                replacements = {}
                published_at = datetime.now(timezone.utc).isoformat()
                # Existing approved native dependencies seed a project's first master.
                # Never copy exports or recursively include vault/commit directories.
                seeds = {}
                if not index.exists():
                    for path in Path(working_directory).iterdir():
                        match = re.fullmatch(r"(.+\.(?:prt|asm|drw))(?:\.(\d+))?", path.name, re.IGNORECASE)
                        if not match or not path.is_file():
                            continue
                        key = match[1].lower()
                        number = int(match[2] or 0)
                        if key not in seeds or number > seeds[key][0]:
                            seeds[key] = (number, {"filename": path.name, "dest_path": str(path), "seed": True})
                for item in [*(entry[1] for entry in seeds.values()), *items]:
                    name = re.sub(r"\.\d+$", "", Path(item["filename"]).name).lower()
                    if not re.fullmatch(r"[^/\\]+\.(prt|asm|drw)", name):
                        raise ValueError(f"Invalid master native filename: {name}")
                    existing = files.get(name) or {}
                    if preserve_newer and int(existing.get("source_order") or 0) > int(item.get("source_order") or 0):
                        continue
                    source = Path(item["dest_path"])
                    staged = Path(staging) / name
                    shutil.copy2(source, staged)
                    digest = hashlib.sha256(staged.read_bytes()).hexdigest()
                    expected = str(item.get("snapshot_sha256") or "")
                    if expected and digest != expected:
                        raise ValueError(f"Frozen native file changed while publishing master: {name}")
                    files[name] = {
                        "source_commit_id": None if item.get("seed") else str(commit_id),
                        "source_filename": item["filename"],
                        "sha256": digest,
                        "published_at": published_at,
                        "published_by": actor_user_id,
                        "source_order": int(item.get("source_order") or 0),
                        "part_ids": item.get("part_ids") or [item.get("part_id")],
                        "cad_document_id": item.get("cad_document_id"),
                    }
                    replacements[root / name] = staged
                staged_index = Path(staging) / "manifest.json"
                staged_index.write_text(json.dumps({"files": files}, indent=2), encoding="utf-8")
                replacements[index] = staged_index
                backups = {}
                try:
                    for number, (destination, staged) in enumerate(replacements.items()):
                        backup = Path(staging) / f"backup-{number}"
                        if destination.exists():
                            shutil.copy2(destination, backup)
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
        return str(root)

    @staticmethod
    def read_manifest(working_directory):
        if not working_directory:
            return {}
        path = Path(working_directory) / "master" / "manifest.json"
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
    if source_id and source_commit and source_status in {"validated", "approved"}:
        current = source_id == latest_id
        state = "ok" if current else "previous"
        text = "current validated native" if current else "previous validated native; master has newer work"
        return dict(details, state=state, tooltip=f"{doc_type}: {text}. Source: {source_id}; master: {latest_id or 'unknown'}")
    return dict(details, state="unverified", tooltip=(
        f"{doc_type}: available, validation/provenance unverified. "
        f"Source: {source_id or 'unknown'}; master: {latest_id or 'unknown'}"
    ))
