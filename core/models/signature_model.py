from dataclasses import dataclass
from typing import Optional

@dataclass
class Signature:
    action: str
    note : str
    timestamp: str
    username: Optional[str] = None