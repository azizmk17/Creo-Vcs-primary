import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.services.diag_service import DiagService
from core.services.master_work_service import MasterWorkService
from core.repositories.commit_repository import CommitRepository


class WorkingDirectoryDiagnosticTests(unittest.TestCase):
    def test_commit_lookup_includes_rows_without_a_user_and_excludes_other_projects(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'commits.db'
            with sqlite3.connect(database) as connection:
                connection.executescript('''
                    CREATE TABLE commits(project_id INTEGER,filename TEXT,base_file_name TEXT,
                        approved_version INTEGER,status TEXT,commit_id TEXT,nexus_branch_id TEXT);
                    INSERT INTO commits VALUES(1,'a.prt.1','a.prt',2,'Approved','ok',NULL);
                    INSERT INTO commits VALUES(1,'b.prt.1','b.prt',NULL,'Rejected','no',NULL);
                    INSERT INTO commits VALUES(2,'c.prt.1','c.prt',NULL,'Pending','other',NULL);
                ''')
            connection.close()
            repository = object.__new__(CommitRepository)
            repository.db_name = str(database)
            records = repository.get_working_directory_commit_records(1)
            self.assertEqual([row['commit_id'] for row in records], ['ok'])

    def test_exact_root_files_are_compared_with_snapshots_and_commits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('saved.PRT.1', 'OLD.drw.2', 'approved.prt.7', 'pending.asm',
                         'published.prt.4', 'registered.prt.99', 'branch.prt.2', 'unknown.DRW', 'ignore.pdf'):
                (root / name).write_bytes(b'x')
            (root / 'folder.prt.9').mkdir()
            manifest = MasterWorkService.manifest_path(root)
            manifest.parent.mkdir()
            manifest.write_text(json.dumps({'files': {'published.prt': {
                'path': 'published.prt.4', 'source_commit_id': 'push'}}}), encoding='utf-8')
            service = object.__new__(DiagService)
            service.session = SimpleNamespace(project_id=1)
            service.snap_repo = SimpleNamespace(get_all=lambda project: [
                {'snapshot_data': json.dumps({'files': [{'filename': 'saved.prt.1'}]})},
                {'snapshot_data': json.dumps({'files': [{'filename': 'old.DRW.2'}]})},
            ])
            service.repo = SimpleNamespace(get_working_directory_commit_records=lambda project: [
                {'filename': 'approved.prt.2', 'approved_version': 7, 'status': 'Approved', 'commit_id': 'v', 'nexus_branch_id': None},
                {'filename': 'pending.asm', 'status': 'Pending', 'commit_id': 'p', 'nexus_branch_id': None},
                {'filename': 'published.prt.1', 'status': 'Integrated', 'commit_id': 'push', 'nexus_branch_id': None},
                {'filename': 'branch.prt.2', 'status': 'Branched', 'commit_id': 'branch', 'nexus_branch_id': 'branch-id'},
            ])
            names = {row[0] for row in service.check_working_directory(str(root))}
            self.assertEqual(names, {'registered.prt.99', 'branch.prt.2', 'unknown.DRW'})
            service.snap_repo = SimpleNamespace(get_all=lambda project: [])
            names = {row[0] for row in service.check_working_directory(str(root))}
            self.assertIn('saved.PRT.1', names)
            self.assertNotIn('pending.asm', names)

if __name__ == '__main__':
    unittest.main()
