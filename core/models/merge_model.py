

from dataclasses import dataclass
from typing import Optional


@dataclass
class Merge:
    id: int
    part_id: int
    status: Optional[str]
    type: str
    filename: str
    title: Optional[str]
    commit_id: Optional[str]
    designer_username: str
    
    
