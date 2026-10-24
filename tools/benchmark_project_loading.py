"""Measure project-load components against a disposable database copy.

The working directory is read, never copied or modified. Results describe local,
warm-cache component timings, not login/startup/network or CAD regeneration.
"""
import argparse
import contextlib
import gc
import json
import os
import platform
from pathlib import Path
import sqlite3
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def measure(database, project_id, max_scan_seconds):
    import config
    config.DB_NAME.set(str(database))
    from PyQt5.QtCore import QCoreApplication, QEvent
    from PyQt5.QtWidgets import QApplication
    from core.session_manager import SessionManager
    from core.repositories.bom_repository import BomRepository
    from core.repositories.bom_children_repository import BomChildrenRepository
    from core.repositories.lock_repository import LockRepository
    from core.repositories.signature_repository import SignatureRepository
    from core.services.bom_service import BomService
    from core.services.issue_service import IssueService
    from core.services.diag_service import DiagService
    from pages.bom_page import BomPage, _ProjectStructureWorker

    with contextlib.closing(sqlite3.connect(database)) as conn:
        directory = conn.execute("SELECT working_directory FROM projects WHERE id=?", (project_id,)).fetchone()[0]
        actor = conn.execute("SELECT user_id FROM user_projects WHERE project_id=? ORDER BY user_id LIMIT 1", (project_id,)).fetchone()[0]
    session = SessionManager()
    session.start_session(actor, "benchmark", None, project_id=project_id, is_admin=True)
    app = QApplication.instance() or QApplication([])
    start = time.perf_counter()
    service = BomService(BomRepository(), BomChildrenRepository(), LockRepository(), SignatureRepository())
    issues = IssueService()
    result = {"app_version": config.APP_VERSION, "project_id": project_id,
              "service_setup_seconds": time.perf_counter() - start,
              "python_version": platform.python_version(), "platform": platform.platform(),
              "runtime": "frozen" if getattr(sys, "frozen", False) else "source",
              "executable": sys.executable}
    manifest_path = Path(directory) / "benchmark_manifest.json"
    if manifest_path.is_file():
        result["dataset_id"] = json.loads(manifest_path.read_text(encoding="utf-8"))["dataset_id"]
    worker = _ProjectStructureWorker(1, project_id, "ebom", service, issues)
    payloads, errors = [], []
    worker.finished.connect(lambda *args: payloads.append(args[-1]))
    worker.failed.connect(lambda *args: errors.append(args[-1]))
    start = time.perf_counter()
    worker.run()
    result["structure_worker_seconds"] = time.perf_counter() - start
    if errors or not payloads:
        raise RuntimeError(errors or "No structure payload")
    payload = payloads[0]
    def count(nodes):
        return sum(1 + count(n.get("children") or []) for n in nodes)
    result["initial_payload_item_occurrences"] = count(payload["data"].get("roots") or [])
    result["initial_payload_documents"] = len(payload.get("documents") or [])
    start = time.perf_counter()
    page = BomPage(service)
    result["bom_widget_setup_seconds"] = time.perf_counter() - start
    start = time.perf_counter()
    page._apply_released_ebom_tree(payload)
    result["initial_row_creation_seconds"] = time.perf_counter() - start
    result["initial_root_rows"] = page._ebom_tree.topLevelItemCount()
    start = time.perf_counter()
    page._apply_released_ebom_tree(payload)
    result["bom_refresh_update_seconds"] = time.perf_counter() - start
    page._cancel_background_work()
    page.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    diag = DiagService()
    updates, scanned = [0], [0]
    update = diag.bom_repo.update_bom_file_names
    def tracked_update(*args, **kwargs):
        updates[0] += 1
        return update(*args, **kwargs)
    diag.bom_repo.update_bom_file_names = tracked_update
    if hasattr(diag.bom_repo, "update_bom_file_names_batch"):
        update_batch = diag.bom_repo.update_bom_file_names_batch
        def tracked_batch(rows, project_id):
            rows = list(rows)
            updates[0] += len(rows)
            return update_batch(rows, project_id)
        diag.bom_repo.update_bom_file_names_batch = tracked_batch
    check = diag._sync_bom_record
    def tracked_check(*args, **kwargs):
        scanned[0] += 1
        return check(*args, **kwargs)
    diag._sync_bom_record = tracked_check
    start = time.perf_counter()
    findings = diag.sync_bom_files(directory, cancel_cb=lambda: time.perf_counter() - start > max_scan_seconds)
    result["file_sync_seconds"] = time.perf_counter() - start
    result["file_sync_rows_checked"] = scanned[0]
    result["file_sync_write_calls"] = updates[0]
    result["file_sync_findings"] = len(findings)
    result["file_sync_timed_out"] = time.perf_counter() - start > max_scan_seconds
    start = time.perf_counter()
    diag.check_working_directory(directory)
    result["working_directory_check_seconds"] = time.perf_counter() - start

    # Measure real metadata writes on a second disposable copy. Clearing only
    # the derived base-name cache fields makes this deterministic without
    # touching the supplied database or the normal no-op scan above.
    with tempfile.TemporaryDirectory(prefix="nexus-metadata-benchmark-") as temporary:
        dirty_database = Path(temporary) / "metadata.db"
        with contextlib.closing(sqlite3.connect(database)) as source, \
                contextlib.closing(sqlite3.connect(dirty_database)) as output:
            source.backup(output)
            output.execute(
                "UPDATE bom SET base_file_name=NULL,base_drw_name=NULL WHERE project_id=?",
                (project_id,),
            )
            output.commit()
        config.DB_NAME.set(str(dirty_database))
        dirty_diag = DiagService()
        metadata_rows = [0]
        update_batch = dirty_diag.bom_repo.update_bom_file_names_batch
        def tracked_metadata_batch(rows, target_project_id):
            rows = list(rows)
            metadata_rows[0] += len(rows)
            return update_batch(rows, target_project_id)
        dirty_diag.bom_repo.update_bom_file_names_batch = tracked_metadata_batch
        start = time.perf_counter()
        dirty_findings = dirty_diag.sync_bom_files(directory)
        result["metadata_update_seconds"] = time.perf_counter() - start
        result["metadata_update_rows"] = metadata_rows[0]
        result["metadata_update_findings"] = len(dirty_findings)
        config.DB_NAME.set(str(database))
    result["measurement_scope"] = (
        "Disposable DB copy; warm-cache local component timings; hidden Qt GUI initial render and refresh; "
        "metadata rewrite on a second disposable copy; not complete interactive startup or network timing."
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--project", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-scan-seconds", type=float, default=120)
    args = parser.parse_args()
    # Import configuration after selecting the copy; the company DB is opened RO.
    with tempfile.TemporaryDirectory(prefix="nexus-loading-benchmark-") as temporary:
        root = Path(temporary).resolve()
        assert root.parent == Path(tempfile.gettempdir()).resolve()
        target = root / "benchmark.db"
        with contextlib.closing(sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True)) as source, \
                contextlib.closing(sqlite3.connect(target)) as output:
            source.backup(output)
        try:
            result = measure(target, args.project, args.max_scan_seconds)
        finally:
            gc.collect()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
