"""Small, shared trust protocol for the company release feed.

Only local or UNC filesystem feeds are supported. The release public key comes
from the installed application, never from the feed being verified.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re


MAX_MANIFEST_BYTES = 128 * 1024
MANIFEST_SCHEMA = 1
PLATFORM = "windows-x64"
_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
_BUILD_ID = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,78}[A-Za-z0-9_-])?\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                   *(f"LPT{i}" for i in range(1, 10))}


def canonical_json(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def version_tuple(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or len(value) > 32 or not _VERSION.fullmatch(value):
        raise ValueError("Version must be a numeric X.Y.Z value without leading zeroes.")
    result = tuple(int(component) for component in value.split("."))
    if any(component > 2147483647 for component in result):
        raise ValueError("Version component is too large.")
    return result


def hash_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _integer(value, label: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum or value > 9223372036854775807:
        raise ValueError(f"{label} must be an integer of at least {minimum}.")
    return value


def _timestamp(value: str) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("expires_at must be a timezone-aware ISO 8601 timestamp.")
    try:
        result = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as exc:
        raise ValueError("expires_at is not a valid ISO 8601 timestamp.") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("expires_at must include a timezone.")
    try:
        return result.astimezone(timezone.utc)
    except (OverflowError, ValueError) as exc:
        raise ValueError("expires_at is outside the supported timestamp range.") from exc


def validate_relative_path(value: str) -> str:
    """Accept a Windows-safe feed-relative path, never a URL or drive path."""
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise ValueError("Package path must be a nonempty relative filesystem path.")
    if ("\\" in value or any(character in value for character in '<>:"|?*')
            or any(ord(character) < 32 for character in value)):
        raise ValueError("Package path contains an unsafe character.")
    parts = value.split("/")
    if (PurePosixPath(value).is_absolute()
            or any(part in {"", ".", ".."} or part.endswith((" ", "."))
                   or part.split(".")[0].upper() in _RESERVED_NAMES for part in parts)):
        raise ValueError("Package path must stay inside the release feed.")
    return value


def validate_payload(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Signed release payload must be an object.")
    if type(payload.get("schema")) is not int or payload["schema"] != MANIFEST_SCHEMA:
        raise ValueError("Unsupported release manifest schema.")
    if payload.get("product") != "Nexus" or payload.get("platform") != PLATFORM:
        raise ValueError("Release manifest is for a different product or platform.")
    version_tuple(payload.get("version"))
    version_tuple(payload.get("min_updater_version"))
    build_id = payload.get("build_id")
    if not isinstance(build_id, str) or not _BUILD_ID.fullmatch(build_id):
        raise ValueError("build_id must be a safe nonempty release identifier.")
    _integer(payload.get("release_sequence"), "release_sequence", minimum=1)
    _timestamp(payload.get("expires_at"))
    package = payload.get("package")
    if not isinstance(package, dict):
        raise ValueError("package must be an object.")
    validate_relative_path(package.get("path"))
    _integer(package.get("size"), "package.size", minimum=1)
    if not isinstance(package.get("sha256"), str) or not _SHA256.fullmatch(package["sha256"]):
        raise ValueError("package.sha256 must be a lowercase SHA-256 digest.")
    schema = payload.get("supported_schema")
    if not isinstance(schema, dict):
        raise ValueError("supported_schema must be an object.")
    minimum = _integer(schema.get("min"), "supported_schema.min")
    maximum = _integer(schema.get("max"), "supported_schema.max")
    if minimum > maximum:
        raise ValueError("supported_schema.min must not exceed supported_schema.max.")
    notes = payload.get("release_notes")
    if not isinstance(notes, str) or len(notes) > 16000:
        raise ValueError("release_notes must be a string of at most 16000 characters.")
    if len(canonical_json(payload)) > MAX_MANIFEST_BYTES - 256:
        raise ValueError("Release manifest is too large.")
    return payload


def _public_key(public_key_hex: str):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        raw = bytes.fromhex(public_key_hex)
        if len(raw) != 32:
            raise ValueError("wrong length")
        return Ed25519PublicKey.from_public_bytes(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("Release public key must be 32 bytes encoded as hex.") from exc


def _private_key(private_path: str | Path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    try:
        raw = bytes.fromhex(Path(private_path).read_text(encoding="ascii").strip())
        if len(raw) != 32:
            raise ValueError("wrong length")
        return Ed25519PrivateKey.from_private_bytes(raw)
    except (UnicodeError, ValueError) as exc:
        raise ValueError("Release private key must be a 32-byte hex Ed25519 seed.") from exc


def sign_manifest(payload: dict, private_path: str | Path) -> dict:
    validate_payload(payload)
    signature = _private_key(private_path).sign(canonical_json(payload))
    return {"signed": payload, "signature": base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")}


def verify_manifest(envelope: dict, public_key_hex: str, *, last_sequence: int = 0,
                    now: datetime | None = None) -> dict:
    if not isinstance(envelope, dict) or set(envelope) != {"signed", "signature"}:
        raise ValueError("Release manifest must contain signed and signature fields.")
    payload = validate_payload(envelope["signed"])
    encoded = envelope["signature"]
    try:
        if not isinstance(encoded, str) or not re.fullmatch(r"[A-Za-z0-9_-]{86}", encoded):
            raise ValueError("invalid signature encoding")
        signature = base64.b64decode(encoded + "==", altchars=b"-_", validate=True)
        if base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii") != encoded:
            raise ValueError("noncanonical signature encoding")
        _public_key(public_key_hex).verify(signature, canonical_json(payload))
    except Exception as exc:
        raise ValueError("Release manifest signature is invalid.") from exc
    _integer(last_sequence, "last_sequence")
    if payload["release_sequence"] < last_sequence:
        raise ValueError("Release sequence is older than the last trusted release.")
    current = datetime.now(timezone.utc) if now is None else now
    if not isinstance(current, datetime) or current.tzinfo is None:
        raise ValueError("Verification time must be timezone-aware.")
    if _timestamp(payload["expires_at"]) <= current.astimezone(timezone.utc):
        raise ValueError("Release manifest has expired.")
    return payload


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field in release manifest: " + key)
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("Invalid JSON constant in release manifest: " + value)


def load_manifest(path: str | Path, public_key_hex: str, *, last_sequence: int = 0,
                  now: datetime | None = None) -> dict:
    name = os.fspath(path)
    if "://" in name:
        raise ValueError("Only local or UNC filesystem release feeds are supported.")
    with Path(path).open("rb") as stream:
        content = stream.read(MAX_MANIFEST_BYTES + 1)
    if len(content) > MAX_MANIFEST_BYTES:
        raise ValueError("Release manifest is too large.")
    try:
        envelope = json.loads(content.decode("utf-8"), object_pairs_hook=_unique_object,
                              parse_constant=_reject_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("Release manifest is not valid UTF-8 JSON.") from exc
    return verify_manifest(envelope, public_key_hex, last_sequence=last_sequence, now=now)


def resolve_package_path(feed_dir: str | Path, relative_path: str) -> Path:
    """Resolve a verified manifest's package without following feed escapes."""
    validate_relative_path(relative_path)
    root = Path(feed_dir).resolve()
    candidate = root.joinpath(*relative_path.split("/"))
    resolved = candidate.resolve()
    if root not in resolved.parents:
        raise ValueError("Release package escapes the release feed.")
    return resolved
