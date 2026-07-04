from typing import List, Dict
import os
import sqlite3
from datetime import datetime
from core.models.bom_model import Bom
from core.repositories.bom_repository import BomRepository
from core.repositories.bom_children_repository import BomChildrenRepository
from core.repositories.lock_repository import LockRepository
from core.repositories.signature_repository import SignatureRepository
from core.repositories.permission_repository import PermissionRepository
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
        self.session = SessionManager()
        self._tree_cache: dict = {}    # project_id -> tree dict
        self._tree_dirty: set = set()  # project_ids that need re-fetch
        print(self.user_id)

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
    # CHECKIN PART
    # -------------------------------
    def checkin_part(self, part_id: str, as_user_id: int | None = None):
        part = self.bom_repo.get_by_id(part_id)
        if not part:
            raise ValueError("Part not found")
        locked = self.lock_repo.get_by_part(part.id)
        if not locked:
            raise ValueError("Part is not checked out.")
        actor_user_id = int(self.session.user_id) if self.session.user_id is not None else None
        if not actor_user_id:
            raise PermissionError("You must be logged in")

        if locked.user_id != actor_user_id:
            if not self.permission_repo.user_has_permission(actor_user_id, "merge", self.session.project_id):
                raise ValueError("Part is checked out by another user.")
            effective_user_id = int(as_user_id) if as_user_id is not None else int(actor_user_id)
        else:
            effective_user_id = int(actor_user_id)

        signature = self.signature_repo.add_signature(
            "checkin",
            effective_user_id,
            note="Checked in part (admin override)" if locked.user_id != actor_user_id else "Checked in part",
        )
        success = self.lock_repo.checkin(part.id, effective_user_id, signature)
        if not success:
            raise ValueError("Failed to check in part")
        self.bom_repo.checkin_bom(part.id)
        self._tree_dirty.add(int(self.session.project_id))
        return True

    def checkout_part(self, part_id: str, as_user_id: int | None = None):
        part = self.bom_repo.get_by_id(part_id)
        if not part:
            raise ValueError("Part not found")
        existing = self.lock_repo.get_by_part(part.id)
        if existing:
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

        signature = self.signature_repo.add_signature("checkout", effective_user_id, note="Checked out part")
        success = self.lock_repo.checkout(part.id, effective_user_id, signature)
        if not success:
            raise ValueError("Failed to check out part")
        self.bom_repo.checkout_bom(part.id)
        self._tree_dirty.add(int(self.session.project_id))
        return True

    def checkin_by_part_id(self, part_id: int, user_id: int):
        locked = self.lock_repo.get_by_part(part_id)
        if not locked:
            raise ValueError("Part is not checked out.")

        signature = self.signature_repo.add_signature("checkin", user_id, note="Checked in part")
        success = self.lock_repo.checkin(part_id, user_id, signature)
        if not success:
            raise ValueError("Failed to check in part")
        self.bom_repo.checkin_bom(part_id)
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
        all_parts = self.bom_repo.get_all(self.session.project_id)
        try:
            lock_owner = self.lock_repo.get_lock_owners_for_project(self.session.project_id) if self.session.project_id else {}
        except Exception:
            lock_owner = {}
        results = []
        query_lower = query.lower()
        
        for part in all_parts:
            if (part.aes_number and query_lower in part.aes_number.lower()) or \
               (part.name and query_lower in part.name.lower()) or \
               (part.part_number and query_lower in part.part_number.lower()):
                d = part.__dict__.copy()
                if d.get("locked"):
                    try:
                        d["locked_by_username"] = lock_owner.get(int(d.get("id")))
                    except Exception:
                        d["locked_by_username"] = None
                results.append(d)
                
        return results

    # -------------------------------
    # GET BOM TREE
    # -------------------------------
    def get_bom_tree(self, project_id) -> Dict:
        """
        Returns a nested dict representing the BOM tree.
        Built by part ID instead of AES number.
        """
        pid = int(project_id)
        if pid not in self._tree_dirty and pid in self._tree_cache:
            return self._tree_cache[pid]

        # Get all parts for the project
        all_parts = {b.id: b for b in self.bom_repo.get_all(project_id)}
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
        """
        Remove a child relation (parent_id, child_id).

        Prevent removal if this would make the child disappear from the current
        project's BOM (i.e. it's the last occurrence of that child under any
        parent in this project).
        """
        # validate parts
        parent = self.bom_repo.get_by_id(parent_id)
        child = self.bom_repo.get_by_id(child_id)
        if not parent or not child:
            raise ValueError("Parent or child not found")

        # Count occurrences of this child under parents that belong to the same project
        try:
            parents = self.children_repo.get_parents(child_id) or []
        except Exception:
            parents = []

        occ = 0
        for rel in parents:
            try:
                pid = getattr(rel, "parent_id", None) or rel[0]
            except Exception:
                pid = None
            if not pid:
                continue
            p = self.bom_repo.get_by_id(pid)
            if not p:
                continue
            # consider only parents in current project
            if int(p.project_id or 0) == int(self.session.project_id):
                occ += 1

        # If there's only one occurrence (or none), prevent removal to avoid orphaning the part
        if occ <= 1:
            raise ValueError("Cannot remove this child because it would be the last occurrence in the BOM for this project.")

        # Proceed to delete the relation
        try:
            self.children_repo.delete(int(parent_id), int(child_id))
            #self._tree_dirty.add(int(self.session.project_id))
            return True
        except Exception as e:
            raise

    # alias
    def remove_child(self, parent_id: int, child_id: int) -> bool:
        return self.remove_child_by_id(parent_id, child_id)

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
