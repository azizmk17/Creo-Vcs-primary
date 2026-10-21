"""Local, versioned installation and verified update activation.

Application versions are immutable folders. Only current.json is replaced at
activation; workspace databases and licenses are never copied or rolled back.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from .protocol import hash_file, load_manifest, version_tuple

UPDATER_VERSION = "1.0.0"
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
_PRIVATE_SUFFIXES = {".db", ".db3", ".db-wal", ".db-shm", ".db-journal", ".sqlite", ".sqlite3", ".lic", ".license", ".key", ".pfx", ".p12"}
_PRIVATE_NAMES = {".git", ".aws", ".codex", ".env", "working_directory", "working_directories", "project_versions", "id_rsa", "id_ed25519", "private_key", "private_key.pem"}
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


class InstallError(RuntimeError):
    pass


def default_install_root() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Nexus"


def _root(root=None) -> Path:
    return Path(root or default_install_root()).resolve()


def installed_root_for_executable(executable=None) -> Path | None:
    path = Path(executable or sys.executable).resolve()
    if path.parent.name.casefold() == "bootstrap":
        candidate = path.parent.parent
    elif path.parent.parent.name.casefold() == "versions":
        candidate = path.parent.parent.parent
    else:
        return None
    if (candidate / "install.json").is_file() and (candidate / "current.json").is_file():
        return candidate
    return None


def _read_json(path: Path, default=None):
    if not path.exists():
        return {} if default is None else default
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InstallError(f"Invalid installation metadata: {path.name}") from exc
    if not isinstance(value, dict):
        raise InstallError(f"Invalid installation metadata: {path.name}")
    return value


def _atomic_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _try_lock(stream) -> bool:
    stream.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(stream):
    stream.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def _lock(path: Path, *, timeout=0.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if path.stat().st_size == 0:
            stream.write(b"\0")
            stream.flush()
        deadline = time.monotonic() + timeout
        while not _try_lock(stream):
            if time.monotonic() >= deadline:
                raise InstallError("Another installation or activation operation is running. Try again when it finishes.")
            time.sleep(0.1)
        try:
            yield stream
        finally:
            _unlock(stream)


@contextlib.contextmanager
def acquire_run_lease(root=None):
    """Keep an OS lock for this process lifetime; stale PID files grant no lease."""
    root = _root(root)
    lease = root / "leases" / f"{os.getpid()}-{uuid.uuid4().hex}.json"
    stream = None
    with _lock(root / "state.lock", timeout=10):
        lease.parent.mkdir(parents=True, exist_ok=True)
        stream = lease.open("w+b")
        stream.write(b"\0" + json.dumps({"pid": os.getpid(), "executable": sys.executable}).encode("utf-8"))
        stream.flush()
        if not _try_lock(stream):
            stream.close()
            raise InstallError("Could not establish the application run lease.")
    try:
        yield lease
    finally:
        _unlock(stream)
        stream.close()
        lease.unlink(missing_ok=True)


def live_processes(root=None) -> list[dict]:
    """Return held leases, relying on OS locks rather than reusable process IDs."""
    result = []
    for lease in (_root(root) / "leases").glob("*.json"):
        try:
            with lease.open("r+b") as stream:
                if _try_lock(stream):
                    _unlock(stream)
                    stale = True
                else:
                    stale = False
                    stream.seek(1)
                    try:
                        info = json.loads(stream.read().decode("utf-8"))
                    except (ValueError, UnicodeError):
                        info = {"pid": None, "executable": "Nexus or CADViewer"}
                    result.append(info)
            if stale:
                lease.unlink(missing_ok=True)
        except FileNotFoundError:
            continue
    return result


def _safe_relative(value: str) -> PurePosixPath:
    if not isinstance(value, str) or any(character in value for character in '\\<>:"|?*') or any(ord(character) < 32 for character in value):
        raise InstallError("Unsafe package or installation path")
    path = PurePosixPath(value)
    if not value or path.is_absolute() or any(part in {"", ".", ".."} for part in value.rstrip("/").split("/")):
        raise InstallError("Unsafe package or installation path")
    for part in path.parts:
        if part.endswith((".", " ")) or part.split(".")[0].upper() in _RESERVED:
            raise InstallError("Unsafe Windows package path")
    return path


def _under(root: Path, relative: str) -> Path:
    path = root.joinpath(*_safe_relative(relative).parts).resolve()
    if not path.is_relative_to(root.resolve()):
        raise InstallError("Installation path escapes its root")
    return path


def _version_directory(payload: dict) -> str:
    version_tuple(payload["version"])
    build = payload["build_id"]
    if not isinstance(build, str) or not _TOKEN.fullmatch(build) or build in {".", ".."}:
        raise InstallError("Invalid release build identifier")
    return f"versions/{payload['version']}-{build}"


def current_application(root=None) -> Path:
    root = _root(root)
    current = _read_json(root / "current.json")
    relative = current.get("directory", "")
    if not relative.startswith("versions/") or len(PurePosixPath(relative).parts) != 2:
        raise InstallError("No valid active Nexus installation. Run the installer first.")
    executable = _under(root, relative) / "Nexus.exe"
    if not executable.is_file():
        raise InstallError("The active Nexus executable is missing. Reinstall the trusted application folder.")
    return executable


def _private_path(path: PurePosixPath) -> bool:
    return any(part.casefold() in _PRIVATE_NAMES for part in path.parts) or path.suffix.casefold() in _PRIVATE_SUFFIXES


def _is_link(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _tree_inventory(directory: Path) -> dict:
    inventory = {}
    for path in directory.rglob("*"):
        if _is_link(path):
            raise InstallError("Application folders cannot contain links or junctions")
        if path.is_file():
            inventory[path.relative_to(directory).as_posix()] = (path.stat().st_size, hash_file(path))
    return inventory


def _zip_inventory(archive: zipfile.ZipFile) -> dict:
    inventory = {}
    seen = set()
    total = 0
    for info in archive.infolist():
        path = _safe_relative(info.filename)
        folded = str(path).casefold()
        if folded in seen:
            raise InstallError("Package contains duplicate Windows paths")
        seen.add(folded)
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode) or (info.flag_bits & 1):
            raise InstallError("Package links and encrypted entries are not allowed")
        if _private_path(path):
            raise InstallError(f"Package contains private workspace or license content: {path}")
        if info.is_dir():
            continue
        total += info.file_size
        if total > 16 * 1024 ** 3 or len(seen) > 100000:
            raise InstallError("Package exceeds installation size limits")
        # Bootstrap is installed only from the trusted manual installer. An
        # update can never replace its currently executing updater/launcher.
        if path.parts[0].casefold() == "bootstrap":
            continue
        digest = hashlib.sha256()
        with archive.open(info) as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        inventory[str(path)] = (info.file_size, digest.hexdigest())
    for required in ("Nexus.exe", "CADViewer.exe"):
        if required not in inventory:
            raise InstallError(f"Package is missing {required}")
    if not any(path.startswith("_internal/") for path in inventory):
        raise InstallError("Package is missing its DLL/runtime folder")
    return inventory


def _copy_package(source: Path, destination: Path):
    with source.open("rb") as incoming, destination.open("wb") as outgoing:
        shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)


def _smoke_version(directory: Path, reports: Path):
    reports.mkdir(parents=True, exist_ok=True)
    for name in ("Nexus.exe", "CADViewer.exe"):
        result_path = reports / (Path(name).stem + ".json")
        env = os.environ.copy()
        for key in list(env):
            upper = key.upper()
            if upper.startswith(("PYTHON", "CONDA", "QT_", "CSF_", "OCCT_")) or upper in {"CASROOT", "NEXUS_INSTALL_ROOT"}:
                env.pop(key, None)
        env["QT_QPA_PLATFORM"] = "windows"
        system = Path(os.environ.get("SystemRoot", "C:/Windows"))
        env["PATH"] = os.pathsep.join([str(system / "System32"), str(system)])
        completed = subprocess.run(
            [str(directory / name), "--packaging-smoke", str(result_path)],
            cwd=str(directory), env=env, timeout=180, check=False,
        )
        result = _read_json(result_path)
        checks = result.get("checks", {})
        complete = all(isinstance(checks.get(name), dict) and checks[name].get("ok") is True for name in ("pdf", "step", "qt_viewer", "application"))
        if completed.returncode or result.get("ok") is not True or result.get("frozen") is not True or not complete:
            raise InstallError(f"The staged {name} failed its PDF/CAD/runtime check. The current application was kept.")


def initial_install(source, root=None, feed="", public_key_hex="") -> dict:
    """Copy an explicitly trusted distribution; never obtain trust from a feed."""
    source, root = Path(source).resolve(), _root(root)
    if source == root or root.is_relative_to(source) or source.is_relative_to(root):
        raise InstallError("The source distribution and installation must be separate folders")
    for relative in ("Nexus.exe", "CADViewer.exe", "bootstrap/NexusLauncher.exe", "bootstrap/NexusUpdater.exe"):
        if not (source / relative).is_file():
            raise InstallError(f"Trusted distribution is missing {relative}")
    if not (source / "_internal").is_dir() or not (source / "bootstrap/_internal").is_dir():
        raise InstallError("The complete application and bootstrap runtime folders are required")
    manifest = _read_json(source / "build_manifest.json")
    try:
        if len(bytes.fromhex(public_key_hex)) != 32:
            raise ValueError("wrong key length")
    except (TypeError, ValueError) as exc:
        raise InstallError("The trusted installer must have a baked 32-byte release public key") from exc
    if str(manifest.get("release_public_key", "")).lower() != public_key_hex.lower():
        raise InstallError("The distribution release public key does not match this trusted installer")
    supported_schema = manifest.get("supported_schema")
    if (not isinstance(supported_schema, dict) or set(supported_schema) != {"min", "max"}
            or any(type(supported_schema.get(key)) is not int for key in ("min", "max"))
            or not 0 <= supported_schema["min"] <= supported_schema["max"]):
        raise InstallError("The distribution must declare a valid supported database schema range")
    version = manifest.get("version", "")
    version_tuple(version)
    build_id = manifest.get("build_id") or "initial-" + hash_file(source / "build_manifest.json")[:12]
    payload = {"version": version, "build_id": build_id, "release_sequence": 0}
    relative = _version_directory(payload)
    source_inventory = _tree_inventory(source)
    for name in source_inventory:
        if _private_path(PurePosixPath(name)):
            raise InstallError(f"Trusted distribution contains private workspace or license content: {name}")
    with _lock(root / "stage.lock"), _lock(root / "state.lock"):
        if (root / "current.json").exists():
            raise InstallError("Nexus is already installed here. Use the updater or choose a separate installation folder.")
        target = _under(root, relative)
        if target.exists() or (root / "bootstrap").exists():
            raise InstallError("An incomplete installation already exists. Choose an empty installation folder.")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target, ignore=lambda path, names: ["bootstrap"] if Path(path) == source and "bootstrap" in names else [])
        shutil.copytree(source / "bootstrap", root / "bootstrap")
        _atomic_json(root / "install.json", {"schema": 1, "feed": str(feed), "installed_at": int(time.time())})
        current = {**payload, "directory": relative, "supported_schema": supported_schema}
        _atomic_json(root / "current.json", current)
    return current


def _check_release(payload: dict, current: dict, *, allow_rollback=False):
    if version_tuple(payload["min_updater_version"]) > version_tuple(UPDATER_VERSION):
        raise InstallError("This release requires a newer trusted Nexus installer. Update the bootstrap manually.")
    if current and version_tuple(payload["version"]) < version_tuple(current["version"]) and not allow_rollback:
        raise InstallError("A normal update cannot downgrade Nexus")
    if (current and payload["version"] == current["version"]
            and payload["build_id"] != current.get("build_id") and not allow_rollback):
        raise InstallError("A different build must use a new application version; normal updates cannot replace an existing version")


def _last_sequence(root: Path, current: dict, public_key_hex="") -> int:
    from . import settings
    observed = settings.last_sequence(public_key_hex) if public_key_hex else 0
    return max(int(current.get("release_sequence", 0)), int(_read_json(root / "install.json").get("last_sequence", 0)), observed)


def stage_release(feed, public_key_hex, root=None, *, smoke_runner=None, db_path=None, allow_rollback=False) -> dict:
    root, feed = _root(root), Path(feed).resolve()
    with _lock(root / "stage.lock"):
        current = _read_json(root / "current.json")
        sequence = _last_sequence(root, current, public_key_hex)
        payload = load_manifest(feed / "latest.json", public_key_hex, last_sequence=sequence)
        from . import settings
        settings.remember_sequence(public_key_hex, payload["release_sequence"])
        with _lock(root / "state.lock", timeout=10):
            installation = _read_json(root / "install.json")
            installation["last_sequence"] = max(_last_sequence(root, current, public_key_hex), payload["release_sequence"])
            _atomic_json(root / "install.json", installation)
        _check_release(payload, current, allow_rollback=allow_rollback)
        relative = _version_directory(payload)
        package_source = _under(feed, payload["package"]["path"])
        token = uuid.uuid4().hex
        work = _under(root, f"staging/{token}")
        work.mkdir(parents=True)
        manifest_path = work / "latest.json"
        shutil.copyfile(feed / "latest.json", manifest_path)
        # Reverify the copied announcement to close a feed-change race.
        copied = load_manifest(manifest_path, public_key_hex, last_sequence=sequence)
        if copied != payload:
            raise InstallError("The update announcement changed while staging; try again")
        package = work / "package.zip"
        _copy_package(package_source, package)
        if package.stat().st_size != payload["package"]["size"] or hash_file(package) != payload["package"]["sha256"]:
            raise InstallError("The update archive size or SHA-256 does not match its signed announcement")
        extracted = _under(root, f"staging/{token}/application")
        extracted.mkdir()
        with zipfile.ZipFile(package) as archive:
            inventory = _zip_inventory(archive)
            for name in inventory:
                target = _under(extracted, name)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(name) as incoming, target.open("wb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
        if _tree_inventory(extracted) != inventory:
            raise InstallError("The extracted application does not match its verified archive")
        (smoke_runner or _smoke_version)(extracted, work / "smoke")
        target = _under(root, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if _tree_inventory(target) != inventory:
                raise InstallError("A different application already occupies this version folder")
            shutil.rmtree(_under(root, f"staging/{token}/application"))
        else:
            os.replace(extracted, target)
        staged = {
            "version": payload["version"], "build_id": payload["build_id"],
            "directory": relative, "manifest": f"staging/{token}/latest.json",
            "package": f"staging/{token}/package.zip", "payload": payload,
        }
        if db_path:
            staged["db_path"] = str(Path(db_path).resolve())
        with _lock(root / "state.lock", timeout=10):
            current = _read_json(root / "current.json")
            _check_release(payload, current, allow_rollback=allow_rollback)
            if payload["release_sequence"] < current.get("release_sequence", 0):
                raise InstallError("A newer release was activated while this one was staging")
            installation = _read_json(root / "install.json")
            installation["last_sequence"] = max(_last_sequence(root, current, public_key_hex), payload["release_sequence"])
            _atomic_json(root / "install.json", installation)
            _atomic_json(root / "staged.json", staged)
        return staged


def activate_staged(root=None, public_key_hex="", *, schema_version=None, db_path=None, allow_rollback=False) -> dict:
    root = _root(root)
    with _lock(root / "stage.lock"), _lock(root / "state.lock", timeout=10):
        active = live_processes(root)
        if active:
            raise InstallError("Close all Nexus and CADViewer windows before activating the update. No process was stopped.")
        current = _read_json(root / "current.json")
        staged = _read_json(root / "staged.json")
        if not staged:
            raise InstallError("There is no staged update to activate")
        manifest = _under(root, staged.get("manifest", ""))
        package = _under(root, staged.get("package", ""))
        if not manifest.is_relative_to(root / "staging") or not package.is_relative_to(root / "staging"):
            raise InstallError("Invalid staged update metadata")
        payload = load_manifest(manifest, public_key_hex, last_sequence=_last_sequence(root, current, public_key_hex))
        _check_release(payload, current, allow_rollback=allow_rollback)
        relative = _version_directory(payload)
        if staged.get("directory") != relative:
            raise InstallError("The staged version path does not match its signed announcement")
        if package.stat().st_size != payload["package"]["size"] or hash_file(package) != payload["package"]["sha256"]:
            raise InstallError("The staged package changed after verification")
        with zipfile.ZipFile(package) as archive:
            inventory = _zip_inventory(archive)
        if _tree_inventory(_under(root, relative)) != inventory:
            raise InstallError("The staged application changed after its runtime checks")
        db_path = db_path or staged.get("db_path")
        if db_path:
            from .compatibility import check_database
            state = check_database(db_path, app_version=payload["version"], max_schema=payload["supported_schema"]["max"])
            schema_version = state["schema"]
        downgrade = current and version_tuple(payload["version"]) < version_tuple(current["version"])
        if schema_version is not None:
            supported = payload["supported_schema"]
            if not supported["min"] <= schema_version <= supported["max"]:
                raise InstallError("The selected release cannot read the current workspace database schema")
        elif downgrade and not db_path:
            raise InstallError("Rollback requires a known compatible workspace database schema; databases are never restored automatically")
        previous = current.get("directory")
        activated = {key: payload[key] for key in ("version", "build_id", "release_sequence", "supported_schema")}
        activated.update(directory=relative, previous_directory=previous)
        _atomic_json(root / "current.json", activated)
        (root / "staged.json").unlink(missing_ok=True)
        return activated
