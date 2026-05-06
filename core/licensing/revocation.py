"""
core/licensing/revocation.py
============================
Signed file-based license revocation check for CreoVCS.

The revocation list is a JSON file stored in the shared network folder
alongside ``creo_vcs.db``.  It is **cryptographically signed** with the same
Ed25519 private key used to sign licenses.  This means:

* Any user can **read** the file (they only see opaque base64url strings).
* No user can **forge** or **silently modify** the file — without the private
  key, any edit produces an invalid ``"rl_signature"``.
* A file with an invalid signature is **ignored** (fail-open) rather than
  causing an error, so a corrupt or missing file never locks out legitimate
  users.

Fail-open vs fail-closed
-------------------------
This module uses **fail-closed**: if the revocation file is missing, unreadable,
or has an invalid/tampered signature, the app blocks with a ``LicenseError``.
This prevents the bypass-by-deletion attack (deleting ``revoked.json`` to
avoid being blocked).

A valid signed ``revoked.json`` must always exist alongside the database.
On a fresh installation with no revocations yet, generate an empty signed
file using the vendor's signing tool and ship it alongside ``creovcs.lic``.

Signed revocation file format
------------------------------
Produced by ``tools/licensing/sign_revocation.py``::

    {
        "issued_at":    "2026-02-18T12:00:00+00:00",
        "revoked":      [
            "base64url-license-signature-1",
            "base64url-license-signature-2"
        ],
        "rl_signature": "base64url-Ed25519-sig-of-canonical-payload"
    }

The canonical payload that is signed is::

    canonical_serialize({"issued_at": "...", "revoked": [...]})

i.e. the full dict with ``"rl_signature"`` removed, serialised with sorted
keys and no optional whitespace — identical to the license signing algorithm.

Admin workflow
--------------
To revoke a license, run the offline signing tool on a machine with the
private key, then copy the output to the shared folder::

    python tools/licensing/sign_revocation.py \\
        --add "base64urlSigOfRevokedLicense" \\
        --existing \\\\\\\\server\\\\share\\\\revoked.json \\
        --private-key keys/ed25519_private.key \\
        --out \\\\\\\\server\\\\share\\\\revoked.json

No application rebuild or redistribution is required.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Optional

from core.licensing.crypto import canonical_serialize
from core.licensing.exceptions import LicenseError


def check_file_revocation(
    signature: str,
    revocation_path: Path,
    public_key_bytes: bytes,
) -> None:
    """Check whether *signature* appears in the signed revocation file.

    Reads ``revoked.json`` at *revocation_path*, verifies its Ed25519
    ``"rl_signature"`` field, then checks whether *signature* is listed
    under ``"revoked"``.

    The function is **fail-closed**: if the file is missing, unreadable,
    or has an invalid/tampered signature, a ``LicenseError`` is raised.
    Only a cryptographically valid file whose ``"revoked"`` list does NOT
    contain *signature* allows the app to proceed.

    Args:
        signature: The base64url signature string from the loaded
            ``LicensePayload.signature`` field.
        revocation_path: Filesystem path to ``revoked.json`` in the shared
            folder (e.g. ``Path(DB_NAME).parent / "revoked.json"``).
        public_key_bytes: Raw 32-byte Ed25519 public key — the same bytes
            used to verify licenses.  Passed through from
            ``LicenseManager.initialize()``; never stored.

    Raises:
        LicenseError: If the revocation file is missing, unreadable, or
            has an invalid signature — or if *signature* appears in
            ``"revoked"``.

    Example::

        check_file_revocation(
            payload.signature,
            Path("revoked.json"),
            public_key_bytes,
        )
    """
    revoked_set = _load_revoked_set(revocation_path, public_key_bytes)
    if revoked_set is None:
        # File missing, unreadable, or tampered — fail CLOSED.
        raise LicenseError(
            "License revocation status cannot be verified. "
            "The revocation file is missing or has been tampered with. "
            "Contact your administrator."
        )

    if signature in revoked_set:
        raise LicenseError(
            "This license has been revoked by the administrator. "
            "Contact your administrator for a new license."
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_revoked_set(
    path: Path,
    public_key_bytes: bytes,
) -> Optional[frozenset]:
    """Read, verify, and return the set of revoked signatures.

    Returns:
        A ``frozenset`` of revoked signature strings if the file is present
        and has a valid ``"rl_signature"``.
        ``None`` on any error — caller applies fail-open policy.
    """
    # ---- Step 1: Read and JSON-parse the file ----------------------------
    try:
        text = Path(path).read_text(encoding="utf-8")
        data: dict = json.loads(text)
    except (OSError, json.JSONDecodeError, ValueError, Exception):
        return None

    if not isinstance(data, dict):
        return None

    # ---- Step 2: Extract the rl_signature field --------------------------
    rl_sig_b64 = data.get("rl_signature")
    if not isinstance(rl_sig_b64, str) or not rl_sig_b64:
        # No signature — unsigned file, treat as untrusted.
        return None

    # ---- Step 3: Reconstruct canonical bytes (without rl_signature) ------
    payload_dict = {k: v for k, v in data.items() if k != "rl_signature"}
    try:
        canonical_bytes = canonical_serialize(payload_dict)
    except Exception:
        return None

    # ---- Step 4: Verify the Ed25519 signature ----------------------------
    if not _verify_rl_signature(rl_sig_b64, canonical_bytes, public_key_bytes):
        # Signature invalid — tampered or wrong key; fail open.
        return None

    # ---- Step 5: Return the revoked set ----------------------------------
    revoked_list = data.get("revoked", [])
    if not isinstance(revoked_list, list):
        return None

    return frozenset(str(s) for s in revoked_list if isinstance(s, str))


def _verify_rl_signature(
    rl_sig_b64: str,
    canonical_bytes: bytes,
    public_key_bytes: bytes,
) -> bool:
    """Return ``True`` if *rl_sig_b64* is a valid Ed25519 sig of *canonical_bytes*.

    Returns ``False`` on any error rather than raising, so the caller can
    apply the fail-open policy with a simple ``if not`` check.
    """
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.exceptions import InvalidSignature

        sig_bytes = base64.urlsafe_b64decode(rl_sig_b64 + "==")
        if len(sig_bytes) != 64:
            return False

        pub_key = Ed25519PublicKey.from_public_bytes(public_key_bytes)
        pub_key.verify(sig_bytes, canonical_bytes)
        return True

    except Exception:
        # InvalidSignature, ValueError (bad key), decode error, etc.
        return False
