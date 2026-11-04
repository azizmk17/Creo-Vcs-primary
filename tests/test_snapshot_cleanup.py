import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from core.services.snapshot_service import SnapshotService, _remove_if_backed_up
from core.repositories.snapshot_repository import SnapshotRepository

class SnapshotCleanupTests(unittest.TestCase):
    def test_item_with_pending_commit_keeps_its_existing_reference(self):
        from types import SimpleNamespace
        from core.services.master_work_service import MasterWorkService
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp); root=base/'work'; backup=base/'backup'; root.mkdir(); backup.mkdir()
            for n in range(1, 4): (root/f'part.prt.{n}').write_bytes(str(n).encode())
            db=base/'db.sqlite'
            with sqlite3.connect(db) as c:
                c.executescript('''CREATE TABLE snapshots(id INTEGER PRIMARY KEY,project_id INTEGER,snapshot_name TEXT,description TEXT,snapshot_data TEXT,created_by TEXT,created_at TEXT); CREATE TABLE commits(project_id INTEGER,commit_id TEXT,status TEXT,filename TEXT,title TEXT,designer INTEGER,file_path TEXT,pr_path TEXT,nexus_branch_id TEXT,part_id INTEGER,last_snapshot INTEGER,approved_version INTEGER); CREATE TABLE users(id INTEGER,username TEXT); CREATE TABLE bom(id INTEGER,project_id INTEGER,filename TEXT,drawing TEXT); INSERT INTO bom VALUES(1,1,'part.prt.1',NULL); INSERT INTO commits VALUES(1,'C1','Approved','part.prt.1','done',NULL,NULL,NULL,NULL,1,NULL,3); INSERT INTO commits VALUES(1,'C2','Pending','part.prt.3','pending',NULL,NULL,NULL,NULL,1,NULL,NULL);''')
            c.close()
            MasterWorkService.publish(root,'C1',[{'filename':'part.prt.3','dest_path':str(root/'part.prt.3'),'root_filename':'part.prt.3','validated':True,'part_ids':[1]}])
            repo=SnapshotRepository(str(db)); svc=object.__new__(SnapshotService); svc.repo=repo
            svc.commit_rep=SimpleNamespace(get_conn=repo.get_conn)
            svc.issue_repo=SimpleNamespace(snapshot_state=lambda project:{})
            svc.revision_repo=SimpleNamespace(project_configuration_snapshot=lambda project:{})
            snapshot_id=svc.create_backup_snapshot(1,'snapshot',root,'user',backup)
            self.assertTrue((root/'part.prt.1').exists()); self.assertFalse((root/'part.prt.2').exists())
            self.assertTrue((root/'part.prt.3').exists())
            with sqlite3.connect(db) as c:
                self.assertEqual(c.execute('SELECT filename FROM bom WHERE id=1').fetchone()[0], 'part.prt.1')
            c.close()
            self.assertEqual(json.loads(repo.get_by_id(snapshot_id)['snapshot_data'])['backup']['updated_item_files'], [])

    def test_removes_unreferenced_newer_draft_and_does_not_reuse_its_iteration(self):
        from types import SimpleNamespace
        from core.services.master_work_service import MasterWorkService
        from utils import get_next_version_number
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); root = base/'work'; backup = base/'backup'; root.mkdir(); backup.mkdir()
            for number in range(1, 5): (root/f'part.prt.{number}').write_bytes(str(number).encode())
            db = base/'db.sqlite'
            with sqlite3.connect(db) as c:
                c.executescript('''CREATE TABLE snapshots(id INTEGER PRIMARY KEY,project_id INTEGER,snapshot_name TEXT,description TEXT,snapshot_data TEXT,created_by TEXT,created_at TEXT); CREATE TABLE commits(project_id INTEGER,commit_id TEXT,status TEXT,filename TEXT,title TEXT,designer INTEGER,file_path TEXT,pr_path TEXT,nexus_branch_id TEXT,part_id INTEGER,last_snapshot INTEGER,approved_version INTEGER); CREATE TABLE users(id INTEGER,username TEXT); CREATE TABLE bom(id INTEGER,project_id INTEGER,filename TEXT,drawing TEXT); INSERT INTO bom VALUES(1,1,'part.prt.1',NULL); INSERT INTO commits VALUES(1,'C1','Approved','part.prt.1','done',NULL,NULL,NULL,NULL,1,NULL,3);''')
            c.close()
            MasterWorkService.publish(root, 'C1', [{'filename':'part.prt.3','dest_path':str(root/'part.prt.3'),'root_filename':'part.prt.3','validated':True,'part_ids':[1]}])
            repo = SnapshotRepository(str(db)); svc = object.__new__(SnapshotService); svc.repo = repo
            svc.commit_rep = SimpleNamespace(get_conn=repo.get_conn)
            svc.issue_repo = SimpleNamespace(snapshot_state=lambda project: {})
            svc.revision_repo = SimpleNamespace(project_configuration_snapshot=lambda project: {})
            svc.create_backup_snapshot(1, 'snapshot', root, 'user', backup)
            self.assertEqual(sorted(p.name for p in root.glob('part.prt.*')), ['part.prt.3'])
            self.assertEqual(get_next_version_number(str(root), 'part.prt'), 5)
            source = root/'incoming.prt.1'; source.write_bytes(b'draft')
            item = {'filename':'part.prt.4', 'dest_path':str(source)}
            MasterWorkService.publish(root, 'draft', [item], always_new=True)
            self.assertEqual(item['published_filename'], 'part.prt.5')

    def test_remaps_stale_current_item_to_last_validated_and_prunes_empty_folders(self):
        from types import SimpleNamespace
        from core.services.master_work_service import MasterWorkService
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); root = base/'work'; backup = base/'backup'; root.mkdir(); backup.mkdir()
            for number in range(1, 5): (root/f'part.prt.{number}').write_bytes(str(number).encode())
            commit_folder = root/'commits'/'user'/'done_C1'; commit_folder.mkdir(parents=True)
            (commit_folder/'part.prt.1').write_bytes(b'commit')
            db = base/'db.sqlite'
            with sqlite3.connect(db) as c:
                c.executescript('''CREATE TABLE snapshots(id INTEGER PRIMARY KEY,project_id INTEGER,snapshot_name TEXT,description TEXT,snapshot_data TEXT,created_by TEXT,created_at TEXT); CREATE TABLE commits(project_id INTEGER,commit_id TEXT,status TEXT,filename TEXT,title TEXT,designer INTEGER,file_path TEXT,pr_path TEXT,nexus_branch_id TEXT,part_id INTEGER,last_snapshot INTEGER,approved_version INTEGER); CREATE TABLE users(id INTEGER,username TEXT); CREATE TABLE bom(id INTEGER,project_id INTEGER,filename TEXT,drawing TEXT); INSERT INTO users VALUES(1,'user'); INSERT INTO bom VALUES(1,1,'part.prt.1',NULL); INSERT INTO commits VALUES(1,'C1','Approved','part.prt.1','done',1,NULL,NULL,NULL,1,NULL,3); INSERT INTO commits VALUES(1,'C2','Pending','part.prt.4','work',1,NULL,NULL,NULL,2,NULL,NULL);''')
            c.close()
            with sqlite3.connect(db) as c:
                c.execute("INSERT INTO commits VALUES(1,'C3','Integrated','part.prt.2','draft',1,NULL,NULL,NULL,1,NULL,NULL)")
                c.executescript("CREATE TABLE cad_documents(id INTEGER, project_id INTEGER, latest_creo_file_name TEXT, latest_creo_file_version INTEGER, checked_out_by INTEGER); CREATE TABLE cad_item_associations(item_id INTEGER,cad_document_id INTEGER,active INTEGER); INSERT INTO cad_documents VALUES(10,1,'part.prt.1',1,NULL); INSERT INTO cad_documents VALUES(11,1,'part.prt.1',1,NULL); INSERT INTO cad_item_associations VALUES(1,10,1);")
            c.close()
            MasterWorkService.publish(root, 'C1', [{'filename':'part.prt.3','dest_path':str(root/'part.prt.3'), 'root_filename':'part.prt.3','validated':True,'part_ids':[1]}])
            MasterWorkService.publish(root, 'C2', [{'filename':'part.prt.4','dest_path':str(root/'part.prt.4'), 'root_filename':'part.prt.4','part_ids':[1]}])
            repo = SnapshotRepository(str(db)); svc = object.__new__(SnapshotService); svc.repo = repo
            svc.commit_rep = SimpleNamespace(get_conn=repo.get_conn)
            svc.issue_repo = SimpleNamespace(snapshot_state=lambda project: {})
            svc.revision_repo = SimpleNamespace(project_configuration_snapshot=lambda project: {})
            snapshot_id = svc.create_backup_snapshot(1, 'snapshot', root, 'user', backup)
            self.assertFalse((root/'part.prt.1').exists()); self.assertFalse((root/'part.prt.2').exists())
            self.assertTrue((root/'part.prt.3').exists()); self.assertTrue((root/'part.prt.4').exists())
            self.assertFalse(commit_folder.exists())
            with sqlite3.connect(db) as c:
                self.assertEqual(c.execute('SELECT filename FROM bom WHERE id=1').fetchone()[0], 'part.prt.4')
                self.assertEqual(c.execute('SELECT latest_creo_file_name,latest_creo_file_version FROM cad_documents WHERE id=10').fetchone(), ('part.prt.4',4))
                self.assertEqual(c.execute('SELECT latest_creo_file_name,latest_creo_file_version FROM cad_documents WHERE id=11').fetchone(), ('part.prt.4',4))
            c.close()
            record = json.loads(repo.get_by_id(snapshot_id)['snapshot_data'])['backup']
            self.assertEqual(record['updated_item_files'][0]['from'], 'part.prt.1')
            self.assertEqual(record['updated_cad_files'][0]['from'], 'part.prt.1')
            self.assertTrue(record['removed_directories'])

    def test_full_snapshot_scans_copies_and_updates_commit_metadata(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); root = base / 'work'; backup = base / 'backup'; root.mkdir(); backup.mkdir()
            (root / 'part.prt.1').write_bytes(b'old'); (root / 'part.prt.2').write_bytes(b'new')
            db = base / 'db.sqlite'
            with sqlite3.connect(db) as c:
                c.executescript('''CREATE TABLE snapshots(id INTEGER PRIMARY KEY,project_id INTEGER,snapshot_name TEXT,description TEXT,snapshot_data TEXT,created_by TEXT,created_at TEXT); CREATE TABLE commits(project_id INTEGER,commit_id TEXT,status TEXT,filename TEXT,title TEXT,designer INTEGER,file_path TEXT,pr_path TEXT,nexus_branch_id TEXT,part_id INTEGER,last_snapshot INTEGER); CREATE TABLE users(id INTEGER,username TEXT); CREATE TABLE bom(project_id INTEGER,filename TEXT,drawing TEXT); INSERT INTO commits VALUES(1,'C1','Approved','part.prt.1','done',NULL,NULL,NULL,NULL,1,NULL);''')
            c.close()
            repo = SnapshotRepository(str(db)); svc = object.__new__(SnapshotService); svc.repo = repo
            svc.commit_rep = SimpleNamespace(get_conn=repo.get_conn)
            svc.issue_repo = SimpleNamespace(snapshot_state=lambda project: {})
            svc.revision_repo = SimpleNamespace(project_configuration_snapshot=lambda project: {})
            snapshot_id = svc.create_backup_snapshot(1, 'snapshot', root, 'user', backup)
            self.assertFalse((root / 'part.prt.1').exists())
            self.assertTrue((root / 'part.prt.2').exists())
            with sqlite3.connect(db) as c:
                self.assertEqual(c.execute('SELECT last_snapshot FROM commits').fetchone()[0], snapshot_id)
            c.close()
            data = json.loads(repo.get_by_id(snapshot_id)['snapshot_data'])
            self.assertEqual(data['backup']['cleanup'], 'Completed')
            self.assertEqual(data['backup']['removed'], ['part.prt.1'])

    def test_archive_only_unshared_closed_creo_files(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); root = base / 'work'; backup = base / 'backup'
            root.mkdir(); backup.mkdir(); db = base / 'db.sqlite'
            def put(name, value):
                path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(value); return path
            old = put('a.prt.1', b'old'); latest = put('a.prt.2', b'new')
            approved = put('commits/user/done_C1/a.prt.1', b'closed')
            pending = put('commits/user/pending_C2/b.prt.1', b'pending')
            branch = put('commits/user/branch_C3/c.prt.1', b'branch')
            pr = put('Pull Requests/approvals/one/a.prt.3', b'pr')
            pdf = put('Pull Requests/approvals/one/a.pdf', b'pdf')
            blob = put('commits/_snapshots/objects/aa/blob.1', b'blob')
            shared = put('commits/_snapshots/objects/bb/shared.1', b'shared')
            with sqlite3.connect(db) as c:
                c.executescript('''CREATE TABLE snapshots(id INTEGER PRIMARY KEY,project_id INTEGER,snapshot_name TEXT,description TEXT,snapshot_data TEXT,created_by TEXT,created_at TEXT); CREATE TABLE commits(project_id INTEGER,commit_id TEXT,status TEXT,filename TEXT,title TEXT,designer INTEGER,file_path TEXT,pr_path TEXT,nexus_branch_id TEXT,part_id INTEGER); CREATE TABLE users(id INTEGER,username TEXT); CREATE TABLE bom(project_id INTEGER,filename TEXT,drawing TEXT); CREATE TABLE cad_commit_snapshots(project_id INTEGER,commit_id TEXT,manifest_json TEXT,manifest_sha256 TEXT); INSERT INTO users VALUES(1,'user');''')
                c.execute('INSERT INTO commits VALUES(1,?,?,?,?,?,?,?,NULL,1)', ('C1','Approved','a.prt.1','done',1,str(approved),str(pr)))
                c.execute('INSERT INTO commits VALUES(1,?,?,?,?,?,?,?,NULL,2)', ('C2','Pending','b.prt.1','pending',1,str(pending),None))
                c.execute('INSERT INTO commits VALUES(1,?,?,?,?,?,?,?,?,3)', ('C3','Branched','c.prt.1','branch',1,str(branch),None,'branch-id'))
                for commit, paths in [('C1',['_snapshots/objects/aa/blob.1','_snapshots/objects/bb/shared.1']),('C2',['_snapshots/objects/bb/shared.1'])]:
                    raw = json.dumps({'files':[{'filename':'a.prt.1','blob_path':path} for path in paths]},sort_keys=True)
                    c.execute('INSERT INTO cad_commit_snapshots VALUES(?,?,?,?)',(1,commit,raw,hashlib.sha256(raw.encode()).hexdigest()))
            c.close()
            svc = object.__new__(SnapshotService); svc.repo = SnapshotRepository(str(db))
            svc.update_last_snapshot_in_commit = lambda *args: None
            svc.generate_snapshot_data = lambda path, pid, **kw: {'working_dir':path,'files':[{'filename':f.name,'size':f.stat().st_size,'checksum':hashlib.md5(f.read_bytes()).hexdigest()} for f in root.iterdir() if f.is_file()]}
            progress = []
            svc.create_backup_snapshot(1,'snapshot',root,'user',backup,progress=lambda a,b,c:progress.append((a,b)))
            for path in (old, approved, pr, blob):
                self.assertFalse(path.exists()); self.assertTrue(Path(svc.find_archived_creo_file(1,path)['path']).exists())
            for path in (latest,pending,branch,pdf,shared): self.assertTrue(path.exists())
            self.assertIsNotNone(svc.find_archived_creo_folder(1,approved.parent))
            self.assertEqual(progress[-1][0],progress[-1][1])

    def test_changed_source_and_backup_are_never_removed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root/'part.prt.1'; backup = root/'backup'
            source.write_bytes(b'changed'); backup.write_bytes(b'original')
            expected = hashlib.md5(b'original').hexdigest()
            self.assertFalse(_remove_if_backed_up(str(source),str(backup),expected)); self.assertTrue(source.exists())
            source.write_bytes(b'original'); backup.write_bytes(b'changed')
            self.assertFalse(_remove_if_backed_up(str(source),str(backup),expected)); self.assertTrue(source.exists())

    @unittest.skipUnless(__import__('os').name == 'nt', 'Windows exclusive handles')
    def test_open_source_is_skipped_until_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root/'part.prt.1'; backup = root/'backup'
            source.write_bytes(b'CAD'); backup.write_bytes(b'CAD')
            expected = hashlib.md5(b'CAD').hexdigest()
            with source.open('rb'):
                self.assertFalse(_remove_if_backed_up(str(source), str(backup), expected))
            self.assertTrue(_remove_if_backed_up(str(source), str(backup), expected))
            self.assertFalse(source.exists())

if __name__ == '__main__': unittest.main()
