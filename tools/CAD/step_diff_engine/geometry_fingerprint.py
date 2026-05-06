from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .step_parser import FaceGeometry, ModelGeometry
from .utils import round_float, round_vector, stable_hash


@dataclass(slots=True)
class FingerprintedFace:
    """Face geometry decorated with base + full fingerprints."""

    face: FaceGeometry
    base_fingerprint: str
    fingerprint: str
    rounded_payload: dict[str, Any]


def _face_rounded_payload(face: FaceGeometry, digits: int = 6) -> dict[str, Any]:
    """Build stable rounded payload used for full fingerprint generation."""
    rounded_params: dict[str, Any] = {}
    for key, value in (face.parameters or {}).items():
        if isinstance(value, (int, float)):
            rounded_params[key] = round_float(float(value), digits)
        elif isinstance(value, (tuple, list)):
            try:
                rounded_params[key] = list(round_vector(value, digits) or [])
            except Exception:
                rounded_params[key] = value
        else:
            rounded_params[key] = value

    return {
        "surface_type": face.surface_type,
        "area": round_float(face.area, digits),
        "center": list(round_vector(face.center, digits) or []),
        "normal": list(round_vector(face.normal, digits) or []) if face.normal is not None else None,
        "radius": round_float(face.radius, digits),
        "axis_direction": list(round_vector(face.axis_direction, digits) or []) if face.axis_direction is not None else None,
        "axis_location": list(round_vector(face.axis_location, digits) or []) if face.axis_location is not None else None,
        "parameters": rounded_params,
    }


def _face_base_payload(face: FaceGeometry, digits: int = 6) -> dict[str, Any]:
    """Build base payload (without tunable params) for modified surface matching."""
    return {
        "surface_type": face.surface_type,
        "area": round_float(face.area, digits),
        "center": list(round_vector(face.center, digits) or []),
        "normal": list(round_vector(face.normal, digits) or []) if face.normal is not None else None,
    }


def fingerprint_face(face: FaceGeometry, digits: int = 6) -> FingerprintedFace:
    """Generate stable base and full fingerprint for a single face."""
    full_payload = _face_rounded_payload(face, digits=digits)
    base_payload = _face_base_payload(face, digits=digits)
    return FingerprintedFace(
        face=face,
        base_fingerprint=stable_hash(base_payload),
        fingerprint=stable_hash(full_payload),
        rounded_payload=full_payload,
    )


def fingerprint_model(model: ModelGeometry, digits: int = 6) -> list[FingerprintedFace]:
    """Fingerprint all faces from one model."""
    return [fingerprint_face(face, digits=digits) for face in model.faces]
