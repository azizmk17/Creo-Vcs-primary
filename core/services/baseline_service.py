import json
import os
import shutil
from datetime import datetime
from typing import Dict, List, Optional, Set

from core.repositories.baseline_repository import BaselineRepository
from core.repositories.baseline_file_repository import BaselineFileRepository
from core.repositories.bom_children_repository import BomChildrenRepository
from core.repositories.bom_repository import BomRepository
from core.repositories.bom_revision_repository import BomRevisionRepository
from core.services.base_service import BaseService
from core.services.export_naming import exported_document_filename, safe_filename
from core.services.part_file_service import PartFileService


class BaselineService(BaseService):
    def __init__(
        self,
        baseline_repo: Optional[BaselineRepository] = None,
        baseline_file_repo: Optional[BaselineFileRepository] = None,
        bom_repo: Optional[BomRepository] = None,
        bom_children_repo: Optional[BomChildrenRepository] = None,
        part_file_service: Optional[PartFileService] = None,
    ):
        super().__init__()
        self.baseline_repo = baseline_repo or BaselineRepository()
        self.baseline_file_repo = baseline_file_repo or BaselineFileRepository()
        self.bom_repo = bom_repo or BomRepository()
        self.children_repo = bom_children_repo or BomChildrenRepository()
        self.revision_repo = BomRevisionRepository()
        self.part_file_service = part_file_service or PartFileService()

    def _collect_part_ids_recursive(self, root_part_id: int) -> List[int]:
        visited: Set[int] = set()
        stack: List[int] = [int(root_part_id)]
        order: List[int] = []

        while stack:
            pid = int(stack.pop())
            if pid in visited:
                continue
            visited.add(pid)
            order.append(pid)
            for rel in self.children_repo.get_children(pid):
                child_id = getattr(rel, "child_id", None)
                if child_id is not None and int(child_id) not in visited:
                    stack.append(int(child_id))

        return order

    def _safe_filename(self, s: str) -> str:
        return safe_filename(s)

    def list_baselines(self) -> List[Dict]:
        rows = self.baseline_repo.list_for_project(self.project_id)
        return [
            {
                "id": int(b.id),
                "name": b.name,
                "created_at": b.created_at,
                "include_children": int(getattr(b, "include_children", 1) or 0),
            }
            for b in rows
        ]

    def create_baseline(self, name: str, part_ids: List[int], include_children: bool = True) -> Dict:
        if not name or not name.strip():
            raise ValueError("Baseline name is required")
        if not part_ids:
            raise ValueError("At least one part must be selected")

        selected_root_ids = [int(x) for x in part_ids]
        expanded: List[int] = []
        seen: Set[int] = set()
        for pid in selected_root_ids:
            ids = self._collect_part_ids_recursive(pid) if include_children else [pid]
            for x in ids:
                if x not in seen:
                    seen.add(x)
                    expanded.append(x)

        baseline_id = self.baseline_repo.create(
            project_id=self.project_id,
            name=name.strip(),
            created_by=self.user_id,
            include_children=1 if include_children else 0,
            part_ids_json=json.dumps(selected_root_ids),
        )

        missing: List[Dict] = []
        created_rows = 0

        for pid in expanded:
            attachments = self.part_file_service.list_attachments(pid)
            object_context = self.revision_repo.get_current_context(int(pid))

            def pick(file_type: str):
                for att in attachments:
                    if (att.file_type or "").upper() != file_type.upper():
                        continue
                    ver = self.part_file_service.get_active_version(att.id)
                    if not ver:
                        continue
                    return att.id, ver.id, (ver.lifecycle_state or "")
                return None, None, ""

            for ft in ("PDF", "STEP"):
                file_id, version_id, state = pick(ft)
                self.baseline_file_repo.add(
                    baseline_id,
                    pid,
                    ft,
                    file_id,
                    version_id,
                    object_iteration_id=object_context.get("current_iteration_id"),
                )
                created_rows += 1

                if not version_id:
                    missing.append({"part_id": pid, "file_type": ft, "reason": "no_version"})
                # The lifecycle state check is deferred to export time, since files may be in development at baseline creation but released later
                # elif str(state).upper() != "RELEASED":
                #     missing.append({"part_id": pid, "file_type": ft, "reason": "not_released"})

        return {
            "baseline_id": baseline_id,
            "name": name.strip(),
            "part_ids": selected_root_ids,
            "include_children": bool(include_children),
            "expanded_part_ids": expanded,
            "rows": created_rows,
            "missing": missing,
        }

    def export_baseline(self, baseline_id: int, destination_dir: str, package_name: Optional[str] = None) -> Dict:
        if not baseline_id:
            raise ValueError("baseline_id is required")
        if not destination_dir:
            raise ValueError("destination_dir is required")

        baseline = self.baseline_repo.get_by_id(int(baseline_id))
        if not baseline:
            raise ValueError("Baseline not found")

        files = self.baseline_file_repo.list_for_baseline(int(baseline_id))

        package_name = (package_name or baseline.name or f"baseline_{baseline_id}").strip()
        out_dir = os.path.join(destination_dir, self._safe_filename(f"baseline_{baseline_id}_{package_name}"))
        pdf_dir = os.path.join(out_dir, "PDF")
        step_dir = os.path.join(out_dir, "STEP")
        os.makedirs(pdf_dir, exist_ok=True)
        os.makedirs(step_dir, exist_ok=True)

        exported: List[Dict] = []
        missing: List[Dict] = []

        for bf in files:
            part = self.bom_repo.get_by_id(int(bf.part_id))
            aes = getattr(part, "aes_number", None) if part else None

            if not bf.version_id:
                missing.append({"part_id": bf.part_id, "aes_number": aes, "file_type": bf.file_type, "reason": "no_version"})
                continue

            ver = self.part_file_service.repo.get_version_by_id(int(bf.version_id))
            if not ver:
                missing.append({"part_id": bf.part_id, "aes_number": aes, "file_type": bf.file_type, "version_id": bf.version_id, "reason": "version_not_found"})
                continue

            src = self.part_file_service.resolve_version_path(ver)
            state = str(getattr(ver, "lifecycle_state", "") or "")
            # The lifecycle state check is optional, since some users may want to export in-development files. If you want to enforce it, uncomment the following lines:
            # if state.upper() != "RELEASED":
            #     missing.append({"part_id": bf.part_id, "aes_number": aes, "file_type": bf.file_type, "version_id": bf.version_id, "reason": "not_released", "state": state})
            #     continue

            if not src or not os.path.exists(src):
                missing.append({"part_id": bf.part_id, "aes_number": aes, "file_type": bf.file_type, "version_id": bf.version_id, "reason": "file_missing", "expected": src})
                continue

            file_type = str(bf.file_type or "").upper()
            dst_dir = pdf_dir if file_type == "PDF" else step_dir
            dst_name = exported_document_filename(
                part=part,
                file_type=file_type,
                source_path=src,
                revision=getattr(ver, "revision", None) or "",
                project_version_label=getattr(ver, "project_version_label", None) or "",
                include_date=True,
            )
            dst_path = os.path.join(dst_dir, dst_name)
            shutil.copy2(src, dst_path)

            exported.append(
                {
                    "part_id": bf.part_id,
                    "aes_number": aes,
                    "file_type": bf.file_type,
                    "file_id": bf.file_id,
                    "version_id": bf.version_id,
                    "object_iteration_id": getattr(bf, "object_iteration_id", None),
                    "object_version": (
                        self.revision_repo.get_iteration_context(
                            int(getattr(bf, "object_iteration_id"))
                        ).get("version_label")
                        if getattr(bf, "object_iteration_id", None) else ""
                    ),
                    "src": src,
                    "dst": dst_path,
                }
            )

        manifest = {
            "baseline": {
                "id": int(baseline.id),
                "name": baseline.name,
                "project_id": int(baseline.project_id),
                "created_at": baseline.created_at,
                "created_by": baseline.created_by,
                "include_children": bool(int(getattr(baseline, "include_children", 1) or 0)),
                "part_ids": json.loads(baseline.part_ids_json) if baseline.part_ids_json else [],
            },
            "package": {
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "output_dir": out_dir,
            },
            "exported": exported,
            "missing": missing,
        }

        with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        return manifest
