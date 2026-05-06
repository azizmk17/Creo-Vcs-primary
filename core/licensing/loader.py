"""
core/licensing/loader.py
========================
License file loading and parsing for the CreoVCS license verification system.

``load_license`` is the single entry point for reading a ``.lic`` file from
disk, validating its structure, and cryptographically verifying its signature.
It returns an immutable ``LicensePayload`` or raises a ``LicenseError``
subclass on any failure.

Responsibility boundary
-----------------------
This module is responsible for:

* File I/O (opening and reading the ``.lic`` file).
* JSON parsing and schema validation (required fields are present and
  have the correct types).
* Converting raw string timestamps to UTC-aware ``datetime`` objects.
* Constructing the ``LicensePayload`` dataclass.
* Invoking ``verify_signature`` to cryptographically verify the payload.

This module is **not** responsible for:

* Expiry checking — done by ``checks.check_expiry``.
* Feature checking — done by ``checks.check_feature``.
* Machine binding — done by ``checks.check_machine``.

Those runtime checks are the caller's responsibility (``LicenseManager``).
This separation means a license can be loaded and parsed once, with runtime
checks performed as many times as needed without re-reading the file.

JSON schema
-----------
Required fields::

    {
        "product":    "CreoVCS",                     // string
        "licensee":   "Acme Engineering SAS",         // string
        "issued_at":  "2026-02-18T00:00:00+00:00",   // ISO-8601 UTC
        "features":   ["bom_management", "commit"],  // list of strings
        "signature":  "base64url..."                  // Ed25519 sig
    }

Optional fields::

        "expires_at":         "2027-02-18T00:00:00+00:00",  // null = perpetual
        "version_constraint": ">=4.0,<5.0",
        "machine_id":         ["a1b2c3d4..."]
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.licensing.exceptions import (
    LicenseFileNotFoundError,
    LicenseParseError,
    LicenseSignatureError,  # noqa: F401 — re-raised transparently from verify_signature
)
from core.licensing.payload import LicensePayload


# ---------------------------------------------------------------------------
# Required top-level keys in the license JSON document.
# ---------------------------------------------------------------------------
_REQUIRED_FIELDS: tuple[str, ...] = (
    "product",
    "licensee",
    "issued_at",
    "features",
    "signature",
)

# The expected product name.  If the license is for a different product the
# loader rejects it with LicenseParseError rather than LicenseSignatureError
# so the error message is actionable.
_EXPECTED_PRODUCT = "CreoVCS"


def load_license(path: Path, public_key_bytes: bytes) -> LicensePayload:
    """Load, parse, and cryptographically verify a CreoVCS license file.

    This function orchestrates the complete loading pipeline:

    1. Opens and reads the ``.lic`` file from disk.
    2. JSON-parses the file contents.
    3. Validates that all required fields are present and have the expected types.
    4. Validates that ``product`` equals ``"CreoVCS"``.
    5. Converts ISO-8601 timestamp strings to UTC-aware ``datetime`` objects.
    6. Constructs an immutable ``LicensePayload``.
    7. Cryptographically verifies the Ed25519 signature via ``verify_signature``.
    8. Returns the verified ``LicensePayload``.

    The function does **not** check expiry, feature flags, or machine binding.
    Those runtime checks are performed by ``LicenseManager.initialize`` after
    this function returns successfully.

    Args:
        path: Filesystem path to the signed ``.lic`` JSON file.  Can be
            relative (resolved against the current working directory) or
            absolute.
        public_key_bytes: Raw 32-byte Ed25519 public key bytes.  Must never
            be hard-coded in this module; it is always supplied by the caller
            (typically ``LicenseManager.initialize``).

    Returns:
        An immutable, verified ``LicensePayload`` instance.

    Raises:
        LicenseFileNotFoundError: If *path* does not exist, is a directory,
            or cannot be opened for reading.
        LicenseParseError: If the file content is not valid JSON, any
            required field is missing, a field has the wrong type, or
            ``product`` does not equal ``"CreoVCS"``.
        LicenseSignatureError: If the Ed25519 signature verification fails
            for any reason (tampered file, wrong public key, corrupt
            signature).

    Example::

        import os
        from pathlib import Path
        from core.licensing.loader import load_license

        pub_key = bytes.fromhex(os.environ["CREOVCS_PUBLIC_KEY_HEX"])
        payload = load_license(Path("creovcs.lic"), pub_key)
        print(payload.licensee)   # "Acme Engineering SAS"
        print(payload.features)   # ("bom_management", "commit", ...)
    """
    # ------------------------------------------------------------------ #
    # Step 1: Read the file
    # ------------------------------------------------------------------ #
    path = Path(path)  # Coerce str → Path if caller passes a string.
    try:
        raw_text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise LicenseFileNotFoundError(
            f"License file not found: '{path}'. "
            "Contact your administrator to obtain a valid license file."
        )
    except IsADirectoryError:
        raise LicenseFileNotFoundError(
            f"Expected a license file but found a directory at '{path}'."
        )
    except OSError as exc:
        raise LicenseFileNotFoundError(
            f"License file '{path}' could not be read: {exc}"
        ) from exc

    # ------------------------------------------------------------------ #
    # Step 2: Parse JSON
    # ------------------------------------------------------------------ #
    try:
        raw: dict[str, Any] = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise LicenseParseError(
            f"License file '{path}' is not valid JSON: {exc.msg} "
            f"(line {exc.lineno}, column {exc.colno})."
        ) from exc

    if not isinstance(raw, dict):
        raise LicenseParseError(
            f"License file '{path}' must contain a JSON object at the top level."
        )

    # ------------------------------------------------------------------ #
    # Step 3: Validate required fields
    # ------------------------------------------------------------------ #
    missing = [f for f in _REQUIRED_FIELDS if f not in raw]
    if missing:
        raise LicenseParseError(
            f"License file '{path}' is missing required field(s): "
            + ", ".join(f"'{f}'" for f in missing)
            + "."
        )

    # ------------------------------------------------------------------ #
    # Step 4: Type-check scalar fields
    # ------------------------------------------------------------------ #
    for str_field in ("product", "licensee", "issued_at", "signature"):
        if not isinstance(raw[str_field], str):
            raise LicenseParseError(
                f"License field '{str_field}' must be a string, "
                f"got {type(raw[str_field]).__name__!r}."
            )

    if not isinstance(raw["features"], list):
        raise LicenseParseError(
            f"License field 'features' must be a list, "
            f"got {type(raw['features']).__name__!r}."
        )
    for i, feat in enumerate(raw["features"]):
        if not isinstance(feat, str):
            raise LicenseParseError(
                f"License field 'features[{i}]' must be a string, "
                f"got {type(feat).__name__!r}."
            )

    # Optional: expires_at may be string or null
    raw_expires = raw.get("expires_at", None)
    if raw_expires is not None and not isinstance(raw_expires, str):
        raise LicenseParseError(
            f"License field 'expires_at' must be a string or null, "
            f"got {type(raw_expires).__name__!r}."
        )

    # Optional: machine_id may be list or absent
    raw_machine = raw.get("machine_id", None)
    if raw_machine is not None:
        if not isinstance(raw_machine, list):
            raise LicenseParseError(
                f"License field 'machine_id' must be a list or absent, "
                f"got {type(raw_machine).__name__!r}."
            )
        for i, mid in enumerate(raw_machine):
            if not isinstance(mid, str):
                raise LicenseParseError(
                    f"License field 'machine_id[{i}]' must be a string, "
                    f"got {type(mid).__name__!r}."
                )

    # ------------------------------------------------------------------ #
    # Step 5: Product name guard
    # ------------------------------------------------------------------ #
    if raw["product"] != _EXPECTED_PRODUCT:
        raise LicenseParseError(
            f"This license is for product '{raw['product']}', "
            f"but this application requires a '{_EXPECTED_PRODUCT}' license."
        )

    # ------------------------------------------------------------------ #
    # Step 6: Parse timestamps
    # ------------------------------------------------------------------ #
    issued_at = _parse_iso_utc(raw["issued_at"], "issued_at")
    expires_at: datetime | None = None
    if raw_expires is not None:
        expires_at = _parse_iso_utc(raw_expires, "expires_at")

    # ------------------------------------------------------------------ #
    # Step 7: Construct the payload
    # ------------------------------------------------------------------ #
    try:
        payload = LicensePayload(
            product=raw["product"],
            licensee=raw["licensee"],
            issued_at=issued_at,
            expires_at=expires_at,
            features=tuple(raw["features"]),
            signature=raw["signature"],
            version_constraint=raw.get("version_constraint"),
            machine_id=tuple(raw_machine) if raw_machine else (),
        )
    except (TypeError, ValueError) as exc:
        raise LicenseParseError(
            f"License payload construction failed: {exc}"
        ) from exc

    # ------------------------------------------------------------------ #
    # Step 8: Verify signature — must be last
    # ------------------------------------------------------------------ #
    # Import here (not at module level) so that the cryptography library is
    # only loaded when a license file is actually being loaded.  This
    # slightly improves startup time for code paths that import this module
    # but never call load_license.
    from core.licensing.crypto import verify_signature  # noqa: PLC0415
    verify_signature(payload, raw, public_key_bytes)

    return payload


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_iso_utc(value: str, field_name: str) -> datetime:
    """Parse an ISO-8601 timestamp string and ensure it is UTC-aware.

    Accepts strings with an explicit UTC offset (``+00:00``, ``Z``) or
    without a timezone designator (assumed UTC for backward compatibility
    with tools that omit the suffix).

    Args:
        value: The raw timestamp string from the JSON file.
        field_name: The JSON field name, used for error messages.

    Returns:
        A timezone-aware ``datetime`` in UTC.

    Raises:
        LicenseParseError: If *value* cannot be parsed as an ISO-8601
            datetime.
    """
    # Python 3.7+ fromisoformat does not support trailing 'Z'.
    # Replace 'Z' with '+00:00' for broad compatibility.
    normalised = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalised)
    except ValueError as exc:
        raise LicenseParseError(
            f"License field '{field_name}' is not a valid ISO-8601 timestamp: "
            f"'{value}'. Expected format: '2026-02-18T00:00:00+00:00'."
        ) from exc

    if dt.tzinfo is None:
        # No timezone info — assume UTC for backward compatibility.
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        # Normalise to UTC so all comparisons are in the same timezone.
        dt = dt.astimezone(timezone.utc)

    return dt
