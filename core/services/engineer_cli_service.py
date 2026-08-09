import os
import re
import inspect
import json
import shlex
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime

from config import DB_NAME
from core.repositories.bom_children_repository import BomChildrenRepository
from core.repositories.bom_repository import BomRepository
from core.repositories.lock_repository import LockRepository
from core.repositories.permission_repository import PermissionRepository
from core.repositories.signature_repository import SignatureRepository
from core.repositories.user_repository import UserRepository
from core.services.bom_service import BomService
from core.services.diag_service import DiagService
from core.services.package_export_service import PackageExportService
from core.services.pdm_service import PdmService
from core.services.project_service import ProjectService
from core.services.snapshot_service import SnapshotService
from core.services.baseline_service import BaselineService
from core.services.dashboard_service import DashboardService
from core.session_manager import SessionManager


class EngineerCliService:
    """Controlled in-app operator CLI for Nexus PDM actions.

    This is intentionally not a Python shell, SQL console, or OS terminal.
    Commands are routed through Nexus services so a human engineer or a local
    AI operator can perform repeatable PDM work without bypassing checkout,
    lifecycle, association, and package rules.
    """

    def __init__(self):
        self.session = SessionManager()
        self.user_repo = UserRepository()
        self.permission_repo = PermissionRepository()
        self.project_service = ProjectService()
        self._bom_service = None
        self._pdm_service = None
        self._diag_service = None
        self._snapshot_service = None
        self._baseline_service = None
        self._dashboard_service = None
        self.package_export_service = PackageExportService()
        self._pending_clarification: dict | None = None
        self.ollama_host = os.environ.get("NEXUS_OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
        self.ollama_model = os.environ.get("NEXUS_OLLAMA_MODEL", "llama3.1")

    @property
    def bom_service(self) -> BomService:
        if self._bom_service is None:
            self._bom_service = BomService(
                BomRepository(),
                BomChildrenRepository(),
                LockRepository(),
                SignatureRepository(),
            )
        return self._bom_service

    @property
    def pdm_service(self) -> PdmService:
        if self._pdm_service is None:
            self._pdm_service = PdmService()
        return self._pdm_service

    @property
    def diag_service(self) -> DiagService:
        if self._diag_service is None:
            self._diag_service = DiagService()
        return self._diag_service

    @property
    def snapshot_service(self) -> SnapshotService:
        if self._snapshot_service is None:
            self._snapshot_service = SnapshotService()
        return self._snapshot_service

    @property
    def baseline_service(self) -> BaselineService:
        if self._baseline_service is None:
            self._baseline_service = BaselineService()
        return self._baseline_service

    @property
    def dashboard_service(self) -> DashboardService:
        if self._dashboard_service is None:
            self._dashboard_service = DashboardService()
        return self._dashboard_service

    def is_available_for_current_user(self) -> bool:
        user_id = getattr(self.session, "user_id", None)
        if not user_id:
            return False
        if self._is_admin_user():
            return True
        return self.user_repo.is_cli_enabled(int(user_id))

    def execute(self, command_line: str) -> str:
        command_line = str(command_line or "").strip()
        if not command_line:
            return ""
        if not self.is_available_for_current_user():
            raise PermissionError("The Engineer CLI is disabled for this user.")

        pending_response = self._try_resume_pending(command_line)
        if pending_response is not None:
            return pending_response

        tokens = [self._strip_quotes(token) for token in shlex.split(command_line, posix=False)]
        if not tokens:
            return ""
        verb = tokens[0].lower()
        args = tokens[1:]

        if verb in {"help", "?"}:
            return self._help()
        if verb in {"ollama", "olama", "llm"}:
            return self._ollama(args)
        if verb in {"db", "database"}:
            return self._db(args)
        if verb == "sql":
            return self._sql(args)
        if verb in {"service", "svc"}:
            return self._service(args)
        if verb in {"context", "ctx"}:
            return self._context()
        if verb == "find":
            return self._find(args)
        if verb == "show":
            return self._show(args)
        if verb in {"diag", "diagnose", "diagnostic"}:
            return self._diag(args)
        if verb in {"assist", "ai"}:
            return self._natural_language(" ".join(args), mode="assist")
        if verb in {"ask", "advisor"}:
            return self._natural_language(" ".join(args), mode="ask")
        if verb in {"act", "agent", "do"}:
            return self._natural_language(" ".join(args), mode="act")
        if verb == "bulk":
            return self._bulk(args)
        if verb == "item":
            return self._item(args)
        if verb in {"associate", "assoc"}:
            return self._associate(args)
        if verb in {"unassociate", "unassoc"}:
            return self._unassociate(args)
        if verb == "drawing":
            return self._drawing(args)
        if verb == "build":
            return self._build(args)
        if verb == "auto-associate":
            return self._auto_associate(args)
        if verb == "fix":
            return self._fix(args)
        if verb == "script":
            return self._script(args)
        if verb == "checkout":
            return self._checkout(args)
        if verb == "checkin":
            return self._checkin(args)
        if verb == "undo":
            return self._undo(args)
        if verb == "export":
            return self._export(args)
        return self._natural_language(command_line, mode="auto")

    def _project_id(self) -> int:
        project_id = getattr(self.session, "project_id", None)
        if not project_id:
            raise ValueError("Select a product/version before using the CLI.")
        return int(project_id)

    def _conn(self):
        conn = sqlite3.connect(DB_NAME)
        conn.row_factory = sqlite3.Row
        return conn

    def _db(self, args: list[str]) -> str:
        if not args:
            raise ValueError("Usage: db tables | db schema <table> | db describe <table> | db count <table>")
        action = args[0].lower()
        with self._conn() as conn:
            if action == "tables":
                rows = [
                    dict(r) for r in conn.execute(
                        """
                        SELECT name,type
                        FROM sqlite_master
                        WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%'
                        ORDER BY type,name
                        """
                    ).fetchall()
                ]
                for row in rows:
                    try:
                        row["rows"] = self._scalar(conn, f"SELECT COUNT(*) FROM {self._quote_ident(row['name'])}")
                    except Exception:
                        row["rows"] = ""
                return self._format_rows(rows, ["type", "name", "rows"])
            if action in {"schema", "describe", "desc"}:
                if len(args) < 2:
                    raise ValueError("Usage: db schema <table>")
                table = self._safe_table_name(args[1])
                if not self._table_exists(conn, table):
                    raise ValueError(f"Table not found: {table}")
                cols = [
                    {
                        "cid": r[0],
                        "name": r[1],
                        "type": r[2],
                        "notnull": r[3],
                        "default": r[4],
                        "pk": r[5],
                    }
                    for r in conn.execute(f"PRAGMA table_info({self._quote_ident(table)})").fetchall()
                ]
                indexes = [
                    {"index": r[1], "unique": r[2]}
                    for r in conn.execute(f"PRAGMA index_list({self._quote_ident(table)})").fetchall()
                ]
                return "\n".join([
                    f"Schema: {table}",
                    self._format_rows(cols, ["cid", "name", "type", "notnull", "default", "pk"]),
                    "",
                    "Indexes:",
                    self._format_rows(indexes, ["index", "unique"]) if indexes else "  none",
                ])
            if action == "count":
                if len(args) < 2:
                    raise ValueError("Usage: db count <table>")
                table = self._safe_table_name(args[1])
                return f"{table}: {self._scalar(conn, f'SELECT COUNT(*) FROM {self._quote_ident(table)}')} row(s)"
        raise ValueError("Usage: db tables | db schema <table> | db describe <table> | db count <table>")

    def _sql(self, args: list[str]) -> str:
        positional, options = self._parse_options(args)
        sql = " ".join(positional).strip()
        if not sql:
            raise ValueError('Usage: sql "SELECT * FROM bom LIMIT 20"')
        statements = [s.strip() for s in sql.split(";") if s.strip()]
        if len(statements) != 1:
            raise ValueError("SQL CLI accepts one statement at a time.")
        statement = statements[0]
        kind = statement.split(None, 1)[0].lower() if statement.split() else ""
        readonly = kind in {"select", "pragma", "with", "explain"}
        if not readonly:
            if not (options.get("apply") and options.get("confirm")):
                return "\n".join([
                    "SQL write blocked.",
                    "To execute a data-changing statement, rerun with:",
                    f"  sql --apply --confirm \"{statement}\"",
                ])
            self._require_admin("execute SQL write statements")
        with self._conn() as conn:
            cur = conn.execute(statement)
            if readonly:
                rows = [dict(r) for r in cur.fetchmany(200)]
                if not rows:
                    return "Query returned no rows."
                columns = list(rows[0].keys())
                return self._format_rows(rows, columns)
            return f"SQL executed. Rows affected: {cur.rowcount}"

    def _ollama(self, args: list[str]) -> str:
        action = (args[0].lower() if args else "status")
        if action == "status":
            ok, detail = self._ollama_status()
            return "\n".join([
                "Ollama integration",
                f"  Host: {self.ollama_host}",
                f"  Model: {self.ollama_model}",
                f"  Status: {'available' if ok else 'unavailable'}",
                f"  Detail: {detail}",
            ])
        if action == "models":
            models = self._ollama_models()
            if not models:
                return "No Ollama models found or Ollama is not reachable."
            return self._format_rows(models, ["name", "modified_at", "size"])
        if action == "model":
            if len(args) < 2:
                return f"Current Ollama model: {self.ollama_model}"
            self.ollama_model = str(args[1]).strip()
            return (
                f"Ollama model for this CLI session set to: {self.ollama_model}\n"
                "To make it permanent, set environment variable NEXUS_OLLAMA_MODEL."
            )
        raise ValueError("Usage: ollama status | ollama models | ollama model <name>")

    def _service(self, args: list[str]) -> str:
        if not args:
            raise ValueError("Usage: service list | service help <service>[.<function>] | service call <service.function>")
        action = args[0].lower()
        registry = self._service_registry()
        if action == "list":
            rows = []
            for name, service in registry.items():
                methods = self._service_methods(service)
                rows.append({
                    "service": name,
                    "class": service.__class__.__name__,
                    "functions": len(methods),
                })
            return "\n".join([
                "Available backend services:",
                self._format_rows(rows, ["service", "class", "functions"]),
                "",
                "Use: service help <service>",
                "Use: service call <service.function> --args \"[]\" --kwargs \"{}\" --confirm",
            ])
        if action == "help":
            if len(args) < 2:
                raise ValueError("Usage: service help <service>[.<function>]")
            target = args[1]
            if "." in target:
                service_name, func_name = target.split(".", 1)
                service = self._get_service(service_name, registry)
                func = self._get_service_function(service, func_name)
                return self._describe_function(service_name, func_name, func)
            service = self._get_service(target, registry)
            rows = []
            for method_name, func in self._service_methods(service).items():
                rows.append({
                    "function": method_name,
                    "signature": str(inspect.signature(func)),
                    "risk": self._service_call_risk(method_name),
                })
            return self._format_rows(rows, ["function", "signature", "risk"])
        if action == "call":
            positional, options = self._parse_options(args[1:])
            if not positional or "." not in positional[0]:
                raise ValueError("Usage: service call <service.function> --args \"[]\" --kwargs \"{}\"")
            service_name, func_name = positional[0].split(".", 1)
            service = self._get_service(service_name, registry)
            func = self._get_service_function(service, func_name)
            risk = self._service_call_risk(func_name)
            if risk != "read" and not options.get("confirm"):
                return "\n".join([
                    f"Service call blocked because {service_name}.{func_name} may modify data.",
                    "Rerun with --confirm after reviewing:",
                    f"  service help {service_name}.{func_name}",
                ])
            if risk != "read":
                self._require_admin(f"call mutating service function {service_name}.{func_name}")
            call_args = self._parse_json_option(options.get("args"), default=[])
            call_kwargs = self._parse_json_option(options.get("kwargs"), default={})
            if not isinstance(call_args, list):
                raise ValueError("--args must be a JSON list.")
            if not isinstance(call_kwargs, dict):
                raise ValueError("--kwargs must be a JSON object.")
            result = func(*call_args, **call_kwargs)
            return "\n".join([
                f"Called {service_name}.{func_name}",
                self._format_value(result),
            ])
        raise ValueError("Usage: service list | service help <service>[.<function>] | service call <service.function>")

    def _help(self) -> str:
        return "\n".join(
            [
                "Nexus Engineer CLI",
                "",
                "Natural language agent:",
                "  ask \"what blocks release?\"",
                "  act \"checkout ASSY MECANISM and all its children\"",
                "  act \"create an item called variant-1 with all project parts as children, aes is DJB9010123\"",
                "  Plain English is accepted directly; exact commands are still supported.",
                "  Ollama is used automatically when available, then rules are used as fallback.",
                "",
                "Ollama:",
                "  ollama status",
                "  ollama models",
                "  ollama model llama3.1",
                "  Environment: NEXUS_OLLAMA_HOST=http://127.0.0.1:11434",
                "  Environment: NEXUS_OLLAMA_MODEL=llama3.1",
                "",
                "Database power tools:",
                "  db tables",
                "  db schema <table>",
                "  db describe <table>",
                "  db count <table>",
                "  sql \"SELECT id,name FROM bom LIMIT 20\"",
                "  sql --apply --confirm \"UPDATE bom SET lifecycle_state='WIP' WHERE id=123\"",
                "",
                "Direct backend service calls:",
                "  service list",
                "  service help bom",
                "  service help bom.checkout_item",
                "  service call bom.checkout_item --args \"[123]\" --kwargs \"{\\\"include_owner_cad\\\": true}\" --confirm",
                "  service call pdm.list_cad_documents --args \"[5]\"",
                "",
                "Read commands:",
                "  context",
                "  find item <text>",
                "  find cad <text>",
                "  show item <item_id>",
                "  show cad <cad_document_id>",
                "  diag summary",
                "  diag missing-docs",
                "  diag associations",
                "  diag checkouts",
                "  diag orphans",
                "  diag duplicate-docs",
                "  assist [release|cleanup|package|checkout]",
                "",
                "Controlled actions:",
                "  checkout item <item_id> [--with-cad] [--as <username>]",
                "  checkout cad <cad_document_id> [--as <username>]",
                "  checkin item <item_id> --note \"text\"",
                "  checkin cad <cad_document_id> --path \"C:\\path\\file.prt.23\" --note \"text\"",
                "  undo item <item_id> [--note \"text\"]",
                "  undo cad <cad_document_id> [--note \"text\"]",
                "  bulk checkout items --items 12,18 [--with-cad]",
                "  bulk undo cad --cad 45,46",
                "  item create --name variant-1 --type asm --children all-project",
                "  associate item <item_id> cad <cad_id> --type OWNER|IMAGE|CONTRIBUTING_IMAGE",
                "  unassociate <association_id>",
                "  drawing select item <item_id> cad <model_cad_id> --drawings 70,71 --primary 70",
                "  build cad <root_cad_id> [--single-level]",
                "  auto-associate [--apply]",
                "  fix orphan-associations [--apply]",
                "  export package --items 12,18,25 --to \"C:\\Export\" [--no-children] [--zip]",
                "  script --path \"C:\\work\\nexus_commands.txt\"",
                "",
                "Rules:",
                "  - No SQL, no Python, no OS shell execution.",
                "  - Admin users may use --as to act for another Nexus user.",
                "  - Commands use Nexus backend services and respect lifecycle/checkout rules.",
            ]
        )

    def _parse_options(self, args: list[str]) -> tuple[list[str], dict[str, object]]:
        positional = []
        options: dict[str, object] = {}
        i = 0
        while i < len(args):
            token = str(args[i])
            if token.startswith("--"):
                name = token[2:].strip().lower()
                if name in {"with-cad", "no-children", "zip", "apply", "single-level", "confirm"}:
                    options[name] = True
                    i += 1
                    continue
                if i + 1 >= len(args):
                    raise ValueError(f"Missing value for --{name}")
                options[name] = self._strip_quotes(args[i + 1])
                i += 2
                continue
            positional.append(self._strip_quotes(token))
            i += 1
        return positional, options

    @staticmethod
    def _strip_quotes(value: str) -> str:
        text = str(value or "").strip()
        if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
            return text[1:-1]
        return text

    def _actor_id(self, options: dict[str, object]) -> int | None:
        username = str(options.get("as") or "").strip()
        if not username:
            return None
        if not self._is_admin_user():
            raise PermissionError("Only admin users can use --as.")
        user = self.user_repo.find_by_username(username)
        if not user:
            raise ValueError(f"User not found: {username}")
        return int(user.id)

    def _context(self) -> str:
        project_id = self._project_id()
        project = self.project_service.get_project_by_id(project_id) or {}
        user = str(getattr(self.session, "username", "") or "")
        return "\n".join(
            [
                f"User: {user} (ID {getattr(self.session, 'user_id', '')})",
                f"Project: {project.get('name') or project_id}",
                f"Version: {project.get('version_label') or '-'} / {project.get('version_state') or '-'}",
                f"Working directory: {project.get('working_directory') or '-'}",
                f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            ]
        )

    def _find(self, args: list[str]) -> str:
        if len(args) < 2:
            raise ValueError("Usage: find item <text> | find cad <text>")
        kind = args[0].lower()
        query = " ".join(args[1:]).strip()
        if not query:
            raise ValueError("Search text is required.")
        if kind == "item":
            return self._find_items(query)
        if kind == "cad":
            return self._find_cad(query)
        raise ValueError("Usage: find item <text> | find cad <text>")

    def _find_items(self, query: str) -> str:
        project_id = self._project_id()
        like = f"%{query.lower()}%"
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT id, part_number, aes_number, name, type, lifecycle_state
                FROM bom
                WHERE project_id=?
                  AND represented_part_id IS NULL
                  AND (
                    lower(COALESCE(part_number,'')) LIKE ?
                    OR lower(COALESCE(aes_number,'')) LIKE ?
                    OR lower(COALESCE(name,'')) LIKE ?
                    OR lower(COALESCE(drawing_number,'')) LIKE ?
                    OR lower(COALESCE(base_file_name,'')) LIKE ?
                  )
                ORDER BY lower(COALESCE(part_number,'')), lower(name), id
                LIMIT 40
                """,
                (project_id, like, like, like, like, like),
            ).fetchall()
        return self._format_rows(
            [dict(r) for r in rows],
            ["id", "part_number", "aes_number", "name", "type", "lifecycle_state"],
        )

    def _find_cad(self, query: str) -> str:
        project_id = self._project_id()
        like = f"%{query.lower()}%"
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT id, file_name, name, category, revision, iteration,
                       lifecycle_state, checked_out_by
                FROM cad_documents
                WHERE project_id=?
                  AND (
                    lower(COALESCE(file_name,'')) LIKE ?
                    OR lower(COALESCE(name,'')) LIKE ?
                    OR lower(COALESCE(base_file_name,'')) LIKE ?
                  )
                ORDER BY lower(file_name), id
                LIMIT 40
                """,
                (project_id, like, like, like),
            ).fetchall()
        return self._format_rows(
            [dict(r) for r in rows],
            ["id", "file_name", "name", "category", "revision", "iteration", "lifecycle_state", "checked_out_by"],
        )

    def _diag(self, args: list[str]) -> str:
        topic = (args[0].lower() if args else "summary")
        if topic == "summary":
            return self._diag_summary()
        if topic in {"missing-docs", "docs", "deliverables"}:
            return self._diag_missing_docs()
        if topic in {"associations", "assoc"}:
            return self._diag_associations()
        if topic in {"checkouts", "checkout"}:
            return self._diag_checkouts()
        if topic in {"orphans", "orphan-associations"}:
            return self._diag_orphan_associations()
        if topic in {"duplicate-docs", "duplicates"}:
            return self._diag_duplicate_docs()
        raise ValueError(
            "Usage: diag summary|missing-docs|associations|checkouts|orphans|duplicate-docs"
        )

    def _diag_summary(self) -> str:
        project_id = self._project_id()
        with self._conn() as conn:
            if not self._table_exists(conn, "bom"):
                return "Nexus project tables are not initialized for the selected database."
            counts = {
                "items": self._scalar(conn, "SELECT COUNT(*) FROM bom WHERE project_id=? AND represented_part_id IS NULL", project_id),
                "cad": self._scalar(conn, "SELECT COUNT(*) FROM cad_documents WHERE project_id=?", project_id),
                "drawings": self._scalar(conn, "SELECT COUNT(*) FROM cad_documents WHERE project_id=? AND upper(category)='DRAWING'", project_id),
                "active_associations": self._scalar(conn, "SELECT COUNT(*) FROM cad_item_associations WHERE project_id=? AND active=1", project_id),
                "item_checkouts": self._scalar(conn, "SELECT COUNT(*) FROM locks l JOIN bom b ON b.id=l.part_id WHERE b.project_id=?", project_id),
                "cad_checkouts": self._scalar(conn, "SELECT COUNT(*) FROM cad_documents WHERE project_id=? AND checked_out_by IS NOT NULL", project_id),
                "pending_commits": self._scalar(conn, "SELECT COUNT(DISTINCT commit_id) FROM commits WHERE project_id=? AND lower(COALESCE(status,''))='pending'", project_id),
            }
            missing_docs = self._missing_doc_rows(conn, limit=100000)
            orphan_assoc = self._orphan_association_rows(conn, limit=100000)
            no_owner = self._cad_without_owner_rows(conn, limit=100000)
        lines = [
            "Product diagnostic summary",
            f"  Items: {counts['items']}",
            f"  CAD Documents: {counts['cad']} ({counts['drawings']} drawings)",
            f"  Active CAD/Item associations: {counts['active_associations']}",
            f"  Item checkouts: {counts['item_checkouts']}",
            f"  CAD checkouts: {counts['cad_checkouts']}",
            f"  Pending commits: {counts['pending_commits']}",
            "",
            "Attention:",
            f"  Missing required PDF/STEP rows: {len(missing_docs)}",
            f"  Active associations pointing to deleted Items: {len(orphan_assoc)}",
            f"  CAD documents without OWNER Item: {len(no_owner)}",
        ]
        return "\n".join(lines)

    def _diag_missing_docs(self) -> str:
        with self._conn() as conn:
            rows = self._missing_doc_rows(conn, limit=80)
        if not rows:
            return "No missing required PDF/STEP deliverables found."
        return self._format_rows(
            rows,
            ["item_id", "part_number", "aes_number", "name", "missing"],
        )

    def _diag_associations(self) -> str:
        project_id = self._project_id()
        with self._conn() as conn:
            if not self._table_exists(conn, "cad_documents"):
                return "CAD Document tables are not initialized for the selected database."
            no_owner = self._cad_without_owner_rows(conn, limit=60)
            multi_owner = [
                dict(r) for r in conn.execute(
                    """
                    SELECT d.id AS cad_id,d.file_name,COUNT(a.id) AS owner_count
                    FROM cad_documents d
                    JOIN cad_item_associations a
                      ON a.cad_document_id=d.id AND a.active=1
                     AND upper(a.association_type)='OWNER'
                    WHERE d.project_id=? AND upper(d.category)<>'DRAWING'
                    GROUP BY d.id,d.file_name
                    HAVING COUNT(a.id)>1
                    ORDER BY owner_count DESC, lower(d.file_name)
                    LIMIT 60
                    """,
                    (project_id,),
                ).fetchall()
            ]
            item_without_owner = [
                dict(r) for r in conn.execute(
                    """
                    SELECT b.id AS item_id,b.part_number,b.aes_number,b.name
                    FROM bom b
                    WHERE b.project_id=? AND b.represented_part_id IS NULL
                      AND upper(COALESCE(b.cad_requirement,'OPTIONAL'))='REQUIRED'
                      AND NOT EXISTS (
                        SELECT 1 FROM cad_item_associations a
                        JOIN cad_documents d ON d.id=a.cad_document_id
                        WHERE a.item_id=b.id AND a.active=1
                          AND upper(a.association_type)='OWNER'
                          AND upper(d.category)<>'DRAWING'
                      )
                    ORDER BY lower(b.name),b.id
                    LIMIT 60
                    """,
                    (project_id,),
                ).fetchall()
            ]
        blocks = [
            f"CAD without OWNER Item: {len(no_owner)}",
            self._format_rows(no_owner[:20], ["cad_id", "file_name", "category"]) if no_owner else "  none",
            "",
            f"CAD with multiple OWNER associations: {len(multi_owner)}",
            self._format_rows(multi_owner[:20], ["cad_id", "file_name", "owner_count"]) if multi_owner else "  none",
            "",
            f"Required-CAD Items without OWNER CAD: {len(item_without_owner)}",
            self._format_rows(item_without_owner[:20], ["item_id", "part_number", "aes_number", "name"]) if item_without_owner else "  none",
        ]
        return "\n".join(blocks)

    def _diag_checkouts(self) -> str:
        project_id = self._project_id()
        with self._conn() as conn:
            if not self._table_exists(conn, "bom"):
                return "Nexus project tables are not initialized for the selected database."
            lock_cols = self._table_columns(conn, "locks")
            checkout_time_col = (
                "checked_out_at" if "checked_out_at" in lock_cols
                else ("locked_at" if "locked_at" in lock_cols else None)
            )
            checkout_time_expr = (
                f"l.{checkout_time_col}" if checkout_time_col else "''"
            )
            checkout_origin_expr = (
                "l.checkout_origin" if "checkout_origin" in lock_cols else "'ITEM'"
            )
            item_rows = [
                dict(r) for r in conn.execute(
                    f"""
                    SELECT b.id AS item_id,b.part_number,b.name,u.username,
                           {checkout_time_expr} AS checked_out_at,
                           {checkout_origin_expr} AS checkout_origin
                    FROM locks l
                    JOIN bom b ON b.id=l.part_id
                    LEFT JOIN users u ON u.id=l.user_id
                    WHERE b.project_id=?
                    ORDER BY checked_out_at DESC
                    LIMIT 80
                    """,
                    (project_id,),
                ).fetchall()
            ]
            cad_rows = [
                dict(r) for r in conn.execute(
                    """
                    SELECT d.id AS cad_id,d.file_name,u.username,d.checked_out_at,
                           d.checkout_workspace_name
                    FROM cad_documents d
                    LEFT JOIN users u ON u.id=d.checked_out_by
                    WHERE d.project_id=? AND d.checked_out_by IS NOT NULL
                    ORDER BY d.checked_out_at DESC
                    LIMIT 80
                    """,
                    (project_id,),
                ).fetchall()
            ] if self._table_exists(conn, "cad_documents") else []
        return "\n".join([
            f"Item checkouts: {len(item_rows)}",
            self._format_rows(item_rows[:25], ["item_id", "part_number", "name", "username", "checked_out_at", "checkout_origin"]) if item_rows else "  none",
            "",
            f"CAD checkouts: {len(cad_rows)}",
            self._format_rows(cad_rows[:25], ["cad_id", "file_name", "username", "checkout_workspace_name"]) if cad_rows else "  none",
        ])

    def _diag_orphan_associations(self) -> str:
        with self._conn() as conn:
            rows = self._orphan_association_rows(conn, limit=80)
        if not rows:
            return "No orphan CAD/Item associations found."
        return self._format_rows(rows, ["association_id", "item_id", "cad_document_id", "association_type", "file_name"])

    def _diag_duplicate_docs(self) -> str:
        project_id = self._project_id()
        with self._conn() as conn:
            rows = [
                dict(r) for r in conn.execute(
                    """
                    SELECT pf.part_id,pf.file_type,COUNT(*) AS document_count,
                           GROUP_CONCAT(pf.id) AS file_ids
                    FROM part_files pf
                    JOIN bom b ON b.id=pf.part_id
                    WHERE b.project_id=? AND upper(pf.file_type) IN ('PDF','STEP','STP')
                      AND pf.deleted_at IS NULL
                    GROUP BY pf.part_id,upper(pf.file_type)
                    HAVING COUNT(*)>1
                    ORDER BY document_count DESC, pf.part_id
                    LIMIT 80
                    """,
                    (project_id,),
                ).fetchall()
            ] if self._table_exists(conn, "part_files") else []
        if not rows:
            return "No duplicate PDF/STEP document containers found."
        return self._format_rows(rows, ["part_id", "file_type", "document_count", "file_ids"])

    def _assist(self, args: list[str]) -> str:
        focus = (args[0].lower() if args else "release")
        project_id = self._project_id()
        with self._conn() as conn:
            if not self._table_exists(conn, "bom"):
                return "Nexus project tables are not initialized for the selected database."
            missing_docs = self._missing_doc_rows(conn, limit=100000)
            orphan_assoc = self._orphan_association_rows(conn, limit=100000)
            no_owner = self._cad_without_owner_rows(conn, limit=100000)
            checked_out_cad = self._scalar(conn, "SELECT COUNT(*) FROM cad_documents WHERE project_id=? AND checked_out_by IS NOT NULL", project_id)
            checked_out_items = self._scalar(conn, "SELECT COUNT(*) FROM locks l JOIN bom b ON b.id=l.part_id WHERE b.project_id=?", project_id)
            duplicate_docs = []
            if self._table_exists(conn, "part_files"):
                duplicate_docs = [
                    dict(r) for r in conn.execute(
                        """
                        SELECT pf.part_id,pf.file_type,COUNT(*) AS n
                        FROM part_files pf
                        JOIN bom b ON b.id=pf.part_id
                        WHERE b.project_id=? AND upper(pf.file_type) IN ('PDF','STEP','STP')
                          AND pf.deleted_at IS NULL
                        GROUP BY pf.part_id,upper(pf.file_type)
                        HAVING COUNT(*)>1
                        """,
                        (project_id,),
                    ).fetchall()
                ]
        lines = [
            f"Nexus AI-style assistant plan ({focus})",
            "This is deterministic local analysis from Nexus data; no external AI service is used.",
            "",
            "Priority findings:",
            f"  1. Orphan associations: {len(orphan_assoc)}",
            f"  2. CAD without OWNER Item: {len(no_owner)}",
            f"  3. Missing required deliverables: {len(missing_docs)}",
            f"  4. Duplicate PDF/STEP containers: {len(duplicate_docs)}",
            f"  5. Open checkouts: {checked_out_items} Item / {checked_out_cad} CAD",
            "",
            "Recommended CLI sequence:",
        ]
        if orphan_assoc:
            lines.append("  fix orphan-associations --apply")
        if no_owner:
            lines.append("  auto-associate")
            lines.append("  auto-associate --apply     # only after reviewing proposed matches")
        if missing_docs:
            sample = ",".join(str(r["item_id"]) for r in missing_docs[:10])
            lines.append(f"  diag missing-docs          # review all required missing files")
            lines.append(f"  export package --items {sample} --to \"C:\\Nexus\\review\" --no-children")
        if duplicate_docs:
            lines.append("  diag duplicate-docs        # consolidate PDF/STEP containers before release")
        if checked_out_items or checked_out_cad:
            lines.append("  diag checkouts             # resolve or confirm active engineering work")
        if not any((orphan_assoc, no_owner, missing_docs, duplicate_docs, checked_out_items, checked_out_cad)):
            lines.append("  No obvious blocker found. Run export/package or release validation.")
        lines.extend([
            "",
            "Useful next commands:",
            "  diag summary",
            "  diag associations",
            "  diag missing-docs",
            "  script --path \"C:\\work\\nexus_cleanup.txt\"",
        ])
        return "\n".join(lines)

    def _natural_language(self, text: str, mode: str = "auto") -> str:
        raw = str(text or "").strip()
        if not raw:
            raise ValueError("Tell Nexus what you want to analyze or do.")
        plan = self._ollama_plan(raw, mode=mode) or self._plan_from_text(raw)
        if not plan:
            return self._agent_unknown(raw)

        should_execute = mode == "act" or (
            mode == "auto" and bool(plan.get("auto_execute"))
        )
        if mode in {"ask", "assist"}:
            should_execute = False
        if plan.get("risk") == "high" and not self._has_explicit_confirmation(raw):
            should_execute = False

        lines = [
            "Nexus Agent",
            f"Understood: {plan['summary']}",
            f"Confidence: {plan.get('confidence', 0):.0%}",
        ]
        if plan.get("risk"):
            lines.append(f"Risk: {plan['risk']}")
        if plan.get("analysis"):
            lines.extend(["", "Analysis:", *[f"  - {x}" for x in plan["analysis"]]])
        commands = plan.get("commands") or []
        if commands:
            lines.extend(["", "Planned command(s):", *[f"  {cmd}" for cmd in commands]])
        missing = plan.get("missing") or []
        if missing:
            self._pending_clarification = {
                "plan": plan,
                "raw": raw,
                "missing": missing,
                "commands": commands,
            }
            lines.extend([
                "",
                "I need one detail before I can execute:",
                f"  {missing[0]['question']}",
            ])
            return "\n".join(lines)
        if plan.get("requires_confirmation") and not self._has_explicit_confirmation(raw):
            lines.extend([
                "",
                "I did not execute because this action needs explicit confirmation.",
                "Run again with: confirm",
            ])
            return "\n".join(lines)
        if not should_execute:
            lines.extend([
                "",
                "No action executed.",
                "To execute this plan, write the same request with `act`, for example:",
                f"  act \"{raw}\"",
            ])
            return "\n".join(lines)

        lines.append("")
        lines.append("Executing:")
        for command in commands:
            lines.append(f"> {command}")
            try:
                result = self.execute(command)
                if result:
                    lines.append(result)
            except Exception as exc:
                clarification = self._clarification_from_error(command, exc, plan, raw)
                if clarification:
                    lines.extend(["", clarification])
                else:
                    lines.append(f"ERROR: {exc}")
                break
        return "\n".join(lines)

    def _plan_from_text(self, raw: str) -> dict | None:
        text = raw.lower()
        create_project_item = self._parse_create_project_item_intent(raw)
        if create_project_item:
            command = (
                f'item create --name "{create_project_item["name"]}" '
                f'--type {create_project_item["type"]} --children all-project'
            )
            if create_project_item.get("aes"):
                command += f' --aes "{create_project_item["aes"]}"'
            if create_project_item.get("number"):
                command += f' --number "{create_project_item["number"]}"'
            missing = []
            if not create_project_item.get("aes"):
                missing.append({
                    "field": "aes",
                    "question": (
                        "This new Item is for delivery, so it needs an AES number. "
                        "What AES number should I use?"
                    ),
                    "option": "--aes",
                })
            return {
                "summary": (
                    f"create a top-level EBOM assembly Item named "
                    f"{create_project_item['name']} and add all project Items as children"
                ),
                "confidence": 0.93,
                "risk": "medium",
                "auto_execute": self._looks_imperative(text),
                "commands": [command],
                "missing": missing,
                "analysis": [
                    "The new item is treated as an assembly because it represents the whole project.",
                    "Existing project Items are added as manual EBOM usages under the new parent.",
                    "The command checks out the new parent before editing its structure.",
                ],
            }
        if self._matches_any(text, "release", "block", "ready", "approve", "merge readiness"):
            return {
                "summary": "analyze release blockers and readiness",
                "confidence": 0.9,
                "risk": "low",
                "commands": ["assist release"],
                "analysis": [self._compact_diag_summary()],
            }
        if self._matches_any(text, "missing", "pdf", "step", "deliverable", "package ready", "delivery"):
            return {
                "summary": "inspect required PDF/STEP delivery gaps",
                "confidence": 0.88,
                "risk": "low",
                "commands": ["diag missing-docs"],
                "analysis": ["Nexus will ignore non-deliverable philosophy where item metadata marks delivery documents optional."],
            }
        if self._matches_any(text, "association", "associated", "owner", "cad item", "link", "related cad"):
            apply = self._matches_any(text, "fix", "apply", "auto")
            return {
                "summary": "inspect or repair CAD/Item association issues",
                "confidence": 0.86,
                "risk": "medium" if apply else "low",
                "auto_execute": self._looks_imperative(text) and apply,
                "commands": ["auto-associate --apply" if apply else "diag associations"],
                "analysis": ["OWNER associations drive checkout coupling and EBOM build behavior."],
            }
        if self._matches_any(text, "checkout", "checked out", "lock", "workspace"):
            item_ids = self._extract_ids_after_words(raw, ("item", "items", "article", "articles"))
            cad_ids = self._extract_ids_after_words(raw, ("cad", "document", "documents"))
            item_name = "" if item_ids else self._extract_checkout_item_name(raw)
            name_resolution = None
            if item_name:
                name_resolution = self._resolve_item_name_for_agent(item_name)
                if name_resolution.get("item_id"):
                    item_ids = [int(name_resolution["item_id"])]
            if self._matches_any(text, "undo", "cancel"):
                commands = []
                if item_ids:
                    commands.append(f"bulk undo items --items {','.join(map(str, item_ids))}")
                if cad_ids:
                    commands.append(f"bulk undo cad --cad {','.join(map(str, cad_ids))}")
                return {
                    "summary": "undo checkout for selected controlled objects",
                    "confidence": 0.82 if commands else 0.65,
                    "risk": "medium",
                    "auto_execute": bool(commands) and self._looks_imperative(text),
                    "commands": commands or ["diag checkouts"],
                    "analysis": self._checkout_resolution_analysis(name_resolution)
                    + ["Undo Item checkout also follows the configured CAD checkout rules."],
                }
            commands = []
            if item_ids:
                include_children = self._matches_any(text, "children", "childrens", "subtree", "all its child", "all child")
                resolved_ids = item_ids
                if include_children:
                    resolved_ids = self._expand_item_ids_with_children(item_ids)
                suffix = " --with-cad" if self._matches_any(text, "with cad", "cad also", "associated cad") else ""
                commands.append(f"bulk checkout items --items {','.join(map(str, resolved_ids))}{suffix}")
            if cad_ids:
                commands.append(f"bulk checkout cad --cad {','.join(map(str, cad_ids))}")
            if name_resolution and name_resolution.get("ambiguous"):
                return {
                    "summary": f"resolve which Item named like '{item_name}' should be checked out",
                    "confidence": 0.55,
                    "risk": "low",
                    "commands": [],
                    "missing": [{
                        "field": "item_id",
                        "question": self._ambiguous_item_question(name_resolution),
                        "option": "--items",
                    }],
                    "analysis": ["I found multiple matching Items and need the exact one before checkout."],
                }
            if name_resolution and name_resolution.get("not_found"):
                return {
                    "summary": f"find the Item to check out from name '{item_name}'",
                    "confidence": 0.55,
                    "risk": "low",
                    "commands": [f'find item "{item_name}"'],
                    "analysis": [
                        f"I could not resolve '{item_name}' to a unique EBOM Item, so I will search first."
                    ],
                }
            return {
                "summary": "checkout selected Items or CAD Documents",
                "confidence": 0.82 if commands else 0.7,
                "risk": "medium",
                "auto_execute": bool(commands) and self._looks_imperative(text),
                "commands": commands or ["diag checkouts"],
                "analysis": self._checkout_resolution_analysis(name_resolution)
                + ["CAD checkout automatically coordinates associated Item locks."],
            }
        if self._matches_any(text, "cleanup", "orphan", "deleted item", "ghost association"):
            apply = self._matches_any(text, "fix", "apply", "delete", "remove", "clean")
            return {
                "summary": "clean orphan CAD/Item associations pointing to deleted Items",
                "confidence": 0.88,
                "risk": "medium" if apply else "low",
                "auto_execute": self._looks_imperative(text) and apply,
                "commands": ["fix orphan-associations --apply" if apply else "fix orphan-associations"],
                "analysis": ["This targets active associations where the EBOM Item row no longer exists."],
            }
        if text.startswith("find ") or self._matches_any(text, "search", "look for", "find"):
            query = self._extract_search_text(raw)
            if query:
                return {
                    "summary": f"search Items and CAD Documents for '{query}'",
                    "confidence": 0.8,
                    "risk": "low",
                    "commands": [f'find item "{query}"', f'find cad "{query}"'],
                }
        if self._matches_any(text, "diagnostic", "diagnose", "health", "summary"):
            return {
                "summary": "run project diagnostic summary",
                "confidence": 0.78,
                "risk": "low",
                "commands": ["diag summary"],
            }
        return None

    def _agent_unknown(self, raw: str) -> str:
        return "\n".join([
            "Nexus Agent",
            "I could not build a safe PDM plan from that request yet.",
            "",
            "Try phrasing it as an engineering action, for example:",
            "  act \"create an item called variant-1 with all project parts as children\"",
            "  ask \"what blocks release?\"",
            "  act \"fix orphan associations\"",
            "  act \"checkout items 12, 18, 25 with associated CAD\"",
            "  ask \"find output connector\"",
            "",
            f"Original request: {raw}",
        ])

    def _ollama_status(self) -> tuple[bool, str]:
        try:
            req = urllib.request.Request(f"{self.ollama_host}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=2.5) as resp:
                if resp.status != 200:
                    return False, f"HTTP {resp.status}"
                data = json.loads(resp.read().decode("utf-8") or "{}")
            names = [m.get("name") for m in data.get("models", []) if m.get("name")]
            if not names:
                return True, "Ollama reachable, no local models listed."
            if self.ollama_model not in names and not any(n.startswith(self.ollama_model + ":") for n in names):
                return True, f"Ollama reachable. Current model not found in list: {self.ollama_model}"
            return True, "Ollama reachable."
        except Exception as exc:
            return False, str(exc)

    def _ollama_models(self) -> list[dict]:
        try:
            req = urllib.request.Request(f"{self.ollama_host}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = json.loads(resp.read().decode("utf-8") or "{}")
            rows = []
            for model in data.get("models", []) or []:
                rows.append({
                    "name": model.get("name") or "",
                    "modified_at": model.get("modified_at") or "",
                    "size": model.get("size") or "",
                })
            return rows
        except Exception:
            return []

    def _ollama_plan(self, raw: str, mode: str = "auto") -> dict | None:
        ok, _detail = self._ollama_status()
        if not ok:
            return None
        prompt = self._ollama_planner_prompt(raw, mode)
        payload = {
            "model": self.ollama_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are Nexus PDM Agent, a local engineering data management planner. "
                        "Return ONLY valid JSON. Do not wrap in markdown. "
                        "You do not execute tools; you produce a safe plan using allowed Nexus CLI commands."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.1},
        }
        try:
            req = urllib.request.Request(
                f"{self.ollama_host}/api/chat",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=45) as resp:
                data = json.loads(resp.read().decode("utf-8") or "{}")
            content = ((data.get("message") or {}).get("content") or "").strip()
            if not content:
                return None
            plan = json.loads(content)
            return self._normalize_ollama_plan(plan)
        except Exception:
            return None

    def _ollama_planner_prompt(self, raw: str, mode: str) -> str:
        context = ""
        try:
            context = self._context()
        except Exception:
            context = "No active project context."
        return f"""
User request:
{raw}

Mode: {mode}

Current Nexus context:
{context}

Allowed CLI commands you may put in commands[]:
- context
- find item "<text>"
- find cad "<text>"
- show item <id>
- show cad <id>
- diag summary
- diag missing-docs
- diag associations
- diag checkouts
- diag orphans
- diag duplicate-docs
- bulk checkout items --items 1,2,3 [--with-cad]
- bulk checkout cad --cad 1,2,3
- bulk undo items --items 1,2,3
- bulk undo cad --cad 1,2,3
- item create --name "<name>" --type asm|prt --children all-project|none [--aes "<aes>"] [--number "<number>"]
- associate item <item_id> cad <cad_id> --type OWNER|IMAGE|CONTRIBUTING_IMAGE
- unassociate <association_id>
- drawing select item <item_id> cad <model_cad_id> --drawings 70,71 --primary 70
- build item <item_id>
- build cad <cad_id>
- auto-associate
- auto-associate --apply
- fix orphan-associations
- fix orphan-associations --apply
- export package --items 1,2,3 --to "<folder>" [--no-children] [--zip]
- db tables
- db schema <table>
- db count <table>
- sql "<SELECT/PRAGMA/WITH/EXPLAIN only>"
- service list
- service help <service>
- service help <service.function>
- service call <service.function> --args "<json list>" --kwargs "<json object>"

If an action needs missing information, do NOT invent it. Put it in missing[].
For name-based object requests, prefer commands that search first if no ID is known.
Never include destructive SQL unless the user explicitly asked for SQL.

Return JSON object:
{{
  "summary": "short understood intent",
  "confidence": 0.0,
  "risk": "low|medium|high",
  "analysis": ["short reason"],
  "commands": ["allowed command"],
  "missing": [{{"field":"...", "question":"...", "option":"--..."}}],
  "requires_confirmation": false,
  "auto_execute": false
}}
""".strip()

    def _normalize_ollama_plan(self, plan: dict) -> dict | None:
        if not isinstance(plan, dict):
            return None
        commands = plan.get("commands") or []
        if isinstance(commands, str):
            commands = [commands]
        safe_commands = []
        for command in commands:
            command = str(command or "").strip()
            if not command:
                continue
            if self._ollama_command_allowed(command):
                safe_commands.append(command)
        missing = plan.get("missing") or []
        if not isinstance(missing, list):
            missing = []
        analysis = plan.get("analysis") or []
        if isinstance(analysis, str):
            analysis = [analysis]
        try:
            confidence = float(plan.get("confidence", 0.6))
        except Exception:
            confidence = 0.6
        risk = str(plan.get("risk") or "medium").lower()
        if risk not in {"low", "medium", "high"}:
            risk = "medium"
        return {
            "summary": str(plan.get("summary") or "interpret the request with Ollama"),
            "confidence": max(0.0, min(1.0, confidence)),
            "risk": risk,
            "analysis": [str(x) for x in analysis[:8]],
            "commands": safe_commands,
            "missing": missing,
            "requires_confirmation": bool(plan.get("requires_confirmation", False)),
            "auto_execute": bool(plan.get("auto_execute", False)),
        }

    @staticmethod
    def _ollama_command_allowed(command: str) -> bool:
        first = str(command or "").strip().split(" ", 1)[0].lower()
        return first in {
            "context", "find", "show", "diag", "bulk", "item", "associate",
            "unassociate", "drawing", "build", "auto-associate", "fix",
            "export", "db", "sql", "service",
        }

    def _parse_create_project_item_intent(self, text: str) -> dict | None:
        lowered = str(text or "").lower()
        if not any(word in lowered for word in ("create", "make", "add")):
            return None
        if "item" not in lowered:
            return None
        if not any(phrase in lowered for phrase in (
            "entire project",
            "whole project",
            "all the part",
            "all parts",
            "all item",
            "all children",
            "as childrens",
            "as children",
        )):
            return None
        name = ""
        patterns = [
            r"called\s+['\"]?([^,'\"]+?)['\"]?(?:\s*,|\s+this|\s+which|\s+with|\s+and|$)",
            r"named\s+['\"]?([^,'\"]+?)['\"]?(?:\s*,|\s+this|\s+which|\s+with|\s+and|$)",
            r"item\s+['\"]?([^,'\"]+?)['\"]?(?:\s*,|\s+this|\s+which|\s+with|\s+and|$)",
        ]
        for pattern in patterns:
            match = re.search(pattern, str(text or ""), flags=re.IGNORECASE)
            if match:
                name = match.group(1).strip()
                break
        if not name:
            name = "variant"
        aes = self._extract_named_value(text, (
            "aes number", "aes", "index aes", "aes index",
        ))
        number = self._extract_named_value(text, (
            "item number", "article number", "part number", "plm number",
        ))
        return {"name": name, "type": "asm", "aes": aes, "number": number}

    @staticmethod
    def _matches_any(text: str, *needles: str) -> bool:
        normalized = str(text or "").lower()
        return any(str(needle).lower() in normalized for needle in needles)

    @staticmethod
    def _looks_imperative(text: str) -> bool:
        normalized = str(text or "").strip().lower()
        return normalized.startswith((
            "act ", "do ", "make ", "create ", "add ", "fix ", "clean ",
            "checkout ", "check out ", "undo ", "export ", "build ",
            "associate ", "select ", "set ",
        ))

    @staticmethod
    def _has_explicit_confirmation(text: str) -> bool:
        normalized = str(text or "").lower()
        return any(token in normalized for token in (
            " confirm", " confirmed", " i confirm", " yes do it", " execute now",
            " apply now",
        ))

    @staticmethod
    def _extract_ids_after_words(raw: str, words) -> list[int]:
        text = str(raw or "")
        ids: list[int] = []
        word_pattern = "|".join(re.escape(w) for w in words)
        pattern = rf"(?:{word_pattern})\s+((?:\d+[\s,;]*)+)"
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            for value in re.findall(r"\d+", match.group(1)):
                ids.append(int(value))
        return sorted(dict.fromkeys(ids))

    @staticmethod
    def _extract_search_text(raw: str) -> str:
        text = str(raw or "").strip()
        patterns = [
            r"find\s+(.+)$",
            r"search\s+(?:for\s+)?(.+)$",
            r"look\s+for\s+(.+)$",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                value = match.group(1).strip().strip("'\"")
                return value
        return ""

    @staticmethod
    def _extract_checkout_item_name(raw: str) -> str:
        text = str(raw or "").strip()
        text = re.sub(r"^act\s+", "", text, flags=re.IGNORECASE).strip()
        patterns = [
            r"checkout\s+(.+?)(?:\s+and\s+all\s+(?:its\s+)?child(?:ren|rens)?|\s+with\s+children|\s+subtree|$)",
            r"check\s+out\s+(.+?)(?:\s+and\s+all\s+(?:its\s+)?child(?:ren|rens)?|\s+with\s+children|\s+subtree|$)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                value = match.group(1).strip().strip("'\" .,;")
                value = re.sub(r"\b(item|items|article|articles|ebom)\b", "", value, flags=re.IGNORECASE)
                return " ".join(value.split())
        return ""

    def _resolve_item_name_for_agent(self, name: str) -> dict:
        query = str(name or "").strip()
        if not query:
            return {}
        project_id = self._project_id()
        tokens = [t for t in re.split(r"\s+", query.lower()) if t]
        with self._conn() as conn:
            if not self._table_exists(conn, "bom"):
                return {}
            rows = [
                dict(r) for r in conn.execute(
                    """
                    SELECT id,part_number,aes_number,name,type,lifecycle_state
                    FROM bom
                    WHERE project_id=? AND represented_part_id IS NULL
                    ORDER BY lower(name),id
                    """,
                    (project_id,),
                ).fetchall()
            ]
        scored = []
        normalized_query = query.lower()
        for row in rows:
            haystacks = [
                str(row.get("name") or "").lower(),
                str(row.get("part_number") or "").lower(),
                str(row.get("aes_number") or "").lower(),
            ]
            score = 0
            if any(h == normalized_query for h in haystacks):
                score = 100
            elif any(normalized_query in h for h in haystacks):
                score = 80
            elif tokens and all(any(token in h for h in haystacks) for token in tokens):
                score = 70
            elif tokens:
                score = sum(1 for token in tokens if any(token in h for h in haystacks)) * 10
            if score:
                scored.append((score, row))
        scored.sort(key=lambda item: (-item[0], str(item[1].get("name") or ""), int(item[1]["id"])))
        if not scored:
            return {"query": query, "not_found": True}
        best_score = scored[0][0]
        best = [row for score, row in scored if score == best_score]
        if len(best) > 1 and best_score < 100:
            return {"query": query, "ambiguous": True, "matches": best[:10]}
        return {"query": query, "item_id": int(best[0]["id"]), "item": best[0], "score": best_score}

    def _expand_item_ids_with_children(self, item_ids: list[int]) -> list[int]:
        project_id = self._project_id()
        wanted = [int(x) for x in item_ids or []]
        if not wanted:
            return []
        with self._conn() as conn:
            children: dict[int, list[int]] = {}
            if self._table_exists(conn, "item_usages"):
                for row in conn.execute(
                    "SELECT parent_item_id,child_item_id FROM item_usages WHERE project_id=?",
                    (project_id,),
                ).fetchall():
                    children.setdefault(int(row["parent_item_id"]), []).append(int(row["child_item_id"]))
            elif self._table_exists(conn, "bom_children"):
                for row in conn.execute(
                    """
                    SELECT bc.parent_id,bc.child_id
                    FROM bom_children bc
                    JOIN bom p ON p.id=bc.parent_id
                    WHERE p.project_id=?
                    """,
                    (project_id,),
                ).fetchall():
                    children.setdefault(int(row["parent_id"]), []).append(int(row["child_id"]))
        ordered = []
        seen = set()

        def visit(item_id: int):
            if item_id in seen:
                return
            seen.add(item_id)
            ordered.append(item_id)
            for child_id in children.get(item_id, []):
                visit(int(child_id))

        for item_id in wanted:
            visit(item_id)
        return ordered

    @staticmethod
    def _ambiguous_item_question(resolution: dict) -> str:
        rows = resolution.get("matches") or []
        lines = [
            f"I found multiple Items matching '{resolution.get('query')}'. Which Item ID should I use?"
        ]
        for row in rows[:10]:
            lines.append(
                f"  {row.get('id')}: {row.get('part_number') or '-'} | "
                f"{row.get('aes_number') or '-'} | {row.get('name') or '-'}"
            )
        return "\n".join(lines)

    @staticmethod
    def _checkout_resolution_analysis(resolution: dict | None) -> list[str]:
        if not resolution:
            return []
        if resolution.get("not_found"):
            return [f"No Item matched '{resolution.get('query')}'. I used checkout diagnostics instead."]
        if resolution.get("item_id"):
            item = resolution.get("item") or {}
            return [
                f"Resolved '{resolution.get('query')}' to Item {resolution.get('item_id')} "
                f"({item.get('name') or '-'}) with score {resolution.get('score')}."
            ]
        return []

    def _compact_diag_summary(self) -> str:
        try:
            lines = self._diag_summary().splitlines()
            attention = [line.strip() for line in lines if line.strip().startswith(("Missing", "Active", "CAD"))]
            return "; ".join(attention[:3]) if attention else lines[0]
        except Exception as exc:
            return f"Diagnostic summary unavailable: {exc}"

    def _try_resume_pending(self, text: str) -> str | None:
        pending = self._pending_clarification
        if not pending:
            return None
        # If the user clearly starts a new command/request, drop the pending state.
        first = str(text or "").strip().split(" ", 1)[0].lower()
        if first in {
            "help", "context", "find", "show", "diag", "diagnose", "assist",
            "ask", "act", "bulk", "item", "associate", "unassociate", "drawing",
            "build", "auto-associate", "fix", "script", "checkout", "checkin",
            "undo", "export",
        }:
            self._pending_clarification = None
            return None

        missing = list(pending.get("missing") or [])
        if not missing:
            self._pending_clarification = None
            return None
        field = missing[0].get("field")
        value = self._extract_answer_value(text, field)
        if not value:
            return (
                "I am still waiting for the missing value.\n"
                f"Question: {missing[0].get('question')}"
            )

        commands = list(pending.get("commands") or [])
        if not commands:
            self._pending_clarification = None
            return None
        option = missing[0].get("option") or f"--{field}"
        commands[0] = f'{commands[0]} {option} "{value}"'
        missing.pop(0)
        if missing:
            pending["commands"] = commands
            pending["missing"] = missing
            self._pending_clarification = pending
            return "\n".join([
                f"Got {field}: {value}",
                "I still need one more detail:",
                f"  {missing[0].get('question')}",
            ])

        self._pending_clarification = None
        lines = [
            f"Got {field}: {value}",
            "Continuing the planned action.",
            "",
        ]
        for command in commands:
            lines.append(f"> {command}")
            try:
                result = self.execute(command)
                if result:
                    lines.append(result)
            except Exception as exc:
                clarification = self._clarification_from_error(
                    command, exc, pending.get("plan") or {}, pending.get("raw") or ""
                )
                if clarification:
                    lines.append(clarification)
                else:
                    lines.append(f"ERROR: {exc}")
                break
        return "\n".join(lines)

    def _clarification_from_error(self, command: str, exc: Exception, plan: dict, raw: str) -> str | None:
        message = str(exc or "")
        lowered = message.lower()
        if "aes number is required" in lowered:
            self._pending_clarification = {
                "plan": plan,
                "raw": raw,
                "commands": [command],
                "missing": [{
                    "field": "aes",
                    "option": "--aes",
                    "question": (
                        "This action needs an AES number before I can continue. "
                        "What AES number should I use?"
                    ),
                }],
            }
            return (
                "I need one detail before I can continue:\n"
                "  This action needs an AES number. What AES number should I use?"
            )
        if "requires --path" in lowered or "path" in lowered and "required" in lowered:
            self._pending_clarification = {
                "plan": plan,
                "raw": raw,
                "commands": [command],
                "missing": [{
                    "field": "path",
                    "option": "--path",
                    "question": "Which file path should I use?",
                }],
            }
            return "I need one detail before I can continue:\n  Which file path should I use?"
        return None

    def _extract_answer_value(self, text: str, field: str | None) -> str:
        if field == "aes":
            return (
                self._extract_named_value(text, ("aes number", "aes", "index aes", "aes index"))
                or self._extract_code_like_value(text)
            )
        if field == "path":
            named = self._extract_named_value(text, ("path", "file", "source path"))
            return named or str(text or "").strip().strip("'\"")
        return str(text or "").strip().strip("'\"")

    @staticmethod
    def _extract_named_value(text: str, labels) -> str:
        source = str(text or "")
        for label in labels:
            pattern = (
                rf"{re.escape(label)}\s*(?:is|=|:)?\s*"
                rf"['\"]?([A-Za-z0-9_.\\/-]+(?:\s+[A-Za-z0-9_.\\/-]+)*)['\"]?"
            )
            match = re.search(pattern, source, flags=re.IGNORECASE)
            if match:
                value = match.group(1).strip().strip(" ,.;'\"")
                # Stop accidental capture at common sentence continuations.
                value = re.split(
                    r"\s+(?:and|with|which|that|this|for|as)\s+",
                    value,
                    maxsplit=1,
                    flags=re.IGNORECASE,
                )[0].strip()
                if value:
                    return value
        return ""

    @staticmethod
    def _extract_code_like_value(text: str) -> str:
        source = str(text or "").strip()
        quoted = re.search(r"['\"]([^'\"]+)['\"]", source)
        if quoted:
            return quoted.group(1).strip()
        code = re.search(r"\b[A-Z]{2,}[A-Z0-9_-]{2,}\b", source, flags=re.IGNORECASE)
        return code.group(0).strip() if code else ""

    def _show(self, args: list[str]) -> str:
        if len(args) != 2:
            raise ValueError("Usage: show item <item_id> | show cad <cad_document_id>")
        kind = args[0].lower()
        obj_id = int(args[1])
        if kind == "item":
            return self._show_item(obj_id)
        if kind == "cad":
            return self._show_cad(obj_id)
        raise ValueError("Usage: show item <item_id> | show cad <cad_document_id>")

    def _show_item(self, item_id: int) -> str:
        part = self.bom_service.bom_repo.get_by_id(int(item_id))
        if not part:
            raise ValueError("Item not found.")
        associations = self.bom_service.list_item_cad_associations(int(item_id))
        lines = [
            f"Item {item_id}",
            f"  Number: {getattr(part, 'part_number', '') or '-'}",
            f"  AES: {getattr(part, 'aes_number', '') or '-'}",
            f"  Name: {getattr(part, 'name', '') or '-'}",
            f"  Type: {getattr(part, 'type', '') or '-'}",
            f"  Lifecycle: {getattr(part, 'lifecycle_state', '') or '-'}",
            f"  Associated CAD: {len(associations)}",
        ]
        for cad in associations[:20]:
            lines.append(
                "    - "
                f"{cad.get('id')} {cad.get('file_name')} "
                f"[{cad.get('association_type') or '-'}]"
            )
        return "\n".join(lines)

    def _show_cad(self, cad_document_id: int) -> str:
        cad = self.bom_service.pdm_service.repo.get_cad_document(int(cad_document_id))
        if not cad:
            raise ValueError("CAD Document not found.")
        associations = self.bom_service.list_cad_item_associations(int(cad_document_id))
        drawings = self.bom_service.pdm_service.repo.list_related_drawings(int(cad_document_id))
        lines = [
            f"CAD Document {cad_document_id}",
            f"  File: {cad.get('file_name') or '-'}",
            f"  Name: {cad.get('name') or '-'}",
            f"  Category: {cad.get('category') or '-'}",
            f"  CAD rev/iter: {cad.get('revision') or '-'}.{cad.get('iteration') or '-'}",
            f"  Lifecycle: {cad.get('lifecycle_state') or '-'}",
            f"  Creo approved file: {cad.get('latest_creo_file_name') or '-'}",
            f"  Checked out by: {cad.get('checked_out_by_username') or cad.get('checked_out_by') or '-'}",
            f"  Item associations: {len(associations)}",
        ]
        for assoc in associations[:20]:
            lines.append(
                "    - "
                f"{assoc.get('item_id')} {assoc.get('item_number') or assoc.get('part_number') or ''} "
                f"{assoc.get('item_name') or ''} [{assoc.get('association_type') or '-'}]"
            )
        if drawings:
            lines.append(f"  Related drawings: {len(drawings)}")
            for drw in drawings[:20]:
                lines.append(f"    - {drw.get('id')} {drw.get('file_name')}")
        return "\n".join(lines)

    def _bulk(self, args: list[str]) -> str:
        if len(args) < 2:
            raise ValueError("Usage: bulk checkout|undo item|cad --items/--cad 1,2,3")
        action = args[0].lower()
        kind = args[1].lower()
        _positional, options = self._parse_options(args[2:])
        actor_id = self._actor_id(options)
        ids = self._ids_from_options(options, "items" if kind in {"item", "items"} else "cad")
        if not ids:
            raise ValueError("Provide IDs with --items or --cad.")
        ok = []
        failed = []
        for obj_id in ids:
            try:
                if action == "checkout" and kind in {"item", "items"}:
                    self.bom_service.checkout_item(
                        obj_id,
                        as_user_id=actor_id,
                        include_owner_cad=bool(options.get("with-cad")),
                    )
                elif action == "checkout" and kind == "cad":
                    self.bom_service.checkout_pdm_cad_document(obj_id, as_user_id=actor_id)
                elif action == "undo" and kind in {"item", "items"}:
                    self.bom_service.undo_item_checkout(obj_id, as_user_id=actor_id)
                elif action == "undo" and kind == "cad":
                    self.bom_service.undo_checkout_pdm_cad_document(
                        obj_id, "CLI bulk undo", as_user_id=actor_id
                    )
                else:
                    raise ValueError("Supported: bulk checkout item|cad, bulk undo item|cad")
                ok.append(obj_id)
            except Exception as exc:
                failed.append({"id": obj_id, "error": str(exc)})
        lines = [
            f"Bulk {action} {kind}",
            f"  Succeeded: {len(ok)} {ok[:40]}",
            f"  Failed: {len(failed)}",
        ]
        for row in failed[:30]:
            lines.append(f"    - {row['id']}: {row['error']}")
        return "\n".join(lines)

    def _item(self, args: list[str]) -> str:
        positional, options = self._parse_options(args)
        if not positional or positional[0].lower() != "create":
            raise ValueError(
                "Usage: item create --name variant-1 --type asm --children all-project"
            )
        with self._conn() as conn:
            if not self._table_exists(conn, "bom"):
                raise ValueError(
                    "Nexus project tables are not initialized for the selected database."
                )
        name = str(options.get("name") or "").strip()
        if not name:
            raise ValueError("Item creation requires --name.")
        item_type = str(options.get("type") or "asm").strip().lower()
        child_mode = str(options.get("children") or "").strip().lower()
        number = str(options.get("number") or options.get("part-number") or "").strip()
        aes = str(options.get("aes") or options.get("aes-number") or "").strip()

        if child_mode and child_mode not in {"all-project", "none"}:
            raise ValueError("Supported --children values: all-project, none.")

        created_id = int(self.bom_service.add_part({
            "name": name,
            "type": item_type,
            "part_number": number,
            "aes_number": aes,
            "status": "Design",
            "created": datetime.now().isoformat(timespec="seconds"),
            "modified": datetime.now().isoformat(timespec="seconds"),
            "classification": options.get("classification") or "PHYSICAL",
            "default_ebom_behavior": options.get("behavior") or "NORMAL",
            "cad_requirement": options.get("cad-requirement") or "OPTIONAL",
            "drawing_requirement": options.get("drawing-requirement") or "OPTIONAL",
            "item_type": options.get("item-type") or "MECHANICAL_PART",
            "assembly_mode": options.get("assembly-mode") or (
                "SEPARABLE" if item_type in {"asm", "assembly"} else "COMPONENT"
            ),
            "procurement_source": options.get("source") or "MAKE",
            "item_view": options.get("view") or "DESIGN",
            "default_unit": options.get("unit") or "EA",
            "notes": options.get("notes") or "Created from Engineer CLI.",
        }))

        added = []
        skipped = []
        if child_mode == "all-project":
            # Structure edits require checkout. The new item is WIP, but using the
            # normal service path keeps future rules and audit behavior aligned.
            self.bom_service.checkout_item(created_id)
            child_ids = self._all_project_child_item_ids(exclude_id=created_id)
            for child_id in child_ids:
                try:
                    self.bom_service.add_manual_item_usage(created_id, child_id, 1)
                    added.append(child_id)
                except Exception as exc:
                    skipped.append({"id": child_id, "error": str(exc)})
            try:
                self.bom_service.checkin_item_data(
                    created_id,
                    f"CLI created {name} and added {len(added)} project Item children.",
                )
            except Exception:
                # It is acceptable to leave the new parent checked out if check-in
                # requires a workflow condition. The command reports the result.
                pass

        lines = [
            f"Created Item {created_id}: {name}",
            f"  Type: {item_type}",
        ]
        if child_mode == "all-project":
            lines.extend([
                f"  Added children: {len(added)}",
                f"  Skipped children: {len(skipped)}",
            ])
            for row in skipped[:25]:
                lines.append(f"    - {row['id']}: {row['error']}")
        return "\n".join(lines)

    def _associate(self, args: list[str]) -> str:
        positional, options = self._parse_options(args)
        if len(positional) != 4 or positional[0].lower() != "item" or positional[2].lower() != "cad":
            raise ValueError("Usage: associate item <item_id> cad <cad_id> --type OWNER|IMAGE")
        item_id = int(positional[1])
        cad_id = int(positional[3])
        association_type = str(options.get("type") or "OWNER").strip().upper()
        self._actor_id(options)
        assoc = self.bom_service.associate_cad_document(
            item_id,
            cad_id,
            association_type,
        )
        drawing_ids = self._ids_from_options(options, "drawings")
        if drawing_ids:
            self.bom_service.set_item_model_drawings(
                item_id,
                cad_id,
                drawing_ids,
                primary_drawing_id=(
                    int(options.get("primary")) if options.get("primary") else drawing_ids[0]
                ),
            )
        return (
            f"Associated Item {item_id} to CAD {cad_id} as {association_type}. "
            f"Association ID: {assoc.get('id') or assoc.get('association_id') or '-'}"
        )

    def _unassociate(self, args: list[str]) -> str:
        if len(args) != 1:
            raise ValueError("Usage: unassociate <association_id>")
        association_id = int(args[0])
        removed = self.bom_service.remove_cad_item_association(association_id)
        return f"Association {association_id} removed." if removed else f"Association {association_id} was not active."

    def _drawing(self, args: list[str]) -> str:
        positional, options = self._parse_options(args)
        if (
            len(positional) != 5
            or positional[0].lower() != "select"
            or positional[1].lower() != "item"
            or positional[3].lower() != "cad"
        ):
            raise ValueError("Usage: drawing select item <item_id> cad <model_cad_id> --drawings 70,71 --primary 70")
        item_id = int(positional[2])
        model_cad_id = int(positional[4])
        drawing_ids = self._ids_from_options(options, "drawings")
        if not drawing_ids:
            raise ValueError("Provide drawing CAD document IDs with --drawings.")
        primary = int(options.get("primary")) if options.get("primary") else drawing_ids[0]
        self.bom_service.set_item_model_drawings(
            item_id,
            model_cad_id,
            drawing_ids,
            primary_drawing_id=primary,
        )
        return f"Selected drawings {drawing_ids} for Item {item_id} / CAD {model_cad_id}; primary={primary}."

    def _build(self, args: list[str]) -> str:
        positional, options = self._parse_options(args)
        if len(positional) != 2 or positional[0].lower() not in {"cad", "item"}:
            raise ValueError("Usage: build item <item_id> | build cad <owner_cad_id> [--single-level]")
        target_kind = positional[0].lower()
        target_id = int(positional[1])
        if target_kind == "item":
            result = self.bom_service.build_item_structure_from_cad(
                target_id, multi_level=not bool(options.get("single-level"))
            )
            label = f"Item {target_id}"
        else:
            owner_rows = self.bom_service.list_cad_item_associations(target_id)
            owner = next(
                (
                    row for row in owner_rows
                    if str(row.get("association_type") or "").upper() == "OWNER"
                ),
                None,
            )
            if not owner:
                raise ValueError("Build requires an OWNER Item for the CAD Document.")
            item_id = int(owner["item_id"])
            result = self.bom_service.build_item_structure_from_cad(
                item_id, multi_level=not bool(options.get("single-level"))
            )
            label = f"CAD {target_id} / Item {item_id}"
        return "\n".join([
            f"Built EBOM structure from {label}.",
            f"  Created: {result.get('created', 0)}",
            f"  Updated: {result.get('updated', 0)}",
            f"  Removed: {result.get('removed', 0)}",
            f"  Excluded: {result.get('excluded', 0)}",
            f"  No related Item: {result.get('no_related_item', 0)}",
            f"  Conflicts: {result.get('conflicts', 0)}",
        ])

    def _auto_associate(self, args: list[str]) -> str:
        _positional, options = self._parse_options(args)
        project_id = self._project_id()
        if options.get("apply"):
            result = self.bom_service.auto_associate_cad_documents()
            return (
                f"Auto-association applied. Associated: {len(result.get('associated') or [])}; "
                f"Unresolved: {len(result.get('unresolved') or [])}"
            )
        proposals = self.bom_service.pdm_service.auto_associate_candidates(project_id)
        rows = []
        for proposal in proposals:
            doc = proposal.get("cad_document") or {}
            matches = proposal.get("matches") or []
            rows.append({
                "cad_id": doc.get("id"),
                "file_name": doc.get("file_name"),
                "status": proposal.get("status"),
                "match_basis": proposal.get("match_basis"),
                "item_id": proposal.get("proposed_item_id") or (
                    matches[0].get("id") if len(matches) == 1 else ""
                ),
            })
        return self._format_rows(rows[:80], ["cad_id", "file_name", "status", "match_basis", "item_id"])

    def _fix(self, args: list[str]) -> str:
        positional, options = self._parse_options(args)
        if positional != ["orphan-associations"]:
            raise ValueError("Usage: fix orphan-associations [--apply]")
        with self._conn() as conn:
            rows = self._orphan_association_rows(conn, limit=100000)
        if not rows:
            return "No orphan associations to fix."
        if not options.get("apply"):
            return "\n".join([
                f"Dry run: {len(rows)} active orphan association(s) found.",
                "Run: fix orphan-associations --apply",
                self._format_rows(rows[:30], ["association_id", "item_id", "cad_document_id", "association_type", "file_name"]),
            ])
        count = self.bom_service.pdm_service.repo.cleanup_orphan_item_associations()
        return f"Deactivated {count} orphan CAD/Item association(s)."

    def _script(self, args: list[str]) -> str:
        _positional, options = self._parse_options(args)
        path = str(options.get("path") or "").strip()
        if not path:
            raise ValueError("Usage: script --path \"C:\\work\\commands.txt\"")
        if not os.path.isfile(path):
            raise ValueError(f"Script file not found: {path}")
        outputs = []
        with open(path, "r", encoding="utf-8-sig") as handle:
            lines = handle.readlines()
        for number, raw in enumerate(lines, start=1):
            command = raw.strip()
            if not command or command.startswith("#"):
                continue
            try:
                outputs.append(f"[{number}] > {command}")
                outputs.append(self.execute(command))
            except Exception as exc:
                outputs.append(f"[{number}] ERROR: {exc}")
                break
        return "\n".join(outputs) if outputs else "Script contained no commands."

    def _checkout(self, args: list[str]) -> str:
        positional, options = self._parse_options(args)
        if len(positional) != 2:
            raise ValueError("Usage: checkout item <id> | checkout cad <id>")
        kind = positional[0].lower()
        obj_id = int(positional[1])
        actor_id = self._actor_id(options)
        if kind == "item":
            self.bom_service.checkout_item(
                obj_id,
                as_user_id=actor_id,
                include_owner_cad=bool(options.get("with-cad")),
            )
            return f"Checked out Item {obj_id}."
        if kind == "cad":
            result = self.bom_service.checkout_pdm_cad_document(obj_id, as_user_id=actor_id)
            items = result.get("associated_item_ids") or []
            drawings = result.get("related_drawing_checkout_ids") or []
            return f"Checked out CAD {obj_id}. Items: {items or '-'} Drawings: {drawings or '-'}"
        raise ValueError("Usage: checkout item <id> | checkout cad <id>")

    def _checkin(self, args: list[str]) -> str:
        positional, options = self._parse_options(args)
        if len(positional) != 2:
            raise ValueError("Usage: checkin item <id> --note text | checkin cad <id> --path path")
        kind = positional[0].lower()
        obj_id = int(positional[1])
        actor_id = self._actor_id(options)
        note = str(options.get("note") or "CLI check-in")
        if kind == "item":
            if actor_id is not None:
                raise ValueError("Item check-in --as is not available in CLI yet.")
            self.bom_service.checkin_item_data(obj_id, note)
            return f"Checked in Item {obj_id}."
        if kind == "cad":
            source_path = str(options.get("path") or "").strip()
            if not source_path:
                raise ValueError("CAD check-in requires --path.")
            result = self.bom_service.checkin_pdm_cad_document(
                obj_id,
                source_path,
                note,
                as_user_id=actor_id,
                source_file_name=os.path.basename(source_path),
            )
            return f"Checked in CAD {obj_id}. CAD version {result.get('revision')}.{result.get('iteration')}."
        raise ValueError("Usage: checkin item <id> | checkin cad <id>")

    def _undo(self, args: list[str]) -> str:
        positional, options = self._parse_options(args)
        if len(positional) != 2:
            raise ValueError("Usage: undo item <id> | undo cad <id>")
        kind = positional[0].lower()
        obj_id = int(positional[1])
        actor_id = self._actor_id(options)
        note = str(options.get("note") or "CLI undo checkout")
        if kind == "item":
            self.bom_service.undo_item_checkout(obj_id, as_user_id=actor_id)
            return f"Undid checkout for Item {obj_id}."
        if kind == "cad":
            result = self.bom_service.undo_checkout_pdm_cad_document(
                obj_id, note, as_user_id=actor_id
            )
            return f"Undid checkout for CAD {obj_id}. Item checkout: {result.get('item_checkout') or '-'}"
        raise ValueError("Usage: undo item <id> | undo cad <id>")

    def _export(self, args: list[str]) -> str:
        positional, options = self._parse_options(args)
        if positional != ["package"]:
            raise ValueError("Usage: export package --items 12,18 --to C:\\Export")
        raw_items = str(options.get("items") or "").strip()
        destination = str(options.get("to") or "").strip()
        if not raw_items or not destination:
            raise ValueError("export package requires --items and --to.")
        item_ids = [int(x.strip()) for x in raw_items.split(",") if x.strip()]
        result = self.package_export_service.export_package_for_parts(
            item_ids,
            destination,
            include_children=not bool(options.get("no-children")),
            create_zip=bool(options.get("zip")),
        )
        package = result.get("package") or {}
        return (
            f"Package exported: {package.get('output_dir') or destination}\n"
            f"Exported: {len(result.get('exported') or [])}  "
            f"Missing: {len(result.get('missing') or [])}  "
            f"Skipped: {len(result.get('skipped') or [])}"
        )

    def _ids_from_options(self, options: dict[str, object], key: str) -> list[int]:
        raw = str(options.get(key) or "").strip()
        if not raw and key == "cad":
            raw = str(options.get("cads") or "").strip()
        if not raw and key == "items":
            raw = str(options.get("item") or "").strip()
        ids = []
        for part in raw.replace(";", ",").split(","):
            text = part.strip()
            if not text:
                continue
            ids.append(int(text))
        return ids

    def _service_registry(self) -> dict[str, object]:
        return {
            "bom": self.bom_service,
            "pdm": self.pdm_service,
            "project": self.project_service,
            "package": self.package_export_service,
            "diag": self.diag_service,
            "snapshot": self.snapshot_service,
            "baseline": self.baseline_service,
            "dashboard": self.dashboard_service,
            "user_repo": self.user_repo,
        }

    def _service_methods(self, service: object) -> dict[str, object]:
        methods = {}
        for name, func in inspect.getmembers(service, predicate=callable):
            if name.startswith("_"):
                continue
            # Hide low-level DB handles and noisy object helpers.
            if name in {"get_conn"}:
                continue
            methods[name] = func
        return dict(sorted(methods.items()))

    def _get_service(self, name: str, registry: dict[str, object]) -> object:
        key = str(name or "").strip()
        if key not in registry:
            raise ValueError(
                f"Unknown service '{key}'. Available: {', '.join(sorted(registry))}"
            )
        return registry[key]

    def _get_service_function(self, service: object, func_name: str):
        name = str(func_name or "").strip()
        if name.startswith("_"):
            raise ValueError("Private service functions cannot be called from CLI.")
        func = getattr(service, name, None)
        if not callable(func):
            raise ValueError(f"Service function not found: {name}")
        return func

    def _describe_function(self, service_name: str, func_name: str, func) -> str:
        doc = inspect.getdoc(func) or ""
        return "\n".join([
            f"{service_name}.{func_name}{inspect.signature(func)}",
            f"Risk: {self._service_call_risk(func_name)}",
            "",
            doc or "No docstring.",
            "",
            "Call example:",
            f"  service call {service_name}.{func_name} --args \"[]\" --kwargs \"{{}}\" --confirm",
        ])

    @staticmethod
    def _service_call_risk(func_name: str) -> str:
        name = str(func_name or "").lower()
        mutating_prefixes = (
            "add", "assign", "associate", "auto_associate", "build", "checkin",
            "checkout", "clear", "create", "delete", "duplicate", "export",
            "freeze", "import", "insert", "materialize", "move", "register",
            "release", "remove", "rename", "reorder", "reset", "revise",
            "set", "sync", "undo", "unregister", "update", "upsert",
        )
        return "write" if name.startswith(mutating_prefixes) else "read"

    def _parse_json_option(self, value, default):
        if value is None or str(value).strip() == "":
            return default
        text = str(value).strip()
        try:
            return json.loads(text)
        except Exception as exc:
            raise ValueError(f"Invalid JSON option: {text}") from exc

    def _format_value(self, value) -> str:
        if value is None:
            return "None"
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, indent=2, default=str, ensure_ascii=False)
        except Exception:
            return str(value)

    def _require_admin(self, action: str) -> None:
        if not self._is_admin_user():
            raise PermissionError(f"Only admins can {action}.")

    def _is_admin_user(self) -> bool:
        user_id = getattr(self.session, "user_id", None)
        if not user_id:
            return False
        if bool(getattr(self.session, "is_admin", False)):
            return True
        try:
            user = self.user_repo.find_by_id(int(user_id))
            if user and int(getattr(user, "is_admin", 0) or 0) == 1:
                return True
        except Exception:
            pass
        try:
            return bool(
                self.permission_repo.user_has_permission(
                    int(user_id), "admin_panel", getattr(self.session, "project_id", None)
                )
            )
        except Exception:
            return False

    @staticmethod
    def _safe_table_name(table_name: str) -> str:
        name = str(table_name or "").strip()
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
            raise ValueError(f"Unsafe table name: {table_name}")
        return name

    @staticmethod
    def _quote_ident(identifier: str) -> str:
        name = str(identifier or "")
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
            raise ValueError(f"Unsafe identifier: {identifier}")
        return '"' + name.replace('"', '""') + '"'

    def _all_project_child_item_ids(self, exclude_id: int | None = None) -> list[int]:
        project_id = self._project_id()
        excluded = int(exclude_id) if exclude_id is not None else None
        with self._conn() as conn:
            if not self._table_exists(conn, "bom"):
                return []
            rows = conn.execute(
                """
                SELECT id
                FROM bom
                WHERE project_id=?
                  AND represented_part_id IS NULL
                  AND (? IS NULL OR id<>?)
                ORDER BY lower(COALESCE(part_number,'')), lower(name), id
                """,
                (project_id, excluded, excluded),
            ).fetchall()
        return [int(row["id"]) for row in rows]

    def _table_exists(self, conn, table_name: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (str(table_name),),
        ).fetchone()
        return bool(row)

    def _table_columns(self, conn, table_name: str) -> set[str]:
        if not self._table_exists(conn, table_name):
            return set()
        return {
            str(row[1])
            for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }

    def _scalar(self, conn, sql: str, *params) -> int:
        try:
            row = conn.execute(sql, params).fetchone()
            return int(row[0] or 0) if row else 0
        except Exception:
            return 0

    def _orphan_association_rows(self, conn, limit: int = 80) -> list[dict]:
        project_id = self._project_id()
        if not self._table_exists(conn, "cad_item_associations"):
            return []
        return [
            dict(r) for r in conn.execute(
                """
                SELECT a.id AS association_id,a.item_id,a.cad_document_id,
                       a.association_type,d.file_name
                FROM cad_item_associations a
                LEFT JOIN bom b ON b.id=a.item_id
                LEFT JOIN cad_documents d ON d.id=a.cad_document_id
                WHERE a.project_id=? AND a.active=1 AND b.id IS NULL
                ORDER BY a.id
                LIMIT ?
                """,
                (project_id, int(limit)),
            ).fetchall()
        ]

    def _cad_without_owner_rows(self, conn, limit: int = 80) -> list[dict]:
        project_id = self._project_id()
        if not self._table_exists(conn, "cad_documents"):
            return []
        return [
            dict(r) for r in conn.execute(
                """
                SELECT d.id AS cad_id,d.file_name,d.category
                FROM cad_documents d
                WHERE d.project_id=?
                  AND upper(COALESCE(d.category,''))<>'DRAWING'
                  AND NOT EXISTS (
                    SELECT 1 FROM cad_item_associations a
                    JOIN bom b ON b.id=a.item_id
                    WHERE a.cad_document_id=d.id
                      AND a.active=1
                      AND upper(a.association_type)='OWNER'
                  )
                ORDER BY lower(d.file_name),d.id
                LIMIT ?
                """,
                (project_id, int(limit)),
            ).fetchall()
        ]

    def _missing_doc_rows(self, conn, limit: int = 80) -> list[dict]:
        project_id = self._project_id()
        if not self._table_exists(conn, "bom"):
            return []
        rows = [
            dict(r) for r in conn.execute(
                """
                SELECT b.id AS item_id,b.part_number,b.aes_number,b.name,
                       upper(COALESCE(b.classification,'PHYSICAL')) AS classification,
                       upper(COALESCE(b.drawing_requirement,'OPTIONAL')) AS drawing_requirement,
                       upper(COALESCE(b.default_ebom_behavior,'NORMAL')) AS behavior
                FROM bom b
                WHERE b.project_id=?
                  AND b.represented_part_id IS NULL
                  AND upper(COALESCE(b.classification,'PHYSICAL'))='PHYSICAL'
                  AND upper(COALESCE(b.default_ebom_behavior,'NORMAL')) NOT IN
                      ('NON_DELIVERABLE','REFERENCE_ONLY','CAD_ONLY','SUPPLIER_PACKAGE')
                  AND (
                    upper(COALESCE(b.drawing_requirement,'OPTIONAL'))='REQUIRED'
                    OR upper(COALESCE(b.cad_requirement,'OPTIONAL'))='REQUIRED'
                  )
                ORDER BY lower(b.name),b.id
                LIMIT ?
                """,
                (project_id, int(limit)),
            ).fetchall()
        ]
        if not rows:
            return []
        item_ids = [int(row["item_id"]) for row in rows]
        available: dict[int, set[str]] = {item_id: set() for item_id in item_ids}
        if self._table_exists(conn, "part_files"):
            placeholders = ",".join("?" for _ in item_ids)
            for row in conn.execute(
                f"""
                SELECT pf.part_id,upper(pf.file_type) AS file_type
                FROM part_files pf
                JOIN part_file_versions v ON v.id=pf.active_version_id
                WHERE pf.deleted_at IS NULL
                  AND v.deleted_at IS NULL
                  AND pf.part_id IN ({placeholders})
                  AND upper(pf.file_type) IN ('PDF','STEP','STP')
                """,
                item_ids,
            ).fetchall():
                ftype = "STEP" if str(row["file_type"]).upper() == "STP" else str(row["file_type"]).upper()
                available[int(row["part_id"])].add(ftype)
        missing = []
        for row in rows:
            item_id = int(row["item_id"])
            absent = []
            if "PDF" not in available.get(item_id, set()):
                absent.append("PDF")
            if "STEP" not in available.get(item_id, set()):
                absent.append("STEP")
            if absent:
                missing.append({**row, "missing": ",".join(absent)})
        return missing

    def _format_rows(self, rows: list[dict], columns: list[str]) -> str:
        if not rows:
            return "No results."
        widths = {
            col: max(len(col), *(len(str(row.get(col, "") or "")) for row in rows))
            for col in columns
        }
        header = "  ".join(col.ljust(widths[col]) for col in columns)
        sep = "  ".join("-" * widths[col] for col in columns)
        body = [
            "  ".join(str(row.get(col, "") or "").ljust(widths[col]) for col in columns)
            for row in rows
        ]
        return "\n".join([header, sep, *body])
