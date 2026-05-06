from dataclasses import dataclass
from typing import Optional


@dataclass
class BaselineFile:
    id: int
    baseline_id: int
    part_id: int
    file_type: str  # PDF | STEP
    file_id: Optional[int] = None
    version_id: Optional[int] = None
