from dataclasses import dataclass
from typing import Optional


@dataclass
class PartFileVersion:
    id: int
    file_id: int
    version_no: int
    original_filename: str
    vault_rel_path: str
    sha256: Optional[str] = None
    size_bytes: Optional[int] = None
    note: Optional[str] = None
    revision: Optional[str] = None
    created_by: Optional[int] = None
    created_at: Optional[str] = None
    lifecycle_state: Optional[str] = None  # WIP | Released | Obsolete
    released_by: Optional[int] = None
    released_at: Optional[str] = None
    root_project_id: Optional[int] = None
    project_version_label: Optional[str] = None
    object_iteration_id: Optional[int] = None
    storage_scheme: str = "legacy"
    source_kind: str = "manual"
    source_commit_id: Optional[str] = None
    integrity_status: str = "Unknown"
    derived_from_version_id: Optional[int] = None
    deleted_at: Optional[str] = None
