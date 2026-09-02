import json
import os
import shutil
from dataclasses import asdict
from datetime import datetime
from typing import Callable, Dict, List, Optional, Set, Tuple

from core.repositories.bom_children_repository import BomChildrenRepository
from core.repositories.bom_repository import BomRepository
from core.services.export_naming import exported_document_filename, safe_filename
from core.services.part_file_service import PartFileService
from utils import ensure_dir_exists, safe_copy2, safe_exists, safe_open


class PackageExportService:
    """Export a delivery package containing PDF + STEP for each part."""

    def __init__(
        self,
        bom_repo: Optional[BomRepository] = None,
        bom_children_repo: Optional[BomChildrenRepository] = None,
        part_file_service: Optional[PartFileService] = None,
    ):
        self.bom_repo = bom_repo or BomRepository()
        self.children_repo = bom_children_repo or BomChildrenRepository()
        self.part_file_service = part_file_service or PartFileService()

    def _collect_part_ids_recursive(self, root_part_id: int) -> List[int]:
        # DFS using bom_children table
        visited: Set[int] = set()
        stack: List[int] = [root_part_id]
        order: List[int] = []

        while stack:
            pid = stack.pop()
            if pid in visited:
                continue
            visited.add(pid)
            order.append(pid)
            children = self.children_repo.get_children(pid)
            for rel in children:
                child_id = getattr(rel, "child_id", None)
                if child_id is not None and child_id not in visited:
                    stack.append(int(child_id))

        return order

    def _safe_filename(self, s: str) -> str:
        return safe_filename(s)

    def export_package(
        self,
        root_part_id: int,
        destination_dir: str,
        include_children: bool = True,
        package_name: Optional[str] = None,
        create_zip: bool = False,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
    ) -> Dict:
        if not root_part_id:
            raise ValueError("root_part_id is required")
        return self.export_package_for_parts(
            part_ids=[root_part_id],
            destination_dir=destination_dir,
            include_children=include_children,
            package_name=package_name,
            create_zip=create_zip,
            progress_callback=progress_callback,
        )

    def export_package_for_parts(
        self,
        part_ids: List[int],
        destination_dir: str,
        include_children: bool = True,
        package_name: Optional[str] = None,
        create_zip: bool = False,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
    ) -> Dict:
        if not part_ids:
            raise ValueError("part_ids is required")
        if not destination_dir:
            raise ValueError("destination_dir is required")

        # Expand selection
        selected: List[int] = []
        seen: Set[int] = set()
        for pid in part_ids:
            if pid is None:
                continue
            if include_children:
                expanded = self._collect_part_ids_recursive(int(pid))
            else:
                expanded = [int(pid)]
            for e in expanded:
                if e not in seen:
                    seen.add(e)
                    selected.append(e)

        package_name = (package_name or f"package_{datetime.now().strftime('%Y%m%d_%H%M%S')}").strip()
        out_dir = os.path.join(destination_dir, self._safe_filename(package_name))
        pdf_dir = os.path.join(out_dir, "PDF")
        step_dir = os.path.join(out_dir, "STEP")
        ensure_dir_exists(pdf_dir)
        ensure_dir_exists(step_dir)

        exported: List[Dict] = []
        missing: List[Dict] = []
        skipped: List[Dict] = []
        total_steps = max(1, len(selected) * 2 + (1 if create_zip else 0) + 1)
        completed_steps = 0

        def report(message: str) -> None:
            if progress_callback:
                progress_callback(str(message), completed_steps, total_steps)

        def step(message: str) -> None:
            nonlocal completed_steps
            completed_steps = min(total_steps, completed_steps + 1)
            if progress_callback:
                progress_callback(str(message), completed_steps, total_steps)

        report("Preparing package export...")

        for pid in selected:
            part = self.bom_repo.get_by_id(pid)
            if not part:
                missing.append({"part_id": pid, "reason": "part_not_found"})
                step(f"Missing Item {pid}")
                step(f"Skipping Item {pid}")
                continue

            if getattr(part, "represented_part_id", None):
                skipped.append({
                    "part_id": pid,
                    "aes_number": getattr(part, "aes_number", None),
                    "name": getattr(part, "name", None),
                    "reason": "cad_representation",
                    "represented_part_id": int(part.represented_part_id),
                })
                step(f"Skipping CAD-only representation {getattr(part, 'name', pid)}")
                step(f"Skipping CAD-only representation {getattr(part, 'name', pid)}")
                continue

            part_info = {
                "part_id": pid,
                "aes_number": getattr(part, "aes_number", None),
                "name": getattr(part, "name", None),
                "type": getattr(part, "type", None),
            }

            attachments = self.part_file_service.list_attachments(pid)

            def pick_active_path(file_type: str) -> Tuple[Optional[str], Optional[Dict]]:
                for att in attachments:
                    if (att.file_type or "").upper() != file_type.upper():
                        continue
                    active_ver = self.part_file_service.get_active_version(att.id)
                    if not active_ver:
                        continue

                    # Note: We used to require the active version to be in "RELEASED" state, but that was too restrictive for some use cases. Instead, we will export the active version regardless of lifecycle state, but include the lifecycle state and version note in the metadata so the consumer can decide how to handle it.

                    active_path = self.part_file_service.resolve_active_path(att.id)
                    if active_path:
                        return active_path, {
                            "file_id": att.id,
                            "display_name": att.display_name,
                            "file_type": att.file_type,
                            "active_version_id": att.active_version_id,
                            "active_version_note": getattr(active_ver, "note", None),
                            "active_version_revision": getattr(active_ver, "revision", None),
                            "active_original_filename": getattr(active_ver, "original_filename", None),
                            "project_version_label": getattr(active_ver, "project_version_label", None),
                        }
                return None, None

            pdf_path, pdf_meta = pick_active_path("PDF")
            step_path, step_meta = pick_active_path("STEP")

            row = {**part_info, "pdf": None, "step": None}

            if pdf_path and safe_exists(pdf_path):
                active_revision = (pdf_meta or {}).get("active_version_revision") or getattr(part, "revision", None) or ""
                project_version_label = (pdf_meta or {}).get("project_version_label") or ""
                dst_name = exported_document_filename(
                    part=part,
                    file_type="PDF",
                    source_path=pdf_path,
                    revision=active_revision,
                    project_version_label=project_version_label,
                    include_date=True,
                )
                dst_path = os.path.join(pdf_dir, dst_name)
                safe_copy2(pdf_path, dst_path)
                row["pdf"] = {"src": pdf_path, "dst": dst_path, **(pdf_meta or {})}
                step(f"Copied PDF for {getattr(part, 'name', pid)}")
            else:
                reason = (pdf_meta or {}).get("reason")
                missing.append({**part_info, "missing": "PDF", "expected": pdf_path, **({"reason": reason} if reason else {})})
                step(f"PDF missing for {getattr(part, 'name', pid)}")

            if step_path and safe_exists(step_path):
                dst_name = exported_document_filename(
                    part=part,
                    file_type="STEP",
                    source_path=step_path,
                    revision="",
                    project_version_label=(step_meta or {}).get("project_version_label") or "",
                    include_date=True,
                )
                dst_path = os.path.join(step_dir, dst_name)
                safe_copy2(step_path, dst_path)
                row["step"] = {"src": step_path, "dst": dst_path, **(step_meta or {})}
                step(f"Copied STEP for {getattr(part, 'name', pid)}")
            else:
                reason = (step_meta or {}).get("reason")
                missing.append({**part_info, "missing": "STEP", "expected": step_path, **({"reason": reason} if reason else {})})
                step(f"STEP missing for {getattr(part, 'name', pid)}")


            exported.append(row)

        manifest = {
            "package": {
                "name": package_name,
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "part_ids": selected,
                "include_children": include_children,
                "output_dir": out_dir,
            },
            "exported": exported,
            "missing": missing,
            "skipped": skipped,
        }

        if create_zip:
            # Creates: <destination_dir>/<package_name>.zip
            report("Creating ZIP archive...")
            zip_base = os.path.join(destination_dir, self._safe_filename(package_name))
            zip_path = shutil.make_archive(zip_base, "zip", root_dir=out_dir)
            manifest["package"]["zip_path"] = zip_path
            step("ZIP archive created")

        with safe_open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
        completed_steps = total_steps
        report("Package export complete")

        return manifest
