import os
from datetime import datetime
from core.session_manager import SessionManager
from core.repositories.commit_repository import CommitRepository
from core.repositories.snapshot_repository import SnapshotRepository
from core.repositories.bom_repository import BomRepository
from core.repositories.pdm_repository import PdmRepository
from core.services.pdm_service import PdmService
from core.working_directory_inventory import WorkingDirectoryInventory
from core.performance_recorder import timed
from core.service_resources import ServiceResources

import re
import json
import uuid
from utils import (
    is_creo_file
)
from core.services.permission_decorators import require_permission

class DiagService:
    def __init__(self, *, resources=None):

        self.session = SessionManager()
        self.resources = resources or ServiceResources()
        repository = self.resources.repository
        self.repo = repository(CommitRepository)
        self.snap_repo = repository(SnapshotRepository)
        self.bom_repo = repository(BomRepository)
        self.pdm_repo = repository(PdmRepository)
        self.pdm_service = PdmService(db_name=self.resources.db_name, repository=self.pdm_repo)

    @staticmethod
    def _creo_base_name(filename: str) -> str:
        """Return ``name.ext`` from a Creo versioned filename ``name.ext.N``."""
        name = os.path.basename(str(filename or ""))
        match = re.match(r"^(.*\.(?:prt|asm|drw))\.\d+$", name, flags=re.IGNORECASE)
        if match:
            return match.group(1)
        stem, suffix = os.path.splitext(name)
        return stem if suffix.lstrip(".").isdigit() else name

    def list_supplier_packages(self):
        if not self.session.project_id:
            return []
        return self.bom_repo.list_supplier_packages(int(self.session.project_id))

    def list_cad_dependencies(self):
        if not self.session.project_id:
            return []
        return self.bom_repo.list_cad_dependencies(int(self.session.project_id))

    def _managed_cad_bases(self) -> set[str]:
        if not self.session.project_id:
            return set()
        pdm_repo = getattr(self, "pdm_repo", None)
        if pdm_repo is None:
            db_name = getattr(self.bom_repo, "db_name", None)
            pdm_repo = PdmRepository(db_name) if db_name else PdmRepository()
            self.pdm_repo = pdm_repo
        with pdm_repo.get_conn() as conn:
            rows = conn.execute(
                "SELECT file_name,base_file_name FROM cad_documents WHERE project_id=?",
                (int(self.session.project_id),),
            ).fetchall()
        return {pdm_repo.normalize_base(row[0] or row[1]) for row in rows if row[0] or row[1]}

    @require_permission("merge")
    def assign_cad_dependencies(self, owner_bom_id: int, filenames) -> int:
        if not self.session.project_id:
            raise ValueError("No active project.")
        dependencies = []
        for filename in filenames or []:
            name = os.path.basename(str(filename or ""))
            if not is_creo_file(name):
                raise ValueError(f"Not a versioned Creo file: {name}")
            dependencies.append({
                "base_file_name": self._creo_base_name(name),
                "original_filename": name,
            })
        count = self.bom_repo.assign_cad_dependencies(
            int(self.session.project_id),
            int(owner_bom_id),
            dependencies,
            assigned_by=self.session.user_id,
        )
        pdm_service = getattr(self, "pdm_service", None)
        if pdm_service is None:
            db_name = getattr(self.bom_repo, "db_name", None)
            pdm_service = PdmService(db_name) if db_name else PdmService()
            self.pdm_service = pdm_service
        for dependency in dependencies:
            pdm_service.register_supplier_dependency(
                int(self.session.project_id), int(owner_bom_id),
                dependency["original_filename"],
            )
        return count

    @require_permission("merge")
    def remove_cad_dependencies(self, dependency_ids) -> int:
        if not self.session.project_id:
            raise ValueError("No active project.")
        wanted = {int(value) for value in dependency_ids or []}
        records = [
            row for row in self.bom_repo.list_cad_dependencies(
                int(self.session.project_id)
            )
            if int(row.get("id") or 0) in wanted
        ]
        count = self.bom_repo.remove_cad_dependencies(
            int(self.session.project_id), wanted
        )
        pdm_service = getattr(self, "pdm_service", None)
        if pdm_service is None:
            db_name = getattr(self.bom_repo, "db_name", None)
            pdm_service = PdmService(db_name) if db_name else PdmService()
            self.pdm_service = pdm_service
        for record in records:
            pdm_service.unregister_supplier_dependency(
                int(self.session.project_id), int(record["owner_bom_id"]),
                str(record.get("base_file_name") or record.get("original_filename") or ""),
            )
        return count

    # --- Filesystem ---
    def list_commit_folders(self, commits_dir):
        if not commits_dir or not os.path.isdir(commits_dir):
            return []
        folders = []
        for user in os.listdir(commits_dir):
            user_dir = os.path.join(commits_dir, user)
            if not os.path.isdir(user_dir):
                continue
            for folder in os.listdir(user_dir):
                path = os.path.join(user_dir, folder)
                if os.path.isdir(path):
                    folders.append({"username": user, "folder": folder})
        return folders
    
    def list_commit_files(self, commits_dir, username, commit_folder):
        commit_folder_path = os.path.join(commits_dir,username, commit_folder)
        #print(f"Listing files in commit folder path: {commit_folder_path}")
        if not os.path.isdir(commit_folder_path):
            return []
        files = []
        for file in os.listdir(commit_folder_path):
            file_path = os.path.join(commit_folder_path, file)
            if os.path.isfile(file_path):
                files.append({"filename": file, "path": file_path})
        #print(f" Found files: {[f['filename'] for f in files]}")
        return files

    def list_working_parts(self, working_dir):
        if not working_dir or not os.path.isdir(working_dir):
            return []
        return [f for f in os.listdir(working_dir) if os.path.isfile(os.path.join(working_dir, f)) and is_creo_file(os.path.join(working_dir, f)) ]

    def file_exists(self, path):
        return os.path.exists(path)


    def extract_folder_title(self, folder_name: str) -> str:
        """Removes trailing _YYYYMMDD_HHMMSS pattern and normalizes name."""
        clean_name = re.sub(r'_\d{8}_\d{6}$', '', folder_name).strip().lower()
        return clean_name

    def check_db_vs_commits(self, commits_dir):
        db_commits = self.repo.get_by_status("Pending", self.session.project_id)
        fs_commits = self.list_commit_folders(commits_dir)

        results = []
        synced, missing, extra = 0, 0, 0

        if not commits_dir or not os.path.isdir(commits_dir):
            for c in db_commits:
                results.append((c.commit_id, "Missing", "Commit exists in DB but commits folder does not exist"))
                missing += 1
            if not db_commits:
                results.append((commits_dir or "commits", "Missing", "Commits folder does not exist"))
                missing += 1
            return {
                "rows": results,
                "summary": {
                    "synced": synced,
                    "missing": missing,
                    "extra": extra
                }
            }

        print(f"FS Folders: {[f['folder'] for f in fs_commits]}")

        # Map file system folders by username for fast lookup
        fs_map = {(f["username"], self.extract_folder_title(f["folder"])): f for f in fs_commits}

        # -------------------------
        # Step 1: Check DB commits
        # -------------------------
        for c in db_commits:
            db_title = c.title.strip().lower()
            folder_entry = None

            # find matching folder for same username
            for (u, folder_title), f in fs_map.items():
                if u == c.username and db_title == folder_title:
                    folder_entry = f
                    break

            if folder_entry:
                # Folder exists, now check file match
                fs_files = self.list_commit_files(commits_dir, c.username, folder_entry["folder"])
                db_files = [c.filename for c in db_commits if c.title == c.title]

                fs_filenames = [f["filename"] for f in fs_files]
                all_files_match = all(f in fs_filenames for f in db_files)
                extra_files = [f for f in fs_filenames if f not in db_files]

                if all_files_match and not extra_files:
                    results.append((c.commit_id, "✅ Synced", "Folder and files match database"))
                    synced += 1
                else:
                    if not all_files_match:
                        results.append((c.commit_id, "⚠️ Missing", "Some files exist in DB but not in filesystem"))
                        missing += 1
                    if extra_files:
                        for ef in extra_files:
                            results.append((f"{ef} in {folder_entry['folder']}", "🗑 Extra File", "Not in database"))
                            extra += 1
            else:
                results.append((c.commit_id, "⚠️ Missing", "Commit exists in DB but not in filesystem"))
                missing += 1

        # -------------------------
        # Step 2: Check for orphan folders
        # -------------------------
        db_titles = [(c.title.strip().lower(), c.filename) for c in db_commits]

        for f in fs_commits:
            folder_title = self.extract_folder_title(f['folder'])
            if not any(db_title == folder_title for db_title, _ in db_titles):
                results.append((f["folder"], "🗑 Extra Folder", "Not in database"))
                extra += 1

        return {
            "rows": results,
            "summary": {
                "synced": synced,
                "missing": missing,
                "extra": extra
            }
        }


    @timed("diagnostics.integrity_check")
    def check_working_directory(self, working_dir, *, cancel_cb=None, inventory=None):
        if not working_dir or not os.path.isdir(working_dir):
            return []
        try:
            inventory = inventory or WorkingDirectoryInventory(working_dir, cancel_cb=cancel_cb)
        except InterruptedError:
            return []
        working_files = [name for name in inventory.names if is_creo_file(name)]
        approved_commits = self.repo.get_by_status("Approved",self.session.project_id)
        

        last_snapshot_id = self.snap_repo.get_last_snapshot_id(self.session.project_id)


        last_snapshot = self.snap_repo.get_last_snapshot(self.session.project_id)
        if not last_snapshot:
            return "error_no_snapshot"
        last_snapshot_data_files = json.loads(last_snapshot['snapshot_data'])['files']
        snap_files = {item['filename'] for item in last_snapshot_data_files}
        approved_files = {
            str(c.base_file_name) + "." + str(c.approved_version)
            for c in approved_commits
            if c.last_snapshot == "None" or c.last_snapshot != last_snapshot_id
        }

        # NEW: allow files that were force-integrated (treated as "legal" in working dir)
        try:
            integrated_filenames = set(self.repo.get_forced_integrated_filenames(self.session.project_id) or [])
        except Exception:
            integrated_filenames = set()
        dependency_bases = self.bom_repo.dependency_base_names(
            int(self.session.project_id)
        )
        managed_cad_bases = self._managed_cad_bases()

        results = []

        for part in working_files:
            if cancel_cb and cancel_cb():
                return []
            if self.pdm_repo.normalize_base(
                self._creo_base_name(part)
            ) in managed_cad_bases:
                continue
            if self._creo_base_name(part).casefold() in dependency_bases:
                continue
            
            # allowlist check first
            if part in integrated_filenames:
                results.append((part, "🟢 Force Integrated", "Force integrated part"))
                continue

            #check if the part is in last snappshotted list
            if part in snap_files:
                continue
            else:
                is_committed = part in approved_files
                if is_committed:
                    print(f'part : {part} Matches approved commit')
                    #results.append((part, "✅ Up-to-date", "Matches approved commit"))
                else: 
                    results.append((part, "❌ Unexpected", "unexpected new part"))

        return results

    @require_permission("merge")
    def force_integrate_part(self, filename: str, working_dir: str = ""):
        """Mark an *unexpected* file as legal in working directory.

        This does NOT create a real commit folder; it only records a DB entry with a special status.

        NOTE: commits.part_id is NOT NULL in your schema, so we must resolve the BOM part id.
        The BOM is searched by base_file_name, where base_file_name is the filename without
        the Creo version suffix (e.g. part.prt.6 -> part.prt).
        """
        if not filename:
            raise ValueError("filename is required")
        project_id = self.session.project_id
        user_id = self.session.user_id
        if not project_id or not user_id:
            raise ValueError("No active session/project")

        name = os.path.basename(filename)

        # Example: wedge_block.prt.6 -> wedge_block.prt
        base = name
        m = re.match(r"^(.*\.(?:prt|asm|drw))\.\d+$", name, flags=re.IGNORECASE)
        if m:
            base = m.group(1)
        else:
            # fallback: only remove last numeric suffix if present
            parts = name.rsplit(".", 1)
            if len(parts) == 2 and parts[1].isdigit():
                base = parts[0]

        wd = working_dir or ""
        file_path = os.path.join(wd, name) if wd else name

        # resolve BOM part_id (project-scoped)
        bom = self.bom_repo.get_by_base_file_name_for_commit(base, project_id, preferred_user_id=user_id)
        if not bom:
            raise ValueError(f"BOM part not found for base_file_name: {base}")

        # idempotent
        if self.repo.is_forced_integrated_filename(name, project_id):
            return

        self.repo.insert_forced_integrated(
            part_id=int(bom.id),
            part_type="Integrated",
            filename=name,
            filepath=file_path,
            base_file_name=base,
            designer=user_id,
            committer=user_id,
            message="Force integrated from diagnostic (unexpected part)",
            signature=str(uuid.uuid4()),
            project_id=project_id,
            title=f"integrated:{base}",
            commit_id=f"integrated:{base}",
        )

        # Update BOM to point to this new file (file or drawing)
        try:
            part_ext = os.path.splitext(base)[1].lower()  # base keeps .prt/.asm/.drw
            if part_ext == ".drw":
                # Update drawing
                bom.drawing = name
                # Keep base_drw_name consistent (strip Creo numeric suffix)
                bom.base_drw_name = base
            else:
                # Update part filename
                bom.filename = name
                bom.base_file_name = base
            self.bom_repo.update(bom)
        except Exception:
            # Don't fail force integration if BOM update could not be persisted
            pass

    @require_permission("merge")
    def delete_unexpected_files(self, filenames: list[str], working_dir: str) -> tuple[int, list[tuple[str, str]], list[str]]:
        """Delete files from the working directory.

        Returns: (ok_count, failed[(filename, error)], deleted_paths[list[str]])
        """
        if not working_dir or not os.path.isdir(working_dir):
            raise ValueError("Invalid working_dir")

        if not filenames:
            return 0, [], []

        ok = 0
        failed: list[tuple[str, str]] = []
        deleted_paths: list[str] = []

        wd_real = os.path.realpath(working_dir)
        for fn in filenames:
            try:
                name = os.path.basename(fn)
                full_path = os.path.realpath(os.path.join(working_dir, name))

                # Path traversal guard: ensure within working_dir
                if not (full_path == wd_real or full_path.startswith(wd_real + os.sep)):
                    raise PermissionError("Unsafe path")

                if not os.path.exists(full_path):
                    raise FileNotFoundError(full_path)

                os.remove(full_path)
                ok += 1
                deleted_paths.append(full_path)
            except Exception as e:
                failed.append((str(fn), str(e)))

        return ok, failed, deleted_paths

    def resolve_part_id_from_filename(self, filename: str, working_dir: str = "") -> int | None:
        """Best-effort resolve of BOM part_id from a working-dir Creo filename.

        Uses the same base filename stripping logic as force-integrate.
        Returns None if not resolvable.
        """
        if not filename:
            return None
        project_id = getattr(self.session, "project_id", None)
        user_id = getattr(self.session, "user_id", None)
        if not project_id or not user_id:
            return None

        name = os.path.basename(filename)

        # Example: wedge_block.prt.6 -> wedge_block.prt
        base = name
        m = re.match(r"^(.*\.(?:prt|asm|drw))\.\d+$", name, flags=re.IGNORECASE)
        if m:
            base = m.group(1)
        else:
            parts = name.rsplit(".", 1)
            if len(parts) == 2 and parts[1].isdigit():
                base = parts[0]

        bom = self.bom_repo.get_by_base_file_name_for_commit(base, int(project_id), preferred_user_id=int(user_id))
        if not bom:
            return None
        try:
            return int(getattr(bom, "id", None))
        except Exception:
            return None

    def check_orphan_files(self, working_dir):
        # all_files = os.listdir(working_dir)
        # commit_files = [f for f in all_files if f.endswith(".prt") or f.endswith(".asm")]
        # orphan_files = [f for f in all_files if f not in commit_files]

        # results = [(f, "🗑 Orphan", "Not linked to any commit") for f in orphan_files]
        # return results

        #check all files in working directory if they are linked to any bom part with base_file_name or base_drw_name
        if not working_dir or not os.path.isdir(working_dir):
            return []
        all_files = os.listdir(working_dir)
        bom_parts = self.bom_repo.get_all(self.session.project_id)
        linked_files = set()
        for part in bom_parts:
            if part.base_file_name:
                linked_files.add(part.base_file_name)
            if part.base_drw_name:
                linked_files.add(part.base_drw_name)
        linked_files = {str(value).casefold() for value in linked_files if value}
        linked_files.update(
            self.bom_repo.dependency_base_names(int(self.session.project_id))
        )
        managed_cad_bases = self._managed_cad_bases()
        orphan_files = [
            f for f in all_files
            if self._creo_base_name(f).casefold() not in linked_files
            and self.pdm_repo.normalize_base(
                self._creo_base_name(f)
            ) not in managed_cad_bases
            and is_creo_file(os.path.join(working_dir, f))
        ]
        results = [(f, "🗑 Orphan", "Not linked to any BOM part") for f in orphan_files
        ]
        return results
    

    def _sync_bom_record(self, working_dir, bom, files_in_dir=None, force_integrated_filenames=None,
                         *, inventory=None, metadata_updates=None):
        """Check missing/outdated files for one BOM row and keep base names in sync."""
        if not bom or not working_dir:
            return []
        if inventory is None:
            if not os.path.isdir(working_dir):
                return []
            inventory = WorkingDirectoryInventory(working_dir)
        if force_integrated_filenames is None:
            try:
                force_integrated_filenames = set(self.repo.get_forced_integrated_filenames(self.session.project_id) or [])
            except Exception:
                force_integrated_filenames = set()

        result = []
        base_file_name = None
        base_drw_name = None

        if bom.filename:
            base_file_name = self._creo_base_name(bom.filename)
            if not inventory.exists(bom.filename):
                result.append((bom.id, 'missing_file', bom.filename))
            else:
                suffix = bom.filename.rsplit(".", 1)[-1]
                if (suffix.isdigit() and int(suffix) < inventory.latest_version(base_file_name)
                        and bom.filename not in force_integrated_filenames):
                    result.append((bom.id, 'outdated_file', bom.filename))

        if bom.drawing:
            base_drw_name = self._creo_base_name(bom.drawing)
            if not inventory.exists(bom.drawing):
                result.append((bom.id, 'missing_drawing', bom.drawing))

        if getattr(bom, "pdf_path", None):
            pdf_path = bom.pdf_path
            if not inventory.exists(pdf_path):
                result.append((bom.id, 'missing_pdf', pdf_path))

        if getattr(bom, "step_path", None):
            step_path = bom.step_path
            if not inventory.exists(step_path):
                result.append((bom.id, 'missing_step', step_path))

        if ((base_file_name or base_drw_name)
                and (base_file_name != bom.base_file_name or base_drw_name != bom.base_drw_name)):
            if metadata_updates is None:
                self.bom_repo.update_bom_file_names_batch(
                    [(bom.id, bom.filename, bom.drawing, base_file_name, base_drw_name)],
                    self.session.project_id,
                )
            else:
                metadata_updates.append((bom.id, bom.filename, bom.drawing, base_file_name, base_drw_name))

        return result

    def sync_bom_part_files(self, working_dir, bom_id):
        """Run the BOM file diagnostic for one part only."""
        if not bom_id or not working_dir or not os.path.isdir(working_dir):
            return []
        bom = self.bom_repo.get_by_id(int(bom_id))
        if not bom:
            return []
        try:
            force_integrated_filenames = set(self.repo.get_forced_integrated_filenames(self.session.project_id) or [])
        except Exception:
            force_integrated_filenames = set()
        return self._sync_bom_record(working_dir, bom, force_integrated_filenames=force_integrated_filenames)

    # everytime the app refreshed the bom page check if the filename and drawing exists in the working directory and check if the update the base_file_name and base_drw_name columns in the commit table regarding to the filename and drawing columns in the bom table
    @timed("diagnostics.file_synchronization")
    def sync_bom_files(self, working_dir, *, cancel_cb=None, inventory=None):
        if not working_dir or not os.path.isdir(working_dir):
            return []
        try:
            force_integrated_filenames = set(self.repo.get_forced_integrated_filenames(self.session.project_id) or [])
        except Exception:
            force_integrated_filenames = set()

        #if the filname or the drawing name not exists in the working directory return a list or map of bom id with missing files
        try:
            inventory = inventory or WorkingDirectoryInventory(working_dir, cancel_cb=cancel_cb)
        except InterruptedError:
            return []

        result = []
        metadata_updates = []
        boms = self.bom_repo.get_all(self.session.project_id)
        for bom in boms:
            if cancel_cb and cancel_cb():
                return []
            result.extend(self._sync_bom_record(
                working_dir, bom, force_integrated_filenames=force_integrated_filenames,
                inventory=inventory, metadata_updates=metadata_updates,
            ))

        if metadata_updates and not (cancel_cb and cancel_cb()):
            self.bom_repo.update_bom_file_names_batch(metadata_updates, self.session.project_id)

        return result


