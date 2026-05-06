from dataclasses import dataclass
from typing import Optional


@dataclass
class Baseline:
    id: int
    project_id: int
    name: str
    created_by: Optional[int] = None
    created_at: Optional[str] = None
    include_children: int = 1
    part_ids_json: Optional[str] = None
