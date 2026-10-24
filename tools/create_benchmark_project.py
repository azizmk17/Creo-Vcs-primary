"""Create an isolated synthetic CAD/BOM load-test project (schema 49).

Standard library only. Dry run by default. Never rewrites embedded Creo references.
See tools/README_benchmark_project.md before use.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import time
import uuid

GIB = 1024 ** 3
NOTE = "SYNTHETIC BENCHMARK: copied CAD; embedded Creo references unchanged."
SNAPSHOT_FIELDS = (
    "type name part_number drawing_number aes_number filename drawing base_file_name "
    "base_drw_name material weight notes pdf_path step_path classification "
    "default_ebom_behavior cad_requirement drawing_requirement represented_part_id "
    "cad_control_mode item_type assembly_mode procurement_source item_view default_unit"
).split()


class ClosingConnection(sqlite3.Connection):
    """Commit/rollback and release Windows file handles on leaving a with block."""

    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def connect(path, readonly=False):
    conn = sqlite3.connect(path.as_uri() + ("?mode=ro" if readonly else "?mode=rw"),
                           uri=True, timeout=60, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def insert(conn, table, **values):
    # Table/column identifiers are internal constants, never CLI input.
    return conn.execute(
        f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})",
        tuple(values.values()),
    ).lastrowid


def make_plan(source, parts, assemblies):
    templates = defaultdict(lambda: defaultdict(list))
    for path in sorted(source.iterdir()):
        match = re.fullmatch(r"(.+)\.(prt|asm|drw)\.(\d+)", path.name, re.I)
        if path.is_file() and match:
            templates[match[2].lower()][match[1]].append((int(match[3]), path))
    for kind in ("prt", "asm", "drw"):
        if not templates[kind]:
            raise ValueError(f"No numbered .{kind} source files in {source}")
    families = {k: [sorted(v) for _, v in sorted(groups.items())]
                for k, groups in templates.items()}
    items, files = [], []
    for kind, count in (("asm", assemblies), ("prt", parts)):
        for i in range(count):
            stem = f"bench_{kind}_{i + 1:06d}"
            family = families[kind][i % len(families[kind])]
            native = []
            for iteration in range(1, 4):
                src = family[min(iteration - 1, len(family) - 1)][1]
                filename = f"{stem}.{kind}.{iteration}"
                native.append((src, filename))
                files.append((src, filename))
            drawing = None
            if i % 4 == 0:
                src = families["drw"][i % len(families["drw"])][0][1]
                drawing = (src, f"{stem}.drw.1")
                files.append(drawing)
            items.append(dict(stem=stem, kind=kind, native=native, drawing=drawing))

    # Ten product lines, a bounded-depth assembly tree, shared leaf assemblies,
    # and two reused components per leaf. Every item is reachable; no cycles.
    root_count = min(10, max(1, assemblies // 10))
    edges = {}
    for i in range(root_count, assemblies):
        edges[((i - root_count) // 10, i)] = 1
    parents = {p for p, _ in edges}
    leaves = [i for i in range(assemblies) if i not in parents]
    for i in range(parts):
        edges[(leaves[i % len(leaves)], assemblies + i)] = 1 + (i % 4)
    for i, leaf in enumerate(leaves):
        for offset in (1, 2):
            child = assemblies + ((i + 1 + offset * len(leaves)) % parts)
            edges.setdefault((leaf, child), 2)
    for i in range(root_count):
        leaf = leaves[-1 - (i % len(leaves))]
        if i != leaf:
            edges.setdefault((i, leaf), 1)
    children = defaultdict(list)
    for parent, child in edges:
        children[parent].append(child)
    def size(index, depth=1):
        totals = [size(child, depth + 1) for child in children[index]]
        return 1 + sum(n for n, _ in totals), max([depth] + [d for _, d in totals])
    expanded = [size(i) for i in range(root_count)]
    # Include allocation-unit overhead for many small files.
    allocation = sum(((src.stat().st_size + 4095) // 4096) * 4096 for src, _ in files)
    return items, files, edges, dict(
        parts=parts, assemblies=assemblies, roots=root_count,
        drawings=sum(bool(i["drawing"]) for i in items), native_file_iterations=3,
        files=len(files), file_bytes=sum(src.stat().st_size for src, _ in files),
        estimated_allocated_bytes=allocation, usage_links=len(edges),
        expanded_bom_rows=sum(n for n, _ in expanded), max_levels=max(d for _, d in expanded),
    )


def populate(conn, destination, name, source_id, items, edges):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    project_id = insert(conn, "projects", name=name, description=NOTE,
                        working_directory=destination.as_posix(), created_at=now,
                        version_label="A", version_state="WIP", is_readonly=0)
    conn.execute("UPDATE projects SET root_project_id=? WHERE id=?", (project_id, project_id))
    conn.execute("INSERT INTO user_projects(user_id,project_id,is_current) "
                 "SELECT user_id,?,0 FROM user_projects WHERE project_id=?", (project_id, source_id))
    ids = []
    hashes = {}
    def cad(item_id, native, category, owner=None):
        latest = native[-1][1]
        base = latest.rsplit(".", 1)[0]
        doc_id = insert(conn, "cad_documents", project_id=project_id, number=base,
                        name=base.rsplit(".", 1)[0], file_name=base,
                        base_file_name=base.rsplit(".", 1)[0], category=category,
                        legacy_bom_id=item_id if owner is None else None,
                        drawing_owner_cad_document_id=owner, iteration=len(native),
                        latest_creo_file_version=len(native), latest_creo_file_name=latest)
        for iteration, (src, filename) in enumerate(native, 1):
            if src not in hashes:
                hashes[src] = hashlib.sha256(src.read_bytes()).hexdigest()
            insert(conn, "cad_document_iterations", cad_document_id=doc_id,
                   revision="A", iteration=iteration, primary_path=filename,
                   sha256=hashes[src], size_bytes=src.stat().st_size,
                   creo_file_version=iteration, source_file_name=filename, checkin_note=NOTE)
        insert(conn, "cad_item_associations", project_id=project_id, item_id=item_id,
               cad_document_id=doc_id, association_type="CONTENT" if owner else "OWNER",
               drives_structure=0 if owner else 1, drives_attributes=0 if owner else 1,
               participates_in_structure=0 if owner else 1,
               is_primary_drawing=1 if owner else 0, drawing_model_cad_document_id=owner)
        return doc_id
    for index, item in enumerate(items):
        stem, kind = item["stem"], item["kind"]
        drawing = item["drawing"][1] if item["drawing"] else None
        record = dict(type=kind, name=stem, part_number=stem.upper(), aes_number=stem.upper(),
                      drawing_number=f"DRW-{stem.upper()}" if drawing else "",
                      filename=item["native"][-1][1], drawing=drawing,
                      base_file_name=f"{stem}.{kind}", base_drw_name=f"{stem}.drw" if drawing else None,
                      material="Steel" if index % 2 else "Aluminium", weight=str(1 + index % 100 / 10),
                      notes=NOTE, pdf_path="", step_path="", classification="PHYSICAL",
                      default_ebom_behavior="NORMAL", cad_requirement="REQUIRED",
                      drawing_requirement="REQUIRED" if drawing else "OPTIONAL",
                      represented_part_id=None, cad_control_mode="CONTROLLED",
                      item_type="MECHANICAL_PART", assembly_mode="SEPARABLE" if kind == "asm" else "COMPONENT",
                      procurement_source="MAKE", item_view="DESIGN", default_unit="EA")
        item_id = insert(conn, "bom", **record, project_id=project_id, status="Design",
                         lifecycle_state="WIP", revision="A", cad_revision="A",
                         created=now, modified=now, sort_order=(index + 1) * 10)
        revision_id = insert(conn, "bom_revisions", bom_id=item_id, revision_code="A", state="In Work")
        iterations = []
        for iteration in range(1, 4):
            snapshot = {key: record[key] for key in SNAPSHOT_FIELDS}
            snapshot["filename"] = item["native"][iteration - 1][1]
            iterations.append(insert(conn, "bom_iterations", revision_id=revision_id,
                                     iteration_number=iteration, checkin_note=NOTE,
                                     object_data_json=json.dumps(snapshot, sort_keys=True)))
        conn.execute("UPDATE bom SET current_revision_id=?,current_iteration_id=? WHERE id=?",
                     (revision_id, iterations[-1], item_id))
        doc_id = cad(item_id, item["native"], "ASSEMBLY" if kind == "asm" else "COMPONENT")
        if item["drawing"]:
            cad(item_id, [item["drawing"]], "DRAWING", doc_id)
        ids.append((item_id, revision_id, iterations, doc_id))
    order = defaultdict(int)
    for (parent, child), quantity in sorted(edges.items()):
        p_id, _, p_iters, p_cad = ids[parent]
        c_id, c_rev, c_iters, c_cad = ids[child]
        order[parent] += 10
        legacy_id = insert(conn, "bom_children", parent_id=p_id, child_id=c_id,
                           quantity=quantity, sort_order=order[parent])
        member_id = insert(conn, "cad_document_members", parent_cad_document_id=p_cad,
                           child_cad_document_id=c_cad, quantity=quantity,
                           sort_order=order[parent], legacy_usage_id=legacy_id)
        insert(conn, "item_usages", project_id=project_id, parent_item_id=p_id,
               child_item_id=c_id, quantity=quantity, sort_order=order[parent],
               line_number=order[parent], source="MANUAL", build_status="COMPLETED",
               cad_member_id=member_id, legacy_usage_id=legacy_id)
        for p_iter, c_iter in zip(p_iters, c_iters):
            insert(conn, "bom_iteration_bindings", parent_iteration_id=p_iter, usage_id=legacy_id,
                   child_bom_id=c_id, child_revision_id=c_rev, child_iteration_id=c_iter,
                   quantity=quantity, sort_order=order[parent])
    for parent in order:
        item_id, _, iterations, _ = ids[parent]
        usages = [dict(r, occurrences=[]) for r in conn.execute(
            "SELECT * FROM item_usages WHERE parent_item_id=? ORDER BY sort_order,id", (item_id,))]
        insert(conn, "item_structure_iterations", project_id=project_id, parent_item_id=item_id,
               structure_iteration=1, item_revision="A", item_iteration_id=iterations[-1],
               source="MANUAL", structure_json=json.dumps(usages, sort_keys=True))
    return project_id


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--name", default="BENCHMARK - Company 11000")
    parser.add_argument("--parts", type=int, default=10000)
    parser.add_argument("--assemblies", type=int, default=1000)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    db, source, dest = (p.resolve() for p in (args.database, args.source, args.destination))
    if args.parts < 1 or args.assemblies < 1:
        raise ValueError("Parts and assemblies must both be positive")
    if dest.exists() or not dest.parent.is_dir() or dest == source or source in dest.parents:
        raise ValueError("Destination must be a new directory outside the source; parent must exist")
    with connect(db, readonly=True) as conn:
        if conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] != 49:
            raise ValueError("This generator is tested against schema 49 only; no migrations performed")
        matching = [r for r in conn.execute("SELECT id,working_directory FROM projects")
                    if Path(r["working_directory"]).resolve() == source]
        if len(matching) != 1:
            raise ValueError("Source must match exactly one registered project")
        source_id = matching[0]["id"]
        if conn.execute("SELECT 1 FROM projects WHERE name=?", (args.name,)).fetchone():
            raise ValueError("Benchmark project name already exists; choose a new name")
    items, files, edges, stats = make_plan(source, args.parts, args.assemblies)
    # Conservative headroom covers DB growth, rollback journal, backup and manifest.
    required = stats["estimated_allocated_bytes"] + 512 * 1024 ** 2 + db.stat().st_size * 3
    if required > 4 * GIB:
        raise ValueError("Plan exceeds the fixed 4 GiB total allocation budget")
    if shutil.disk_usage(dest.parent).free - required < 6 * GIB:
        raise ValueError("Plan would leave less than the required 6 GiB free")
    if shutil.disk_usage(db.parent).free < 6 * GIB + db.stat().st_size * 3 + 512 * 1024 ** 2:
        raise ValueError("Database disk lacks backup/journal headroom")
    manifest = dict(format_version=1, dataset_id=uuid.uuid4().hex, status="planned",
                    created_at=datetime.now(timezone.utc).isoformat(), database=str(db),
                    source=str(source), source_project_id=source_id, destination=str(dest),
                    project_name=args.name, version="A", stats=stats,
                    conservative_total_budget_bytes=required, limitations=NOTE,
                    deliverables="No PDF/STEP generated or validation approvals fabricated.")
    print(json.dumps(manifest, indent=2), flush=True)
    if not args.apply:
        print("Dry run only. Add --apply to create this isolated project.")
        return manifest
    start = time.perf_counter()
    dest.mkdir()  # exclusive; prevents overwriting an earlier/partial run
    marker = dest / "benchmark_manifest.json"
    def save_manifest():
        temporary = marker.with_suffix(".tmp")
        temporary.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        temporary.replace(marker)
    manifest["status"] = "copying"
    save_manifest()
    try:
        source_hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in sorted({src for src, _ in files})}
        manifest["templates"] = source_hashes
        for index, (src, filename) in enumerate(files, 1):
            if index % 250 == 1 and shutil.disk_usage(dest).free < 6 * GIB + 512 * 1024 ** 2:
                raise OSError("Stopping to preserve free disk space")
            target = dest / filename
            with src.open("rb") as input_file, target.open("xb") as output_file:
                shutil.copyfileobj(input_file, output_file)
            # Check every copy, not only a sample; this is outside timing measurements.
            if hashlib.sha256(target.read_bytes()).hexdigest() != source_hashes[str(src)]:
                raise OSError(f"Copy verification failed: {target}")
            if index % 2000 == 0:
                print(f"Copied and verified {index:,}/{len(files):,} files", flush=True)
        backup = db.with_name(db.name + ".before_benchmark_" + manifest["dataset_id"] + ".bak")
        # SQLite backup API produces a consistent backup even if the app is open.
        with connect(db, readonly=True) as original, sqlite3.connect(backup, factory=ClosingConnection) as output:
            original.backup(output)
        manifest["database_backup"] = str(backup)
        manifest["status"] = "registering"
        save_manifest()
        with connect(db) as conn:
            conn.execute("BEGIN IMMEDIATE")
            before_fk = {tuple(r) for r in conn.execute("PRAGMA foreign_key_check")}
            if conn.execute("SELECT 1 FROM projects WHERE name=?", (args.name,)).fetchone():
                raise ValueError("Project name was registered by another process")
            project_id = populate(conn, dest, args.name, source_id, items, edges)
            after_fk = {tuple(r) for r in conn.execute("PRAGMA foreign_key_check")}
            if after_fk - before_fk:
                raise ValueError(f"New foreign-key violations: {after_fk - before_fk}")
            if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("Database quick_check failed; rolling back")
            manifest["project_id"] = project_id
            manifest["status"] = "database_commit_pending"
            save_manifest()
        manifest["status"] = "complete"
        manifest["elapsed_creation_seconds"] = round(time.perf_counter() - start, 2)
        manifest["remaining_free_bytes"] = shutil.disk_usage(dest).free
        save_manifest()
        print(json.dumps(manifest, indent=2), flush=True)
        return manifest
    except Exception as exc:
        # Preserve evidence and owned files. Never remove directories automatically.
        # If commit succeeded but the final manifest write failed, inspect project_id.
        manifest["status"] = "failed_check_database_before_retry"
        manifest["error"] = str(exc)
        save_manifest()
        raise


if __name__ == "__main__":
    main()
