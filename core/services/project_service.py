from datetime import datetime
import os
import shutil

from core.models.bom_model import Bom
from core.repositories.bom_repository import BomRepository
from core.repositories.role_repository import RoleRepository
from core.repositories.project_repository import ProjectRepository


def _is_dir_empty(path: str) -> bool:
    try:
        return not any(os.scandir(path))
    except FileNotFoundError:
        return True


def _is_iteration_filename(filename: str) -> bool:
    # e.g. part.prt.2, asm.asm.10, drw.drw.1
    parts = filename.split(".")
    return len(parts) >= 3 and parts[-1].isdigit()


def _iteration_key_and_no(filename: str):
    parts = filename.split(".")
    if len(parts) < 3 or not parts[-1].isdigit():
        return None, None
    iter_no = int(parts[-1])
    key = ".".join(parts[:-1])  # includes ext
    return key, iter_no

class ProjectService:
    def __init__(self):
        self.project_repo = ProjectRepository()
        

    def get_all_projects(self):
        return self.project_repo.get_all()
    
    def get_projects_for_user(self, user_id):
        return self.project_repo.get_projects_for_user(user_id)
    
    def get_project_by_id(self, project_id):
        return self.project_repo.get_project_by_id(project_id)
    
    def get_project_id(self, project_name):
        return self.project_repo.get_project_id(project_name)

    def add_user_to_project(self, user_id, project_id):
        return self.project_repo.add_user_to_project(user_id, project_id)

    def remove_user_from_project(self, user_id, project_id):
        return self.project_repo.remove_user_from_project(user_id, project_id)

    def get_users_for_project(self, project_id: int):
        return self.project_repo.get_users_for_project(int(project_id))
    

    def add_project(self, name: str, working_directory: str, description: str = "") -> int:
        # ProjectRepository has create_project(), not insert()
        return self.project_repo.create_project(name, working_directory, description)

    # Back-compat for AdminPage
    def create_project(self, name: str, working_directory: str = "", description: str = "") -> int:
        return self.add_project(name, working_directory, description)

    def delete_project(self, project_id: int) -> bool:
        return self.project_repo.delete_project(int(project_id))

    def update_project(self, project_id: int, name: str, working_directory: str, description: str = "") -> bool:
        return self.project_repo.update_project(int(project_id), name, working_directory, description)

    def duplicate_project(self, source_project_id: int, new_project_name: str, user_id: int) -> int:
        # Delegate to repository method
        return self.project_repo.duplicate_project(source_project_id, new_project_name, user_id)

    def get_project_by_root_and_label(self, root_project_id: int, version_label: str):
        return self.project_repo.get_project_by_root_and_label(root_project_id, version_label)

    def _purge_copy_working_directory(self, src_dir: str, dst_dir: str, progress_cb=None, cancel_cb=None):
        """Copy only latest Creo iterations (e.g. part.prt.2) from src_dir into dst_dir.

        - Preserves relative directory structure
        - Skips transient/internal folders: commits, .nexus, __pycache__, .git
        - Requires dst_dir to be empty (or non-existent)
        """

        if not src_dir or not os.path.isdir(src_dir):
            raise ValueError("Source working directory not found")
        if not dst_dir:
            raise ValueError("Destination working directory is required")

        if os.path.exists(dst_dir) and not _is_dir_empty(dst_dir):
            raise ValueError("Destination working directory must be empty")
        os.makedirs(dst_dir, exist_ok=True)

        # Keep vault (attachments) so history/files remain available in the new revision.
        skip_dirs = {"commits", ".nexus", "__pycache__", ".git"}

        for root, dirs, files in os.walk(src_dir):
            if callable(cancel_cb) and cancel_cb():
                raise RuntimeError("Cancelled")
            # prune skipped dirs
            dirs[:] = [d for d in dirs if d not in skip_dirs]

            rel_root = os.path.relpath(root, src_dir)
            if rel_root == ".":
                rel_root = ""

            latest = {}  # key -> (iter_no, filename)
            passthrough = []

            for fn in files:
                key, it = _iteration_key_and_no(fn)
                if key is not None:
                    prev = latest.get(key)
                    if prev is None or it > prev[0]:
                        latest[key] = (it, fn)
                else:
                    passthrough.append(fn)

            dst_subdir = os.path.join(dst_dir, rel_root)
            os.makedirs(dst_subdir, exist_ok=True)

            to_copy = [v[1] for v in latest.values()] + passthrough
            for fn in to_copy:
                if callable(cancel_cb) and cancel_cb():
                    raise RuntimeError("Cancelled")
                src_path = os.path.join(root, fn)
                dst_path = os.path.join(dst_subdir, fn)
                try:
                    shutil.copy2(src_path, dst_path)
                except Exception:
                    # best-effort: skip unreadable files
                    pass

        if callable(progress_cb):
            try:
                progress_cb(100, "Working directory copied")
            except Exception:
                pass

    def create_new_version(
        self,
        source_project_id: int,
        user_id: int,
        new_working_directory: str,
        version_label: str | None = None,
        new_project_name: str | None = None,
        progress_cb=None,
        cancel_cb=None,
    ) -> int:
        """PLM-like: create a new project version and purge-copy CAD files."""

        src = self.project_repo.get_project_by_id(int(source_project_id)) or {}
        src_wd = (src.get("working_directory") or "").strip()

        if callable(progress_cb):
            try:
                progress_cb(0, "Starting new version...")
            except Exception:
                pass

        new_project_id = self.project_repo.create_project_version(
            source_project_id=int(source_project_id),
            user_id=int(user_id),
            new_working_directory=new_working_directory,
            version_label=version_label,
            new_project_name=new_project_name,
            progress_cb=progress_cb,
            cancel_cb=cancel_cb,
        )

        # Purge-copy the working directory content
        if callable(progress_cb):
            try:
                # Switch to late-stage message; percent stays where repo left it.
                progress_cb(95, "Copying working directory...")
            except Exception:
                pass

        self._purge_copy_working_directory(src_wd, new_working_directory, progress_cb=progress_cb, cancel_cb=cancel_cb)
        return int(new_project_id)

