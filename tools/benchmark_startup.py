"""Time real Python/EXE startup through a verified license to the first login paint.

Uses a disposable database copy, temporary APPDATA and an ephemeral signed test
license via the application's existing development public-key override. Never
authenticates, changes the user's license/configuration, or opens a BOM session.
"""
import argparse
import base64
from contextlib import closing
from datetime import datetime, timedelta, timezone
import gc
import hashlib
import json
import os
from pathlib import Path
import platform
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run(database, output, *, executable=None, runs=3, timeout=60):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    from core.licensing.crypto import canonical_serialize
    from tools.build_exe import smoke_environment
    if runs < 1:
        raise ValueError("At least one run is required")
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    reports = []
    with tempfile.TemporaryDirectory(prefix="nexus-startup-profile-") as temporary:
        root = Path(temporary).resolve()
        assert root.parent == Path(tempfile.gettempdir()).resolve()
        workspace = root / "workspace"
        workspace.mkdir()
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as source, \
                closing(sqlite3.connect(workspace / "creo_vcs.db")) as target:
            source.backup(target)
        appdata = root / "appdata"
        (appdata / "creovcs").mkdir(parents=True)
        (appdata / "creovcs/config.json").write_text(json.dumps({"last_workspace_path": str(workspace)}))
        key = Ed25519PrivateKey.generate()  # ephemeral; never saved or printed
        public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
        now = datetime.now(timezone.utc)
        payload = dict(product="CreoVCS", licensee="Isolated startup benchmark",
                       issued_at=now.isoformat(), expires_at=(now + timedelta(days=1)).isoformat(),
                       features=["bom_management"], version_constraint=">=4.0,<5.0", machine_id=[])
        payload["signature"] = base64.urlsafe_b64encode(key.sign(canonical_serialize(payload))).decode().rstrip("=")
        license_path = root / "benchmark.lic"
        license_path.write_text(json.dumps(payload))
        revocations = dict(issued_at=now.isoformat(), revoked=[])
        revocations["rl_signature"] = base64.urlsafe_b64encode(
            key.sign(canonical_serialize(revocations))
        ).decode().rstrip("=")
        (workspace / "revoked.json").write_text(json.dumps(revocations))
        for index in range(runs):
            profile = root / f"trace-{index}.json"
            env = smoke_environment() if executable else os.environ.copy()
            env.update(APPDATA=str(appdata), LOCALAPPDATA=str(root / "localappdata"),
                       CREOVCS_PUBLIC_KEY_HEX=public, CREOVCS_LICENSE_PATH=str(license_path),
                       QT_QPA_PLATFORM="offscreen", NEXUS_CREO_BRIDGE_DISABLED="1",
                       PYTHONFAULTHANDLER="1", NEXUS_STARTUP_PROFILE_EACH_STAGE="1",
                       NEXUS_STARTUP_PROFILE_FILE=str(profile), NEXUS_STARTUP_PROFILE_EXIT_AT_LOGIN="1")
            start = time.perf_counter()
            env["NEXUS_STARTUP_PROFILE_ORIGIN"] = str(start)
            command = [str(executable)] if executable else [sys.executable, str(ROOT / "main3.py")]
            try:
                process = subprocess.run(command, cwd=root, env=env, timeout=timeout,
                                         capture_output=True, text=True)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("Startup did not reach a painted login within the deadline; inspect workspace/license/runtime errors.") from exc
            if not profile.is_file():
                raise RuntimeError(f"No startup trace (exit {process.returncode}): {process.stderr[-1500:]}")
            report = json.loads(profile.read_text())
            stages = {event["stage"]: event["seconds"] for event in report["events"]}
            if process.returncode or not {"workspace_compatible", "license_verified", "login_painted"} <= stages.keys():
                raise RuntimeError(f"Startup failed or a required gate was not reached: {stages}; {process.stderr[-2500:]}")
            report["process_lifetime_seconds"] = time.perf_counter() - start
            reports.append(report)
            print(f"Run {index + 1}: login painted {stages['login_painted']:.3f}s; "
                  f"frozen={report['frozen']}; optional={report['loaded_optional_modules']}", flush=True)
        gc.collect()
    names = {event["stage"] for report in reports for event in report["events"]}
    summary = dict(version=reports[0]["version"], frozen=reports[0]["frozen"], runs=reports,
                   platform=platform.platform(), python=platform.python_version(),
                   source_database_unchanged=before == hashlib.sha256(database.read_bytes()).hexdigest(),
                   scope="Real process launch through normal workspace/license checks to first offscreen login paint; warm cache; no authentication or BOM session.",
                   stage_medians={name: statistics.median(next(e["seconds"] for e in r["events"] if e["stage"] == name)
                                                        for r in reports) for name in names})
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--exe", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--runs", default=3, type=int)
    parser.add_argument("--timeout", default=60, type=float)
    args = parser.parse_args()
    result = run(args.database.resolve(), args.output.resolve(),
                 executable=args.exe.resolve() if args.exe else None, runs=args.runs, timeout=args.timeout)
    print(json.dumps({"version": result["version"], "frozen": result["frozen"],
                      "medians": result["stage_medians"]}, indent=2))
