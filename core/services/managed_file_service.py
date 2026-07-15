import hashlib
import os
import re
from typing import Optional

from core.repositories.bom_repository import BomRepository
from core.repositories.bom_revision_repository import BomRevisionRepository
from core.repositories.managed_file_repository import ManagedFileRepository
from core.services.part_file_service import PartFileService
from core.services.project_service import ProjectService
from core.session_manager import SessionManager
from utils import ensure_dir_exists, safe_copy2, safe_exists, safe_getsize, safe_open


class ManagedFileService:
    """Unifies Creo content and document attachments for a BOM object iteration."""

    ROLE_LABELS = {
        "native_cad": "Native CAD",
        "drawing": "Drawing",
        "generated_pdf": "Generated PDF",
        "generated_step": "Generated STEP",
        "document": "Document",
    }

    def __init__(
        self,
        repo: Optional[ManagedFileRepository] = None,
        part_file_service: Optional[PartFileService] = None,
    ):
        self.repo = repo or ManagedFileRepository()
        self.part_file_service = part_file_service or PartFileService()
        self.bom_repo = BomRepository()
        self.revision_repo = BomRevisionRepository()
        self.project_service = ProjectService()
        self.session = SessionManager()
        self._username_cache = {}

    def _username(self, user_id) -> str:
        if user_id is None:
            return ""
        try:
            key = int(user_id)
        except (TypeError, ValueError):
            return str(user_id or "")
        if key not in self._username_cache:
            try:
                with self.repo.get_conn() as conn:
                    row = conn.execute(
                        "SELECT username FROM users WHERE id=?", (key,)
                    ).fetchone()
                self._username_cache[key] = str(row["username"] or "") if row else str(key)
            except Exception:
                self._username_cache[key] = str(key)
        return self._username_cache[key]

    @staticmethod
    def role_for_type(file_type: str, preferred: str = "") -> str:
        if preferred:
            return str(preferred).strip().lower()
        normalized = str(file_type or "").strip().upper()
        if normalized == "PDF":
            return "generated_pdf"
        if normalized in {"STEP", "STP"}:
            return "generated_step"
        if normalized in {"DRW", "DWG", "DXF"}:
            return "drawing"
        if normalized in {"PRT", "ASM"}:
            return "native_cad"
        return "document"

    @classmethod
    def role_label(cls, role: str) -> str:
        normalized = str(role or "document").strip().lower()
        return cls.ROLE_LABELS.get(normalized, normalized.replace("_", " ").title())

    @staticmethod
    def creo_iteration(filename: str):
        match = re.search(r"\.(\d+)$", str(filename or "").strip())
        return int(match.group(1)) if match else None

    @staticmethod
    def _safe_filename(filename: str) -> str:
        name = os.path.basename(str(filename or "file"))
        return "".join(ch if ch.isalnum() or ch in "._- ()" else "_" for ch in name)

    @staticmethod
    def _sha256(path: str) -> str:
        digest = hashlib.sha256()
        with safe_open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _project_for_bom(self, bom_id: int) -> dict:
        part = self.bom_repo.get_by_id(int(bom_id))
        project_id = getattr(part, "project_id", None) if part else None
        if not project_id:
            project_id = self.session.project_id
        return self.project_service.get_project_by_id(int(project_id)) or {} if project_id else {}

    def _family_root(self, bom_id: int) -> str:
        project = self._project_for_bom(int(bom_id))
        root_id = project.get("root_project_id") or project.get("id")
        root = self.project_service.get_project_by_id(int(root_id)) or {} if root_id else {}
        return str(root.get("working_directory") or project.get("working_directory") or "").strip()

    def _working_dir(self, bom_id: int) -> str:
        return str(self._project_for_bom(int(bom_id)).get("working_directory") or "").strip()

    def store_blob(self, bom_id: int, source_path: str) -> dict:
        if not source_path or not safe_exists(source_path):
            raise ValueError("Source file does not exist.")
        root = self._family_root(int(bom_id))
        if not root:
            raise ValueError("Project working directory is not configured.")
        sha256 = self._sha256(source_path)
        filename = self._safe_filename(source_path)
        rel_path = os.path.join(
            ".nexus", "vault", "blobs", sha256[:2], sha256, filename
        )
        destination = os.path.join(root, rel_path)
        if not safe_exists(destination):
            ensure_dir_exists(os.path.dirname(destination))
            safe_copy2(source_path, destination)
        return {
            "storage_scheme": "managed_blob",
            "vault_rel_path": rel_path,
            "sha256": sha256,
            "size_bytes": safe_getsize(destination),
            "integrity_status": "Verified",
            "path": destination,
        }

    def resolve_manifest_path(self, entry: dict) -> str:
        version_id = entry.get("part_file_version_id")
        if version_id:
            version = self.part_file_service.repo.get_version_by_id(int(version_id))
            if version:
                return self.part_file_service.resolve_version_path(version)
        rel_path = str(entry.get("vault_rel_path") or "").strip()
        if not rel_path:
            return ""
        if str(entry.get("storage_scheme") or "").lower() == "managed_blob":
            return os.path.join(self._family_root(int(entry["bom_id"])), rel_path)
        return os.path.join(self._working_dir(int(entry["bom_id"])), rel_path)

    def _native_entry(self, bom_id: int, context: dict, role: str, filename: str) -> dict:
        working_path = os.path.join(self._working_dir(int(bom_id)), filename) if filename else ""
        exists = bool(working_path and safe_exists(working_path))
        lowered = str(filename or "").lower()
        if ".prt." in lowered or lowered.endswith(".prt"):
            extension = "PRT"
        elif ".asm." in lowered or lowered.endswith(".asm"):
            extension = "ASM"
        elif ".drw." in lowered or lowered.endswith(".drw"):
            extension = "DRW"
        else:
            extension = os.path.splitext(filename)[1].lstrip(".").upper()
        return {
            "bom_id": int(bom_id),
            "iteration_id": int(context["iteration_id"]),
            "file_role": role,
            "file_type": extension,
            "source_kind": "creo",
            "part_file_id": None,
            "part_file_version_id": None,
            "filename": filename,
            "file_revision": "",
            "creo_iteration": self.creo_iteration(filename),
            "storage_scheme": "working_reference",
            "vault_rel_path": filename,
            "sha256": None,
            "size_bytes": safe_getsize(working_path) if exists else None,
            "integrity_status": "Available" if exists else "Missing",
            "lifecycle_state": str(context.get("state") or "In Work"),
            "source_commit_id": context.get("source_commit_id"),
            "created_by": context.get("iteration_created_by") or context.get("created_by"),
            "created_at": context.get("iteration_created_at") or context.get("created_at"),
            "version_label": context.get("version_label") or "",
            "path": working_path,
        }

    def capture_iteration(
        self,
        bom_id: int,
        iteration_id: int,
        source_commit_id=None,
        inherit_from_iteration_id=None,
    ) -> list[dict]:
        contexts = self.revision_repo.get_iteration_object_contexts([int(iteration_id)])
        context = contexts.get(int(iteration_id))
        if not context or int(context.get("bom_id") or 0) != int(bom_id):
            raise ValueError("The BOM iteration was not found.")
        context["iteration_id"] = int(iteration_id)
        context["source_commit_id"] = source_commit_id or context.get("source_commit_id")
        context["iteration_created_by"] = context.get("created_by")
        context["iteration_created_at"] = context.get("created_at")

        if inherit_from_iteration_id is not None:
            self.repo.inherit_iteration_files(
                int(bom_id),
                int(inherit_from_iteration_id),
                int(iteration_id),
                lifecycle_state=str(context.get("state") or "In Work"),
            )

        captured = []
        for role, field in (("native_cad", "filename"), ("drawing", "drawing")):
            filename = str(context.get(field) or "").strip()
            if not filename:
                continue
            entry = self._native_entry(int(bom_id), context, role, filename)
            source_path = entry.pop("path", "")
            if source_path and safe_exists(source_path):
                entry.update(self.store_blob(int(bom_id), source_path))
                entry.pop("path", None)
            elif inherit_from_iteration_id is not None:
                inherited = [
                    item for item in self.repo.list_for_iteration(int(iteration_id))
                    if str(item.get("file_role") or "") == role
                ]
                if inherited:
                    captured.extend(inherited)
                    continue
            self.repo.upsert(entry)
            captured.append(entry)

        attachments = self.part_file_service.list_attachments(int(bom_id))
        if str(context.get("state") or "").strip().lower() != "released":
            self.repo.prune_iteration_documents(
                int(iteration_id), [part_file.id for part_file in attachments]
            )
        for part_file in attachments:
            version = self.part_file_service.get_active_version(int(part_file.id))
            if not version:
                continue
            role = self.role_for_type(part_file.file_type, getattr(part_file, "file_role", ""))
            entry = {
                "bom_id": int(bom_id),
                "iteration_id": int(iteration_id),
                "file_role": role,
                "file_type": str(part_file.file_type or ""),
                "source_kind": str(getattr(version, "source_kind", "manual") or "manual"),
                "part_file_id": int(part_file.id),
                "part_file_version_id": int(version.id),
                "filename": str(version.original_filename or ""),
                "file_revision": str(getattr(version, "revision", "") or ""),
                "creo_iteration": self.creo_iteration(version.original_filename),
                "storage_scheme": str(getattr(version, "storage_scheme", "legacy") or "legacy"),
                "vault_rel_path": str(version.vault_rel_path or ""),
                "sha256": getattr(version, "sha256", None),
                "size_bytes": getattr(version, "size_bytes", None),
                "integrity_status": str(getattr(version, "integrity_status", "Unknown") or "Unknown"),
                "lifecycle_state": str(context.get("state") or version.lifecycle_state or "In Work"),
                "source_commit_id": source_commit_id or getattr(version, "source_commit_id", None),
                "created_by": getattr(version, "created_by", None),
            }
            self.repo.upsert(entry)
            captured.append(entry)
        return captured

    def capture_current_iteration(self, bom_id: int, source_commit_id=None) -> list[dict]:
        context = self.revision_repo.get_current_context(int(bom_id))
        return self.capture_iteration(
            int(bom_id), int(context["current_iteration_id"]), source_commit_id
        )

    def list_current_files(self, bom_id: int) -> list[dict]:
        context = self.revision_repo.get_current_context(int(bom_id))
        iteration_id = int(context["current_iteration_id"])
        entries = self.repo.list_for_iteration(iteration_id)
        context = dict(context)
        context["iteration_id"] = iteration_id
        if str(context.get("state") or "").strip().lower() != "released":
            visible_entries = []
            for entry in entries:
                file_id = entry.get("part_file_id")
                if file_id:
                    part_file = self.part_file_service.repo.get_file_by_id(int(file_id))
                    if not part_file or getattr(part_file, "deleted_at", None):
                        continue
                visible_entries.append(entry)
            entries = visible_entries

        object_context = self.revision_repo.get_iteration_object_contexts([iteration_id]).get(iteration_id, {})
        for role, field in (("native_cad", "filename"), ("drawing", "drawing")):
            filename = str(object_context.get(field) or "").strip()
            if filename and not any(str(item.get("file_role")) == role for item in entries):
                entries.append(self._native_entry(int(bom_id), context, role, filename))

        for part_file in self.part_file_service.list_attachments(int(bom_id)):
            version = self.part_file_service.get_active_version(int(part_file.id))
            if not version:
                continue
            existing = [
                item for item in entries
                if item.get("part_file_id") is not None
                and int(item.get("part_file_id")) == int(part_file.id)
            ]
            if existing and any(
                int(item.get("part_file_version_id") or 0) == int(version.id)
                for item in existing
            ):
                continue
            if existing and str(context.get("state") or "").strip().lower() == "released":
                continue
            if existing:
                entries = [item for item in entries if item not in existing]
            path = self.part_file_service.resolve_version_path(version)
            exists = bool(path and safe_exists(path))
            role = self.role_for_type(part_file.file_type, getattr(part_file, "file_role", ""))
            entries.append({
                "bom_id": int(bom_id),
                "iteration_id": iteration_id,
                "file_role": role,
                "file_type": str(part_file.file_type or ""),
                "source_kind": str(getattr(version, "source_kind", "manual") or "manual"),
                "part_file_id": int(part_file.id),
                "part_file_version_id": int(version.id),
                "filename": str(version.original_filename or ""),
                "file_revision": str(getattr(version, "revision", "") or ""),
                "creo_iteration": self.creo_iteration(version.original_filename),
                "storage_scheme": str(getattr(version, "storage_scheme", "legacy") or "legacy"),
                "vault_rel_path": str(version.vault_rel_path or ""),
                "sha256": getattr(version, "sha256", None),
                "size_bytes": getattr(version, "size_bytes", None),
                "integrity_status": "Available" if exists else "Missing",
                "lifecycle_state": str(context.get("state") or version.lifecycle_state or ""),
                "source_commit_id": getattr(version, "source_commit_id", None),
                "created_by": getattr(version, "created_by", None),
                "created_at": getattr(version, "created_at", None),
                "version_label": context.get("version_label") or "",
                "bound_to": context.get("version_label") or "",
                "path": path,
            })

        rows = []
        for entry in entries:
            item = dict(entry)
            item["role"] = item.get("file_role") or "document"
            item["role_label"] = self.role_label(item["role"])
            if not item.get("file_revision") and item.get("part_file_version_id"):
                version = self.part_file_service.repo.get_version_by_id(
                    int(item["part_file_version_id"])
                )
                item["file_revision"] = str(getattr(version, "revision", "") or "") if version else ""
            item["bound_to"] = item.get("bound_to") or context.get("version_label") or ""
            item["source"] = str(item.get("source_kind") or "legacy").replace("_", " ").title()
            item["state"] = str(item.get("lifecycle_state") or context.get("state") or "")
            item["health"] = str(item.get("integrity_status") or "Unknown")
            item["updated"] = item.get("created_at") or context.get("iteration_created_at") or ""
            item["created_by_label"] = self._username(item.get("created_by"))
            item["file_id"] = item.get("part_file_id")
            item["version_id"] = item.get("part_file_version_id")
            item["path"] = item.get("path") or self.resolve_manifest_path(item)
            rows.append(item)
        order = {"native_cad": 0, "drawing": 1, "generated_pdf": 2, "generated_step": 3, "document": 4}
        rows.sort(key=lambda row: (order.get(row["role"], 9), str(row.get("filename") or "").lower()))
        return rows

    def list_file_history(self, bom_id: int, role: str, file_id=None) -> list[dict]:
        if file_id:
            rows = []
            for version in self.part_file_service.list_versions(int(file_id)):
                bindings = self.repo.iteration_labels_for_version(int(version.id))
                iteration_label = ", ".join(bindings)
                if not iteration_label:
                    part_file = self.part_file_service.repo.get_file_by_id(int(file_id))
                    is_active = bool(
                        part_file and int(part_file.active_version_id or 0) == int(version.id)
                    )
                    if is_active:
                        current = self.revision_repo.get_current_context(int(bom_id))
                        iteration_label = str(current.get("version_label") or "")
                rows.append({
                    "version_id": int(version.id),
                    "bound_to": iteration_label,
                    "role": role,
                    "role_label": self.role_label(role),
                    "filename": str(version.original_filename or ""),
                    "file_revision": str(getattr(version, "revision", "") or ""),
                    "creo_iteration": self.creo_iteration(version.original_filename),
                    "source": str(getattr(version, "source_kind", "manual") or "manual").replace("_", " ").title(),
                    "state": str(version.lifecycle_state or ""),
                    "health": str(getattr(version, "integrity_status", "Unknown") or "Unknown"),
                    "created_at": version.created_at or "",
                    "created_by_label": self._username(getattr(version, "created_by", None)),
                    "note": version.note or "",
                    "path": self.part_file_service.resolve_version_path(version),
                })
            return rows

        rows = []
        for iteration in self.revision_repo.list_iterations(int(bom_id)):
            iteration_id = int(iteration["id"])
            manifest = [
                item for item in self.repo.list_for_iteration(iteration_id)
                if str(item.get("file_role") or "") == str(role or "")
            ]
            if manifest:
                for item in manifest:
                    rows.append({
                        "version_id": None,
                        "bound_to": iteration.get("version_label") or "",
                        "role": role,
                        "role_label": self.role_label(role),
                        "filename": item.get("filename") or "",
                        "file_revision": item.get("file_revision") or "",
                        "creo_iteration": item.get("creo_iteration"),
                        "source": str(item.get("source_kind") or "legacy").replace("_", " ").title(),
                        "state": iteration.get("state") or "",
                        "health": item.get("integrity_status") or "Unknown",
                        "created_at": item.get("created_at") or iteration.get("created_at") or "",
                        "created_by_label": self._username(item.get("created_by") or iteration.get("created_by")),
                        "note": iteration.get("checkin_note") or "",
                        "path": self.resolve_manifest_path(item),
                    })
                continue
            cad = self.revision_repo.get_iteration_cad_files(iteration_id)
            filename = cad.get("filename") if role == "native_cad" else cad.get("drawing")
            if filename:
                rows.append({
                    "version_id": None,
                    "bound_to": iteration.get("version_label") or "",
                    "role": role,
                    "role_label": self.role_label(role),
                    "filename": filename,
                    "file_revision": "",
                    "creo_iteration": self.creo_iteration(filename),
                    "source": "Legacy Snapshot",
                    "state": iteration.get("state") or "",
                    "health": "Unknown",
                    "created_at": iteration.get("created_at") or "",
                    "created_by_label": self._username(iteration.get("created_by")),
                    "note": iteration.get("checkin_note") or "",
                    "path": "",
                })
        return rows

    def release_current_iteration(self, bom_id: int) -> None:
        context = self.revision_repo.get_current_context(int(bom_id))
        self.capture_current_iteration(int(bom_id), context.get("source_commit_id"))
        self.repo.set_iteration_lifecycle(int(context["current_iteration_id"]), "Released")
