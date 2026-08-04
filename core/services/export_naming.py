import os
import re
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


def _looks_like_cad_or_document_filename(value: Optional[str]) -> bool:
    """Return True when a metadata field contains a file name fallback.

    Drawing numbers are user/business metadata.  If the field was populated
    from a CAD/PDF/STEP file name such as ``braid_neutral.drw`` or
    ``p1234567-part.drw.3``, it must not become the first segment of exported
    delivery names.
    """
    text = str(value or "").strip()
    if not text:
        return False
    lower = text.lower()
    if re.search(r"\.(?:drw|prt|asm)(?:\.\d+)?$", lower):
        return True
    if re.search(r"\.(?:pdf|step|stp|iges|igs|dxf|dwg)$", lower):
        return True
    return False


def real_drawing_number(value: Optional[str]) -> str:
    text = str(value or "").strip()
    if _looks_like_cad_or_document_filename(text):
        return ""
    return text


def drawing_index_for_part(part) -> str:
    for field_name in (
        "drawing_index",
        "draw_index",
        "drawing_indice",
        "indice",
        "index",
    ):
        value = str(getattr(part, field_name, "") or "").strip()
        if value and not _looks_like_cad_or_document_filename(value):
            return value
    return ""


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
    drawing_no = real_drawing_number(getattr(part, "drawing_number", ""))
    index = drawing_index_for_part(part)
    part_name = str(getattr(part, "name", "") or "").strip()
    rev = normalize_name_part(revision)

    parts = [drawing_no, index, normalize_name_part(part_name)]
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
