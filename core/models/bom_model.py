from dataclasses import dataclass
from typing import Optional

@dataclass
class Bom:
    id: int
    type: str
    name: str
    part_number: Optional[str] = None
    drawing_number: Optional[str] = None
    aes_number: Optional[str] = None
    filename: Optional[str] = None
    drawing: Optional[str] = None
    base_file_name: Optional[str] = None
    base_drw_name: Optional[str] = None
    material: Optional[str] = None
    weight: Optional[str] = None
    notes: Optional[str] = None
    pdf_path: Optional[str] = None
    step_path: Optional[str] = None
    revision: Optional[str] = "A"
    lifecycle_state: Optional[str] = "WIP"  # WIP | Released | Obsolete
    released_by: Optional[int] = None
    released_at: Optional[str] = None
    status: str = "Design"
    created: Optional[str] = None
    modified: Optional[str] = None
    project_id: int = None
    locked: int = 0
    current_revision_id: Optional[int] = None
    current_iteration_id: Optional[int] = None
    pending_revision_code: Optional[str] = None
    classification: str = "PHYSICAL"
    default_ebom_behavior: str = "NORMAL"
    cad_requirement: str = "OPTIONAL"
    drawing_requirement: str = "OPTIONAL"
    represented_part_id: Optional[int] = None
    cad_control_mode: str = "CONTROLLED"
    item_type: str = "MECHANICAL_PART"
    assembly_mode: str = "COMPONENT"
    procurement_source: str = "MAKE"
    item_view: str = "DESIGN"
    default_unit: str = "EA"
