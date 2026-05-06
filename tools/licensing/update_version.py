"""
tools/licensing/update_version.py
===================================
Offline admin tool: sign a version string and publish it to the shared
``creo_vcs.db`` database so that all users receive an update notification.

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
import json
import sqlite3
import sys
from pathlib import Path


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


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Sign a CreoVCS version string and publish it to the shared database, "
            "so that users running an older version see an update notification."
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
        required=True,
        metavar="X.Y.Z",
        help="Version string to publish (e.g. 4.1.0).",
    )
    parser.add_argument(
        "--private-key",
        required=True,
        metavar="KEY",
        help="Path to the hex-encoded Ed25519 private key file (from keygen.py).",
    )
    parser.add_argument(
        "--db",
        required=True,
        metavar="PATH",
        help="Path to the shared creo_vcs.db SQLite database.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for the update_version command-line tool."""
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    print(f"Publishing latest_version = '{args.version}' to '{args.db}' ...")
    try:
        update_version(
            version=args.version,
            private_key_path=Path(args.private_key),
            db_path=Path(args.db),
        )
    except (FileNotFoundError, ValueError, OSError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Done. Users running a version older than {args.version} will be notified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
