from dataclasses import dataclass
from typing import Optional

@dataclass
class BomChild:
    id: Optional[int]
    parent_id: int
    child_id: int
    quantity: int
    