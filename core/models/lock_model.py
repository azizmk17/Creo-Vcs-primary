from dataclasses import dataclass
from typing import Optional

@dataclass
class Locks:
    id: Optional[int]
    part_id: Optional[int]
    user_id: Optional[int]