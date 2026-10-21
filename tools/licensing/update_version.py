"""
tools/licensing/update_version.py
===================================
Offline admin tool: sign a version string and publish it to the shared
``creo_vcs.db`` database so that all users receive an update notification.

Also publishes verified Nexus folder builds as immutable ZIP packages plus a
signed ``latest.json`` announcement using a separate release signing key.
The original ``--version --private-key --db`` operation is retained unchanged.

Run this on a machine that has the Ed25519 private key.  The tool writes
exactly two rows to the ``app_metadata`` table:

    key = "latest_version"      value = "<version>"
    key = "latest_version_sig"  value = "<base64url Ed25519 signature>"

The signature covers ``canonical_serialize({"latest_version": "<version>"})``
— the same canonical JSON algorithm used for licenses and revocation lists.

Any user who edits ``latest_version`` directly in a SQLite tool invalidates
the signature, and ``version_check.py`` silently skips the notification
(fail-open).  Users cannot forge a valid signature without the private key.

Usage examples
--------------
.. code-block:: powershell

    # Publish version 4.1.0 to the local DB:
    python tools/licensing/update_version.py `
        --version 4.1.0 `
        --private-key keys/ed25519_private.key `
        --db creo_vcs.db

    # Publish to a shared network DB:
    python tools/licensing/update_version.py `
        --version 4.1.0 `
        --private-key keys/ed25519_private.key `
        --db "\\\\\\\\server\\\\share\\\\creo_vcs.db"
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import sqlite3
import stat
import sys
import tempfile
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def update_version(
    version: str,
    private_key_path: Path,
    db_path: Path,
) -> None:
    """Sign *version* and upsert it into ``app_metadata`` in *db_path*.

    Args:
        version: Semver-style version string to publish (e.g. ``"4.1.0"``).
        private_key_path: Path to the hex-encoded Ed25519 private key file.
        db_path: Path to the shared ``creo_vcs.db`` SQLite database.

    Raises:
        FileNotFoundError: If *private_key_path* does not exist.
        FileNotFoundError: If *db_path* does not exist.
        ValueError: If the private key file is malformed.
        RuntimeError: If the ``cryptography`` package is not installed.
        OSError: If the database cannot be written.
    """
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError as exc:
        raise RuntimeError(
            "The 'cryptography' package is required. "
            "Install it with: pip install cryptography"
        ) from exc

    # Reuse canonical_serialize from the project or inline the fallback.
    try:
        from core.licensing.crypto import canonical_serialize
    except ImportError:
        def canonical_serialize(data: dict) -> bytes:  # type: ignore[misc]
            return json.dumps(
                data,
                sort_keys=True,
                separators=(',', ':'),
                ensure_ascii=True,
            ).encode('utf-8')

    # ---- Load private key -------------------------------------------------
    private_key_path = Path(private_key_path)
    if not private_key_path.exists():
        raise FileNotFoundError(
            f"Private key file not found: '{private_key_path}'."
        )

    try:
        priv_hex = private_key_path.read_text(encoding="ascii").strip()
        priv_bytes = bytes.fromhex(priv_hex)
    except ValueError as exc:
        raise ValueError(f"Private key file is not valid hex: {exc}") from exc

    if len(priv_bytes) != 32:
        raise ValueError(
            f"Private key must be 32 bytes (got {len(priv_bytes)})."
        )

    try:
        private_key = Ed25519PrivateKey.from_private_bytes(priv_bytes)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Could not load private key: {exc}") from exc

    # ---- Check DB exists --------------------------------------------------
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(
            f"Database file not found: '{db_path}'. "
            "Run the application once first to create it."
        )

    # ---- Sign the version string ------------------------------------------
    payload = {"latest_version": version}
    canonical_bytes = canonical_serialize(payload)
    sig_raw: bytes = private_key.sign(canonical_bytes)
    sig_b64 = base64.urlsafe_b64encode(sig_raw).rstrip(b"=").decode("ascii")

    # ---- Upsert into app_metadata -----------------------------------------
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_metadata (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO app_metadata (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            ("latest_version", version),
        )
        conn.execute(
            "INSERT INTO app_metadata (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            ("latest_version_sig", sig_b64),
        )
        conn.commit()


def generate_release_keypair(directory: Path) -> tuple[Path, Path]:
    """Explicit administrator action; never overwrite an existing signing key."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    directory = Path(directory)
    private_path = directory / "release_private.key"
    public_path = directory / "release_public.key"
    if private_path.exists() or public_path.exists():
        raise FileExistsError("Release key files already exist; refusing to overwrite them.")
    directory.mkdir(parents=True, exist_ok=True)
    private = Ed25519PrivateKey.generate()
    private_hex = private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                        serialization.NoEncryption()).hex()
    public_hex = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    descriptor = os.open(private_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="ascii") as stream:
        stream.write(private_hex + "\n")
    with public_path.open("x", encoding="ascii") as stream:
        stream.write(public_hex + "\n")
    return private_path, public_path


def _release_public_key(private_path: Path) -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    raw = bytes.fromhex(Path(private_path).read_text(encoding="ascii").strip())
    private = Ed25519PrivateKey.from_private_bytes(raw)
    return private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()


def _is_link(path: Path) -> bool:
    info = path.lstat()
    return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0)
                                     & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _source_files(package_dir: Path) -> list[Path]:
    """Reject links, private material and workspace data before creating a ZIP."""
    unsafe_suffixes = {".db", ".db3", ".db-wal", ".db-shm", ".db-journal", ".sqlite", ".sqlite3",
                       ".sqlite-wal", ".sqlite-shm", ".sqlite-journal", ".lic", ".license", ".key", ".pfx", ".p12"}
    unsafe_names = {".git", ".aws", ".codex", ".env", "id_rsa", "id_ed25519",
                    "working_directory", "working_directories", "project_versions"}
    result = []
    if _is_link(package_dir):
        raise ValueError("Package directory must not be a symbolic link or junction.")
    def fail_scan(error):
        raise error

    for directory, names, filenames in os.walk(package_dir, followlinks=False, onerror=fail_scan):
        for name in names + filenames:
            path = Path(directory) / name
            if _is_link(path):
                raise ValueError("Release packages cannot contain symbolic links or junctions: " + str(path))
            lowered = path.name.casefold()
            if lowered in unsafe_names:
                raise ValueError("Private workspace directory or file in release: " + str(path))
            if path.is_file():
                if (path.suffix.casefold() in unsafe_suffixes or lowered.startswith("trail.txt")
                        or "private_key" in lowered or "ed25519_private" in lowered):
                    raise ValueError("Private key, license or workspace file in release: " + str(path))
                if path.suffix.casefold() == ".pem":
                    # Runtime CA bundles (for example certifi/cacert.pem) are
                    # public resources. A PEM private key must never ship.
                    with path.open("rb") as stream:
                        content = stream.read(8 * 1024 * 1024 + 1)
                    public_markers = (b"-----BEGIN CERTIFICATE-----", b"-----BEGIN TRUSTED CERTIFICATE-----",
                                      b"-----BEGIN PUBLIC KEY-----", b"-----BEGIN RSA PUBLIC KEY-----")
                    if (len(content) > 8 * 1024 * 1024 or b"PRIVATE KEY-----" in content
                            or not any(marker in content for marker in public_markers)):
                        raise ValueError("Private or unrecognized PEM key material in release: " + str(path))
                result.append(path)
    if not result:
        raise ValueError("Package directory is empty.")
    return sorted(result, key=lambda path: path.relative_to(package_dir).as_posix())


def _read_build_manifest(package_dir: Path, version: str, public_key_hex: str) -> tuple[dict, str]:
    from core.updates.protocol import MAX_MANIFEST_BYTES, hash_file

    path = package_dir / "build_manifest.json"
    if not path.is_file() or path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError("A valid build_manifest.json is required in the built application folder.")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("app") != "Nexus" or manifest.get("mode") != "onedir":
        raise ValueError("The package must be a Nexus folder build.")
    if manifest.get("version") != version:
        raise ValueError("Published version does not match build_manifest.json.")
    if str(manifest.get("release_public_key", "")).lower() != public_key_hex:
        raise ValueError("Release signing key does not match the public key pinned into this build.")
    reports = manifest.get("smoke_tests") or {}
    for executable in ("Nexus.exe", "CADViewer.exe"):
        report = reports.get(executable) or {}
        checks = report.get("checks") or {}
        if (report.get("ok") is not True or report.get("frozen") is not True
                or any((checks.get(name) or {}).get("ok") is not True
                       for name in ("pdf", "step", "qt_viewer", "application"))):
            raise ValueError("Both executables must pass all four packaged smoke diagnostics: " + executable)
    bootstrap_reports = manifest.get("bootstrap_tests") or {}
    for executable in ("NexusLauncher.exe", "NexusUpdater.exe"):
        report = bootstrap_reports.get(executable) or {}
        if (report.get("ok") is not True or report.get("frozen") is not True
                or report.get("release_public_key") != public_key_hex):
            raise ValueError("Both bootstrap executables must pass their pinned-key self-test: " + executable)
    for required in ("Nexus.exe", "CADViewer.exe", "bootstrap/NexusLauncher.exe", "bootstrap/NexusUpdater.exe"):
        if not (package_dir / required).is_file():
            raise ValueError("Required release executable is missing: " + required)
    if not (package_dir / "_internal").is_dir() or not (package_dir / "bootstrap/_internal").is_dir():
        raise ValueError("Both application and bootstrap shared runtime folders are required.")
    return manifest, str(manifest.get("build_id") or hash_file(path)[:16])


def _write_atomic(path: Path, content: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _verify_existing_archive(archive: Path, source: Path, files: list[Path]) -> None:
    """Reuse an immutable package only when its entire contents are identical."""
    from core.updates.protocol import hash_file

    expected = {path.relative_to(source).as_posix(): path for path in files}
    with zipfile.ZipFile(archive) as bundle:
        entries = bundle.infolist()
        if len(entries) != len(expected) or {entry.filename for entry in entries} != set(expected):
            raise ValueError("Existing immutable archive differs from the supplied application folder.")
        for entry in entries:
            path = expected[entry.filename]
            if (entry.is_dir() or stat.S_ISLNK(entry.external_attr >> 16) or entry.flag_bits & 1
                    or entry.file_size != path.stat().st_size):
                raise ValueError("Existing immutable archive differs from the supplied application folder.")
            digest = hashlib.sha256()
            with bundle.open(entry) as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != hash_file(path):
                raise ValueError("Existing immutable archive differs from the supplied application folder.")


def publish_release(
    package_dir: Path, feed_dir: Path, release_key_path: Path, *, version: str,
    release_sequence: int, schema_min: int, schema_max: int, notes: str = "",
    expires_days: int = 90, minimum_updater: str = "1.0.0", now: datetime | None = None,
    allow_rollback: bool = False,
) -> dict:
    """Publish an immutable ZIP, then atomically activate its signed announcement.

    Existing expired announcements still establish the sequence floor: expiry
    prevents clients installing stale metadata, not administrators publishing a
    fresh successor. Private keys and company databases are never copied.
    """
    from core.updates.protocol import (canonical_json, hash_file, load_manifest, sign_manifest,
                                       validate_payload, version_tuple, verify_manifest)

    version_tuple(version)
    version_tuple(minimum_updater)
    if type(expires_days) is not int or not 1 <= expires_days <= 3650:
        raise ValueError("expires_days must be between 1 and 3650.")
    current = datetime.now(timezone.utc) if now is None else now
    if current.tzinfo is None:
        raise ValueError("Publication time must be timezone-aware.")
    source_input = Path(package_dir).absolute()
    if not source_input.is_dir():
        raise ValueError("Built package directory does not exist.")
    if _is_link(source_input):
        raise ValueError("Package directory must not be a symbolic link or junction.")
    source = source_input.resolve()
    feed = Path(feed_dir).resolve()
    if feed == source or source in feed.parents:
        raise ValueError("Release feed must be outside the packaged application folder.")
    public_key_hex = _release_public_key(Path(release_key_path))
    files = _source_files(source)
    build, build_id = _read_build_manifest(source, version, public_key_hex)
    if build.get("supported_schema") != {"min": schema_min, "max": schema_max}:
        raise ValueError("Published schema bounds must match build_manifest.json supported_schema exactly.")
    archive_name = f"Nexus-{version}-{build_id}.zip"
    payload = {
        "schema": 1, "product": "Nexus", "platform": "windows-x64", "version": version,
        "build_id": build_id, "release_sequence": release_sequence,
        "expires_at": (current.astimezone(timezone.utc) + timedelta(days=expires_days)).isoformat(),
        "package": {"path": "releases/" + archive_name, "size": 1, "sha256": "0" * 64},
        "supported_schema": {"min": schema_min, "max": schema_max},
        "min_updater_version": minimum_updater, "release_notes": notes,
    }
    validate_payload(payload)
    feed.mkdir(parents=True, exist_ok=True)
    releases = feed / "releases"
    if releases.exists() and _is_link(releases):
        raise ValueError("Feed releases directory must not be a symbolic link or junction.")
    releases.mkdir(exist_ok=True)
    latest = feed / "latest.json"
    archive = releases / archive_name
    lock = feed / ".publish.lock"
    descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    temporary = None
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as stream:
            stream.write(str(os.getpid()))
        if latest.exists():
            # Ignore freshness only for this administrative sequence comparison;
            # signature and payload validation remain mandatory.
            previous = load_manifest(latest, public_key_hex, now=datetime.min.replace(tzinfo=timezone.utc))
            if release_sequence <= previous["release_sequence"]:
                raise ValueError("Release sequence must be greater than the currently published sequence.")
            if version_tuple(version) < version_tuple(previous["version"]) and not allow_rollback:
                raise ValueError("Refusing to publish a version older than the current release.")
        reusing_archive = archive.exists()
        if reusing_archive and not allow_rollback:
            raise FileExistsError("This immutable release already exists: " + str(archive))
        before = {path.relative_to(source).as_posix(): (path.stat().st_size, path.stat().st_mtime_ns)
                  for path in files}
        if reusing_archive:
            _verify_existing_archive(archive, source, files)
            package_path = archive
        else:
            descriptor, name = tempfile.mkstemp(prefix=".package-", suffix=".tmp", dir=releases)
            os.close(descriptor)
            temporary = Path(name)
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
                for path in files:
                    bundle.write(path, path.relative_to(source).as_posix())
            package_path = temporary
        after_files = _source_files(source)
        after = {path.relative_to(source).as_posix(): (path.stat().st_size, path.stat().st_mtime_ns)
                 for path in after_files}
        if after != before:
            raise ValueError("Built application changed during publication; rebuild or retry from an idle folder.")
        with zipfile.ZipFile(package_path) as bundle:
            if bundle.testzip() is not None:
                raise ValueError("Release ZIP verification failed.")
        if temporary is not None:
            with temporary.open("r+b") as stream:
                stream.flush()
                os.fsync(stream.fileno())
        payload["package"] = {"path": "releases/" + archive_name,
                              "size": package_path.stat().st_size, "sha256": hash_file(package_path)}
        envelope = sign_manifest(payload, release_key_path)
        verify_manifest(envelope, public_key_hex, now=current)
        if temporary is not None:
            os.rename(temporary, archive)
            temporary = None
        if archive.stat().st_size != payload["package"]["size"] or hash_file(archive) != payload["package"]["sha256"]:
            raise ValueError("Published release archive failed final hash verification.")
        _write_atomic(latest, canonical_json(envelope))
        return payload
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        lock.unlink(missing_ok=True)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Publish a signed Nexus release package, generate dedicated release keys, "
            "or publish the original signed version announcement to the shared database."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python tools/licensing/update_version.py \\\n"
            "      --version 4.1.0 \\\n"
            "      --private-key keys/ed25519_private.key \\\n"
            "      --db creo_vcs.db\n"
        ),
    )
    parser.add_argument(
        "--version",
        metavar="X.Y.Z",
        help="Version string to publish (e.g. 4.1.0).",
    )
    parser.add_argument(
        "--private-key",
        metavar="KEY",
        help="Path to the hex-encoded Ed25519 private key file (from keygen.py).",
    )
    parser.add_argument(
        "--db",
        metavar="PATH",
        help="Path to the shared creo_vcs.db SQLite database.",
    )
    parser.add_argument("--keygen", type=Path, metavar="DIR", help="Explicitly create separate release signing keys.")
    parser.add_argument("--package-dir", type=Path, help="Built Nexus folder, including bootstrap.")
    parser.add_argument("--feed-dir", type=Path, help="Local or UNC company update feed directory.")
    parser.add_argument("--release-key", type=Path, help="Dedicated release private key; never the license key.")
    parser.add_argument("--sequence", type=int, help="Strictly increasing release publication sequence.")
    parser.add_argument("--notes", default="", help="Release notes included in signed metadata.")
    parser.add_argument("--expires-days", type=int, default=90, help="Signed announcement lifetime (default 90 days).")
    parser.add_argument("--schema-min", type=int, help="Minimum database schema supported by this app.")
    parser.add_argument("--schema-max", type=int, help="Maximum database schema supported by this app.")
    parser.add_argument("--minimum-updater", default="1.0.0", help="Minimum compatible bootstrap updater version.")
    parser.add_argument("--allow-rollback", action="store_true", help="Explicitly announce an older release with a higher sequence; never restore a database.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for the update_version command-line tool."""
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    try:
        if args.keygen is not None:
            if any(value is not None for value in (args.package_dir, args.feed_dir, args.release_key,
                                                   args.version, args.private_key, args.db, args.sequence)):
                parser.error("--keygen is a separate operation; do not combine it with publication options.")
            private, public = generate_release_keypair(args.keygen)
            print(f"Created dedicated release keys:\nPrivate (keep secret): {private}\nPublic: {public}")
            return 0
        publishing = any(value is not None for value in (args.package_dir, args.feed_dir, args.release_key, args.sequence))
        if publishing:
            required = {"--package-dir": args.package_dir, "--feed-dir": args.feed_dir,
                        "--release-key": args.release_key, "--sequence": args.sequence, "--version": args.version,
                        "--schema-min": args.schema_min, "--schema-max": args.schema_max}
            missing = [name for name, value in required.items() if value is None]
            if missing:
                parser.error("release publication requires " + ", ".join(missing))
            if bool(args.db) != bool(args.private_key):
                parser.error("Legacy announcement requires both --db and --private-key (license signing key).")
            if args.db and not Path(args.db).is_file():
                raise FileNotFoundError("Legacy announcement database does not exist: " + args.db)
            if args.private_key:
                _release_public_key(Path(args.private_key))
            payload = publish_release(args.package_dir, args.feed_dir, args.release_key, version=args.version,
                                      release_sequence=args.sequence, notes=args.notes, expires_days=args.expires_days,
                                      schema_min=args.schema_min, schema_max=args.schema_max,
                                      minimum_updater=args.minimum_updater, allow_rollback=args.allow_rollback)
            print(f"Published signed release {payload['version']} ({payload['build_id']}) to {args.feed_dir}")
            if args.db:
                try:
                    update_version(args.version, Path(args.private_key), Path(args.db))
                except (ValueError, OSError, RuntimeError, sqlite3.Error) as exc:
                    raise RuntimeError("Release feed was published, but the optional legacy database announcement failed: " + str(exc)) from exc
            return 0
        if not args.version or not args.private_key or not args.db:
            parser.error("Legacy announcement requires --version, --private-key and --db.")
        if args.allow_rollback:
            parser.error("--allow-rollback applies only to signed release package publication.")
        print(f"Publishing latest_version = '{args.version}' to '{args.db}' ...")
        update_version(
            version=args.version,
            private_key_path=Path(args.private_key),
            db_path=Path(args.db),
        )
    except (ValueError, OSError, RuntimeError, sqlite3.Error, zipfile.BadZipFile) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Done. Users running a version older than {args.version} will be notified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
