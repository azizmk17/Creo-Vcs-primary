import os
import re
import shutil
import uuid
from typing import Iterable, Optional

from core.models.issue_model import (
    ISSUE_CATEGORIES,
    ISSUE_PRIORITIES,
    ISSUE_STATUSES,
    ISSUE_TRANSITIONS,
)
from core.repositories.bom_children_repository import BomChildrenRepository
from core.repositories.bom_repository import BomRepository
from core.repositories.issue_repository import IssueRepository
from core.services.base_service import BaseService


class IssueService(BaseService):
    def __init__(self, repo: Optional[IssueRepository] = None):
        super().__init__()
        self.repo = repo or IssueRepository()
        self.bom_repo = BomRepository()
        self.children_repo = BomChildrenRepository()

    def create_issue(self, data: dict, part_ids: Iterable[int]) -> dict:
        title = (data.get("title") or "").strip()
        if not title:
            raise ValueError("Issue title is required")
        priority = data.get("priority", "Medium")
        status = data.get("status", "Open")
        category = data.get("category", "Design")
        if priority not in ISSUE_PRIORITIES:
            raise ValueError("Invalid issue priority")
        if status not in ISSUE_STATUSES:
            raise ValueError("Invalid issue status")
        if category not in ISSUE_CATEGORIES:
            raise ValueError("Invalid issue category")
        data = dict(data)
        data.update(title=title, project_id=int(data.get("project_id") or self.project_id),
                    created_by=data.get("created_by") or self.user_id)
        return self.repo.create(data, part_ids, self.user_id)

    def update_issue(self, issue_id: int, changes: dict, part_ids=None) -> dict:
        issue = self.repo.update(issue_id, changes, self.user_id)
        if part_ids is not None:
            self.repo.set_parts(issue_id, part_ids, self.user_id, self.project_id)
            issue = self.repo.get_by_id(issue_id)
        return issue

    def transition(self, issue_id: int, status: str, note="") -> dict:
        issue = self.repo.get_by_id(issue_id)
        if not issue:
            raise ValueError("Issue not found")
        allowed = ISSUE_TRANSITIONS.get(issue["status"], set())
        if status not in allowed:
            raise ValueError(f"Cannot move issue from {issue['status']} to {status}")
        return self.repo.transition(issue_id, status, self.user_id, note)

    def archive(self, issue_id: int, reason: str):
        if not (reason or "").strip():
            raise ValueError("Archive reason is required")
        self.repo.archive(issue_id, self.user_id, reason.strip())

    def list_issues(self, filters=None):
        return self.repo.list_issues(int(self.project_id), filters)

    def get_issue(self, issue_id: int):
        return self.repo.get_by_id(issue_id)

    def comments(self, issue_id: int):
        return self.repo.comments(issue_id)

    def add_comment(self, issue_id: int, comment: str):
        if not (comment or "").strip():
            raise ValueError("Comment cannot be empty")
        self.repo.add_comment(issue_id, self.user_id, comment)

    def history(self, issue_id: int):
        return self.repo.history(issue_id)

    def attachments(self, issue_id: int):
        return self.repo.attachments(issue_id)

    def add_attachment(self, issue_id: int, source_path: str, working_dir: str):
        if not os.path.isfile(source_path):
            raise ValueError("Attachment file does not exist")
        if not working_dir:
            raise ValueError("Project working directory is not configured")
        issue = self.repo.get_by_id(issue_id)
        folder = os.path.join(working_dir, ".creo_vcs", "issues", issue["issue_number"])
        os.makedirs(folder, exist_ok=True)
        destination = os.path.join(folder, os.path.basename(source_path))
        if os.path.exists(destination):
            stem, ext = os.path.splitext(os.path.basename(source_path))
            destination = os.path.join(folder, f"{stem}_{uuid.uuid4().hex[:8]}{ext}")
        shutil.copy2(source_path, destination)
        self.repo.add_attachment(issue_id, os.path.basename(source_path), destination, self.user_id)
        return destination

    def part_summary(self):
        issue_sets = self.repo.issue_ids_by_part(int(self.project_id))
        relationships = self.children_repo.get_all_for_project(int(self.project_id))
        parents = {}
        for rel in relationships:
            parents.setdefault(int(rel.child_id), set()).add(int(rel.parent_id))
        direct = {
            pid: {name: set(values[name]) for name in ("all", "active", "critical")}
            for pid, values in issue_sets.items()
        }
        for child_id, values in direct.items():
            queue = list(parents.get(child_id, set()))
            seen = set()
            while queue:
                parent_id = queue.pop()
                if parent_id in seen:
                    continue
                seen.add(parent_id)
                target = issue_sets.setdefault(
                    parent_id, {"all": set(), "active": set(), "critical": set()}
                )
                target["all"].update(values["all"])
                target["active"].update(values["active"])
                target["critical"].update(values["critical"])
                queue.extend(parents.get(parent_id, set()))
        return {
            part_id: {
                "part_id": part_id,
                "active_count": len(values["active"]),
                "total_count": len(values["all"]),
                "critical_count": len(values["critical"]),
            }
            for part_id, values in issue_sets.items()
        }

    def issues_for_part(self, part_id: int):
        return self.repo.list_issues(int(self.project_id), {"part_id": int(part_id)})

    def metrics(self):
        return self.repo.metrics(int(self.project_id))

    def analytics(self):
        return self.repo.analytics(int(self.project_id))

    def active_issues_for_paths(self, paths: Iterable[str], preferred_user_id=None):
        part_ids = set()
        for path in paths or []:
            filename = os.path.basename(path)
            base = re.sub(r"\.\d+$", "", filename)
            part = self.bom_repo.get_by_base_file_name_for_commit(
                base, int(self.project_id), preferred_user_id or self.user_id
            )
            if not part:
                part = self.bom_repo.get_by_drawing_file_name_for_commit(
                    base, int(self.project_id), preferred_user_id or self.user_id
                )
            if part:
                part_ids.add(int(part.id))
        found = {}
        for part_id in part_ids:
            for issue in self.repo.list_issues(int(self.project_id), {"part_id": part_id, "active_only": True}):
                found[int(issue["id"])] = issue
        return list(found.values())

    def link_to_commit(self, issue_ids: Iterable[int], commit_id: str, resolution_comment=""):
        self.repo.link_to_commit(issue_ids, commit_id, self.user_id, resolution_comment)

    def issues_for_commit(self, commit_id: str):
        return self.repo.issues_for_commit(commit_id)

    def validate_commit_issues(self, commit_id: str, confirmed_ids: Iterable[int],
                               rejected_ids: Iterable[int], comment=""):
        for issue_id in confirmed_ids or []:
            self.repo.validate_commit_issue(issue_id, commit_id, True, self.user_id, comment)
        for issue_id in rejected_ids or []:
            self.repo.validate_commit_issue(issue_id, commit_id, False, self.user_id, comment)

    def impacted_part_ids(self, part_ids: Iterable[int]) -> set[int]:
        impacted = {int(x) for x in part_ids or []}
        queue = list(impacted)
        while queue:
            child_id = queue.pop()
            for rel in self.children_repo.get_parents(child_id):
                parent_id = int(rel.parent_id)
                if parent_id not in impacted:
                    impacted.add(parent_id)
                    queue.append(parent_id)
        return impacted

    def dependency_part_ids(self, part_ids: Iterable[int]) -> set[int]:
        dependencies = {int(x) for x in part_ids or []}
        queue = list(dependencies)
        while queue:
            parent_id = queue.pop()
            for rel in self.children_repo.get_children(parent_id):
                child_id = int(rel.child_id)
                if child_id not in dependencies:
                    dependencies.add(child_id)
                    queue.append(child_id)
        return dependencies

    def critical_blockers(self, part_ids=None, include_children=False):
        ids = self.dependency_part_ids(part_ids) if include_children and part_ids else part_ids
        return self.repo.blockers(int(self.project_id), ids)

    def assert_no_critical_issues(self, part_ids=None, operation="release", include_children=False):
        blockers = self.critical_blockers(part_ids, include_children)
        if blockers:
            labels = ", ".join(x["issue_number"] for x in blockers[:5])
            suffix = "" if len(blockers) <= 5 else f" and {len(blockers) - 5} more"
            raise ValueError(
                f"Cannot {operation}. {len(blockers)} unresolved critical issue(s): {labels}{suffix}"
            )

    def snapshot_state(self, project_id=None):
        return self.repo.snapshot_state(int(project_id or self.project_id))

    def health_score(self) -> int:
        metrics = self.metrics()
        score = 100 - int(metrics["open_count"]) - int(metrics["critical_count"]) * 9
        return max(0, min(100, score))

    def sync_validation_findings(self, findings: Iterable[tuple]):
        """Create/reopen validation issues and close findings no longer reported."""
        definitions = {
            "missing_file": ("Missing CAD file", "High", "Restore or relink the expected Creo file."),
            "outdated_file": ("Outdated CAD export", "Medium", "Update the BOM link to the latest approved file."),
            "missing_drawing": ("Missing drawing", "High", "Create or relink the required drawing."),
            "missing_pdf": ("Missing PDF export", "Medium", "Generate and attach the current PDF export."),
            "missing_step": ("Missing STEP export", "Medium", "Generate and attach the current STEP export."),
        }
        active_keys = set()
        for row in findings or []:
            try:
                part_id, finding_type, target = row
                title, priority, suggestion = definitions.get(
                    str(finding_type),
                    ("Validation finding", "Medium", "Review and correct the reported condition."),
                )
                key = f"{int(part_id)}:{finding_type}:{target}"
                active_keys.add(key)
                existing = self.repo.get_by_source(int(self.project_id), "validation", key)
                if existing:
                    if existing["status"] == "Closed":
                        self.repo.transition(existing["id"], "Open", self.user_id, "Finding detected again")
                    continue
                self.create_issue(
                    {
                        "title": f"{title}: {target}",
                        "description": f"Automatically generated by BOM diagnostics.\n\nSuggested fix: {suggestion}",
                        "priority": priority,
                        "category": "Validation",
                        "source_type": "validation",
                        "source_key": key,
                    },
                    [int(part_id)],
                )
            except Exception:
                continue

        for issue in self.repo.validation_source_issues(int(self.project_id)):
            if issue.get("source_key") not in active_keys and issue.get("status") != "Closed":
                self.repo.transition(
                    int(issue["id"]), "Closed", self.user_id,
                    "Automatically closed because the validation finding is no longer present",
                )
