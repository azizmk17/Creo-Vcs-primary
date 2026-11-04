import os, hashlib, shutil, uuid, re
from datetime import datetime
import json
from contextlib import closing
from core.repositories.snapshot_repository import SnapshotRepository
from core.repositories.commit_repository import CommitRepository
from core.repositories.issue_repository import IssueRepository
from core.repositories.bom_revision_repository import BomRevisionRepository
from utils import (
    long_path, safe_open
)


def _remove_if_backed_up(source, backup, expected):
    """Delete only verified bytes; on Windows hold an exclusive file handle."""
    if (os.path.islink(source) or not os.path.isfile(backup)
            or (os.name == 'nt' and getattr(os.lstat(source), 'st_file_attributes', 0) & 0x400)):
        return False
    def digest(stream):
        value = hashlib.md5()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
        return value.hexdigest()
    if os.name != 'nt':
        with safe_open(backup, 'rb') as stream:
            if digest(stream) != expected:
                return False
        before = os.stat(source, follow_symlinks=False)
        with safe_open(source, 'rb') as stream:
            if digest(stream) != expected:
                return False
        after = os.stat(source, follow_symlinks=False)
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
            return False
        os.remove(source)
        return True
    import ctypes
    import msvcrt
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    kernel.SetFileInformationByHandle.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    backup_handle = kernel.CreateFileW(long_path(str(backup)), 0x80000000, 1, None, 3,
                                       0x08000000 | 0x00200000, None)
    if backup_handle == wintypes.HANDLE(-1).value:
        return False
    try:
        backup_fd = msvcrt.open_osfhandle(backup_handle, os.O_RDONLY | os.O_BINARY)
    except Exception:
        kernel.CloseHandle(backup_handle)
        raise
    with os.fdopen(backup_fd, 'rb') as saved:
        if digest(saved) != expected:
            return False
        handle = kernel.CreateFileW(long_path(str(source)), 0x80000000 | 0x00010000, 0, None, 3,
                                    0x08000000 | 0x00200000, None)
        if handle == wintypes.HANDLE(-1).value:
            return False  # Open elsewhere, unavailable, or insufficient permission.
        try:
            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except Exception:
            kernel.CloseHandle(handle)
            raise
        with os.fdopen(fd, 'rb') as stream:
            if digest(stream) != expected:
                return False
            disposition = wintypes.BOOL(True)
            return bool(kernel.SetFileInformationByHandle(handle, 4, ctypes.byref(disposition), ctypes.sizeof(disposition)))


class SnapshotService:
    def __init__(self):
        self.repo = SnapshotRepository()
        self.commit_rep = CommitRepository()
        self.issue_repo = IssueRepository()
        self.revision_repo = BomRevisionRepository()

    def generate_snapshot_data(self, working_dir, project_id, progress=None, cancelled=None):
        """Scans ONLY the main working directory and builds file list with metadata."""
        files = []
        with closing(self.commit_rep.get_conn()) as conn:
            approved_names = {str(row[0]) for row in conn.execute(
                "SELECT DISTINCT filename FROM commits WHERE status='Approved' AND project_id=?", (int(project_id),))}
        for f in os.listdir(working_dir):
            if cancelled and cancelled():
                raise InterruptedError("Snapshot cancelled; the project was not changed.")

            path = os.path.join(working_dir, f)

            # Skip folders — we only want files in the main directory
            if not os.path.isfile(path):
                continue

            if not re.fullmatch(r".+\.(?:prt|asm|drw)(?:\.\d+)?", f, re.IGNORECASE):
                continue

            committed = f in approved_names

            if progress:
                progress(0, 0, "Scanning and checking " + f)

            checksum = self._compute_md5(path)
            size = os.path.getsize(path)
            modified = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M:%S")

            files.append({
                "filename": f,
                "checksum": checksum,
                "size": size,
                "modified": modified,
                "status": "Old" if committed else "New"
            })

        return {
            "working_dir": working_dir,
            "files": files,
            "issue_state": self.issue_repo.snapshot_state(int(project_id)),
            "object_configuration": self.revision_repo.project_configuration_snapshot(int(project_id)),
        }

    def create_snapshot(self, project_id, name, description, working_dir, user):
        data = self.generate_snapshot_data(working_dir, project_id)
        snapshot_id = self.repo.create_snapshot(project_id, name, description, data, user)
        if snapshot_id :
            self.update_last_snapshot_in_commit(data, snapshot_id, project_id)
        return snapshot_id

    def create_backup_snapshot(self, project_id, name, working_dir, user, backup_directory,
                               progress=None, cancelled=None):
        """Back up Creo files and remove verified obsolete project copies."""
        root = os.path.realpath(long_path(str(working_dir)))
        parent = os.path.realpath(long_path(str(backup_directory)))
        try:
            if os.path.commonpath((root, parent)) == root:
                raise ValueError("Choose a backup folder outside the project.")
        except ValueError as exc:
            if "backup folder" in str(exc):
                raise
        safe_name = re.sub(r'[^A-Za-z0-9._ -]', '_', str(name)).strip(' .')[:100] or 'Snapshot'
        destination = os.path.join(parent, safe_name + "_" + uuid.uuid4().hex[:8])
        os.makedirs(destination, exist_ok=False)
        def report(done, total, label):
            if progress:
                progress(done, total, label)
        def check_cancel():
            if cancelled and cancelled():
                raise InterruptedError("Snapshot cancelled. " + (
                    "The verified backup and cleanup record remain; some eligible files may have been cleaned."
                    if recorded else "The project was not changed."))
        recorded = False
        try:
            data = self.generate_snapshot_data(root, project_id, progress=report, cancelled=cancelled)
            files = data["files"]
            saved_checksums = {item["filename"].casefold(): item["checksum"] for item in files}
            if not files:
                raise ValueError("No versioned Creo files were found in the project root.")
            from core.services.snapshot_cleanup_inventory import closed_creo_files
            archive_files = []
            inventory = closed_creo_files(self.repo, root, project_id)
            for relative, metadata in sorted(inventory.items()):
                check_cancel()
                report(0, 0, "Checking historical file " + relative)
                source = os.path.join(root, relative)
                archive_files.append({"relative_path": relative, "filename": os.path.basename(relative),
                                      "commits": metadata["commits"],
                                      "checksum": self._compute_md5(source), "size": os.path.getsize(source)})
            data["archived_commit_files"] = archive_files
            total_bytes = sum(int(item["size"]) for item in files + archive_files)
            total = total_bytes * 3
            done = 0
            for item in files + archive_files:
                check_cancel()
                relative = item.get("relative_path") or item["filename"]
                source = os.path.join(root, relative)
                target = os.path.join(destination, "history", relative) if item.get("relative_path") else os.path.join(destination, relative)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                digest = hashlib.md5()
                with safe_open(source, "rb") as src, safe_open(target, "xb") as dst:
                    for chunk in iter(lambda: src.read(1024 * 1024), b""):
                        check_cancel()
                        dst.write(chunk)
                        digest.update(chunk)
                        done += len(chunk)
                        report(done, total, "Backing up " + item["filename"])
                if digest.hexdigest() != item["checksum"]:
                    raise ValueError("A source file changed during backup: " + item["filename"])
                verified = hashlib.md5()
                with safe_open(target, "rb") as saved:
                    for chunk in iter(lambda: saved.read(1024 * 1024), b""):
                        check_cancel()
                        verified.update(chunk)
                        done += len(chunk)
                        report(done, total, "Verifying " + item["filename"])
                if verified.hexdigest() != item["checksum"]:
                    raise ValueError("A backed-up file failed verification: " + item["filename"])
            check_cancel()
            data["backup"] = {"path": destination, "file_count": len(files),
                              "historical_file_count": len(archive_files), "bytes": total_bytes,
                              "status": "Verified", "cleanup": "In progress",
                              "cleanup_journal": os.path.join(destination, "cleanup.jsonl"),
                              "removed": [], "skipped": [], "removed_history": [], "skipped_history": [],
                              "removed_directories": [], "updated_item_files": [], "updated_cad_files": []}
            snapshot_id = self.repo.create_snapshot(project_id, name, "Verified root CAD backup", data, user)
            recorded = True
            self.update_last_snapshot_in_commit(data, snapshot_id, project_id)
            # Only the newest numbered iteration of each Creo name is kept in
            # the root. Also keep unnumbered files and any file referenced by
            # an active Item or not-yet-approved submission.
            latest = {}
            for item in files:
                match = re.fullmatch(r"(.+\.(?:prt|asm|drw))\.(\d+)", item["filename"], re.I)
                if match:
                    key, number = match[1].casefold(), int(match[2])
                    if key not in latest or number > latest[key][0]:
                        latest[key] = (number, item["filename"])
            # Prevent concurrent database writers from changing submission or
            # Item ownership until cleanup decisions and their record commit.
            conn = self.repo.get_conn()
            master_lock = None
            transaction_started = False
            try:
                conn.execute("BEGIN IMMEDIATE")
                transaction_started = True
                def journal(relative, action):
                    with safe_open(data["backup"]["cleanup_journal"], "a", encoding="utf-8") as stream:
                        stream.write(json.dumps({"path": relative, "action": action,
                                                 "timestamp": datetime.now().isoformat()}) + "\n")
                        stream.flush()
                        os.fsync(stream.fileno())
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                same_root_projects = {int(project_id)}
                if "projects" in tables:
                    for row in conn.execute("SELECT id,working_directory FROM projects WHERE working_directory IS NOT NULL"):
                        if row[1] and os.path.normcase(os.path.realpath(long_path(str(row[1])))) == os.path.normcase(root):
                            same_root_projects.add(int(row[0]))
                marks = ','.join('?' for _ in same_root_projects)
                rows = conn.execute(f"SELECT filename FROM commits WHERE project_id IN ({marks}) AND lower(status) NOT IN "
                                    "('approved','released','withdrawn','rejected','branched','integrated')", tuple(same_root_projects)).fetchall()
                protected = {os.path.basename(str(row[0] or '')).casefold() for row in rows}
                busy_items = {int(row[0]) for row in conn.execute(
                    f"SELECT DISTINCT part_id FROM commits WHERE project_id IN ({marks}) AND part_id IS NOT NULL "
                    "AND lower(status) NOT IN ('approved','released','withdrawn','rejected','branched','integrated')",
                    tuple(same_root_projects))}
                if "locks" in tables:
                    busy_items.update(int(row[0]) for row in conn.execute("SELECT part_id FROM locks WHERE part_id IS NOT NULL"))
                if {"nexus_branch_items", "nexus_branches"}.issubset(tables):
                    busy_items.update(int(row[0]) for row in conn.execute(
                        "SELECT i.bom_id FROM nexus_branch_items i JOIN nexus_branches b ON b.id=i.branch_id "
                        "WHERE b.state IN ('Active','Paused')"))
                if {"cad_item_associations", "cad_documents"}.issubset(tables):
                    busy_items.update(int(row[0]) for row in conn.execute(
                        "SELECT a.item_id FROM cad_item_associations a JOIN cad_documents d ON d.id=a.cad_document_id "
                        "WHERE a.active=1 AND d.checked_out_by IS NOT NULL"))
                # Approved records and the Master manifest identify the last
                # validated version; a larger iteration may be unvalidated.
                from core.services.master_work_service import MasterWorkService
                lock = MasterWorkService.lock_path(root)
                os.makedirs(long_path(str(lock.parent)), exist_ok=True)
                try:
                    descriptor = os.open(long_path(str(lock)), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                except FileExistsError:
                    raise ValueError("Master publication is active; retry cleanup after it finishes.")
                os.close(descriptor)
                master_lock = lock
                master_manifest = MasterWorkService.read_manifest(root)
                # Never reuse a Creo iteration number after its root copy is
                # archived. Save the high-water mark before removing files.
                high_water = dict(master_manifest.get("iteration_high_water") or {})
                for key, (number, _filename) in latest.items():
                    high_water[key] = max(int(high_water.get(key, 0)), number)
                master_manifest["iteration_high_water"] = high_water
                with MasterWorkService.staging_directory(lock.parent) as stage:
                    index = stage / "manifest.json"
                    with safe_open(str(index), "w", encoding="utf-8") as stream:
                        json.dump(master_manifest, stream, indent=2)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(long_path(str(index)), long_path(str(MasterWorkService.manifest_path(root))))
                validated = {}
                def remember_validated(filename):
                    match = re.fullmatch(r"(.+\.(?:prt|asm|drw))\.(\d+)", str(filename or ''), re.I)
                    if match and os.path.isfile(os.path.join(root, str(filename))):
                        key, number = match[1].casefold(), int(match[2])
                        if key not in validated or number > validated[key][0]:
                            validated[key] = (number, str(filename))
                for record in master_manifest.get("validated_files", {}).values():
                    filename = record.get("path") or record.get("source_filename")
                    if filename:
                        validated_path = MasterWorkService.native_path(root, record)
                        from core.services.master_work_service import _file_digest
                        if os.path.isfile(validated_path) and _file_digest(validated_path) == record.get("sha256"):
                            remember_validated(filename)
                commit_columns = {row[1] for row in conn.execute("PRAGMA table_info(commits)")}
                if {"approved_version", "commit_id", "pr_path"}.issubset(commit_columns):
                    for row in conn.execute(f"SELECT filename,approved_version,project_id,commit_id,pr_path FROM commits WHERE project_id IN ({marks}) "
                                            "AND lower(status) IN ('approved','released') AND approved_version IS NOT NULL",
                                            tuple(same_root_projects)):
                        match = re.fullmatch(r"(.+\.(?:prt|asm|drw))(?:\.\d+)?", os.path.basename(str(row[0] or '')), re.I)
                        version = str(row[1] or '').strip()
                        if match and version.isdigit():
                            filename = match[1] + '.' + version
                            candidate = os.path.join(root, filename)
                            if not os.path.isfile(candidate):
                                continue
                            from core.services.master_work_service import _file_digest
                            expected = set()
                            if row[4] and os.path.isfile(long_path(str(row[4]))):
                                expected.add(_file_digest(row[4]))
                            if "cad_commit_snapshots" in tables:
                                for snapshot in conn.execute("SELECT manifest_json,manifest_sha256 FROM cad_commit_snapshots "
                                                             "WHERE project_id=? AND commit_id=?", (row[2], row[3])):
                                    encoded = str(snapshot[0] or '')
                                    if hashlib.sha256(encoded.encode('utf-8')).hexdigest() != str(snapshot[1] or ''):
                                        raise ValueError("A validated commit snapshot failed verification; cleanup stopped.")
                                    for entry in json.loads(encoded).get('files') or []:
                                        entry_base = re.sub(r'\.\d+$', '', str(entry.get('filename') or '')).casefold()
                                        if entry_base == match[1].casefold() and entry.get('sha256'):
                                            expected.add(str(entry['sha256']))
                            if expected and _file_digest(candidate) in expected:
                                remember_validated(filename)
                protected.update(entry[1].casefold() for entry in validated.values())
                # Preserve the current Master, including an unvalidated push.
                current_master = {}
                for key, record in master_manifest.get("files", {}).items():
                    protected.add(os.path.basename(str(record.get("path") or record.get("source_filename") or '')).casefold())
                    filename = str(record.get("path") or record.get("source_filename") or '')
                    match = re.fullmatch(r"(.+\.(?:prt|asm|drw))\.(\d+)", filename, re.I)
                    if match:
                        master_path = MasterWorkService.native_path(root, record)
                        from core.services.master_work_service import _file_digest
                        if os.path.isfile(master_path) and _file_digest(master_path) == record.get("sha256"):
                            current_master[match[1].casefold()] = (int(match[2]), filename)
                # A live Item can still point at an older iteration. Repoint it
                # only when a newer, existing validated file of the same Creo
                # name is proven, and record the change with the snapshot.
                bom_columns = {row[1] for row in conn.execute("PRAGMA table_info(bom)")}
                if "id" in bom_columns:
                    for row in conn.execute(f"SELECT id,project_id,filename,drawing FROM bom WHERE project_id IN ({marks})",
                                            tuple(same_root_projects)).fetchall():
                        if int(row[0]) in busy_items:
                            continue
                        for field, value in (("filename", row[2]), ("drawing", row[3])):
                            current = os.path.basename(str(value or ''))
                            match = re.fullmatch(r"(.+\.(?:prt|asm|drw))\.(\d+)", current, re.I)
                            target = validated.get(match[1].casefold()) if match else None
                            current = current_master.get(match[1].casefold()) if match else None
                            if current and target and current[0] > target[0]:
                                target = current
                            original = os.path.basename(str(value or ''))
                            if (target and int(match[2]) < target[0] and original.casefold() not in protected
                                    and saved_checksums.get(target[1].casefold())
                                    and self._compute_md5(os.path.join(root, target[1])) == saved_checksums[target[1].casefold()]):
                                associated_documents = []
                                if {"cad_item_associations", "cad_documents"}.issubset(tables):
                                    associated_documents = conn.execute(
                                        "SELECT d.id,d.latest_creo_file_name FROM cad_item_associations a "
                                        "JOIN cad_documents d ON d.id=a.cad_document_id "
                                        "WHERE a.item_id=? AND a.active=1 AND d.project_id=?",
                                        (int(row[0]), int(row[1]))).fetchall()
                                    conflict = False
                                    for document in associated_documents:
                                        if os.path.basename(str(document[1] or '')).casefold() != original.casefold():
                                            continue
                                        peers = conn.execute("SELECT item_id FROM cad_item_associations "
                                                             "WHERE cad_document_id=? AND active=1", (document[0],)).fetchall()
                                        if any(int(peer[0]) in busy_items for peer in peers):
                                            conflict = True
                                            break
                                    if conflict:
                                        continue
                                conn.execute(f"UPDATE bom SET {field}=? WHERE id=? AND project_id=? AND {field}=?",
                                             (target[1], int(row[0]), int(row[1]), value))
                                data["backup"]["updated_item_files"].append({"item_id": int(row[0]), "field": field,
                                                                             "from": value, "to": target[1]})
                                for document in associated_documents:
                                    if os.path.basename(str(document[1] or '')).casefold() != original.casefold():
                                        continue
                                    conn.execute("UPDATE cad_documents SET latest_creo_file_name=?,latest_creo_file_version=? "
                                                 "WHERE id=? AND project_id=? AND latest_creo_file_name=?",
                                                 (target[1], target[0], int(document[0]), int(row[1]), document[1]))
                                    data["backup"]["updated_cad_files"].append({"cad_document_id": int(document[0]),
                                                                                "from": document[1], "to": target[1]})
                # CAD-only dependencies can have a current file without an
                # Item association. Keep or update that reference too.
                if "cad_documents" in tables:
                    cad_columns = {row[1] for row in conn.execute("PRAGMA table_info(cad_documents)")}
                    if {"id", "project_id", "latest_creo_file_name", "latest_creo_file_version", "checked_out_by"}.issubset(cad_columns):
                        for document in conn.execute(f"SELECT id,project_id,latest_creo_file_name,checked_out_by FROM cad_documents "
                                                     f"WHERE project_id IN ({marks})", tuple(same_root_projects)).fetchall():
                            value = str(document[2] or '')
                            original = os.path.basename(value)
                            match = re.fullmatch(r"(.+\.(?:prt|asm|drw))\.(\d+)", original, re.I)
                            target = validated.get(match[1].casefold()) if match else None
                            current = current_master.get(match[1].casefold()) if match else None
                            if current and target and current[0] > target[0]:
                                target = current
                            peers = []
                            if "cad_item_associations" in tables:
                                peers = conn.execute("SELECT item_id FROM cad_item_associations WHERE cad_document_id=? AND active=1",
                                                     (document[0],)).fetchall()
                            can_update = (target and int(match[2]) < target[0] and not document[3]
                                          and original.casefold() not in protected
                                          and not any(int(peer[0]) in busy_items for peer in peers)
                                          and saved_checksums.get(target[1].casefold())
                                          and self._compute_md5(os.path.join(root, target[1])) == saved_checksums[target[1].casefold()])
                            if can_update:
                                conn.execute("UPDATE cad_documents SET latest_creo_file_name=?,latest_creo_file_version=? "
                                             "WHERE id=? AND project_id=? AND latest_creo_file_name=?",
                                             (target[1], target[0], int(document[0]), int(document[1]), value))
                                data["backup"]["updated_cad_files"].append({"cad_document_id": int(document[0]), "from": value, "to": target[1]})
                                original = target[1]
                            protected.add(original.casefold())
                # Persist Item changes and their audit record before deleting
                # any former Item file. If this commit fails, nothing has been
                # removed; a crash afterward leaves both versions available.
                if data["backup"]["updated_item_files"] or data["backup"]["updated_cad_files"]:
                    conn.execute("UPDATE snapshots SET snapshot_data=? WHERE id=?", (json.dumps(data), snapshot_id))
                    conn.commit()
                    transaction_started = False
                    conn.execute("BEGIN IMMEDIATE")
                    transaction_started = True
                    # Include work that appeared between the two transactions.
                    rows = conn.execute(f"SELECT filename FROM commits WHERE project_id IN ({marks}) AND lower(status) NOT IN "
                                        "('approved','released','withdrawn','rejected','branched','integrated')", tuple(same_root_projects)).fetchall()
                    protected.update(os.path.basename(str(row[0] or '')).casefold() for row in rows)
                    current_manifest = MasterWorkService.read_manifest(root)
                    for record in current_manifest.get("files", {}).values():
                        protected.add(os.path.basename(str(record.get("path") or record.get("source_filename") or '')).casefold())
                for row in conn.execute(f"SELECT filename,drawing FROM bom WHERE project_id IN ({marks})", tuple(same_root_projects)):
                    protected.update(os.path.basename(str(value or '')).casefold() for value in row)
                for item in files:
                    check_cancel()
                    name_on_disk = item["filename"]
                    match = re.fullmatch(r"(.+\.(?:prt|asm|drw))\.(\d+)", name_on_disk, re.I)
                    source = os.path.join(root, name_on_disk)
                    saved = os.path.join(destination, name_on_disk)
                    report(done, total, "Checking cleanup: " + name_on_disk)
                    keeper = (validated.get(match[1].casefold()) or latest.get(match[1].casefold())) if match else None
                    if (not match or (keeper and name_on_disk.casefold() == keeper[1].casefold())
                            or name_on_disk.casefold() in protected):
                        data["backup"]["skipped"].append(name_on_disk)
                    elif not os.path.isfile(source):
                        data["backup"]["skipped"].append(name_on_disk)
                    else:
                        # Recheck both the live directory and pending records
                        # immediately before touching an iteration.
                        latest_name = keeper[1]
                        active = conn.execute(f"SELECT 1 FROM commits WHERE project_id IN ({marks}) AND lower(filename)=? "
                            "AND lower(status) NOT IN ('approved','released','withdrawn','rejected','branched','integrated') LIMIT 1",
                            (*same_root_projects, name_on_disk.casefold())).fetchone()
                        keeper_path = os.path.join(root, latest_name)
                        if (active or not os.path.isfile(keeper_path)
                                or not saved_checksums.get(latest_name.casefold())
                                or self._compute_md5(keeper_path) != saved_checksums[latest_name.casefold()]):
                            data["backup"]["skipped"].append(name_on_disk)
                        else:
                            journal(name_on_disk, "removal_requested")
                            if _remove_if_backed_up(source, saved, item["checksum"]):
                                data["backup"]["removed"].append(name_on_disk)
                                journal(name_on_disk, "removed")
                            else:
                                data["backup"]["skipped"].append(name_on_disk)
                                journal(name_on_disk, "kept")
                    done += int(item["size"])
                    report(done, total, "Cleaning " + name_on_disk)
                # Rescan ownership after the backup. A file used by a newly
                # pending submission or a branch is no longer eligible.
                still_closed = closed_creo_files(self.repo, root, project_id, connection=conn)
                for item in archive_files:
                    check_cancel()
                    relative = item["relative_path"]
                    source, saved = os.path.join(root, relative), os.path.join(destination, "history", relative)
                    report(done, total, "Checking cleanup: " + relative)
                    if relative not in still_closed or not os.path.isfile(source):
                        data["backup"]["skipped_history"].append(relative)
                    else:
                        journal(relative, "removal_requested")
                        if _remove_if_backed_up(source, saved, item["checksum"]):
                            data["backup"]["removed_history"].append(relative)
                            journal(relative, "removed")
                            folder = os.path.dirname(source)
                            # Remove only directories made empty by this cleanup;
                            # never follow links or delete the project root.
                            while folder and os.path.normcase(folder) != os.path.normcase(root):
                                try:
                                    attrs = os.lstat(folder)
                                    if os.path.islink(folder) or (os.name == 'nt' and getattr(attrs, 'st_file_attributes', 0) & 0x400):
                                        break
                                    os.rmdir(folder)
                                except OSError:
                                    break
                                relative_folder = os.path.relpath(folder, root)
                                data["backup"]["removed_directories"].append(relative_folder)
                                journal(relative_folder, "empty_directory_removed")
                                folder = os.path.dirname(folder)
                        else:
                            data["backup"]["skipped_history"].append(relative)
                            journal(relative, "kept")
                    done += int(item["size"])
                    report(done, total, "Cleaning " + relative)
                data["backup"]["cleanup"] = "Completed"
            except Exception:
                data["backup"]["cleanup"] = "Interrupted; see removed and skipped files"
                raise
            finally:
                try:
                    if transaction_started:
                        conn.execute("UPDATE snapshots SET snapshot_data=? WHERE id=?", (json.dumps(data), snapshot_id))
                        conn.commit()
                finally:
                    conn.close()
                    if master_lock is not None:
                        master_lock.unlink(missing_ok=True)
            report(total, max(total, 1), "Backup recorded")
            return snapshot_id
        except Exception:
            if not recorded:
                shutil.rmtree(destination, ignore_errors=True)
            raise

    def _compute_md5(self, filepath):
        hash_md5 = hashlib.md5()
        with safe_open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()

    def find_archived_creo_file(self, project_id, original_path):
        """Locate a recorded root-file backup by its exact original path."""
        if not original_path:
            return None
        wanted = os.path.normcase(long_path(os.path.abspath(str(original_path))))
        for row in self.repo.get_all(int(project_id)):
            try:
                data = json.loads(row["snapshot_data"] or "{}")
                backup = data.get("backup") or {}
                if backup.get("status") != "Verified" or not backup.get("path"):
                    continue
                working = str(data.get("working_dir") or "")
                for item in data.get("files") or []:
                    name = str(item.get("filename") or "")
                    if os.path.normcase(long_path(os.path.abspath(os.path.join(working, name)))) == wanted:
                        return {"snapshot_id": row["id"], "snapshot_name": row["snapshot_name"],
                                "path": os.path.join(backup["path"], name)}
                for item in data.get("archived_commit_files") or []:
                    relative = str(item.get("relative_path") or "")
                    if os.path.normcase(long_path(os.path.abspath(os.path.join(working, relative)))) == wanted:
                        return {"snapshot_id": row["id"], "snapshot_name": row["snapshot_name"],
                                "path": os.path.join(backup["path"], "history", relative)}
            except (ValueError, TypeError, KeyError):
                continue
        return None

    def find_archived_creo_folder(self, project_id, original_directory):
        wanted = os.path.normcase(long_path(os.path.abspath(str(original_directory))))
        for row in self.repo.get_all(int(project_id)):
            try:
                data = json.loads(row["snapshot_data"] or "{}")
                backup = data.get("backup") or {}
                if backup.get("status") != "Verified":
                    continue
                working = str(data.get("working_dir") or "")
                matches = [item for item in data.get("archived_commit_files") or []
                           if os.path.normcase(long_path(os.path.abspath(os.path.dirname(
                               os.path.join(working, item.get("relative_path") or ""))))) == wanted]
                if matches:
                    relative = os.path.dirname(matches[0]["relative_path"])
                    return {"snapshot_id": row["id"], "snapshot_name": row["snapshot_name"],
                            "path": os.path.join(backup["path"], "history", relative)}
            except (ValueError, TypeError, KeyError):
                continue
        return None
    
    def compare_snapshots(self, id_from, id_to):
        snap1 = self.repo.get_by_id(id_from)
        snap2 = self.repo.get_by_id(id_to)
        data1 = json.loads(snap1["snapshot_data"])
        data2 = json.loads(snap2["snapshot_data"])

        files1 = {f["filename"]: f for f in data1["files"]}
        files2 = {f["filename"]: f for f in data2["files"]}

        added = [f for f in files2.keys() if f not in files1]
        removed = [f for f in files1.keys() if f not in files2]
        modified = [f for f in files1.keys() if f in files2 and files1[f]["checksum"] != files2[f]["checksum"]]

        issues1 = {
            i["issue_number"]: i for i in (data1.get("issue_state") or {}).get("issues", [])
        }
        issues2 = {
            i["issue_number"]: i for i in (data2.get("issue_state") or {}).get("issues", [])
        }
        issue_added = [key for key in issues2 if key not in issues1]
        issue_removed = [key for key in issues1 if key not in issues2]
        issue_changed = [
            key for key in issues1
            if key in issues2 and (
                issues1[key].get("status"), issues1[key].get("priority")
            ) != (
                issues2[key].get("status"), issues2[key].get("priority")
            )
        ]

        config1 = data1.get("object_configuration") or {}
        config2 = data2.get("object_configuration") or {}
        objects1 = {
            str(row.get("part_number") or row.get("aes_number") or row.get("id")): row
            for row in config1.get("objects", [])
        }
        objects2 = {
            str(row.get("part_number") or row.get("aes_number") or row.get("id")): row
            for row in config2.get("objects", [])
        }
        object_versions_changed = sorted(
            key for key in set(objects1) & set(objects2)
            if objects1[key].get("iteration_id") != objects2[key].get("iteration_id")
        )
        bindings_changed = (config1.get("bindings") or []) != (config2.get("bindings") or [])

        return {
            "added": added, "removed": removed, "modified": modified,
            "issue_added": issue_added, "issue_removed": issue_removed,
            "issue_changed": issue_changed,
            "object_versions_changed": object_versions_changed,
            "bindings_changed": bool(bindings_changed),
        }


    def update_last_snapshot_in_commit(self, data, snapshot_id, project_id):
        with closing(self.commit_rep.get_conn()) as conn, conn:
            conn.executemany("UPDATE commits SET last_snapshot=? WHERE filename=? AND project_id=? "
                             "AND EXISTS (SELECT 1 FROM commits c WHERE c.filename=commits.filename "
                             "AND c.project_id=commits.project_id AND c.status='Approved')",
                             [(snapshot_id, f['filename'], int(project_id)) for f in data["files"]])
