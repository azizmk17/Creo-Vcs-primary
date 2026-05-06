from dataclasses import dataclass
from typing import Optional

@dataclass
class Lock_logs:
    id: Optional[int]
    user_name: Optional[str]
    part_id: str
    user_id: str
    action: str
    timestamp: str
    signature: int