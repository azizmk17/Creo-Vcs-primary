

from dataclasses import dataclass
from typing import Optional


@dataclass
class Merge:
    id: int
    part_id: Optional[int]
    status: Optional[str]
    type: str
    filename: str
    title: Optional[str]
    commit_id: Optional[str]
    project_id: Optional[int]
    designer_username: str
    cad_document_id: Optional[int] = None
    creo_file_version: Optional[int] = None
    
    
