"""Identify closed Creo submission files without touching live branches."""
import hashlib
import json
import os
import re
from collections import defaultdict
from contextlib import closing, nullcontext

from utils import long_path

NATIVE = re.compile(r".+\.(?:prt|asm|drw)(?:\.\d+)?$", re.I)
CLOSED = {"approved", "released"}


def closed_creo_files(repository, root, project_id, connection=None):
    """Return relative file paths owned exclusively by closed submissions.

    Determine ownership across the entire database, including older snapshot
    generations, so shared snapshot objects used by pending work are retained.
    """
    root = os.path.realpath(long_path(str(root)))
    owners = defaultdict(set)
    protected = set()
    with (nullcontext(connection) if connection is not None else closing(repository.get_conn())) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        columns = {r[1] for r in conn.execute("PRAGMA table_info(commits)")}
        branch = "c.nexus_branch_id" if "nexus_branch_id" in columns else "NULL"
        rows = conn.execute(f"""SELECT c.project_id,c.commit_id,c.status,c.filename,c.title,
                   c.file_path,c.pr_path,{branch},u.username,c.part_id
            FROM commits c LEFT JOIN users u ON u.id=c.designer""").fetchall()
        grouped = defaultdict(list)
        for row in rows:
            grouped[(int(row[0]), str(row[1] or ''))].append(row)
        closed = {key for key, entries in grouped.items()
                  if key[1] and all(str(r[2] or '').casefold() in CLOSED and not r[7] for r in entries)}
        if 'cad_submission_approvals' in tables:
            for row in conn.execute("SELECT project_id,commit_id FROM cad_submission_approvals "
                                    "WHERE upper(status) <> 'COMPLETED'"):
                closed.discard((int(row[0]), str(row[1] or '')))

        def add(path, key, *, native_name=None):
            if not path or (native_name is not None and not NATIVE.fullmatch(native_name)):
                return
            path = os.path.abspath(long_path(str(path)))
            try:
                if os.path.commonpath((root, path)) != root or path == root:
                    return
            except ValueError:
                return
            relative = os.path.normcase(os.path.relpath(path, root))
            parts = relative.replace('\\', '/').split('/')
            if any(part in ('', '.', '..') for part in parts):
                return
            if parts[0].casefold() not in {'commits', 'pull requests', 'pull resuests',
                                           'pull-requests', 'pull_requests', 'pull_request'}:
                return
            # Do not follow links/junctions out of, or within, project storage.
            cursor = root
            for part in parts:
                cursor = os.path.join(cursor, part)
                try:
                    stat = os.lstat(cursor)
                except FileNotFoundError:
                    return
                if os.path.islink(cursor) or (os.name == 'nt' and getattr(stat, 'st_file_attributes', 0) & 0x400):
                    return
            if not os.path.isfile(path):
                return
            owners[relative].add(key)
            if key not in closed or key[0] != int(project_id):
                protected.add(relative)

        for row in rows:
            key = (int(row[0]), str(row[1] or ''))
            filename, title, username = str(row[3] or ''), str(row[4] or ''), str(row[8] or '')
            if not NATIVE.fullmatch(os.path.basename(filename)):
                continue
            if username and title and key[1]:
                for label in {title, re.sub(r'[<>:"/\\|?*]', '_', title)}:
                    add(os.path.join(root, 'commits', username, label + '_' + key[1], os.path.basename(filename)), key,
                        native_name=os.path.basename(filename))
            for value in (row[5], row[6]):
                if value and os.path.isabs(str(value)):
                    try:
                        relative = os.path.relpath(long_path(str(value)), root)
                    except ValueError:
                        continue
                    top = relative.replace('\\', '/').split('/')[0].casefold()
                    if top in {'commits', 'pull requests', 'pull resuests', 'pull-requests', 'pull_requests', 'pull_request'}:
                        add(value, key, native_name=os.path.basename(filename))

        if 'cad_commit_snapshots' in tables:
            for row in conn.execute('SELECT project_id,commit_id,manifest_json,manifest_sha256 FROM cad_commit_snapshots'):
                key = (int(row[0]), str(row[1] or ''))
                encoded = str(row[2] or '')
                if hashlib.sha256(encoded.encode('utf-8')).hexdigest() != str(row[3] or ''):
                    raise ValueError('A commit snapshot manifest failed verification; cleanup stopped.')
                manifest = json.loads(encoded)
                for item in manifest.get('files') or []:
                    rel = str(item.get('blob_path') or '')
                    filename = str(item.get('filename') or '')
                    if rel and not os.path.isabs(rel):
                        add(os.path.join(root, 'commits', rel), key, native_name=filename)
    return {path: {"commits": [{"project_id": key[0], "commit_id": key[1],
                                "part_ids": sorted({int(row[9]) for row in grouped[key] if row[9] is not None})}
                               for key in sorted(ids)]}
            for path, ids in owners.items() if ids and path not in protected}
