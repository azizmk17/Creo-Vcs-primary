import json
import os
import shutil
from dataclasses import asdict
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

from core.repositories.bom_children_repository import BomChildrenRepository
from core.repositories.bom_repository import BomRepository
from core.services.part_file_service import PartFileService


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
        return "".join(ch if ch.isalnum() or ch in "._- ()" else "_" for ch in (s or ""))

    def export_package(
        self,
        root_part_id: int,
        destination_dir: str,
        include_children: bool = True,
        package_name: Optional[str] = None,
        create_zip: bool = False,
    ) -> Dict:
        if not root_part_id:
            raise ValueError("root_part_id is required")
        return self.export_package_for_parts(
            part_ids=[root_part_id],
            destination_dir=destination_dir,
            include_children=include_children,
            package_name=package_name,
            create_zip=create_zip,
        )

    def export_package_for_parts(
        self,
        part_ids: List[int],
        destination_dir: str,
        include_children: bool = True,
        package_name: Optional[str] = None,
        create_zip: bool = False,
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
        os.makedirs(pdf_dir, exist_ok=True)
        os.makedirs(step_dir, exist_ok=True)

        exported: List[Dict] = []
        missing: List[Dict] = []

        for pid in selected:
            part = self.bom_repo.get_by_id(pid)
            if not part:
                missing.append({"part_id": pid, "reason": "part_not_found"})
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
                            "active_original_filename": getattr(active_ver, "original_filename", None),
                        }
                return None, None

            pdf_path, pdf_meta = pick_active_path("PDF")
            step_path, step_meta = pick_active_path("STEP")

            row = {**part_info, "pdf": None, "step": None}

            if pdf_path and os.path.exists(pdf_path):
                # Build dst name: drawingNumber - aesNumber - name(with underscores) - note - YYYYMMDD
                drawing_no = getattr(part, "drawing_number", None) or ""
                aes = part_info.get("aes_number") or ""
                pname = part_info.get("name") or ""
                # normalize name and note to underscores between words
                def _norm(s: Optional[str]) -> str:
                    if s is None:
                        return ""
                    return "_".join(str(s).split())

                name_us = _norm(pname)
                note_us = _norm((pdf_meta or {}).get("active_version_note") or "")
                date_str = datetime.now().strftime("%Y%m%d")

                parts = [drawing_no, aes, name_us]
                if note_us:
                    parts.append(note_us)
                parts.append(date_str)

                # sanitize each part and join
                safe_parts = [self._safe_filename(p) for p in parts if p and str(p).strip()]
                ext = os.path.splitext(os.path.basename(pdf_path))[1] or ".pdf"
                dst_name = "-".join(safe_parts) + ext
                dst_path = os.path.join(pdf_dir, dst_name)
                shutil.copy2(pdf_path, dst_path)
                row["pdf"] = {"src": pdf_path, "dst": dst_path, **(pdf_meta or {})}
            else:
                reason = (pdf_meta or {}).get("reason")
                missing.append({**part_info, "missing": "PDF", "expected": pdf_path, **({"reason": reason} if reason else {})})

            
            if step_path and os.path.exists(step_path):
                # Build dst name: drawingNumber - aesNumber - name(with underscores) - note - YYYYMMDD
                drawing_no = getattr(part, "drawing_number", None) or ""
                aes = part_info.get("aes_number") or ""
                pname = part_info.get("name") or ""
                # normalize name and note to underscores between words
                def _norm(s: Optional[str]) -> str:
                    if s is None:
                        return ""
                    return "_".join(str(s).split())

                name_us = _norm(pname)
                date_str = datetime.now().strftime("%Y%m%d")
                parts = [drawing_no, aes, name_us]
          
                parts.append(date_str)

                # sanitize each part and join
                safe_parts = [self._safe_filename(p) for p in parts if p and str(p).strip()]
                ext = os.path.splitext(os.path.basename(step_path))[1] or ".step"
                dst_name = "-".join(safe_parts) + ext
                dst_path = os.path.join(step_dir, dst_name)
                shutil.copy2(step_path, dst_path)
                row["step"] = {"src": step_path, "dst": dst_path, **(step_meta or {})}
            else:
                reason = (step_meta or {}).get("reason")
                missing.append({**part_info, "missing": "STEP", "expected": step_path, **({"reason": reason} if reason else {})})


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
        }

        if create_zip:
            # Creates: <destination_dir>/<package_name>.zip
            zip_base = os.path.join(destination_dir, self._safe_filename(package_name))
            zip_path = shutil.make_archive(zip_base, "zip", root_dir=out_dir)
            manifest["package"]["zip_path"] = zip_path

        with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        return manifest
