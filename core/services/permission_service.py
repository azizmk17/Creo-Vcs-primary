from core.repositories.permission_repository import PermissionRepository

class PermissionService:
    def __init__(self):
        self.perm_repo = PermissionRepository()

    def get_all_permissions(self):
        return self.perm_repo.get_all()

    def create_permission(self, name: str) -> int:
        return self.perm_repo.create_permission(name)

    def delete_permission(self, permission_id: int) -> None:
        return self.perm_repo.delete_permission(permission_id)
