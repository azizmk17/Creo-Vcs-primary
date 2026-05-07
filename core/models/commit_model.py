

from dataclasses import dataclass
from typing import Optional


@dataclass
class Commit:
    id: int
    commit_id: str
    title: str
    part_id: int
    type: str
    filename: str
    file_path: Optional[str] = None
    base_file_name: Optional[str] = None
    designer: Optional[int] = None
    message: Optional[str] = None
    committed_by: Optional[int] = None
    checked_by: Optional[int] = None
    status: Optional[str] = None
    approved_version: Optional[str] = None
    last_snapshot: Optional[int] = None
    committed_at: Optional[str] = None
    merged_at: Optional[str] = None
    signature: Optional[str] = None
    project_id: Optional[int] = None
    username: str = ""  # Designer username (when joined)
    step_compare_enabled: Optional[int] = 0
    step_file_path: Optional[str] = None
    step_prev_file_path: Optional[str] = None
    step_diff_path: Optional[str] = None
    step_diff_summary: Optional[str] = None
    step_diff_status: Optional[str] = None
    step_error: Optional[str] = None
    step_face_map_path: Optional[str] = None
