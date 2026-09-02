import os

from core.repositories.bom_repository import BomRepository
from core.repositories.bom_revision_repository import BomRevisionRepository
from core.repositories.lock_repository import LockRepository
from core.repositories.managed_file_repository import ManagedFileRepository
from core.services.managed_file_service import ManagedFileService
from core.services.part_file_service import PartFileService
from core.services.project_service import ProjectService
from core.session_manager import SessionManager
from utils import get_base_name, get_version_number, is_creo_file, safe_exists, safe_listdir


class CheckoutChangeService:
    """Build a single authoritative change summary for a checked-out BOM object."""

    def __init__(self):
        self.session = SessionManager()
        self.bom_repo = BomRepository()
        self.revision_repo = BomRevisionRepository()
        self.lock_repo = LockRepository()
        self.managed_repo = ManagedFileRepository()
        self.managed_files = ManagedFileService(self.managed_repo)
        self.part_files = PartFileService()
        self.project_service = ProjectService()

    @staticmethod
    def _latest_creo_file(working_dir: str, base_name: str) -> tuple[str, str]:
        wanted = str(base_name or "").strip().casefold()
        if not working_dir or not wanted:
            return "", ""
        candidates = []
        try:
            names = safe_listdir(working_dir)
        except OSError:
            names = []
        for name in names:
            if not is_creo_file(name):
                continue
            if str(get_base_name(name) or "").casefold() != wanted:
                continue
            candidates.append((int(get_version_number(name) or 0), str(name)))
        if not candidates:
            return "", ""
        _, filename = max(candidates, key=lambda value: (value[0], value[1].casefold()))
        return filename, os.path.join(working_dir, filename)

    def _working_file_status(
        self,
        *,
        bom_id: int,
        role: str,
        snapshot_field: str,
        base_field: str,
        baseline_object: dict,
        working_object: dict,
        working_dir: str,
        baseline_manifest: list[dict],
    ) -> dict:
        baseline_filename = str(baseline_object.get(snapshot_field) or "").strip()
        base_name = str(
            working_object.get(base_field)
            or get_base_name(baseline_filename)
            or ""
        ).strip()
        working_filename, working_path = self._latest_creo_file(working_dir, base_name)
        baseline_entry = next(
            (
                dict(item) for item in baseline_manifest
                if str(item.get("file_role") or "") == role
            ),
            {},
        )
        baseline_hash = str(baseline_entry.get("sha256") or "").strip()
        working_hash = ""
        modified = False
        reason = ""

        if working_path and safe_exists(working_path) and baseline_hash:
            try:
                working_hash = self.managed_files._sha256(working_path)
                modified = working_hash.casefold() != baseline_hash.casefold()
                reason = "File content differs" if modified else "Content hash matches"
            except OSError:
                working_hash = ""

        if not baseline_hash:
            baseline_version = get_version_number(baseline_filename)
            working_version = get_version_number(working_filename)
            if working_filename and not baseline_filename:
                modified = True
                reason = "New controlled file"
            elif working_filename and baseline_filename:
                modified = (
                    str(working_filename).casefold() != str(baseline_filename).casefold()
                    and (
                        working_version is None
                        or baseline_version is None
                        or int(working_version) > int(baseline_version)
                    )
                )
                reason = "New Creo file iteration" if modified else "No newer Creo file"

        display_path = working_path
        if not display_path and baseline_filename and working_dir:
            display_path = os.path.join(working_dir, baseline_filename)
        return {
            "role": role,
            "modified": bool(modified),
            "baseline_filename": baseline_filename,
            "working_filename": working_filename or baseline_filename,
            "working_path": display_path,
            "working_exists": bool(display_path and safe_exists(display_path)),
            "baseline_hash": baseline_hash,
            "working_hash": working_hash,
            "reason": reason or "No newer controlled file",
        }

    def _document_changes(self, bom_id: int, baseline_manifest: list[dict]) -> list[dict]:
        baseline = {
            int(item["part_file_id"]): int(item.get("part_file_version_id") or 0)
            for item in baseline_manifest
            if item.get("part_file_id") is not None
        }
        current = {}
        labels = {}
        for part_file in self.part_files.list_attachments(int(bom_id)):
            version = self.part_files.get_active_version(int(part_file.id))
            if not version:
                continue
            current[int(part_file.id)] = int(version.id)
            labels[int(part_file.id)] = str(version.original_filename or part_file.display_name or "Document")

        changes = []
        for file_id in sorted(set(current) - set(baseline)):
            changes.append({"kind": "added", "text": f"Added document: {labels.get(file_id, file_id)}"})
        for file_id in sorted(set(baseline) - set(current)):
            changes.append({"kind": "removed", "text": f"Removed document: {file_id}"})
        for file_id in sorted(set(current) & set(baseline)):
            if current[file_id] != baseline[file_id]:
                changes.append({"kind": "updated", "text": f"Updated document: {labels.get(file_id, file_id)}"})
        return changes

    def analyze(self, bom_id: int) -> dict:
        bom_id = int(bom_id)
        part = self.bom_repo.get_by_id(bom_id)
        if not part:
            raise ValueError("BOM item was not found.")
        lock = self.lock_repo.get_by_part(bom_id)
        if not lock:
            raise ValueError("The selected item is not checked out.")

        object_analysis = self.revision_repo.analyze_working_object(bom_id)
        context = dict(object_analysis["context"])
        project = self.project_service.get_project_by_id(int(part.project_id)) or {}
        working_dir = str(project.get("working_directory") or "").strip()
        baseline_manifest = self.managed_repo.list_for_iteration(
            int(context["current_iteration_id"])
        )
        native = self._working_file_status(
            bom_id=bom_id,
            role="native_cad",
            snapshot_field="filename",
            base_field="base_file_name",
            baseline_object=object_analysis["baseline_object"],
            working_object=object_analysis["working_object"],
            working_dir=working_dir,
            baseline_manifest=baseline_manifest,
        )
        drawing = self._working_file_status(
            bom_id=bom_id,
            role="drawing",
            snapshot_field="drawing",
            base_field="base_drw_name",
            baseline_object=object_analysis["baseline_object"],
            working_object=object_analysis["working_object"],
            working_dir=working_dir,
            baseline_manifest=baseline_manifest,
        )
        document_changes = self._document_changes(bom_id, baseline_manifest)
        metadata_changes = list(object_analysis.get("metadata_changes") or [])
        structure_changes = list(object_analysis.get("structure_changes") or [])
        part_type = str(getattr(part, "type", "") or "").strip().lower()
        cad_controlled_structure = part_type in {"asm", "assembly"} and bool(
            object_analysis["baseline_object"].get("filename")
            or getattr(part, "filename", None)
        )
        structure_requires_cad = bool(
            structure_changes and cad_controlled_structure and not native["modified"]
        )
        requires_commit = bool(native["modified"] or drawing["modified"])
        has_non_cad_changes = bool(metadata_changes or structure_changes or document_changes)
        pending_revision = str(context.get("pending_revision_code") or "").strip()
        next_version = (
            f"{pending_revision}.1"
            if pending_revision
            else f"{context.get('revision_code')}.{int(context.get('iteration_number') or 0) + 1}"
        )

        return {
            "bom_id": bom_id,
            "name": str(getattr(part, "name", "") or ""),
            "aes_number": str(getattr(part, "aes_number", "") or ""),
            "part_type": part_type,
            "current_version": str(context.get("version_label") or ""),
            "next_version": next_version,
            "current_iteration_id": int(context["current_iteration_id"]),
            "lock_user_id": int(lock.user_id),
            "working_dir": working_dir,
            "metadata_changes": metadata_changes,
            "structure_changes": structure_changes,
            "document_changes": document_changes,
            "native_cad": native,
            "drawing": drawing,
            "cad_controlled_structure": cad_controlled_structure,
            "structure_requires_cad": structure_requires_cad,
            "requires_commit": requires_commit,
            "has_non_cad_changes": has_non_cad_changes,
            "has_any_changes": bool(has_non_cad_changes or requires_commit),
            "modified_paths": [
                item["working_path"]
                for item in (native, drawing)
                if item["modified"] and item.get("working_path") and item.get("working_exists")
            ],
        }
