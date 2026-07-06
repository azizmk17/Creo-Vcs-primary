import os
from datetime import datetime
from typing import Optional


def safe_filename(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._- ()" else "_" for ch in (value or ""))


def normalize_name_part(value: Optional[str]) -> str:
    if value is None:
        return ""
    return "_".join(str(value).split())


def is_project_revision(revision: Optional[str], project_version_label: Optional[str]) -> bool:
    rev = str(revision or "").strip().upper()
    project_rev = str(project_version_label or "").strip().upper()
    return bool(rev and project_rev and rev == project_rev)


def exported_document_filename(
    *,
    part,
    file_type: str,
    source_path: str,
    revision: Optional[str] = "",
    project_version_label: Optional[str] = "",
    include_date: bool = True,
) -> str:
    """Build customer-facing export names without leaking project version labels."""

    file_type_upper = str(file_type or "").strip().upper()
    drawing_no = str(getattr(part, "drawing_number", "") or "").strip()
    aes = str(getattr(part, "aes_number", "") or "").strip()
    part_name = str(getattr(part, "name", "") or "").strip()
    rev = normalize_name_part(revision)

    parts = [drawing_no, aes, normalize_name_part(part_name)]
    if file_type_upper == "PDF" and rev and not is_project_revision(rev, project_version_label):
        parts.append(rev)
    if include_date:
        parts.append(datetime.now().strftime("%Y%m%d"))

    safe_parts = [safe_filename(p).strip("._- ") for p in parts if p and str(p).strip()]
    stem = "-".join(p for p in safe_parts if p)
    if not stem:
        stem = safe_filename(os.path.splitext(os.path.basename(source_path or ""))[0] or "export")

    ext = os.path.splitext(os.path.basename(source_path or ""))[1]
    if not ext:
        ext = ".pdf" if file_type_upper == "PDF" else ".step" if file_type_upper == "STEP" else ""
    return f"{stem}{ext}"
