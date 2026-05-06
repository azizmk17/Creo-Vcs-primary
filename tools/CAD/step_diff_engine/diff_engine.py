from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any

from .geometry_fingerprint import FingerprintedFace, fingerprint_model
from .step_parser import ModelGeometry
from .utils import round_float, vector_delta


@dataclass(slots=True)
class ModifiedSurface:
    """Represents one face considered modified (same base, changed params)."""

    base_fingerprint: str
    before_fingerprint: str
    after_fingerprint: str
    before_payload: dict[str, Any]
    after_payload: dict[str, Any]
    parameter_deltas: dict[str, Any]


@dataclass(slots=True)
class GeometryDiffResult:
    """Full diff between two model geometry snapshots."""

    commit_a: str
    commit_b: str
    added_surfaces: list[dict[str, Any]]
    removed_surfaces: list[dict[str, Any]]
    modified_surfaces: list[ModifiedSurface]
    volume_before: float
    volume_after: float
    volume_delta: float
    bbox_before: dict[str, tuple[float, float, float]]
    bbox_after: dict[str, tuple[float, float, float]]
    bbox_delta: dict[str, tuple[float, float, float]]


def _index_by_fingerprint(items: list[FingerprintedFace]) -> dict[str, list[FingerprintedFace]]:
    index: dict[str, list[FingerprintedFace]] = defaultdict(list)
    for item in items:
        index[item.fingerprint].append(item)
    return index


def _index_by_base(items: list[FingerprintedFace]) -> dict[str, list[FingerprintedFace]]:
    index: dict[str, list[FingerprintedFace]] = defaultdict(list)
    for item in items:
        index[item.base_fingerprint].append(item)
    return index


def _compute_parameter_deltas(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    deltas: dict[str, Any] = {}

    keys = sorted(set(before.keys()) | set(after.keys()))
    for key in keys:
        b = before.get(key)
        a = after.get(key)
        if b == a:
            continue

        if isinstance(b, (int, float)) and isinstance(a, (int, float)):
            deltas[key] = {"before": float(b), "after": float(a), "delta": float(a) - float(b)}
            continue

        if isinstance(b, list) and isinstance(a, list) and len(b) == len(a) and all(isinstance(v, (int, float)) for v in b + a):
            deltas[key] = {
                "before": b,
                "after": a,
                "delta": [float(ai) - float(bi) for bi, ai in zip(b, a)],
            }
            continue

        if isinstance(b, dict) and isinstance(a, dict):
            nested = _compute_parameter_deltas(b, a)
            if nested:
                deltas[key] = nested
            continue

        deltas[key] = {"before": b, "after": a}

    return deltas


def compare_models(model_a: ModelGeometry, model_b: ModelGeometry, digits: int = 6) -> GeometryDiffResult:
    """Compare two models by stable fingerprints and geometric metadata."""
    fp_a = fingerprint_model(model_a, digits=digits)
    fp_b = fingerprint_model(model_b, digits=digits)

    by_fp_a = _index_by_fingerprint(fp_a)
    by_fp_b = _index_by_fingerprint(fp_b)

    removed: list[FingerprintedFace] = []
    added: list[FingerprintedFace] = []

    all_fps = sorted(set(by_fp_a.keys()) | set(by_fp_b.keys()))
    for key in all_fps:
        ca = len(by_fp_a.get(key, []))
        cb = len(by_fp_b.get(key, []))
        if ca > cb:
            removed.extend(by_fp_a[key][: ca - cb])
        elif cb > ca:
            added.extend(by_fp_b[key][: cb - ca])

    removed_by_base = _index_by_base(removed)
    added_by_base = _index_by_base(added)

    modified: list[ModifiedSurface] = []
    still_removed: list[FingerprintedFace] = []
    still_added: list[FingerprintedFace] = []

    all_base = sorted(set(removed_by_base.keys()) | set(added_by_base.keys()))
    for base in all_base:
        r = list(removed_by_base.get(base, []))
        a = list(added_by_base.get(base, []))
        pairs = min(len(r), len(a))

        for idx in range(pairs):
            before = r[idx]
            after = a[idx]
            modified.append(
                ModifiedSurface(
                    base_fingerprint=base,
                    before_fingerprint=before.fingerprint,
                    after_fingerprint=after.fingerprint,
                    before_payload=before.rounded_payload,
                    after_payload=after.rounded_payload,
                    parameter_deltas=_compute_parameter_deltas(before.rounded_payload, after.rounded_payload),
                )
            )

        still_removed.extend(r[pairs:])
        still_added.extend(a[pairs:])

    volume_delta = round_float(model_b.volume - model_a.volume, digits) or 0.0
    bbox_delta = {
        "min_delta": vector_delta(model_a.bbox_min, model_b.bbox_min, digits),
        "max_delta": vector_delta(model_a.bbox_max, model_b.bbox_max, digits),
    }

    return GeometryDiffResult(
        commit_a=model_a.commit_id,
        commit_b=model_b.commit_id,
        added_surfaces=[{"fingerprint": item.fingerprint, "base_fingerprint": item.base_fingerprint, "face": asdict(item.face), "payload": item.rounded_payload} for item in still_added],
        removed_surfaces=[{"fingerprint": item.fingerprint, "base_fingerprint": item.base_fingerprint, "face": asdict(item.face), "payload": item.rounded_payload} for item in still_removed],
        modified_surfaces=modified,
        volume_before=float(model_a.volume),
        volume_after=float(model_b.volume),
        volume_delta=float(volume_delta),
        bbox_before={"min": tuple(model_a.bbox_min), "max": tuple(model_a.bbox_max)},
        bbox_after={"min": tuple(model_b.bbox_min), "max": tuple(model_b.bbox_max)},
        bbox_delta=bbox_delta,
    )
