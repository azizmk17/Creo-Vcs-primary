import json
import os
import sqlite3
import tempfile
import unittest

from core.services.ebom_service import EbomResolver
from setup.migrations import _migration_22, _migration_29, _migration_30, _migration_31


class EbomMigrationTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            conn.executescript(
                """
                CREATE TABLE bom (
                    id INTEGER PRIMARY KEY, type TEXT, name TEXT,
                    aes_number TEXT, part_number TEXT, drawing_number TEXT,
                    filename TEXT, drawing TEXT, base_file_name TEXT,
                    base_drw_name TEXT, material TEXT, weight TEXT, notes TEXT,
                    pdf_path TEXT, step_path TEXT, revision TEXT DEFAULT 'A',
                    lifecycle_state TEXT DEFAULT 'WIP', status TEXT DEFAULT 'Design',
                    modified TEXT, project_id INTEGER
                );
                CREATE TABLE bom_children (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, parent_id INTEGER,
                    child_id INTEGER, quantity INTEGER DEFAULT 1,
                    sort_order INTEGER DEFAULT 0
                );
                CREATE TABLE baseline_files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT
                );
                INSERT INTO bom(
                    id,type,name,aes_number,revision,lifecycle_state,status,project_id
                ) VALUES
                    (1,'asm','Top','TOP','A','WIP','Design',10),
                    (2,'asm','Subassembly','SUB','A','WIP','Design',10),
                    (3,'prt','Bolt','BLT','A','WIP','Design',10);
                INSERT INTO bom_children(parent_id,child_id,quantity,sort_order)
                VALUES(1,2,2,10),(2,3,3,10);
                """
            )
            _migration_22(conn)

    def tearDown(self):
        try:
            os.remove(self.db_path)
        except OSError:
            pass

    def test_safe_existing_database_migration_preserves_and_backfills(self):
        with sqlite3.connect(self.db_path) as conn:
            before_bom = conn.execute("SELECT COUNT(*) FROM bom").fetchone()[0]
            before_children = conn.execute(
                "SELECT id,parent_id,child_id,quantity,sort_order FROM bom_children ORDER BY id"
            ).fetchall()
            before_iterations = conn.execute(
                "SELECT COUNT(*) FROM bom_iterations"
            ).fetchone()[0]
            _migration_29(conn)
            _migration_29(conn)
            _migration_30(conn)
            _migration_30(conn)
            _migration_31(conn)
            _migration_31(conn)

            self.assertEqual(conn.execute("SELECT COUNT(*) FROM bom").fetchone()[0], before_bom)
            self.assertEqual(
                conn.execute(
                    "SELECT id,parent_id,child_id,quantity,sort_order FROM bom_children ORDER BY id"
                ).fetchall(),
                before_children,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM bom_iterations").fetchone()[0],
                before_iterations,
            )
            policies = conn.execute(
                """
                SELECT DISTINCT classification, default_ebom_behavior,
                                cad_requirement, drawing_requirement
                FROM bom
                """
            ).fetchall()
            self.assertEqual(policies, [("PHYSICAL", "NORMAL", "OPTIONAL", "OPTIONAL")])
            self.assertEqual(
                conn.execute(
                    "SELECT DISTINCT ebom_behavior FROM bom_children"
                ).fetchall(),
                [("INHERIT",)],
            )
            self.assertEqual(
                conn.execute(
                    "SELECT DISTINCT ebom_behavior FROM bom_iteration_bindings"
                ).fetchall(),
                [("INHERIT",)],
            )
            snapshots = [
                json.loads(row[0])
                for row in conn.execute(
                    "SELECT object_data_json FROM bom_iterations ORDER BY id"
                )
            ]
            self.assertTrue(all(row["classification"] == "PHYSICAL" for row in snapshots))
            self.assertTrue(all(row["default_ebom_behavior"] == "NORMAL" for row in snapshots))
            columns = {row[1] for row in conn.execute("PRAGMA table_info(bom)")}
            self.assertIn("represented_part_id", columns)
            self.assertIn("cad_control_mode", columns)
            self.assertIn(
                "bom_cad_dependencies",
                {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")},
            )
            self.assertTrue(
                all(row.get("cad_control_mode") == "CONTROLLED" for row in snapshots)
            )

    def test_existing_bom_resolves_identically_after_migration(self):
        with sqlite3.connect(self.db_path) as conn:
            _migration_29(conn)
            root_iteration_id = conn.execute(
                "SELECT current_iteration_id FROM bom WHERE id=1"
            ).fetchone()[0]

        resolved = EbomResolver(self.db_path).resolve_iteration(root_iteration_id)

        self.assertEqual([row["bom_id"] for row in resolved["rows"]], [2, 3])
        self.assertEqual(resolved["root"]["children"][0]["bom_id"], 2)
        self.assertEqual(
            resolved["root"]["children"][0]["children"][0]["bom_id"], 3
        )
        self.assertEqual(
            [row["resolved_ebom_behavior"] for row in resolved["rows"]],
            ["NORMAL", "NORMAL"],
        )


if __name__ == "__main__":
    unittest.main()
