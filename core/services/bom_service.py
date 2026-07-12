from typing import List, Dict
from collections import defaultdict
import os
import sqlite3
from datetime import datetime
from core.models.bom_model import Bom
from core.repositories.bom_repository import BomRepository
from core.repositories.bom_children_repository import BomChildrenRepository
from core.repositories.lock_repository import LockRepository
from core.repositories.signature_repository import SignatureRepository
from core.repositories.permission_repository import PermissionRepository
from core.repositories.bom_folder_repository import BomFolderRepository
from core.session_manager import SessionManager
from config import DB_NAME

from core.services.base_service import BaseService


class BomService(BaseService):
    def __init__(self, bom_repo: BomRepository, children_repo: BomChildrenRepository, lock_repo: LockRepository, signature_repo: SignatureRepository):
        super().__init__()
        self.bom_repo = bom_repo
        self.children_repo = children_repo
        self.lock_repo = lock_repo
        self.signature_repo = signature_repo
        self.permission_repo = PermissionRepository()
        self.folder_repo = BomFolderRepository()
        self.session = SessionManager()
        self._tree_cache: dict = {}    # project_id -> tree dict
        self._tree_dirty: set = set()  # project_ids that need re-fetch
        self._lazy_index_cache: dict = {}
        print(self.user_id)

    # -------------------------------
    # ORGANIZATIONAL FOLDERS
    # -------------------------------
    def list_bom_folders(self) -> List[Dict]:
        if not self.session.project_id:
            return []
        return self.folder_repo.list_for_project(int(self.session.project_id))

    def create_bom_folder(self, name: str, parent_bom_id=None, parent_folder_id=None) -> Dict:
        if not self.session.project_id:
            raise ValueError("Select a project before creating a folder.")
        result = self.folder_repo.create(
            int(self.session.project_id), name, self.user_id,
            parent_bom_id=parent_bom_id, parent_folder_id=parent_folder_id,
        )
        self._tree_dirty.add(int(self.session.project_id))
        return result

    def rename_bom_folder(self, folder_id: int, name: str) -> Dict:
        result = self.folder_repo.rename(int(self.session.project_id), int(folder_id), name)
        self._tree_dirty.add(int(self.session.project_id))
        return result

    def eligible_bom_folder_items(self, folder_id: int) -> List[Dict]:
        return self.folder_repo.eligible_items(int(self.session.project_id), int(folder_id))

    def set_bom_folder_items(self, folder_id: int, bom_ids) -> List[int]:
        result = self.folder_repo.set_items(
            int(self.session.project_id), int(folder_id), bom_ids, self.user_id
        )
        self._tree_dirty.add(int(self.session.project_id))
        return result

    def delete_bom_folder(self, folder_id: int) -> List[int]:
        result = self.folder_repo.delete(int(self.session.project_id), int(folder_id))
        self._tree_dirty.add(int(self.session.project_id))
        return result

    # -------------------------------
    # INSERT PART / ASM
    # -------------------------------
    def add_part(self, part_data: dict) -> int:
        aes_number = part_data.get("aes_number") or part_data.get("id")
        
        existing = self.bom_repo.get_by_aes(aes_number, self.session.project_id)
        if existing:
            return "existing"
        
        if part_data.get("filename"):
            filename = os.path.basename(part_data.get("filename"))
            base_f_name = ".".join(filename.split(".")[:-1])
        else:
            filename = None
            base_f_name = None

        if part_data.get("drawing"):
            drawing_filename = os.path.basename(part_data.get("drawing"))
            base_drw_name = ".".join(drawing_filename.split(".")[:-1])
        else:
            drawing_filename = None
            base_drw_name = None

        bom_item = Bom(
            id=None,
            type=part_data.get("type", "prt"),
            name=part_data.get("name", "Unnamed"),
            part_number=part_data.get("part_number"),
            drawing_number=part_data.get("drawing_number"),
            aes_number=aes_number,
            filename=filename,
            base_file_name=base_f_name,
            drawing=drawing_filename,
            base_drw_name=base_drw_name,
            material=part_data.get("material"),
            weight=part_data.get("weight"),
            notes=part_data.get("notes"),
            pdf_path=part_data.get("pdf_path"),
            step_path=part_data.get("step_path"),
            status=part_data.get("status", "Design"),
            created=part_data.get("created"),
            modified=part_data.get("modified"),
            project_id=self.session.project_id
        )
        result = self.bom_repo.insert(bom_item)
        self._tree_dirty.add(int(self.session.project_id))
        return result

    # -------------------------------
    # UPDATE PART
    # -------------------------------
    def update_part(self, part_id: str, part_data: dict) -> bool:
        """
        Update an existing part
        """
        part = self.bom_repo.get_by_id(part_id)
        if not part:
            return False
            
        # Update part fields
        for key, value in part_data.items():
            if hasattr(part, key):
                setattr(part, key, value)
                
        result = self.bom_repo.update(part)
        self._tree_dirty.add(int(self.session.project_id))
        return result

    # -------------------------------
    # DELETE PART
    # -------------------------------
    def delete_part(self, part_id: str) -> bool:
        """
        Delete a part by AES number
        """
        part = self.bom_repo.get_by_id(part_id)
        if not part:
            return False
            
        # First remove any child relationships
        self.children_repo.delete_by_parent(part.id)
        self.children_repo.delete_by_child(part.id)
        
        # Then delete the part
        result = self.bom_repo.delete(part.id)
        self._tree_dirty.add(int(self.session.project_id))
        return result

    # -------------------------------
    # ADD CHILD RELATIONSHIP
    # -------------------------------
    
    def add_child_by_id(self, parent_id: int, child_id: int):
        """
        Add a child under a parent by their IDs. Skip if child has underscore.
        """
        parent = self.bom_repo.get_by_id(parent_id)
        child = self.bom_repo.get_by_id(child_id)
        if not parent or not child:
            return -1
        

        result = self.children_repo.insert(parent.id, child.id)
        self._tree_dirty.add(int(self.session.project_id))
        return result
    
    # -------------------------------
    def _parts_sharing_base_file(self, part) -> List:
        base = str(getattr(part, "base_file_name", "") or "").strip()
        project_id = getattr(part, "project_id", None) or self.session.project_id
        if not base or not project_id:
            return [part]
        try:
            related = self.bom_repo.get_all_by_base_file_name_for_commit(base, int(project_id), self.session.user_id) or []
        except Exception:
            related = []
        return related or [part]

    def part_ids_sharing_base_file(self, part_id: int) -> List[int]:
        part = self.bom_repo.get_by_id(int(part_id))
        if not part:
            return []
        return [int(p.id) for p in self._parts_sharing_base_file(part) if getattr(p, "id", None) is not None]

    # CHECKIN PART
    # -------------------------------
    def checkin_part(self, part_id: str, as_user_id: int | None = None):
        part = self.bom_repo.get_by_id(part_id)
        if not part:
            raise ValueError("Part not found")
        related_parts = self._parts_sharing_base_file(part)
        locked_by_part = {
            int(p.id): self.lock_repo.get_by_part(int(p.id))
            for p in related_parts
            if getattr(p, "id", None) is not None
        }
        active_locks = [lock for lock in locked_by_part.values() if lock]
        if not active_locks:
            raise ValueError("Part is not checked out.")
        actor_user_id = int(self.session.user_id) if self.session.user_id is not None else None
        if not actor_user_id:
            raise PermissionError("You must be logged in")

        other_locks = [lock for lock in active_locks if int(lock.user_id) != actor_user_id]
        if other_locks:
            if not self.permission_repo.user_has_permission(actor_user_id, "merge", self.session.project_id):
                raise ValueError("Part is checked out by another user.")
            effective_user_id = int(as_user_id) if as_user_id is not None else int(actor_user_id)
        else:
            effective_user_id = int(actor_user_id)

        for related in related_parts:
            related_id = int(related.id)
            if not locked_by_part.get(related_id):
                self.bom_repo.checkin_bom(related_id)
                continue
            signature = self.signature_repo.add_signature(
                "checkin",
                effective_user_id,
                note="Checked in shared CAD family part" if len(related_parts) > 1 else "Checked in part",
            )
            success = self.lock_repo.checkin(related_id, effective_user_id, signature)
            if not success:
                raise ValueError("Failed to check in part")
            self.bom_repo.checkin_bom(related_id)
        self._tree_dirty.add(int(self.session.project_id))
        return True

    def checkout_part(self, part_id: str, as_user_id: int | None = None):
        part = self.bom_repo.get_by_id(part_id)
        if not part:
            raise ValueError("Part not found")
        related_parts = self._parts_sharing_base_file(part)
        existing_locks = [
            self.lock_repo.get_by_part(int(p.id))
            for p in related_parts
            if getattr(p, "id", None) is not None
        ]
        if any(existing_locks):
            raise ValueError("Part is already checked out.")
        actor_user_id = int(self.session.user_id) if self.session.user_id is not None else None
        if not actor_user_id:
            raise PermissionError("You must be logged in")

        if as_user_id is not None and int(as_user_id) != int(actor_user_id):
            if not self.permission_repo.user_has_permission(actor_user_id, "merge", self.session.project_id):
                raise PermissionError("Only Master/Admin can check out as another user")
            effective_user_id = int(as_user_id)
        else:
            effective_user_id = int(actor_user_id)

        for related in related_parts:
            related_id = int(related.id)
            signature = self.signature_repo.add_signature(
                "checkout",
                effective_user_id,
                note="Checked out shared CAD family part" if len(related_parts) > 1 else "Checked out part",
            )
            success = self.lock_repo.checkout(related_id, effective_user_id, signature)
            if not success:
                raise ValueError("Failed to check out part")
            self.bom_repo.checkout_bom(related_id)
        self._tree_dirty.add(int(self.session.project_id))
        return True

    def checkin_by_part_id(self, part_id: int, user_id: int):
        part = self.bom_repo.get_by_id(int(part_id))
        if not part:
            raise ValueError("Part not found")
        related_parts = self._parts_sharing_base_file(part)
        locked_by_part = {
            int(p.id): self.lock_repo.get_by_part(int(p.id))
            for p in related_parts
            if getattr(p, "id", None) is not None
        }
        if not any(locked_by_part.values()):
            raise ValueError("Part is not checked out.")

        for related in related_parts:
            related_id = int(related.id)
            if not locked_by_part.get(related_id):
                self.bom_repo.checkin_bom(related_id)
                continue
            signature = self.signature_repo.add_signature(
                "checkin",
                user_id,
                note="Checked in shared CAD family part" if len(related_parts) > 1 else "Checked in part",
            )
            success = self.lock_repo.checkin(related_id, user_id, signature)
            if not success:
                raise ValueError("Failed to check in part")
            self.bom_repo.checkin_bom(related_id)
        self._tree_dirty.add(int(self.session.project_id))
        return True

    def checkout_by_part_id(self, part_id: int, user_id: int):
        return self.checkin_by_part_id(part_id, user_id)

    # -------------------------------
    # GET CHILDREN OF A PART
    # -------------------------------
    def get_children(self, part_id: str) -> List[Dict]:
        """
        Get all direct children of a part
        """
        part = self.bom_repo.get_by_id(part_id)
        if not part:
            return []
            
        children_relations = self.children_repo.get_children(part.id)
        result = []
        
        # for relation in children_relations:
        #     # Extract the child_id from the relation object
        #     child_id = relation.child_id if hasattr(relation, 'child_id') else relation
        #     child_part = self.bom_repo.get_by_id(child_id)
        #     result.append(child_part.__dict__)

        for relation in children_relations:
            # Extract the child_id
            child_id = getattr(relation, "child_id", relation)
            child_part = self.bom_repo.get_by_id(child_id)

            if not child_part:
                continue

            # Convert part object to dict
            child_dict = child_part.__dict__.copy()

            # Add the quantity from the relation (default = 1 if not set)
            quantity = getattr(relation, "quantity", 1)
            child_dict["quantity"] = quantity

            result.append(child_dict)

        print(f"Children of part {part_id}: {result}")
                
        return result
    
    # -------------------------------
    # GET HISTORY OF A PART
    # -------------------------------
    def get_history(self, part_id: int) -> List[Dict]:
        """
        Get all history of a part
        """
        part = self.bom_repo.get_by_id(part_id)
        if not part:
            return []
            
        part_history = self.signature_repo.get_all_history_by_part(part.id)
        result = []
        for record in part_history:
            result.append(record.__dict__)
        return result

    # -------------------------------
    # DETAILED HISTORY / ANALYTICS
    # -------------------------------
    def _conn(self):
        conn = sqlite3.connect(DB_NAME)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _parse_dt(s: str):
        if not s:
            return None
        try:
            # ISO format: 2026-01-27T12:34:56 or with offset
            return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        except Exception:
            pass
        try:
            # sqlite datetime('now'): 2026-01-27 12:34:56
            return datetime.strptime(str(s), "%Y-%m-%d %H:%M:%S")
        except Exception:
            return None

    def _project_family_ids(self, project_id: int) -> List[Dict]:
        """Return projects in the same family as project_id.

        Output rows are dicts: {id, name, root_project_id, version_label}
        """
        if not project_id:
            return []
        with self._conn() as conn:
            try:
                cols = [r[1] for r in conn.execute("PRAGMA table_info(projects)").fetchall()]
            except Exception:
                cols = []

            if "root_project_id" not in cols or "version_label" not in cols:
                row = conn.execute("SELECT id, name FROM projects WHERE id = ?", (int(project_id),)).fetchone()
                if not row:
                    return []
                return [
                    {
                        "id": int(row["id"]),
                        "name": row["name"],
                        "root_project_id": int(row["id"]),
                        "version_label": "A",
                    }
                ]

            row = conn.execute(
                "SELECT id, root_project_id FROM projects WHERE id = ?",
                (int(project_id),),
            ).fetchone()
            if not row:
                return []
            root_id = int(row["root_project_id"] or row["id"])
            rows = conn.execute(
                "SELECT id, name, root_project_id, version_label FROM projects WHERE root_project_id = ?",
                (root_id,),
            ).fetchall()
            out = [dict(r) for r in rows]
            for r in out:
                r["id"] = int(r.get("id"))
                r["root_project_id"] = int(r.get("root_project_id") or r["id"])
                r["version_label"] = (r.get("version_label") or "").strip() or "A"
            return out

    def get_history_detailed(self, part_id: int, include_all_revisions: bool = True) -> List[Dict]:
        """Unified event timeline for a part.

        Includes commits, checkin/checkout logs, releases, and attachment version events.
        If include_all_revisions is True, it also aggregates the same AES number across
        all projects in the current project's version family.
        """

        part = self.bom_repo.get_by_id(part_id)
        if not part:
            return []

        aes = (getattr(part, "aes_number", None) or "").strip()
        project_id = getattr(part, "project_id", None) or self.session.project_id

        family = self._project_family_ids(int(project_id)) if include_all_revisions else []
        family_project_ids = [int(p["id"]) for p in family] if family else [int(project_id)]

        events: List[Dict] = []

        with self._conn() as conn:
            # Map projects for label display
            project_map = {}
            if family_project_ids:
                rows = conn.execute(
                    f"SELECT id, name, COALESCE(version_label,'A') AS version_label FROM projects WHERE id IN ({','.join(['?']*len(family_project_ids))})",
                    tuple(family_project_ids),
                ).fetchall()
                for r in rows:
                    project_map[int(r["id"])] = {
                        "project_name": r["name"],
                        "project_version": (r["version_label"] or "A"),
                    }

            # Resolve part ids across revisions via AES
            part_ids: List[int] = [int(part_id)]
            if include_all_revisions and aes and family_project_ids:
                rows = conn.execute(
                    f"SELECT id, project_id, revision, lifecycle_state, released_by, released_at FROM bom WHERE aes_number = ? AND project_id IN ({','.join(['?']*len(family_project_ids))})",
                    (aes, *family_project_ids),
                ).fetchall()
                part_rows = [dict(r) for r in rows]
                part_ids = sorted({int(r["id"]) for r in part_rows})
            else:
                part_rows = [
                    dict(
                        conn.execute(
                            "SELECT id, project_id, revision, lifecycle_state, released_by, released_at FROM bom WHERE id = ?",
                            (int(part_id),),
                        ).fetchone()
                        or {}
                    )
                ]

            if not part_ids:
                return []

            # Release events from BOM rows
            user_cache: dict[int, str] = {}

            def username_for(uid):
                if uid is None:
                    return ""
                try:
                    uid = int(uid)
                except Exception:
                    return ""
                if uid in user_cache:
                    return user_cache[uid]
                rowu = conn.execute("SELECT username FROM users WHERE id = ?", (uid,)).fetchone()
                user_cache[uid] = (rowu[0] if rowu else "")
                return user_cache[uid]

            for pr in part_rows:
                ts = pr.get("released_at")
                if ts:
                    pid = int(pr.get("project_id") or 0)
                    info = project_map.get(pid, {"project_name": "", "project_version": ""})
                    events.append(
                        {
                            "timestamp": ts,
                            "event": "PART_RELEASED",
                            "user": username_for(pr.get("released_by")),
                            "project": info.get("project_name", ""),
                            "version": info.get("project_version", ""),
                            "details": f"Revision {pr.get('revision') or ''}".strip(),
                        }
                    )

            # Commits affecting this part (across the family via part_ids)
            try:
                rows = conn.execute(
                    f"""
                    SELECT c.*, p.name AS project_name, COALESCE(p.version_label,'A') AS project_version,
                           u1.username AS designer_name, u2.username AS checker_name
                    FROM commits c
                    LEFT JOIN projects p ON p.id = c.project_id
                    LEFT JOIN users u1 ON u1.id = c.designer
                    LEFT JOIN users u2 ON u2.id = c.checked_by
                    WHERE c.part_id IN ({','.join(['?']*len(part_ids))})
                    ORDER BY c.committed_at DESC
                    """,
                    tuple(part_ids),
                ).fetchall()
                for r in rows:
                    d = dict(r)
                    step_status = (d.get("step_diff_status") or "").strip()
                    step_suffix = f" | STEP:{step_status}" if step_status else ""
                    events.append(
                        {
                            "timestamp": d.get("committed_at"),
                            "event": "COMMIT",
                            "user": (d.get("designer_name") or ""),
                            "project": (d.get("project_name") or ""),
                            "version": (d.get("project_version") or "A"),
                            "details": f"{(d.get('status') or '').strip()} | {(d.get('title') or '').strip()} | {(d.get('message') or '').strip()}{step_suffix}".strip(" |"),
                            "commit_id": d.get("commit_id"),
                            "commit_unique_id": d.get("id"),
                            "part_id": d.get("part_id"),
                            "cad_type": d.get("type"),
                            "step_diff_status": d.get("step_diff_status"),
                            "step_file_path": d.get("step_file_path"),
                            "step_prev_file_path": d.get("step_prev_file_path"),
                            "step_diff_path": d.get("step_diff_path"),
                            "step_diff_summary": d.get("step_diff_summary"),
                            "step_error": d.get("step_error"),
                        }
                    )
            except Exception:
                pass

            # Lock logs (checkin/checkout)
            try:
                rows = conn.execute(
                    f"""
                    SELECT ll.action, ll.timestamp, u.username AS username,
                           b.project_id AS project_id,
                           sig.note AS note
                    FROM lock_logs ll
                    LEFT JOIN users u ON u.id = ll.user_id
                    LEFT JOIN signature sig ON sig.id = ll.signature
                    LEFT JOIN bom b ON b.id = ll.part_id
                    WHERE ll.part_id IN ({','.join(['?']*len(part_ids))})
                    ORDER BY ll.timestamp DESC
                    """,
                    tuple(part_ids),
                ).fetchall()
                for r in rows:
                    d = dict(r)
                    pid = int(d.get("project_id") or 0)
                    info = project_map.get(pid, {"project_name": "", "project_version": ""})
                    events.append(
                        {
                            "timestamp": d.get("timestamp"),
                            "event": ("CHECKIN" if str(d.get("action")).lower() == "checkin" else "CHECKOUT"),
                            "user": (d.get("username") or ""),
                            "project": info.get("project_name", ""),
                            "version": info.get("project_version", ""),
                            "details": (d.get("note") or "").strip(),
                        }
                    )
            except Exception:
                pass

            # Attachment versions (created + released)
            try:
                tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
                if "part_files" in tables and "part_file_versions" in tables:
                    rows = conn.execute(
                        f"""
                        SELECT pf.part_id, pf.file_type, pf.display_name,
                               pv.version_no, pv.original_filename, pv.note,
                               pv.created_at, pv.created_by,
                               pv.lifecycle_state, pv.released_at, pv.released_by,
                               b.project_id AS project_id
                        FROM part_files pf
                        JOIN part_file_versions pv ON pv.file_id = pf.id
                        LEFT JOIN bom b ON b.id = pf.part_id
                        WHERE pf.part_id IN ({','.join(['?']*len(part_ids))})
                        ORDER BY pv.created_at DESC
                        """,
                        tuple(part_ids),
                    ).fetchall()
                    for r in rows:
                        d = dict(r)
                        pid = int(d.get("project_id") or 0)
                        info = project_map.get(pid, {"project_name": "", "project_version": ""})

                        events.append(
                            {
                                "timestamp": d.get("created_at"),
                                "event": "ATTACHMENT_VERSION",
                                "user": username_for(d.get("created_by")),
                                "project": info.get("project_name", ""),
                                "version": info.get("project_version", ""),
                                "details": f"{(d.get('file_type') or '').upper()} | {(d.get('display_name') or '').strip()} | v{d.get('version_no')} | {(d.get('original_filename') or '').strip()} | {(d.get('note') or '').strip()}".strip(" |"),
                            }
                        )

                        if str(d.get("lifecycle_state") or "").upper() == "RELEASED" and d.get("released_at"):
                            events.append(
                                {
                                    "timestamp": d.get("released_at"),
                                    "event": "ATTACHMENT_RELEASED",
                                    "user": username_for(d.get("released_by")),
                                    "project": info.get("project_name", ""),
                                    "version": info.get("project_version", ""),
                                    "details": f"{(d.get('file_type') or '').upper()} | {(d.get('display_name') or '').strip()} | v{d.get('version_no')}".strip(" |"),
                                }
                            )
            except Exception:
                pass

        # Sort by parsed datetime when possible; fallback to raw string
        def _sort_key(ev):
            dt = self._parse_dt(ev.get("timestamp"))
            return (dt is None, dt or datetime.min, str(ev.get("timestamp") or ""))

        events.sort(key=_sort_key, reverse=True)
        return events

    def get_history_analytics(self, part_id: int, include_all_revisions: bool = True) -> Dict:
        """Small analytics summary for the part history tab."""
        events = self.get_history_detailed(part_id, include_all_revisions=include_all_revisions)
        out = {
            "events_total": len(events),
            "commits": 0,
            "checkins": 0,
            "checkouts": 0,
            "attachment_versions": 0,
            "attachment_releases": 0,
            "part_releases": 0,
            "unique_users": 0,
            "last_activity": "",
        }
        users = set()
        last_ts = ""
        last_dt = None
        for ev in events:
            et = (ev.get("event") or "").upper()
            if et == "COMMIT":
                out["commits"] += 1
            elif et == "CHECKIN":
                out["checkins"] += 1
            elif et == "CHECKOUT":
                out["checkouts"] += 1
            elif et == "ATTACHMENT_VERSION":
                out["attachment_versions"] += 1
            elif et == "ATTACHMENT_RELEASED":
                out["attachment_releases"] += 1
            elif et == "PART_RELEASED":
                out["part_releases"] += 1

            u = (ev.get("user") or "").strip()
            if u:
                users.add(u)

            dt = self._parse_dt(ev.get("timestamp"))
            if dt and (last_dt is None or dt > last_dt):
                last_dt = dt
                last_ts = str(ev.get("timestamp") or "")

        out["unique_users"] = len(users)
        out["last_activity"] = last_ts
        return out

    # -------------------------------
    # SEARCH PARTS
    # -------------------------------
    def search_parts(self, query: str) -> List[Dict]:
        """
        Search parts by AES number, name, or part number
        """
        all_parts = self.bom_repo.search_project(self.session.project_id, query)
        try:
            lock_owner = self.lock_repo.get_lock_owners_for_project(self.session.project_id) if self.session.project_id else {}
        except Exception:
            lock_owner = {}
        results = []
        category_map = self.bom_repo.get_categories_for_boms(part.id for part in all_parts)
        for part in all_parts:
            d = part.__dict__.copy()
            d["category_names"] = list(category_map.get(int(part.id), []))
            if d.get("locked"):
                d["locked_by_username"] = lock_owner.get(int(d.get("id")))
            results.append(d)

        return results

    # -------------------------------
    # PROJECT CATEGORIES
    # -------------------------------
    def list_categories(self) -> List[Dict]:
        if not self.session.project_id:
            return []
        return self.bom_repo.list_categories(int(self.session.project_id))

    def categories_for_part(self, part_id: int) -> List[str]:
        return self.bom_repo.get_categories_for_bom(int(part_id))

    def set_part_categories(self, part_id: int, category_names) -> List[str]:
        if not self.session.project_id:
            raise ValueError("Select a project before assigning categories.")
        categories = self.bom_repo.set_categories_for_bom(
            int(part_id),
            int(self.session.project_id),
            category_names,
            assigned_by=self.user_id,
        )
        self._tree_dirty.add(int(self.session.project_id))
        return categories

    def category_usage(self, category_id: int) -> Dict:
        if not self.session.project_id:
            raise ValueError("Select a project before managing categories.")
        return self.bom_repo.get_category_usage(int(self.session.project_id), int(category_id))

    def delete_category(self, category_id: int) -> Dict:
        if not self.session.project_id:
            raise ValueError("Select a project before managing categories.")
        result = self.bom_repo.delete_category(int(self.session.project_id), int(category_id))
        self._tree_dirty.add(int(self.session.project_id))
        return result

    def search_relation_parents(self, query: str = "") -> List[Dict]:
        if not self.session.project_id:
            return []
        return [
            part.__dict__.copy()
            for part in self.bom_repo.search_project(int(self.session.project_id), query, limit=501)
            if str(getattr(part, "type", "") or "").strip().lower() in {"asm", "assembly"}
        ]

    def search_relation_occurrences(self, query: str = "") -> List[Dict]:
        if not self.session.project_id:
            return []
        return self.children_repo.search_occurrences(int(self.session.project_id), query, limit=501)

    def relation_sources_for_part(self, child_id: int) -> List[Dict]:
        """Return direct source occurrences for resolving a flat search result."""
        if not self.session.project_id:
            return []
        sources = []
        for relation in self.children_repo.get_parents(int(child_id)) or []:
            parent = self.bom_repo.get_by_id(int(relation.parent_id))
            if not parent or int(parent.project_id or 0) != int(self.session.project_id):
                continue
            sources.append({
                "parent_id": int(parent.id),
                "parent_name": str(parent.name or ""),
                "parent_aes_number": str(parent.aes_number or ""),
                "quantity": max(1, int(relation.quantity or 1)),
            })
        return sources

    def apply_child_relation_operation(self, target_parent_id: int, selections, mode: str) -> Dict:
        """Copy or move one or more direct occurrences under an assembly."""
        if not self.session.project_id:
            raise ValueError("Select a project before changing the BOM structure.")
        target = self.bom_repo.get_by_id(int(target_parent_id))
        if not target or int(target.project_id or 0) != int(self.session.project_id):
            raise ValueError("The target parent was not found in the current project.")
        if str(target.type or "").strip().lower() not in {"asm", "assembly"}:
            raise ValueError("Only an assembly can contain child items.")

        normalized = []
        for selection in selections or []:
            child_id = int(selection.get("child_id"))
            source_value = selection.get("source_parent_id")
            source_parent_id = int(source_value) if source_value is not None else None
            child = self.bom_repo.get_by_id(child_id)
            if not child or int(child.project_id or 0) != int(self.session.project_id):
                raise ValueError(f"Child item {child_id} was not found in the current project.")
            if child_id == int(target_parent_id):
                raise ValueError("An assembly cannot be a child of itself.")
            normalized.append({"child_id": child_id, "source_parent_id": source_parent_id})
        if not normalized:
            raise ValueError("Select at least one child occurrence.")
        action = str(mode or "").strip().lower()
        if action == "copy" and any(row["source_parent_id"] is None for row in normalized):
            raise ValueError(
                "A top-level component cannot be copied because top-level membership is derived from having no parent. "
                "Use Move to place it under the target assembly."
            )

        children_by_parent = defaultdict(list)
        for row in self.children_repo.get_structure_rows(int(self.session.project_id)):
            children_by_parent[int(row["parent_id"])].append(int(row["child_id"]))

        def contains_descendant(start_id: int, wanted_id: int) -> bool:
            pending = list(children_by_parent.get(int(start_id), []))
            visited = set()
            while pending:
                current = int(pending.pop())
                if current == int(wanted_id):
                    return True
                if current in visited:
                    continue
                visited.add(current)
                pending.extend(children_by_parent.get(current, []))
            return False

        for selection in normalized:
            if contains_descendant(int(selection["child_id"]), int(target_parent_id)):
                child = self.bom_repo.get_by_id(int(selection["child_id"]))
                name = str(getattr(child, "name", "") or selection["child_id"])
                raise ValueError(f"Cannot place {name} here because it would create a circular BOM structure.")

        result = self.children_repo.apply_child_relations(
            int(target_parent_id), normalized, action
        )
        if action == "move":
            for selection in normalized:
                source_parent_id = selection.get("source_parent_id")
                if source_parent_id is None or int(source_parent_id) == int(target_parent_id):
                    continue
                try:
                    self.folder_repo.unassign_from_context(
                        int(self.session.project_id),
                        int(source_parent_id),
                        int(selection["child_id"]),
                    )
                except Exception:
                    pass
        self._tree_dirty.add(int(self.session.project_id))
        return result

    # -------------------------------
    # GET BOM TREE
    # -------------------------------
    @staticmethod
    def _path_key(path) -> str:
        if isinstance(path, str):
            return path
        return "/".join(str(int(value)) for value in (path or ()))

    def _build_lazy_index(self, project_id: int) -> Dict:
        """Build a small ID-only index used by lazy rows and permanent numbering."""
        pid = int(project_id)
        if pid not in self._tree_dirty and pid in self._lazy_index_cache:
            return self._lazy_index_cache[pid]

        part_ids = self.bom_repo.get_project_ids(pid)
        part_set = set(part_ids)
        children = defaultdict(list)
        all_children = set()
        for row in self.children_repo.get_structure_rows(pid):
            parent_id = int(row["parent_id"])
            child_id = int(row["child_id"])
            if parent_id in part_set and child_id in part_set:
                children[parent_id].append(child_id)
                all_children.add(child_id)
        roots = [part_id for part_id in part_ids if part_id not in all_children]

        try:
            folders = self.folder_repo.list_for_project(pid)
        except Exception:
            folders = []
        folders_by_context = defaultdict(list)
        for folder in folders:
            folders_by_context[folder.get("effective_parent_bom_id")].append(folder)

        row_number = 0
        path_rows = {}
        part_rows = defaultdict(list)
        folder_rows = {}
        folder_path_rows = {}
        visited_paths = set()

        def walk_item(part_id: int, parent_path: tuple):
            nonlocal row_number
            path = parent_path + (int(part_id),)
            if path in visited_paths:
                return
            visited_paths.add(path)
            row_number += 1
            path_rows[self._path_key(path)] = row_number
            part_rows[int(part_id)].append(row_number)
            if int(part_id) in parent_path:
                return
            walk_context(int(part_id), children.get(int(part_id), []), path)

        def walk_context(parent_id, item_ids, parent_path: tuple):
            context_folders = folders_by_context.get(parent_id, [])
            by_parent_folder = defaultdict(list)
            roots_for_context = []
            assigned = set()
            for folder in context_folders:
                assigned.update(int(value) for value in (folder.get("item_ids") or []))
                parent_folder_id = folder.get("parent_folder_id")
                if parent_folder_id is None:
                    roots_for_context.append(folder)
                else:
                    by_parent_folder[int(parent_folder_id)].append(folder)
            sort_key = lambda value: (int(value.get("sort_order") or 0), int(value["id"]))
            roots_for_context.sort(key=sort_key)
            for values in by_parent_folder.values():
                values.sort(key=sort_key)

            for item_id in item_ids:
                if int(item_id) not in assigned:
                    walk_item(int(item_id), parent_path)

            def walk_folder(folder, folder_parent_path: str):
                nonlocal row_number
                row_number += 1
                folder_rows[int(folder["id"])] = row_number
                folder_path = f"{folder_parent_path}/f{int(folder['id'])}" if folder_parent_path else f"f{int(folder['id'])}"
                folder_path_rows[folder_path] = row_number
                allowed = set(int(value) for value in item_ids)
                for item_id in folder.get("item_ids") or []:
                    if int(item_id) in allowed:
                        walk_item(int(item_id), parent_path)
                for child_folder in by_parent_folder.get(int(folder["id"]), []):
                    walk_folder(child_folder, folder_path)

            for folder in roots_for_context:
                walk_folder(folder, self._path_key(parent_path))

        walk_context(None, roots, ())
        # Corrupt/circular legacy structures may have no natural root. Keep them locatable.
        indexed_parts = set(part_rows)
        for part_id in part_ids:
            if part_id not in indexed_parts:
                roots.append(part_id)
                walk_item(part_id, ())

        index = {
            "project_id": pid,
            "roots": roots,
            "children": dict(children),
            "path_rows": path_rows,
            "part_rows": {key: list(value) for key, value in part_rows.items()},
            "folder_rows": folder_rows,
            "folder_path_rows": folder_path_rows,
            "folders": folders,
        }
        self._lazy_index_cache[pid] = index
        self._tree_cache.pop(pid, None)
        self._tree_dirty.discard(pid)
        return index

    def _lazy_level_nodes(self, project_id: int, ids, parent_path=()) -> List[Dict]:
        pid = int(project_id)
        index = self._build_lazy_index(pid)
        path_prefix = tuple(int(value) for value in (parent_path or ()))
        parts = self.bom_repo.get_many(pid, ids)
        categories = self.bom_repo.get_categories_for_boms(part.id for part in parts)
        try:
            lock_owner = self.lock_repo.get_lock_owners_for_project(pid)
        except Exception:
            lock_owner = {}
        nodes = []
        for part in parts:
            node = part.__dict__.copy()
            path = path_prefix + (int(part.id),)
            node["children"] = []
            node["category_names"] = list(categories.get(int(part.id), []))
            node["_tree_path"] = self._path_key(path)
            node["_tree_row_number"] = index["path_rows"].get(node["_tree_path"], "")
            node["_has_children"] = bool(index["children"].get(int(part.id))) and int(part.id) not in path_prefix
            node["_defer_indicators"] = True
            if node.get("locked"):
                node["locked_by_username"] = lock_owner.get(int(part.id))
            nodes.append(node)
        return nodes

    def get_bom_lazy_tree(self, project_id: int) -> Dict:
        index = self._build_lazy_index(int(project_id))
        roots = self._lazy_level_nodes(int(project_id), index["roots"], ())
        return {
            "roots": roots,
            "row_numbers": index["part_rows"],
            "folder_rows": index["folder_rows"],
            "folder_path_rows": index["folder_path_rows"],
            "folders": index["folders"],
        }

    def get_bom_lazy_children(self, project_id: int, parent_id: int, parent_path=()) -> List[Dict]:
        index = self._build_lazy_index(int(project_id))
        child_ids = index["children"].get(int(parent_id), [])
        if isinstance(parent_path, str):
            parent_path = tuple(int(value) for value in parent_path.split("/") if value)
        return self._lazy_level_nodes(int(project_id), child_ids, parent_path)

    def get_bom_lazy_numbering(self, project_id: int) -> Dict:
        index = self._build_lazy_index(int(project_id))
        return {
            "row_numbers": index["part_rows"],
            "path_rows": index["path_rows"],
            "folder_rows": index["folder_rows"],
            "folder_path_rows": index["folder_path_rows"],
            "folders": index["folders"],
            "has_children": {part_id for part_id, values in index["children"].items() if values},
        }

    def get_bom_tree(self, project_id) -> Dict:
        """
        Returns a nested dict representing the BOM tree.
        Built by part ID instead of AES number.
        """
        pid = int(project_id)
        if pid not in self._tree_dirty and pid in self._tree_cache:
            return self._tree_cache[pid]
        self._lazy_index_cache.pop(pid, None)

        # Get all parts for the project
        all_parts = {b.id: b for b in self.bom_repo.get_all(project_id)}
        category_map = self.bom_repo.get_categories_for_boms(all_parts.keys())
        try:
            lock_owner = self.lock_repo.get_lock_owners_for_project(int(project_id))
        except Exception:
            lock_owner = {}
        children_map = {}

        # Get relationships filtered to this project (avoids cross-project full-table scan)
        all_relations = self.children_repo.get_all_for_project(project_id)

        for rel in all_relations:
            parent_id = rel.parent_id if hasattr(rel, 'parent_id') else rel[0]
            child_id = rel.child_id if hasattr(rel, 'child_id') else rel[1]

            if parent_id not in all_parts or child_id not in all_parts:
                continue

            children_map.setdefault(parent_id, []).append(child_id)

        # Recursive builder
        def build_tree(part_id):
            if part_id not in all_parts:
                return None

            part = all_parts[part_id]
            node = part.__dict__.copy()
            node["categories"] = list(category_map.get(int(part_id), []))
            node["children"] = []

            if node.get("locked"):
                try:
                    node["locked_by_username"] = lock_owner.get(int(part_id))
                except Exception:
                    node["locked_by_username"] = None

            if part_id in children_map:
                for child_id in children_map[part_id]:
                    child_node = build_tree(child_id)
                    if child_node:
                        node["children"].append(child_node)

            return node

        # Roots: parts that are not children of anyone
        all_children = {c for cs in children_map.values() for c in cs}
        roots = [p for p in all_parts.keys() if p not in all_children]

        result = {r: build_tree(r) for r in roots}
        self._tree_cache[pid] = result
        self._tree_dirty.discard(pid)
        return result


    # -------------------------------
    # GET PART DETAILS
    # -------------------------------
    def get_part_details(self, part_id: int) -> Dict:
        part = self.bom_repo.get_by_id(part_id)
        if not part:
            return {}

        d = part.__dict__.copy()
        category_names = self.bom_repo.get_categories_for_bom(int(part_id))
        d["category_names"] = list(category_names)
        d["categories"] = ", ".join(category_names)
        try:
            if d.get("locked") and self.session.project_id:
                lock_owner = self.lock_repo.get_lock_owners_for_project(int(self.session.project_id))
                d["locked_by_username"] = lock_owner.get(int(part_id))
        except Exception:
            pass
        return d

    # -------------------------------
    # PLM-lite: Revision / Release
    # -------------------------------
    def set_revision(self, part_id: int, revision: str):
        self.bom_repo.set_revision(part_id, revision)

    def release_part(self, part_id: int):
        from core.services.issue_service import IssueService
        IssueService().assert_no_critical_issues([int(part_id)], operation="release", include_children=True)
        self.bom_repo.release_part(part_id, released_by=self.user_id)

    # -------------------------------
    # REMOVE CHILD RELATIONSHIP
    # -------------------------------
    def remove_child_by_id(self, parent_id: int, child_id: int) -> bool:
        self.remove_children_from_parent(int(parent_id), [int(child_id)])
        return True

    def remove_children_from_parent(self, parent_id: int, child_ids) -> Dict:
        """Remove direct relations; last occurrences become top-level BOM items."""
        if not self.session.project_id:
            raise ValueError("Select a project before changing the BOM structure.")
        result = self.children_repo.remove_children_from_parent(
            int(self.session.project_id), int(parent_id), child_ids
        )
        for child_id in result.get("removed_child_ids") or []:
            try:
                self.folder_repo.unassign_from_context(
                    int(self.session.project_id), int(parent_id), int(child_id)
                )
            except Exception:
                pass
        moved_items = []
        for child_id in result.get("moved_to_root_ids") or []:
            child = self.bom_repo.get_by_id(int(child_id))
            if child:
                moved_items.append({
                    "id": int(child.id),
                    "name": str(child.name or ""),
                    "aes_number": str(child.aes_number or ""),
                })
        result["moved_to_root_items"] = moved_items
        self._tree_dirty.add(int(self.session.project_id))
        return result

    # alias
    def remove_child(self, parent_id: int, child_id: int) -> bool:
        return self.remove_child_by_id(parent_id, child_id)

    def reorder_children(self, parent_id: int, ordered_child_ids: list[int]) -> bool:
        parent = self.bom_repo.get_by_id(int(parent_id))
        if not parent:
            raise ValueError("Parent not found")
        if str(getattr(parent, "type", "") or "").lower() != "asm":
            raise ValueError("Only assembly children can be reordered.")
        current = self.children_repo.ordered_child_ids(int(parent_id))
        if set(map(int, current)) != set(map(int, ordered_child_ids or [])):
            raise ValueError("Reorder must keep the same child associations.")
        result = self.children_repo.set_child_order(int(parent_id), [int(x) for x in ordered_child_ids])
        self._tree_dirty.add(int(self.session.project_id))
        return result

    def ordered_child_ids(self, parent_id: int) -> List[int]:
        return self.children_repo.ordered_child_ids(int(parent_id))

    def get_structure_context(self, part_id: int) -> Dict:
        """Return recursive Uses and direct Where Used relations for one BOM item."""
        selected = self.bom_repo.get_by_id(int(part_id))
        if not selected:
            return {"uses": None, "where_used": None, "uses_count": 0, "where_used_count": 0}

        project_id = int(getattr(selected, "project_id", None) or self.session.project_id)
        parts = {int(part.id): part for part in self.bom_repo.get_all(project_id)}
        relations = self.children_repo.get_all_for_project(project_id)
        children_map = {}
        parents_map = {}
        for relation in relations:
            parent_id = int(relation.parent_id)
            child_id = int(relation.child_id)
            quantity = int(getattr(relation, "quantity", 1) or 1)
            sort_order = int(getattr(relation, "sort_order", 0) or 0)
            children_map.setdefault(parent_id, []).append((sort_order, child_id, quantity))
            parents_map.setdefault(child_id, []).append((parent_id, quantity))

        for values in children_map.values():
            values.sort(key=lambda row: (row[0], row[1]))
        for values in parents_map.values():
            values.sort(
                key=lambda row: (
                    str(getattr(parts.get(row[0]), "aes_number", "") or "").casefold(),
                    str(getattr(parts.get(row[0]), "name", "") or "").casefold(),
                    row[0],
                )
            )

        def part_node(node_id: int, relation_label: str, quantity=None, cycle=False) -> Dict:
            part = parts.get(int(node_id))
            if not part:
                return {}
            node = part.__dict__.copy()
            node.update({
                "relation": relation_label,
                "quantity": quantity,
                "cycle": bool(cycle),
                "children": [],
            })
            return node

        def build_uses(node_id: int, path: set) -> Dict:
            node = part_node(node_id, "Selected Item" if not path else "Uses")
            if not node:
                return {}
            next_path = set(path)
            next_path.add(int(node_id))
            for _order, child_id, quantity in children_map.get(int(node_id), []):
                if child_id in next_path:
                    child = part_node(child_id, "Cycle", quantity, cycle=True)
                else:
                    child = build_uses(child_id, next_path)
                    if child:
                        child["relation"] = "Uses"
                        child["quantity"] = quantity
                if child:
                    node["children"].append(child)
            return node

        def build_where_used(node_id: int) -> Dict:
            node = part_node(node_id, "Selected Item")
            if not node:
                return {}
            for parent_id, quantity in parents_map.get(int(node_id), []):
                parent = part_node(parent_id, "Used By", quantity)
                if parent:
                    node["children"].append(parent)
            return node

        def descendant_count(node: Dict) -> int:
            children = list((node or {}).get("children") or [])
            return len(children) + sum(descendant_count(child) for child in children)

        uses = build_uses(int(part_id), set())
        where_used = build_where_used(int(part_id))
        return {
            "uses": uses,
            "where_used": where_used,
            "uses_count": descendant_count(uses),
            "where_used_count": descendant_count(where_used),
        }

    # -------------------------------
    # EXPORT BOM
    # -------------------------------
    def export_bom(self, file_path: str) -> bool:
        """
        Export BOM to a CSV file
        """
        try:
            import csv
            
            with open(file_path, 'w', newline='', encoding='utf-8') as csvfile:
                fieldnames = ['aes_number', 'name', 'type', 'part_number', 'drawing_number', 
                             'material', 'weight', 'status', 'notes']
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                
                writer.writeheader()
                for part in self.bom_repo.get_all():
                    if "_" not in (part.aes_number or ""):
                        writer.writerow({
                            'aes_number': part.aes_number or '',
                            'name': part.name or '',
                            'type': part.type or '',
                            'part_number': part.part_number or '',
                            'drawing_number': part.drawing_number or '',
                            'material': part.material or '',
                            'weight': part.weight or '',
                            'status': part.status or '',
                            'notes': part.notes or ''
                        })
            
            return True
        except Exception as e:
            print(f"Error exporting BOM: {e}")
            return False
