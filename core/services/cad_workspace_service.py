"""Named, machine-local CAD workspaces used for Creo checkouts.

The shared database owns checkout state.  This service owns only local workspace
registration, the exact baseline copied to disk, and change discovery.  A
workspace is intentionally independent from a project; each manifest entry
stores its own project and CAD Document identity.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core.services.pdm_service import PdmService
from core.services.project_service import ProjectService
from core.session_manager import SessionManager


_MANIFEST_NAME = ".creovcs-workspace.json"
_REGISTRY_NAME = "registry.json"
_CREO_RE = re.compile(r"^(.*\.(?:prt|asm|drw))\.(\d+)$", re.IGNORECASE)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class CadWorkspaceService:
    """Create, materialize, scan, and safely remove local CAD workspaces."""

    def __init__(
        self,
        root: str | os.PathLike | None = None,
        *,
        pdm_service: PdmService | None = None,
        project_service: ProjectService | None = None,
    ) -> None:
        self.root = Path(root) if root else self.default_root()
        self.root = self.root.expanduser().resolve()
        self.registry_path = self.root / _REGISTRY_NAME
        self.pdm_service = pdm_service or PdmService()
        self.project_service = project_service or ProjectService()
        self.session = SessionManager()
        self.machine_id = socket.gethostname().strip() or "unknown-machine"
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def default_root() -> Path:
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        return base / "CreoVCS" / "cad-workspaces"

    @staticmethod
    def logical_name(filename: str) -> str:
        name = os.path.basename(str(filename or "").replace("\\", "/")).strip()
        match = _CREO_RE.match(name)
        return match.group(1) if match else name

    @staticmethod
    def _sha256(path: str | os.PathLike) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def _read_json(path: Path, default):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                value = json.load(handle)
            return value
        except (OSError, ValueError, TypeError):
            return default

    @staticmethod
    def _write_json(path: Path, value) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except Exception:
            try:
                os.remove(temporary)
            except OSError:
                pass
            raise

    def _registry(self) -> dict:
        value = self._read_json(self.registry_path, {"version": 1, "workspaces": []})
        if not isinstance(value, dict):
            value = {"version": 1, "workspaces": []}
        value.setdefault("version", 1)
        value.setdefault("workspaces", [])
        return value

    def _save_registry(self, registry: dict) -> None:
        self._write_json(self.registry_path, registry)

    def workspace_path(self, workspace_id: str) -> Path:
        key = str(workspace_id or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{32}", key):
            raise ValueError("The CAD workspace identifier is invalid.")
        path = (self.root / key).resolve()
        if path.parent != self.root:
            raise ValueError("The CAD workspace path is outside the managed root.")
        return path

    def _manifest_path(self, workspace_id: str) -> Path:
        return self.workspace_path(workspace_id) / _MANIFEST_NAME

    def get_workspace(self, workspace_id: str) -> dict | None:
        key = str(workspace_id or "").strip().lower()
        for row in self.list_workspaces():
            if row["id"] == key:
                return row
        return None

    def list_workspaces(self) -> list[dict]:
        registry = self._registry()
        rows = []
        for raw in registry.get("workspaces") or []:
            row = dict(raw or {})
            owner_user_id = row.get("owner_user_id")
            if (
                owner_user_id is not None and self.session.user_id is not None
                and int(owner_user_id) != int(self.session.user_id)
            ):
                continue
            workspace_id = str(row.get("id") or "").strip().lower()
            if not re.fullmatch(r"[0-9a-f]{32}", workspace_id):
                continue
            path = self.workspace_path(workspace_id)
            row.update({
                "id": workspace_id,
                "path": str(path),
                "available": path.is_dir(),
            })
            manifest = self.load_manifest(workspace_id, required=False)
            entries = list((manifest or {}).get("entries", {}).values())
            row["checkout_count"] = len(entries)
            row["project_count"] = len({
                int(entry["project_id"])
                for entry in entries if entry.get("project_id") is not None
            })
            rows.append(row)
        return sorted(
            rows,
            key=lambda item: (
                str(item.get("last_used_at") or ""),
                str(item.get("name") or "").casefold(),
            ),
            reverse=True,
        )

    def create_workspace(self, name: str, description: str = "") -> dict:
        clean_name = " ".join(str(name or "").strip().split())
        if not clean_name:
            raise ValueError("Workspace name is required.")
        if len(clean_name) > 80:
            raise ValueError("Workspace name must contain at most 80 characters.")
        registry = self._registry()
        if any(
            str(row.get("name") or "").casefold() == clean_name.casefold()
            for row in registry.get("workspaces") or []
        ):
            raise ValueError(f'A workspace named "{clean_name}" already exists.')
        workspace_id = uuid.uuid4().hex
        now = _utc_now()
        record = {
            "id": workspace_id,
            "name": clean_name,
            "description": str(description or "").strip(),
            "machine_id": self.machine_id,
            "owner_user_id": int(self.session.user_id)
            if self.session.user_id is not None else None,
            "owner_username": str(self.session.username or ""),
            "created_at": now,
            "last_used_at": now,
        }
        path = self.workspace_path(workspace_id)
        path.mkdir(parents=True, exist_ok=False)
        manifest = {
            "version": 1,
            "workspace_id": workspace_id,
            "name": clean_name,
            "machine_id": self.machine_id,
            "created_at": now,
            "updated_at": now,
            "entries": {},
        }
        try:
            self._write_json(path / _MANIFEST_NAME, manifest)
            registry.setdefault("workspaces", []).append(record)
            self._save_registry(registry)
        except Exception:
            shutil.rmtree(path, ignore_errors=True)
            raise
        return {**record, "path": str(path), "available": True}

    def rename_workspace(self, workspace_id: str, name: str) -> dict:
        clean_name = " ".join(str(name or "").strip().split())
        if not clean_name:
            raise ValueError("Workspace name is required.")
        registry = self._registry()
        key = str(workspace_id).lower()
        rows = registry.get("workspaces") or []
        if any(
            str(row.get("id") or "").lower() != key
            and str(row.get("name") or "").casefold() == clean_name.casefold()
            for row in rows
        ):
            raise ValueError(f'A workspace named "{clean_name}" already exists.')
        target = next((row for row in rows if str(row.get("id") or "").lower() == key), None)
        if target is None:
            raise ValueError("The CAD workspace was not found.")
        target["name"] = clean_name
        target["last_used_at"] = _utc_now()
        manifest = self.load_manifest(key)
        manifest["name"] = clean_name
        manifest["updated_at"] = _utc_now()
        self._write_json(self._manifest_path(key), manifest)
        self._save_registry(registry)
        return self.get_workspace(key)

    def touch_workspace(self, workspace_id: str) -> None:
        registry = self._registry()
        key = str(workspace_id).lower()
        for row in registry.get("workspaces") or []:
            if str(row.get("id") or "").lower() == key:
                row["last_used_at"] = _utc_now()
                self._save_registry(registry)
                return

    def load_manifest(self, workspace_id: str, *, required: bool = True) -> dict:
        path = self._manifest_path(workspace_id)
        manifest = self._read_json(path, None)
        if not isinstance(manifest, dict):
            if required:
                raise ValueError("The CAD workspace manifest is missing or damaged.")
            return {}
        if str(manifest.get("workspace_id") or "").lower() != str(workspace_id).lower():
            if required:
                raise ValueError("The CAD workspace marker does not match its registry entry.")
            return {}
        manifest.setdefault("entries", {})
        return manifest

    def _save_manifest(self, workspace_id: str, manifest: dict) -> None:
        manifest["updated_at"] = _utc_now()
        self._write_json(self._manifest_path(workspace_id), manifest)
        self.touch_workspace(workspace_id)

    def checkout_descriptor(self, workspace_id: str) -> dict:
        workspace = self.get_workspace(workspace_id)
        if not workspace or not workspace.get("available"):
            raise ValueError("The selected CAD workspace is not available on this machine.")
        self.load_manifest(workspace_id)
        return {
            "workspace_id": workspace["id"],
            "workspace_name": workspace["name"],
            "workspace_machine_id": self.machine_id,
        }

    def _project(self, project_id: int) -> dict:
        project = self.project_service.get_project_by_id(int(project_id)) or {}
        if not project:
            raise ValueError("The CAD Document project was not found.")
        return project

    def _candidate_source_paths(self, document: dict) -> list[Path]:
        project = self._project(int(document["project_id"]))
        working_dir = Path(str(project.get("working_directory") or "")).expanduser()
        iteration = self.pdm_service.repo.get_current_cad_iteration(int(document["id"])) or {}
        values = [
            iteration.get("primary_path"),
            document.get("latest_creo_file_name"),
            document.get("file_name"),
        ]
        candidates = []
        for value in values:
            text = str(value or "").strip()
            if not text:
                continue
            path = Path(text).expanduser()
            if not path.is_absolute():
                path = working_dir / path
            try:
                normalized = path.resolve()
            except OSError:
                normalized = path.absolute()
            if normalized not in candidates:
                candidates.append(normalized)
        logical = self.logical_name(str(document.get("file_name") or "")).casefold()
        if working_dir.is_dir() and logical:
            versioned = []
            try:
                for path in working_dir.iterdir():
                    match = _CREO_RE.match(path.name)
                    if path.is_file() and match and match.group(1).casefold() == logical:
                        versioned.append((int(match.group(2)), path.resolve()))
            except OSError:
                pass
            for _version, path in sorted(versioned, reverse=True):
                if path not in candidates:
                    candidates.append(path)
        return candidates

    def resolve_controlled_source(self, document: dict) -> Path:
        for path in self._candidate_source_paths(document):
            if path.is_file():
                return path
        label = document.get("latest_creo_file_name") or document.get("file_name") or document.get("id")
        raise ValueError(f"The controlled CAD file could not be found for {label}.")

    def materialize_cad_document(
        self,
        workspace_id: str,
        cad_document_id: int,
        *,
        preserve_existing: bool = False,
        source_path: str | os.PathLike | None = None,
    ) -> dict:
        workspace = self.get_workspace(workspace_id)
        if not workspace or not workspace.get("available"):
            raise ValueError("The selected CAD workspace is not available.")
        document = self.pdm_service.repo.get_cad_document(int(cad_document_id))
        if not document:
            raise ValueError("The CAD Document was not found.")
        manifest = self.load_manifest(workspace_id)
        logical = self.logical_name(document.get("file_name") or "")
        logical_key = logical.casefold()
        for raw in manifest.get("entries", {}).values():
            if (
                str(raw.get("logical_file_name") or "").casefold() == logical_key
                and int(raw.get("cad_document_id") or 0) != int(cad_document_id)
            ):
                raise ValueError(
                    f"{logical} already identifies another CAD Document in workspace "
                    f"{workspace['name']}. Choose another workspace."
                )

        path = self.workspace_path(workspace_id)
        existing = [
            child for child in path.iterdir()
            if child.is_file() and self.logical_name(child.name).casefold() == logical_key
            and _CREO_RE.match(child.name)
        ]
        existing_entry = (manifest.get("entries") or {}).get(str(int(cad_document_id)))
        if existing and not existing_entry and not preserve_existing:
            raise ValueError(
                f"Workspace {workspace['name']} already contains unmanaged iterations of "
                f"{logical}. Review that workspace or choose another one."
            )

        if source_path:
            source = Path(source_path).expanduser().resolve()
        elif preserve_existing and existing:
            def _version_key(candidate: Path) -> tuple[int, str]:
                match = _CREO_RE.match(candidate.name)
                version = int(match.group(2)) if match else 0
                return version, candidate.name.casefold()
            source = max(existing, key=_version_key).resolve()
        else:
            source = self.resolve_controlled_source(document)
        if not source.is_file():
            raise ValueError("The controlled CAD source file does not exist.")
        baseline_hash = self._sha256(source)
        destination = path / source.name
        if destination.exists():
            if self._sha256(destination) != baseline_hash and not preserve_existing:
                raise ValueError(
                    f"{destination.name} already exists with different content in the workspace."
                )
        else:
            shutil.copy2(source, destination)

        entry = {
            "cad_document_id": int(cad_document_id),
            "project_id": int(document["project_id"]),
            "logical_file_name": logical,
            "baseline_file_name": source.name,
            "baseline_sha256": baseline_hash,
            "baseline_cad_revision": str(document.get("revision") or ""),
            "baseline_cad_iteration": int(document.get("iteration") or 0),
            "checkout_user_id": int(document["checked_out_by"])
            if document.get("checked_out_by") is not None else None,
            "editable": True,
            # When checkout is started from the staging dialog, the source is
            # already the user's workspace file.  The local file should remain
            # stageable immediately after checkout even if it equals the newly
            # recorded baseline hash.
            "stage_ready_after_checkout": bool(source_path),
            "materialized_at": _utc_now(),
        }
        manifest.setdefault("entries", {})[str(int(cad_document_id))] = entry
        self._save_manifest(workspace_id, manifest)
        return {
            **entry,
            "workspace_id": workspace_id,
            "workspace_name": workspace["name"],
            "workspace_path": str(path),
            "path": str(destination),
        }

    def materialize_cad_document_package(
        self,
        workspace_id: str,
        cad_document_id: int,
        *,
        preserve_existing: bool = False,
        source_path: str | os.PathLike | None = None,
        include_related_drawings: bool = True,
    ) -> list[dict]:
        """Materialize a model CAD Document and its related DRW documents."""
        primary = self.materialize_cad_document(
            workspace_id,
            int(cad_document_id),
            preserve_existing=preserve_existing,
            source_path=source_path,
        )
        materialized = [primary]
        if not include_related_drawings:
            return materialized
        document = self.pdm_service.repo.get_cad_document(int(cad_document_id)) or {}
        if str(document.get("category") or "").upper() == "DRAWING":
            return materialized
        for drawing in self.pdm_service.repo.list_related_drawings(int(cad_document_id)) or []:
            materialized.append(
                self.materialize_cad_document(
                    workspace_id,
                    int(drawing["id"]),
                    preserve_existing=True,
                )
            )
        return materialized

    def release_cad_document(self, workspace_id: str | None, cad_document_id: int) -> None:
        if not workspace_id:
            return
        try:
            manifest = self.load_manifest(str(workspace_id))
        except ValueError:
            return
        if manifest.setdefault("entries", {}).pop(str(int(cad_document_id)), None) is not None:
            self._save_manifest(str(workspace_id), manifest)

    def scan_workspace(self, workspace_id: str, project_id: int, user_id: int) -> list[dict]:
        workspace = self.get_workspace(workspace_id)
        if not workspace or not workspace.get("available"):
            raise ValueError("The selected CAD workspace is not available.")
        manifest = self.load_manifest(workspace_id)
        path = self.workspace_path(workspace_id)
        grouped: dict[str, list[tuple[int, Path]]] = {}
        for child in path.iterdir():
            if not child.is_file():
                continue
            match = _CREO_RE.match(child.name)
            if match:
                grouped.setdefault(match.group(1).casefold(), []).append(
                    (int(match.group(2)), child)
                )

        entries_by_logical = {
            str(entry.get("logical_file_name") or "").casefold(): dict(entry)
            for entry in (manifest.get("entries") or {}).values()
        }
        rows = []
        for logical_key, versions in sorted(grouped.items()):
            _version, candidate = max(versions, key=lambda value: (value[0], value[1].name.casefold()))
            entry = entries_by_logical.get(logical_key, {})
            document = None
            if entry.get("cad_document_id") is not None:
                document = self.pdm_service.repo.get_cad_document(int(entry["cad_document_id"]))
            if document is None:
                document = self.pdm_service.repo.get_cad_document_by_file(int(project_id), candidate.name)
            candidate_hash = self._sha256(candidate)
            baseline_hash = str(entry.get("baseline_sha256") or "")
            modified = bool(not baseline_hash or candidate_hash.casefold() != baseline_hash.casefold())
            status = "UNMAPPED"
            detail = "No managed CAD Document matches this file in the active project."
            selectable = False
            if document:
                document_project = int(document.get("project_id") or 0)
                owner = document.get("checked_out_by")
                if document_project != int(project_id):
                    status, detail = "OTHER_PROJECT", "This CAD Document belongs to another project."
                elif owner is None:
                    status, detail = "NOT_CHECKED_OUT", "Check out this CAD Document before staging it."
                elif int(owner) != int(user_id):
                    status, detail = "CHECKED_OUT_BY_OTHER", "This CAD Document is checked out by another user."
                elif entry.get("stage_ready_after_checkout"):
                    status, detail, selectable = (
                        "READY",
                        "Checked out from this workspace and ready to stage.",
                        True,
                    )
                elif not modified:
                    status, detail = "UNCHANGED", "The latest local content matches the checkout baseline."
                else:
                    status, detail, selectable = "READY", "Modified and ready to stage.", True
            rows.append({
                "workspace_id": workspace_id,
                "workspace_name": workspace["name"],
                "cad_document_id": int(document["id"]) if document else None,
                "project_id": int(document.get("project_id") or 0) if document else None,
                "logical_file_name": self.logical_name(candidate.name),
                "filename": candidate.name,
                "path": str(candidate),
                "candidate_sha256": candidate_hash,
                "baseline_file_name": entry.get("baseline_file_name"),
                "baseline_sha256": baseline_hash or None,
                "modified": modified,
                "status": status,
                "detail": detail,
                "selectable": selectable,
            })
        return rows

    def delete_workspace(self, workspace_id: str, *, force: bool = False) -> None:
        workspace = self.get_workspace(workspace_id)
        if not workspace:
            raise ValueError("The CAD workspace was not found.")
        active = self.pdm_service.repo.list_checked_out_cad_by_workspace(str(workspace_id))
        if active:
            labels = ", ".join(str(row.get("file_name") or row.get("id")) for row in active[:4])
            raise ValueError(
                f"Workspace {workspace['name']} still owns active CAD checkouts: {labels}. "
                "Check in or undo those CAD Documents first."
            )
        path = self.workspace_path(workspace_id)
        manifest = self.load_manifest(workspace_id)
        unknown = [child for child in path.iterdir() if child.name != _MANIFEST_NAME]
        if (manifest.get("entries") or unknown) and not force:
            raise ValueError(
                "The workspace still contains CAD or other files. Use confirmed force deletion "
                "only after preserving any work you need."
            )
        # Re-resolve and re-check containment immediately before recursive deletion.
        resolved = path.resolve()
        if resolved.parent != self.root or resolved == self.root:
            raise ValueError("Refusing to delete an unsafe workspace path.")
        marker = self.load_manifest(workspace_id)
        if str(marker.get("workspace_id") or "").lower() != str(workspace_id).lower():
            raise ValueError("Refusing to delete a workspace with an invalid marker.")
        shutil.rmtree(resolved)
        registry = self._registry()
        registry["workspaces"] = [
            row for row in registry.get("workspaces") or []
            if str(row.get("id") or "").lower() != str(workspace_id).lower()
        ]
        self._save_registry(registry)
