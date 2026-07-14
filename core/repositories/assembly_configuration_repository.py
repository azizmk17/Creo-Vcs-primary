import sqlite3

from config import DB_NAME


class AssemblyConfigurationRepository:
    """Persistence for versioned assembly configuration manifests."""

    _MEMBER_COLUMNS = (
        "sequence_no", "occurrence_path", "parent_occurrence_path", "usage_id",
        "bom_id", "revision_id", "iteration_id", "version_label", "quantity",
        "position", "sort_order", "type", "name", "aes_number", "part_number",
        "drawing_number", "filename", "drawing", "native_source_rel_path",
        "drawing_source_rel_path", "native_frozen_rel_path",
        "drawing_frozen_rel_path", "native_sha256", "drawing_sha256",
    )

    _VERSION_COLUMNS = (
        ("series_key", "series_key TEXT NOT NULL DEFAULT ''"),
        ("configuration_name", "configuration_name TEXT NOT NULL DEFAULT ''"),
        ("version_number", "version_number INTEGER NOT NULL DEFAULT 1"),
        ("based_on_configuration_id", "based_on_configuration_id INTEGER"),
        ("frozen_at", "frozen_at TEXT"),
        ("frozen_by", "frozen_by INTEGER"),
        ("draft_updated_at", "draft_updated_at TEXT"),
    )

    def __init__(self, db_name: str = DB_NAME):
        self.db_name = db_name
        self._ensure_schema()

    def get_conn(self):
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _columns(conn, table_name: str) -> set[str]:
        return {
            str(row[1])
            for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }

    def _ensure_schema(self) -> None:
        with self.get_conn() as conn:
            tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "projects" not in tables or "bom" not in tables:
                return
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS assembly_configurations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL,
                    name TEXT NOT NULL COLLATE NOCASE,
                    series_key TEXT NOT NULL DEFAULT '',
                    configuration_name TEXT NOT NULL DEFAULT '',
                    version_number INTEGER NOT NULL DEFAULT 1,
                    based_on_configuration_id INTEGER,
                    purpose TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    root_bom_id INTEGER NOT NULL,
                    root_iteration_id INTEGER NOT NULL,
                    root_version_label TEXT NOT NULL,
                    root_name TEXT NOT NULL DEFAULT '',
                    source_project_version TEXT NOT NULL DEFAULT '',
                    storage_rel_path TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL DEFAULT 'Draft',
                    member_count INTEGER NOT NULL DEFAULT 0,
                    file_count INTEGER NOT NULL DEFAULT 0,
                    created_by INTEGER,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    draft_updated_at TEXT,
                    frozen_at TEXT,
                    frozen_by INTEGER,
                    last_built_at TEXT,
                    last_built_path TEXT,
                    UNIQUE(project_id, name)
                );
                CREATE TABLE IF NOT EXISTS assembly_configuration_members (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    configuration_id INTEGER NOT NULL,
                    sequence_no INTEGER NOT NULL,
                    occurrence_path TEXT NOT NULL,
                    parent_occurrence_path TEXT,
                    usage_id INTEGER,
                    bom_id INTEGER NOT NULL,
                    revision_id INTEGER NOT NULL,
                    iteration_id INTEGER NOT NULL,
                    version_label TEXT NOT NULL,
                    quantity INTEGER NOT NULL DEFAULT 1,
                    position INTEGER NOT NULL DEFAULT 0,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    type TEXT NOT NULL DEFAULT '',
                    name TEXT NOT NULL DEFAULT '',
                    aes_number TEXT NOT NULL DEFAULT '',
                    part_number TEXT NOT NULL DEFAULT '',
                    drawing_number TEXT NOT NULL DEFAULT '',
                    filename TEXT NOT NULL DEFAULT '',
                    drawing TEXT NOT NULL DEFAULT '',
                    native_source_rel_path TEXT NOT NULL DEFAULT '',
                    drawing_source_rel_path TEXT NOT NULL DEFAULT '',
                    native_frozen_rel_path TEXT NOT NULL DEFAULT '',
                    drawing_frozen_rel_path TEXT NOT NULL DEFAULT '',
                    native_sha256 TEXT NOT NULL DEFAULT '',
                    drawing_sha256 TEXT NOT NULL DEFAULT '',
                    UNIQUE(configuration_id, occurrence_path)
                );
                """
            )
            configuration_columns = self._columns(conn, "assembly_configurations")
            for column, definition in self._VERSION_COLUMNS:
                if column not in configuration_columns:
                    conn.execute(
                        f"ALTER TABLE assembly_configurations ADD COLUMN {definition}"
                    )
            member_columns = self._columns(conn, "assembly_configuration_members")
            for column in ("native_source_rel_path", "drawing_source_rel_path"):
                if column not in member_columns:
                    conn.execute(
                        f"ALTER TABLE assembly_configuration_members "
                        f"ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
                    )
            conn.execute(
                """
                UPDATE assembly_configurations
                SET series_key='legacy:' || id
                WHERE trim(COALESCE(series_key,''))=''
                """
            )
            conn.execute(
                """
                UPDATE assembly_configurations
                SET configuration_name=name
                WHERE trim(COALESCE(configuration_name,''))=''
                """
            )
            conn.execute(
                """
                UPDATE assembly_configurations
                SET version_number=1
                WHERE version_number IS NULL OR version_number < 1
                """
            )
            conn.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_assembly_configurations_project
                    ON assembly_configurations(project_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_assembly_configurations_series
                    ON assembly_configurations(project_id, series_key, version_number DESC);
                CREATE INDEX IF NOT EXISTS idx_assembly_configuration_members_config
                    ON assembly_configuration_members(configuration_id, sequence_no);
                CREATE INDEX IF NOT EXISTS idx_assembly_configuration_members_iteration
                    ON assembly_configuration_members(iteration_id);
                """
            )

    def _insert_members_conn(
        self, conn, configuration_id: int, members: list[dict]
    ) -> None:
        if not members:
            return
        placeholders = ",".join("?" for _ in self._MEMBER_COLUMNS)
        columns = ",".join(self._MEMBER_COLUMNS)
        conn.executemany(
            f"""
            INSERT INTO assembly_configuration_members(
                configuration_id,{columns}
            ) VALUES(?,{placeholders})
            """,
            [
                (
                    int(configuration_id),
                    *(member.get(column) for column in self._MEMBER_COLUMNS),
                )
                for member in members
            ],
        )

    @staticmethod
    def _row_dict(row) -> dict:
        result = dict(row) if row else {}
        if result:
            result["display_name"] = (
                result.get("configuration_name") or result.get("name") or ""
            )
        return result

    def create_configuration(self, header: dict, members: list[dict]) -> int:
        with self.get_conn() as conn:
            duplicate = conn.execute(
                """
                SELECT id FROM assembly_configurations
                WHERE project_id=? AND configuration_name=? COLLATE NOCASE
                """,
                (int(header["project_id"]), str(header["configuration_name"])),
            ).fetchone()
            if duplicate:
                raise ValueError(
                    f"A configuration named '{header['configuration_name']}' already exists in this project."
                )
            cursor = conn.execute(
                """
                INSERT INTO assembly_configurations(
                    project_id, name, series_key, configuration_name, version_number,
                    based_on_configuration_id, purpose, description, root_bom_id,
                    root_iteration_id, root_version_label, root_name,
                    source_project_version, storage_rel_path, state,
                    member_count, file_count, created_by, draft_updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
                """,
                (
                    int(header["project_id"]), str(header["name"]),
                    str(header["series_key"]), str(header["configuration_name"]),
                    int(header.get("version_number") or 1),
                    header.get("based_on_configuration_id"),
                    str(header.get("purpose") or ""),
                    str(header.get("description") or ""),
                    int(header["root_bom_id"]), int(header["root_iteration_id"]),
                    str(header.get("root_version_label") or ""),
                    str(header.get("root_name") or ""),
                    str(header.get("source_project_version") or ""), "", "Draft",
                    len(members), 0, header.get("created_by"),
                ),
            )
            configuration_id = int(cursor.lastrowid)
            self._insert_members_conn(conn, configuration_id, members)
            return configuration_id

    def list_for_project(self, project_id: int) -> list[dict]:
        with self.get_conn() as conn:
            rows = conn.execute(
                """
                SELECT c.*, creator.username AS created_by_name,
                       freezer.username AS frozen_by_name
                FROM assembly_configurations c
                LEFT JOIN users creator ON creator.id=c.created_by
                LEFT JOIN users freezer ON freezer.id=c.frozen_by
                WHERE c.project_id=?
                ORDER BY lower(COALESCE(NULLIF(c.configuration_name,''),c.name)),
                         c.version_number DESC, c.id DESC
                """,
                (int(project_id),),
            ).fetchall()
            return [self._row_dict(row) for row in rows]

    def get_configuration(self, configuration_id: int, project_id=None) -> dict:
        sql = """
            SELECT c.*, creator.username AS created_by_name,
                   freezer.username AS frozen_by_name
            FROM assembly_configurations c
            LEFT JOIN users creator ON creator.id=c.created_by
            LEFT JOIN users freezer ON freezer.id=c.frozen_by
            WHERE c.id=?
        """
        params = [int(configuration_id)]
        if project_id is not None:
            sql += " AND c.project_id=?"
            params.append(int(project_id))
        with self.get_conn() as conn:
            row = conn.execute(sql, params).fetchone()
            return self._row_dict(row)

    def list_members(self, configuration_id: int) -> list[dict]:
        with self.get_conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM assembly_configuration_members
                WHERE configuration_id=?
                ORDER BY sequence_no, id
                """,
                (int(configuration_id),),
            ).fetchall()
            return [dict(row) for row in rows]

    def save_draft(
        self, configuration_id: int, project_id: int, header: dict,
        members: list[dict]
    ) -> None:
        with self.get_conn() as conn:
            row = conn.execute(
                "SELECT state FROM assembly_configurations WHERE id=? AND project_id=?",
                (int(configuration_id), int(project_id)),
            ).fetchone()
            if not row:
                raise ValueError("Configuration was not found in the active project.")
            if str(row["state"] or "").strip().lower() != "draft":
                raise ValueError("Frozen configuration versions cannot be edited.")
            conn.execute(
                """
                UPDATE assembly_configurations
                SET purpose=?, description=?, root_bom_id=?, root_iteration_id=?,
                    root_version_label=?, root_name=?, member_count=?, file_count=0,
                    draft_updated_at=datetime('now')
                WHERE id=?
                """,
                (
                    str(header.get("purpose") or ""),
                    str(header.get("description") or ""),
                    int(header["root_bom_id"]), int(header["root_iteration_id"]),
                    str(header.get("root_version_label") or ""),
                    str(header.get("root_name") or ""), len(members),
                    int(configuration_id),
                ),
            )
            conn.execute(
                "DELETE FROM assembly_configuration_members WHERE configuration_id=?",
                (int(configuration_id),),
            )
            self._insert_members_conn(conn, int(configuration_id), members)

    def freeze_draft(
        self, configuration_id: int, project_id: int, members: list[dict],
        file_count: int, frozen_by=None
    ) -> None:
        with self.get_conn() as conn:
            row = conn.execute(
                "SELECT state FROM assembly_configurations WHERE id=? AND project_id=?",
                (int(configuration_id), int(project_id)),
            ).fetchone()
            if not row:
                raise ValueError("Configuration was not found in the active project.")
            if str(row["state"] or "").strip().lower() != "draft":
                raise ValueError("Only a Draft configuration version can be frozen.")
            conn.execute(
                "DELETE FROM assembly_configuration_members WHERE configuration_id=?",
                (int(configuration_id),),
            )
            self._insert_members_conn(conn, int(configuration_id), members)
            conn.execute(
                """
                UPDATE assembly_configurations
                SET state='Frozen', member_count=?, file_count=?, frozen_at=datetime('now'),
                    frozen_by=?, draft_updated_at=datetime('now')
                WHERE id=?
                """,
                (len(members), int(file_count), frozen_by, int(configuration_id)),
            )

    def create_next_version(
        self, source_configuration_id: int, project_id: int, created_by=None
    ) -> int:
        with self.get_conn() as conn:
            source = conn.execute(
                "SELECT * FROM assembly_configurations WHERE id=? AND project_id=?",
                (int(source_configuration_id), int(project_id)),
            ).fetchone()
            if not source:
                raise ValueError("Configuration was not found in the active project.")
            if str(source["state"] or "").strip().lower() != "frozen":
                raise ValueError("Freeze the current configuration version first.")
            series_key = str(source["series_key"] or f"legacy:{source['id']}")
            latest = conn.execute(
                """
                SELECT id, version_number, state
                FROM assembly_configurations
                WHERE project_id=? AND series_key=?
                ORDER BY version_number DESC, id DESC LIMIT 1
                """,
                (int(project_id), series_key),
            ).fetchone()
            if not latest or int(latest["id"]) != int(source_configuration_id):
                raise ValueError(
                    "Create the next version from the latest configuration version."
                )
            version_number = int(latest["version_number"] or 0) + 1
            configuration_name = str(
                source["configuration_name"] or source["name"] or "Configuration"
            )
            internal_name = (
                f"{configuration_name} [Nexus {series_key[-8:]} v{version_number}]"
            )
            cursor = conn.execute(
                """
                INSERT INTO assembly_configurations(
                    project_id, name, series_key, configuration_name, version_number,
                    based_on_configuration_id, purpose, description, root_bom_id,
                    root_iteration_id, root_version_label, root_name,
                    source_project_version, storage_rel_path, state, member_count,
                    file_count, created_by, draft_updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'','Draft',?,0,?,datetime('now'))
                """,
                (
                    int(project_id), internal_name, series_key, configuration_name,
                    version_number, int(source_configuration_id),
                    str(source["purpose"] or ""), str(source["description"] or ""),
                    int(source["root_bom_id"]), int(source["root_iteration_id"]),
                    str(source["root_version_label"] or ""),
                    str(source["root_name"] or ""),
                    str(source["source_project_version"] or ""),
                    int(source["member_count"] or 0), created_by,
                ),
            )
            configuration_id = int(cursor.lastrowid)
            members = [dict(row) for row in conn.execute(
                """
                SELECT * FROM assembly_configuration_members
                WHERE configuration_id=? ORDER BY sequence_no,id
                """,
                (int(source_configuration_id),),
            ).fetchall()]
            for member in members:
                for field in (
                    "native_source_rel_path", "drawing_source_rel_path",
                    "native_frozen_rel_path", "drawing_frozen_rel_path",
                    "native_sha256", "drawing_sha256",
                ):
                    member[field] = ""
            self._insert_members_conn(conn, configuration_id, members)
            return configuration_id

    def mark_built(self, configuration_id: int, build_path: str) -> None:
        with self.get_conn() as conn:
            conn.execute(
                """
                UPDATE assembly_configurations
                SET last_built_at=datetime('now'), last_built_path=?
                WHERE id=? AND state='Frozen'
                """,
                (str(build_path), int(configuration_id)),
            )

    def delete_configuration(self, configuration_id: int, project_id: int) -> bool:
        with self.get_conn() as conn:
            row = conn.execute(
                "SELECT state FROM assembly_configurations WHERE id=? AND project_id=?",
                (int(configuration_id), int(project_id)),
            ).fetchone()
            if not row:
                return False
            if str(row["state"] or "").strip().lower() != "draft":
                raise ValueError("Frozen configuration versions cannot be deleted.")
            conn.execute(
                "DELETE FROM assembly_configuration_members WHERE configuration_id=?",
                (int(configuration_id),),
            )
            conn.execute(
                "DELETE FROM assembly_configurations WHERE id=?",
                (int(configuration_id),),
            )
            return True
