import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.repositories.commit_repository import CommitRepository


class StepBaselineSelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = CommitRepository.__new__(CommitRepository)
        self.repo.db_name = str(self.root / 'test.db')
        self.repo._row_to_commit = lambda row: SimpleNamespace(**dict(row))
        with self.repo.get_conn() as conn:
            conn.executescript('''
                CREATE TABLE projects (id INTEGER PRIMARY KEY, root_project_id INTEGER);
                INSERT INTO projects VALUES (1, 1), (2, 1), (3, 3);
                CREATE TABLE commits (
                    id INTEGER PRIMARY KEY, project_id INTEGER, part_id INTEGER,
                    base_file_name TEXT, nexus_branch_id TEXT, status TEXT,
                    commit_id TEXT, step_file_path TEXT, committed_at TEXT, merged_at TEXT
                );
            ''')

    def add(self, ident, *, project=2, part=805, branch='branch-a', status='Approved', exists=True, name='case'):
        path = self.root / f'{ident}.step'
        if exists:
            path.write_text('STEP')
        with self.repo.get_conn() as conn:
            conn.execute('INSERT INTO commits VALUES (?,?,?,?,?,?,?,?,?,?)',
                         (ident, project, part, name, branch, status, str(ident), str(path),
                          f'2026-01-{ident:02}', f'2026-01-{ident:02}'))

    def find(self, branch='branch-a'):
        return self.repo.find_approved_step_baseline(805, 2, 'case', branch, 'new')

    def test_skips_reverted_pending_other_branch_and_missing(self):
        self.add(1)
        self.add(2, status='Reverted')
        self.add(3, status='Pending')
        self.add(4, branch='branch-b')
        self.add(5, exists=False)
        self.assertEqual(self.find().commit_id, '1')

    def test_project_family_fallback_is_limited_to_same_branch(self):
        self.add(1, project=1, part=447)
        self.add(2, project=1, part=447, branch='branch-b')
        self.add(3, project=3, part=447)
        self.assertEqual(self.find().commit_id, '1')

    def test_master_does_not_select_a_branch(self):
        self.add(1, branch=None)
        self.add(2)
        self.assertEqual(self.find(None).commit_id, '1')

    def test_no_approved_step_returns_none(self):
        self.add(1, status='Reverted')
        self.assertIsNone(self.find())


if __name__ == '__main__':
    unittest.main()
