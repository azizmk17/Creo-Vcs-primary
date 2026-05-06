# core/services/user_service.py
from core.repositories.user_repository import UserRepository
import hashlib

class UserService:
    def __init__(self, user_repository: UserRepository):
        self.user_repository = user_repository

    def _hash_password(self, password: str) -> str:
        return hashlib.sha256((password or "").encode()).hexdigest()

    def list_usernames(self) -> list[str]:
        """Return list of usernames from User objects"""
        users = self.user_repository.all()
        return [u.username for u in users]
    
    def get_user_by_username(self, username: str):
        return self.user_repository.find_by_username(username)
    
    def get_user_by_id(self, id: int):
        return self.user_repository.find_by_id(id)
    
    def get_all_users(self):
        return self.user_repository.all()

    def create_user(self, username: str, email: str, password: str, is_admin: bool = False, role_id: int | None = None) -> int:
        username = (username or "").strip()
        email = (email or "").strip()
        if not username:
            raise ValueError("Username is required")
        if not email:
            raise ValueError("Email is required")
        if password is None or not str(password):
            raise ValueError("Password is required")

        hashed = self._hash_password(password)
        return int(self.user_repository.create(username, email, hashed, int(bool(is_admin)), role_id))

    def update_user(
        self,
        user_id: int,
        username: str | None = None,
        email: str | None = None,
        password: str | None = None,
        is_admin: bool | None = None,
        role_id: int | None = None,
    ) -> bool:
        hashed = None
        if password is not None:
            if not str(password):
                raise ValueError("Password cannot be empty")
            hashed = self._hash_password(password)

        return bool(
            self.user_repository.update(
                int(user_id),
                username=(username.strip() if isinstance(username, str) else username),
                email=(email.strip() if isinstance(email, str) else email),
                password=hashed,
                is_admin=(int(bool(is_admin)) if is_admin is not None else None),
                role_id=role_id,
            )
        )

    def delete_user(self, user_id: int) -> bool:
        return bool(self.user_repository.delete(int(user_id)))
    
    def authenticate(self, username: str, password: str):
        """
        Check username and password.
        Returns user object if valid, None otherwise.
        """
        user = self.get_user_by_username(username)
        if not user:
            return None

        # Assuming passwords are stored hashed in DB
        hashed_input = self._hash_password(password)
        if hashed_input == user.password:
            return user
        return None
