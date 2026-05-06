from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence, Tuple


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


def round_float(value: float | None, digits: int = 6) -> float | None:
    """Round numeric value with stable behavior for hashing payloads."""
    if value is None:
        return None
    return round(float(value), digits)


def round_vector(values: Sequence[float] | None, digits: int = 6) -> tuple[float, ...] | None:
    """Round all vector components."""
    if values is None:
        return None
    return tuple(round_float(v, digits) for v in values)


def stable_hash(payload: Any) -> str:
    """Return deterministic SHA256 hash of a JSON-serializable payload."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def vector_delta(a: Sequence[float], b: Sequence[float], digits: int = 6) -> tuple[float, ...]:
    """Return rounded component-wise difference b-a."""
    return tuple(round_float(float(bi) - float(ai), digits) or 0.0 for ai, bi in zip(a, b))
