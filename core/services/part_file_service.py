import hashlib
import os
from typing import List, Optional, Tuple

from core.repositories.bom_repository import BomRepository
from core.repositories.part_file_repository import PartFileRepository
from core.session_manager import SessionManager
from core.services.project_service import ProjectService
from core.models.part_file_model import PartFile
from core.models.part_file_version_model import PartFileVersion
from utils import ensure_dir_exists, safe_copy2, safe_exists, safe_open, safe_remove


class PartFileService:
    def __init__(self, repo: Optional[PartFileRepository] = None):
        self.repo = repo or PartFileRepository()
        self.bom_repo = BomRepository()
        self.session = SessionManager()
        self.project_service = ProjectService()

    def _assert_part_revision_mutable(self, part_id: int) -> None:
        part = self.bom_repo.get_by_id(int(part_id))
        if not part:
            raise ValueError("BOM item was not found.")
        state = str(
            getattr(part, "lifecycle_state", "") or getattr(part, "status", "")
        ).strip().lower()
        if state == "released":
            revision = str(getattr(part, "revision", "") or "").strip()
            raise ValueError(
                f"Revision {revision} is released and its associated files are immutable. "
                "Create a new revision first."
            )

    def _working_dir(self) -> str:
        project_id = self.session.project_id
        if not project_id:
            return ""
        project = self.project_service.get_project_by_id(project_id)
        return (project or {}).get("working_directory", "") or ""

    def _vault_root(self) -> str:
        wd = self._working_dir()
        return os.path.join(wd, "vault")

    def _hash_file_sha256(self, path: str) -> str:
        h = hashlib.sha256()
        with safe_open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    def _safe_filename(self, name: str) -> str:
        # keep simple: replace risky characters
        return "".join(ch if ch.isalnum() or ch in "._- ()" else "_" for ch in name)

    def _version_dest_relpath(self, part_id: int, file_id: int, version_no: int, original_filename: str) -> str:
        original_filename = self._safe_filename(os.path.basename(original_filename))
        # Use stable, collision-free folders by IDs
        return os.path.join("vault", f"part_{part_id}", f"file_{file_id}", f"v{version_no}", original_filename)

    def _project_version_context(self) -> Tuple[Optional[int], Optional[str]]:
        project_id = self.session.project_id
        if not project_id:
            return None, None
        project = self.project_service.get_project_by_id(project_id) or {}
        return project.get("root_project_id"), project.get("version_label")

    def _part_revision(self, part_id: int) -> str:
        try:
            part = self.bom_repo.get_by_id(int(part_id))
            return str(getattr(part, "revision", "") or "").strip().upper()
        except Exception:
            return ""

    def _working_dir_for_root_label(self, root_project_id: Optional[int], version_label: Optional[str]) -> str:
        try:
            if root_project_id and version_label:
                proj = self.project_service.get_project_by_root_and_label(int(root_project_id), str(version_label).strip())
                wd = (proj or {}).get("working_directory", "") or ""
                if wd:
                    return wd
        except Exception:
            pass
        return ""

    def _working_dir_for_version(self, root_project_id: Optional[int], version_label: Optional[str]) -> str:
        """Return the working directory for the project version that created the file version.

        Fallback order:
        1) Exact (root_project_id, version_label)
        2) Root project's 'A' (common storage for legacy attachments)
        3) Current session project working directory
        """
        wd = self._working_dir_for_root_label(root_project_id, version_label)
        if wd:
            return wd

        # Common case: older attachments were created before we stamped the label,
        # but physically stored under version A.
        wd_a = self._working_dir_for_root_label(root_project_id, "A") if root_project_id else ""
        if wd_a:
            return wd_a

        return self._working_dir()

    def create_attachment(
        self,
        part_id: int,
        file_type: str,
        display_name: str,
        description: str,
        source_path: str,
        note: str = "",
        revision_override: Optional[str] = None,
    ) -> int:
        if not part_id:
            raise ValueError("part_id is required")
        self._assert_part_revision_mutable(int(part_id))
        wd = self._working_dir()
        if not wd:
            raise ValueError("Project working directory is not set")
        if not source_path or not safe_exists(source_path):
            raise ValueError("Source file does not exist")

        created_by = self.session.user_id
        root_project_id, project_version_label = self._project_version_context()
        revision = self._part_revision(part_id) if revision_override is None else str(revision_override or "")
        file_id = self.repo.create_file(part_id, file_type, display_name, description, created_by=created_by)

        version_no = 1
        rel_path = self._version_dest_relpath(part_id, file_id, version_no, source_path)
        abs_path = os.path.join(wd, rel_path)
        ensure_dir_exists(os.path.dirname(abs_path))

        safe_copy2(source_path, abs_path)
        sha256 = self._hash_file_sha256(abs_path)
        size_bytes = os.path.getsize(abs_path)

        version_id = self.repo.add_version(
            file_id=file_id,
            version_no=version_no,
            original_filename=os.path.basename(source_path),
            vault_rel_path=rel_path,
            sha256=sha256,
            size_bytes=size_bytes,
            note=note,
            revision=revision,
            created_by=created_by,
            root_project_id=root_project_id,
            project_version_label=project_version_label,
        )
        self.repo.set_active_version(file_id, version_id)
        return file_id

    def add_new_version(self, file_id: int, source_path: str, note: str = "") -> int:
        pf = self.repo.get_file_by_id(file_id)
        if not pf:
            raise ValueError("Attachment not found")
        self._assert_part_revision_mutable(int(pf.part_id))
        wd = self._working_dir()
        if not wd:
            raise ValueError("Project working directory is not set")
        if not source_path or not safe_exists(source_path):
            raise ValueError("Source file does not exist")

        created_by = self.session.user_id
        root_project_id, project_version_label = self._project_version_context()
        revision = self._part_revision(pf.part_id)
        version_no = self.repo.get_next_version_no(file_id)

        rel_path = self._version_dest_relpath(pf.part_id, file_id, version_no, source_path)
        abs_path = os.path.join(wd, rel_path)
        ensure_dir_exists(os.path.dirname(abs_path))

        safe_copy2(source_path, abs_path)
        sha256 = self._hash_file_sha256(abs_path)
        size_bytes = os.path.getsize(abs_path)

        version_id = self.repo.add_version(
            file_id=file_id,
            version_no=version_no,
            original_filename=os.path.basename(source_path),
            vault_rel_path=rel_path,
            sha256=sha256,
            size_bytes=size_bytes,
            note=note,
            revision=revision,
            created_by=created_by,
            root_project_id=root_project_id,
            project_version_label=project_version_label,
        )
        self.repo.set_active_version(file_id, version_id)
        return version_id

    def add_new_version_with_revision(
        self,
        file_id: int,
        source_path: str,
        note: str = "",
        revision: Optional[str] = None,
    ) -> int:
        pf = self.repo.get_file_by_id(file_id)
        if not pf:
            raise ValueError("Attachment not found")
        self._assert_part_revision_mutable(int(pf.part_id))
        wd = self._working_dir()
        if not wd:
            raise ValueError("Project working directory is not set")
        if not source_path or not safe_exists(source_path):
            raise ValueError("Source file does not exist")

        created_by = self.session.user_id
        root_project_id, project_version_label = self._project_version_context()
        version_no = self.repo.get_next_version_no(file_id)

        rel_path = self._version_dest_relpath(pf.part_id, file_id, version_no, source_path)
        abs_path = os.path.join(wd, rel_path)
        ensure_dir_exists(os.path.dirname(abs_path))

        safe_copy2(source_path, abs_path)
        sha256 = self._hash_file_sha256(abs_path)
        size_bytes = os.path.getsize(abs_path)

        version_id = self.repo.add_version(
            file_id=file_id,
            version_no=version_no,
            original_filename=os.path.basename(source_path),
            vault_rel_path=rel_path,
            sha256=sha256,
            size_bytes=size_bytes,
            note=note,
            revision=str(revision or ""),
            created_by=created_by,
            root_project_id=root_project_id,
            project_version_label=project_version_label,
        )
        self.repo.set_active_version(file_id, version_id)
        return version_id

    def upsert_part_file_version(
        self,
        part_id: int,
        file_type: str,
        source_path: str,
        note: str = "",
        revision: Optional[str] = None,
        display_name: Optional[str] = None,
        description: str = "",
    ) -> tuple[int, int]:
        normalized_type = str(file_type or "").strip().upper()
        if not normalized_type:
            raise ValueError("file_type is required")

        existing = None
        for attachment in self.list_attachments(part_id):
            if str(attachment.file_type or "").strip().upper() == normalized_type:
                existing = attachment
                break

        if existing:
            version_id = self.add_new_version_with_revision(
                existing.id,
                source_path,
                note=note,
                revision=revision,
            )
            return existing.id, version_id

        file_id = self.create_attachment(
            part_id=part_id,
            file_type=normalized_type,
            display_name=display_name or os.path.splitext(os.path.basename(source_path))[0],
            description=description,
            source_path=source_path,
            note=note,
            revision_override=revision,
        )
        active = self.get_active_version(file_id)
        return file_id, int(active.id) if active else 0

    def list_attachments(self, part_id: int) -> List[PartFile]:
        return self.repo.get_files_for_part(part_id)

    def list_versions(self, file_id: int) -> List[PartFileVersion]:
        return self.repo.get_versions(file_id)

    def set_active_version(self, file_id: int, version_id: int):
        part_file = self.repo.get_file_by_id(int(file_id))
        if not part_file:
            raise ValueError("Attachment not found")
        self._assert_part_revision_mutable(int(part_file.part_id))
        self.repo.set_active_version(file_id, version_id)

    def resolve_version_path(self, version: PartFileVersion) -> str:
        # Prefer the project WD that originally stored this version.
        wd = self._working_dir_for_version(getattr(version, "root_project_id", None), getattr(version, "project_version_label", None))
        if not wd:
            return ""

        p = os.path.join(wd, version.vault_rel_path)

        # If it still doesn't exist, try hard fallback to A (covers cases where the row says 'B'
        # but the physical file was copied/stored only in A's vault).
        try:
            if p and not safe_exists(p):
                root_id = getattr(version, "root_project_id", None)
                if root_id:
                    wd_a = self._working_dir_for_root_label(root_id, "A")
                    if wd_a:
                        p_a = os.path.join(wd_a, version.vault_rel_path)
                        if safe_exists(p_a):
                            return p_a
        except Exception:
            pass

        return p

    def resolve_active_path(self, file_id: int) -> str:
        pf = self.repo.get_file_by_id(file_id)
        if not pf or not pf.active_version_id:
            return ""
        ver = self.repo.get_version_by_id(pf.active_version_id)
        if not ver:
            return ""
        return self.resolve_version_path(ver)

    def get_active_version(self, file_id: int) -> Optional[PartFileVersion]:
        pf = self.repo.get_file_by_id(file_id)
        if not pf or not pf.active_version_id:
            return None
        return self.repo.get_version_by_id(pf.active_version_id)

    def release_version(self, version_id: int):
        version = self.repo.get_version_by_id(version_id)
        if version:
            part_file = self.repo.get_file_by_id(version.file_id)
            if part_file:
                from core.services.issue_service import IssueService
                IssueService().assert_no_critical_issues(
                    [int(part_file.part_id)], operation="release file", include_children=True
                )
        self.repo.release_version(version_id, released_by=self.session.user_id)

    def delete_version(self, file_id: int, version_id: int):
        pf = self.repo.get_file_by_id(file_id)
        if not pf:
            return
        self._assert_part_revision_mutable(int(pf.part_id))
        ver = self.repo.get_version_by_id(version_id)
        if ver:
            if str(getattr(ver, "lifecycle_state", "") or "").strip().lower() == "released":
                raise ValueError("Released attachment versions are immutable and cannot be deleted.")
            abs_path = self.resolve_version_path(ver)
            try:
                if abs_path and safe_exists(abs_path):
                    safe_remove(abs_path)
            except Exception:
                pass

        # clear active if needed
        self.repo.clear_active_if_matches(file_id, version_id)
        self.repo.delete_version(version_id)

        # if no active version, set latest as active
        pf = self.repo.get_file_by_id(file_id)
        if pf and not pf.active_version_id:
            versions = self.repo.get_versions(file_id)
            if versions:
                self.repo.set_active_version(file_id, versions[0].id)

    def delete_attachment(self, file_id: int):
        part_file = self.repo.get_file_by_id(int(file_id))
        if not part_file:
            return
        self._assert_part_revision_mutable(int(part_file.part_id))
        # delete files on disk (best-effort)
        versions = self.repo.get_versions(file_id)
        if any(
            str(getattr(version, "lifecycle_state", "") or "").strip().lower() == "released"
            for version in versions
        ):
            raise ValueError("An attachment containing released versions cannot be deleted.")
        for v in versions:
            abs_path = self.resolve_version_path(v)
            try:
                if abs_path and safe_exists(abs_path):
                    safe_remove(abs_path)
            except Exception:
                pass

        self.repo.delete_file(file_id)

    def update_version_revision(self, version_id: int, revision: str):
        version = self.repo.get_version_by_id(int(version_id))
        if version and str(getattr(version, "lifecycle_state", "") or "").strip().lower() == "released":
            raise ValueError("Released attachment version metadata is immutable.")
        part_file = self.repo.get_file_by_id(int(version.file_id)) if version else None
        if part_file:
            self._assert_part_revision_mutable(int(part_file.part_id))
        self.repo.update_version_metadata(
            int(version_id),
            revision=str(revision or "").strip().upper(),
            update_revision=True,
        )

    def update_version_note(self, version_id: int, note: str):
        version = self.repo.get_version_by_id(int(version_id))
        if version and str(getattr(version, "lifecycle_state", "") or "").strip().lower() == "released":
            raise ValueError("Released attachment version metadata is immutable.")
        part_file = self.repo.get_file_by_id(int(version.file_id)) if version else None
        if part_file:
            self._assert_part_revision_mutable(int(part_file.part_id))
        self.repo.update_version_metadata(
            int(version_id),
            note=str(note or "").strip(),
            update_note=True,
        )
