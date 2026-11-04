import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from core.repositories.bom_repository import BomRepository
from core.services.pdm_service import PdmService


class EbomRootOrderTests(unittest.TestCase):
    def test_refresh_and_reorder_share_the_same_visible_roots_and_order(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / 'root-order.db'
            with sqlite3.connect(database) as conn:
                conn.executescript('''
                    CREATE TABLE bom (id INTEGER PRIMARY KEY,project_id INTEGER,sort_order INTEGER,
                        represented_part_id INTEGER,deleted_at TEXT,status TEXT,lifecycle_state TEXT,
                        revision TEXT,modified TEXT);
                    CREATE TABLE item_usages (id INTEGER PRIMARY KEY,project_id INTEGER,
                        parent_item_id INTEGER,child_item_id INTEGER,quantity INTEGER,source TEXT,sort_order INTEGER);
                    INSERT INTO bom VALUES (1,7,30,NULL,NULL,'Design','Design','A',NULL);
                    INSERT INTO bom VALUES (2,7,10,NULL,NULL,'Design','Design','A',NULL);
                    INSERT INTO bom VALUES (3,7,20,NULL,NULL,'Design','Design','A',NULL);
                    INSERT INTO bom VALUES (4,7,5,1,NULL,'Design','Design','A',NULL);
                    INSERT INTO bom VALUES (5,7,40,NULL,NULL,'Deleted','Deleted','A',NULL);
                    INSERT INTO bom VALUES (6,7,50,NULL,NULL,'Design','Design','A',NULL);
                    INSERT INTO item_usages VALUES (1,7,5,6,1,'MANUAL',10);
                ''')
            conn.close()
            @contextmanager
            def connection():
                conn = sqlite3.connect(database)
                conn.row_factory = sqlite3.Row
                try:
                    with conn:
                        yield conn
                finally:
                    conn.close()
            repository = object.__new__(BomRepository)
            repository.get_conn = connection
            service = object.__new__(PdmService)
            service.repo = SimpleNamespace(get_conn=connection)
            def page_ids():
                return [row['id'] for row in service.get_item_structure_level(7, limit=100)['roots']]
            self.assertEqual(page_ids(), [2, 3, 1, 6])
            self.assertEqual(repository.ordered_root_item_ids(7), page_ids())
            repository.set_root_item_order(7, [6, 1, 3, 2])
            self.assertEqual(page_ids(), [6, 1, 3, 2])
            self.assertEqual(repository.ordered_root_item_ids(7), page_ids())
            self.assertEqual(page_ids(), [6, 1, 3, 2])

if __name__ == '__main__':
    unittest.main()
