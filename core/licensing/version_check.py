"""
core/licensing/version_check.py
================================
Signed version check for CreoVCS.

Reads the ``latest_version`` and ``latest_version_sig`` rows from the
``app_metadata`` table in the shared database, verifies the Ed25519 signature,
compares against the running application version, and returns a notification
message if the app is outdated.

Security
--------
The version string is signed with the same Ed25519 private key used for
licenses.  The signature covers::

    canonical_serialize({"latest_version": "<version>"})

A user who edits ``latest_version`` directly in SQLite Browser (or any tool)
invalidates the signature.  The check is **fail-open**: if the signature is
missing, invalid, or the table does not exist, the function returns ``None``
(no notification shown) rather than blocking the user.

Usage
-----
Called from ``main3.py`` once the main window is visible::

    from core.licensing.version_check import check_version_notification
    pub = bytes.fromhex(_PRODUCTION_PUBLIC_KEY)
    msg = check_version_notification(pub)
    if msg:
        show_toast(self, msg)
"""

from __future__ import annotations

import base64
import sqlite3
from pathlib import Path
from typing import Optional

from config import DB_NAME
from core.licensing.crypto import canonical_serialize


def get_latest_version(public_key_bytes: bytes) -> Optional[str]:
    """Read and verify ``latest_version`` from the shared database.

    Returns the version string (e.g. ``"4.1.0"``) if the row exists and the
    Ed25519 signature is valid.  Returns ``None`` on any error (fail-open).

    Args:
        public_key_bytes: Raw 32-byte Ed25519 public key used to verify the
            signature stored alongside the version.

    Returns:
        A semver-style version string, or ``None`` if unavailable / tampered.
    """
    try:
        db_path = Path(DB_NAME)
        if not db_path.exists():
            return None

        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT key, value FROM app_metadata WHERE key IN (?, ?)",
                ("latest_version", "latest_version_sig"),
            )
            rows = {row["key"]: row["value"] for row in cur.fetchall()}

        version = rows.get("latest_version")
        sig_b64 = rows.get("latest_version_sig")

        if not version or not sig_b64:
            return None

        # Verify the signature over the canonical payload.
        payload = {"latest_version": version}
        canonical_bytes = canonical_serialize(payload)

        if not _verify_sig(sig_b64, canonical_bytes, public_key_bytes):
            # Tampered or wrong key — ignore silently.
            return None

        return version

    except Exception:
        return None


def is_outdated(current: str, latest: str) -> bool:
    """Return ``True`` if *current* is strictly older than *latest*.

    Compares version strings by splitting on ``"."`` and casting each part to
    ``int``.  Non-numeric parts are treated as ``0``.

    Args:
        current: The running application version (e.g. ``"4.0.0"``).
        latest:  The published version from the database (e.g. ``"4.1.0"``).

    Returns:
        ``True`` if *current* < *latest*, ``False`` otherwise.
    """
    def _to_tuple(v: str) -> tuple:
        parts = []
        for part in v.strip().split("."):
            try:
                parts.append(int(part))
            except ValueError:
                parts.append(0)
        # Normalise to at least 3 components so comparisons are consistent.
        while len(parts) < 3:
            parts.append(0)
        return tuple(parts)

    return _to_tuple(current) < _to_tuple(latest)


def check_version_notification(public_key_bytes: bytes) -> Optional[str]:
    """Return a human-readable notification string if the app is outdated.

    Compares the running ``APP_VERSION`` against the signed latest version
    stored in the shared database.  Returns ``None`` if up-to-date, if the
    database is unreachable, or if the version record is missing / tampered.

    Args:
        public_key_bytes: Raw 32-byte Ed25519 public key.

    Returns:
        A notification message string, or ``None``.
    """
    try:
        from config import APP_VERSION
    except ImportError:
        return None

    latest = get_latest_version(public_key_bytes)
    if latest is None:
        return None

    if is_outdated(APP_VERSION, latest):
        return (
            f"A newer version of CreoVCS is available ({latest}). "
            f"You are running version {APP_VERSION}. "
            "Please contact your administrator to update."
        )

    return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _verify_sig(sig_b64: str, canonical_bytes: bytes, public_key_bytes: bytes) -> bool:
    """Return ``True`` if *sig_b64* is a valid Ed25519 signature of *canonical_bytes*."""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        sig_bytes = base64.urlsafe_b64decode(sig_b64 + "==")
        if len(sig_bytes) != 64:
            return False

        pub_key = Ed25519PublicKey.from_public_bytes(public_key_bytes)
        pub_key.verify(sig_bytes, canonical_bytes)
        return True

    except Exception:
        return False
