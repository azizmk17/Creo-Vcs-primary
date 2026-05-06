from dataclasses import dataclass
from typing import Optional


@dataclass
class PartFile:
    id: int
    part_id: int
    file_type: str
    display_name: str
    description: Optional[str] = None
    created_by: Optional[int] = None
    created_at: Optional[str] = None
    active_version_id: Optional[int] = None
