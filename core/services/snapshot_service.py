import os, hashlib
from datetime import datetime
import json
from core.repositories.snapshot_repository import SnapshotRepository
from core.repositories.commit_repository import CommitRepository
from core.repositories.issue_repository import IssueRepository
from core.repositories.bom_revision_repository import BomRevisionRepository
from utils import (
    is_creo_file
)

class SnapshotService:
    def __init__(self):
        self.repo = SnapshotRepository()
        self.commit_rep = CommitRepository()
        self.issue_repo = IssueRepository()
        self.revision_repo = BomRevisionRepository()

    def generate_snapshot_data(self, working_dir, project_id):
        """Scans ONLY the main working directory and builds file list with metadata."""
        files = []
        for f in os.listdir(working_dir):

            path = os.path.join(working_dir, f)

            # Skip folders — we only want files in the main directory
            if not os.path.isfile(path):
                continue

            if not is_creo_file(path):
                print(f'{path} is not a creo file')
                continue

            committed = self.commit_rep.is_filename_exist(f, project_id)

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

    def _compute_md5(self, filepath):
        hash_md5 = hashlib.md5()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    
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
        for f in data["files"]:
            if self.commit_rep.is_filename_exist(f['filename'], project_id):
                print('commit found.')
                #update commits DB last snapshot id
                self.commit_rep.update_snapshot(f['filename'], snapshot_id, project_id)
