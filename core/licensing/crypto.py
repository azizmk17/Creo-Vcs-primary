"""
core/licensing/crypto.py
========================
Pure cryptographic primitives for the CreoVCS license verification system.

This module contains exactly two public functions:

* ``canonical_serialize`` — deterministic JSON serialisation of a payload dict.
* ``verify_signature``    — Ed25519 signature verification against the canonical form.

Obfuscation readiness
---------------------
This file is intentionally free of:

* ``eval`` / ``exec`` calls
* ``inspect`` module usage
* Dynamic ``__import__`` calls or ``importlib`` calls at runtime
* Any dependency on PyQt5 or other large runtime libraries

These constraints make this module a prime candidate for compilation with
Cython or Nuitka without modification.  To compile::

    cython --embed core/licensing/crypto.py
    # or, as part of a Nuitka build:
    nuitka --module core/licensing/crypto.py

Library choice: ``cryptography`` (PyCA)
-----------------------------------------
Ed25519 operations are performed via the ``cryptography`` package rather than
PyNaCl.  Rationale:

* Ships as a pre-compiled wheel on Windows (no libsodium build step).
* Already likely present in projects that use PyInstaller or other tooling.
* ``Ed25519PublicKey.from_public_bytes()`` accepts a raw 32-byte key, which is
  the simplest possible key format for embedding in a built binary.
* The same library can later be used for TLS/X.509 work without adding a dep.

Canonical serialisation algorithm
----------------------------------
The canonical form of a license payload is the deterministic byte string that
was signed by the vendor.  It is produced by:

1. Copying the payload dict and **removing** the ``"signature"`` key (the
   signature was not present when the document was originally signed).
2. Calling ``json.dumps`` with ``sort_keys=True, separators=(',', ':')``.
   * ``sort_keys=True`` eliminates JSON key-ordering ambiguity across Python
     versions and JSON implementations.
   * The compact separator pair removes all optional whitespace (no space
     after ``:`` or ``,``), producing the shortest possible representation.
3. Setting ``ensure_ascii=True`` so any non-ASCII characters in e.g. the
   licensee name are ``\\uXXXX``-escaped.  This guarantees byte-level
   determinism regardless of the host's locale settings.
4. Encoding the resulting string to UTF-8 bytes.

Both this verification path and the offline signing tool
(``tools/licensing/sign_license.py``) call the same ``canonical_serialize``
function, so the round-trip is exact.
"""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.licensing.payload import LicensePayload


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def canonical_serialize(data: dict) -> bytes:
    """Produce the canonical UTF-8 byte string for signing or verification.

    The canonical form is deterministic: keys are sorted lexicographically,
    all optional whitespace is removed, and non-ASCII characters are
    ``\\uXXXX``-escaped.

    The caller is responsible for removing the ``"signature"`` key from
    *data* before calling this function.  This is a deliberate design
    decision: keeping the removal outside ``canonical_serialize`` makes the
    function a pure transform with no special-casing, which is easier to
    audit and compile.

    Args:
        data: The license payload dict.  The ``"signature"`` key MUST have
            been removed by the caller before this function is invoked.
            Passing a dict that still contains ``"signature"`` will produce
            a canonical form that does NOT match the one used at signing
            time and verification will fail.

    Returns:
        UTF-8 encoded bytes representing the canonical JSON form of *data*.

    Examples:
        >>> canonical_serialize({"b": 2, "a": 1})
        b'{"a":1,"b":2}'

        >>> canonical_serialize({"licensee": "Müller GmbH", "product": "X"})
        b'{"licensee":"M\\\\u00fcller GmbH","product":"X"}'

        >>> canonical_serialize({"expires_at": None, "product": "Y"})
        b'{"expires_at":null,"product":"Y"}'
    """
    return json.dumps(
        data,
        sort_keys=True,
        separators=(',', ':'),
        ensure_ascii=True,
    ).encode('utf-8')


def verify_signature(
    payload: "LicensePayload",
    raw_payload: dict,
    public_key_bytes: bytes,
) -> None:
    """Verify the Ed25519 signature of a license payload.

    Reconstructs the canonical byte string from *raw_payload* (with the
    ``"signature"`` key stripped internally), then verifies that
    *payload.signature* is a valid Ed25519 signature of those bytes under
    *public_key_bytes*.

    This function raises on any error — it never returns a boolean.  The
    calling code (``loader.load_license``) must propagate the exception.

    Args:
        payload: The already-parsed ``LicensePayload`` whose ``signature``
            field will be verified.
        raw_payload: The original ``dict`` returned by ``json.load()`` on
            the ``.lic`` file.  It must still contain the ``"signature"``
            key; this function removes it internally before hashing.
        public_key_bytes: Raw 32-byte Ed25519 public key.  This must be
            exactly 32 bytes; passing a PEM string or DER blob will raise
            ``LicenseSignatureError``.

    Raises:
        LicenseSignatureError: In any of the following cases:

            * *payload.signature* cannot be base64url-decoded.
            * The decoded signature is not exactly 64 bytes.
            * The Ed25519 signature verification fails (wrong key or tampered
              payload).
            * *public_key_bytes* is not a valid 32-byte Ed25519 public key.

    Security note:
        The error message surfaced to the user should always be the generic
        "License signature is invalid" string.  The inner cause (stored as
        ``__cause__`` on the raised exception) contains cryptographic
        implementation details and must not be shown in production UI.

    Example::

        # In loader.py — called after constructing the LicensePayload:
        verify_signature(payload, raw_json_dict, public_key_bytes)
        # If this returns without raising, the signature is valid.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.exceptions import InvalidSignature
    from core.licensing.exceptions import LicenseSignatureError

    # Build the canonical bytes from the raw dict, minus the signature key.
    # Using dict comprehension (not dict.pop) to avoid mutating the caller's
    # dict, which might be reused for error reporting.
    signing_dict = {k: v for k, v in raw_payload.items() if k != "signature"}
    canonical_bytes = canonical_serialize(signing_dict)

    # Decode the base64url signature.  We add '==' padding unconditionally;
    # urlsafe_b64decode ignores superfluous padding.
    try:
        sig_bytes = base64.urlsafe_b64decode(payload.signature + "==")
    except Exception as exc:
        raise LicenseSignatureError(
            "License signature could not be decoded — the file may be corrupt."
        ) from exc

    # Ed25519 signatures are always exactly 64 bytes.
    if len(sig_bytes) != 64:
        raise LicenseSignatureError(
            "License signature has an unexpected length — the file may be corrupt."
        )

    # Load the public key and verify.
    try:
        pub_key = Ed25519PublicKey.from_public_bytes(public_key_bytes)
        pub_key.verify(sig_bytes, canonical_bytes)
    except InvalidSignature as exc:
        raise LicenseSignatureError(
            "License signature is invalid — the file may have been tampered with."
        ) from exc
    except (ValueError, TypeError) as exc:
        raise LicenseSignatureError(
            "The configured public key is malformed and cannot be used for verification."
        ) from exc
    except Exception as exc:
        raise LicenseSignatureError(
            "An unexpected error occurred during signature verification."
        ) from exc
    # If pub_key.verify() returned without raising InvalidSignature, the
    # signature is cryptographically valid.  No explicit return value needed.
