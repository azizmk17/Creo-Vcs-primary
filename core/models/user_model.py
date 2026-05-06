

from dataclasses import dataclass

@dataclass
class User:
    id: int
    username: str
    email: str
    password: str
    role_id: int | None = None
    is_admin: int = 0
