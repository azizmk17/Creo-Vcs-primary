from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .diff_engine import GeometryDiffResult
from .geometry_fingerprint import FingerprintedFace, fingerprint_model
from .step_parser import ModelGeometry
from .utils import utc_now_iso

DEFAULT_DB_PATH = Path(__file__).resolve().parent / "step_diff_history.json"


class JsonDiffDatabase:
    """Lightweight JSON database for commit-level geometry history."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)

    def _load(self) -> dict[str, Any]:
        if not self.db_path.exists():
            return {"records": []}

        with self.db_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if "records" not in data or not isinstance(data["records"], list):
            raise ValueError(f"Invalid DB format in {self.db_path}")
        return data

    def _save(self, data: dict[str, Any]) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.db_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)

    def append_comparison(
        self,
        *,
        model_a: ModelGeometry,
        model_b: ModelGeometry,
        diff: GeometryDiffResult,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append one comparison record with commit metadata and per-face fingerprints."""
        data = self._load()

        fp_a = fingerprint_model(model_a)
        fp_b = fingerprint_model(model_b)

        record = {
            "created_at": utc_now_iso(),
            "metadata": metadata or {},
            "commit_a": _model_to_record(model_a, fp_a),
            "commit_b": _model_to_record(model_b, fp_b),
            "diff": _diff_to_record(diff),
        }

        data["records"].append(record)
        self._save(data)
        return record

    def get_history(self, fingerprint: str) -> list[dict[str, Any]]:
        """Return evolution events for a fingerprint across all stored comparisons."""
        history: list[dict[str, Any]] = []
        data = self._load()

        for rec in data.get("records", []):
            created_at = rec.get("created_at")

            commit_a = rec.get("commit_a", {})
            commit_b = rec.get("commit_b", {})
            diff = rec.get("diff", {})

            a_id = commit_a.get("commit_id")
            b_id = commit_b.get("commit_id")

            a_hits = [f for f in commit_a.get("faces", []) if f.get("fingerprint") == fingerprint]
            b_hits = [f for f in commit_b.get("faces", []) if f.get("fingerprint") == fingerprint]

            for _ in a_hits:
                history.append(
                    {
                        "timestamp": created_at,
                        "event": "present_before",
                        "commit_id": a_id,
                        "comparison_to": b_id,
                    }
                )
            for _ in b_hits:
                history.append(
                    {
                        "timestamp": created_at,
                        "event": "present_after",
                        "commit_id": b_id,
                        "comparison_from": a_id,
                    }
                )

            for item in diff.get("added_surfaces", []):
                if item.get("fingerprint") == fingerprint:
                    history.append(
                        {
                            "timestamp": created_at,
                            "event": "added",
                            "commit_id": b_id,
                            "from_commit": a_id,
                            "details": item,
                        }
                    )

            for item in diff.get("removed_surfaces", []):
                if item.get("fingerprint") == fingerprint:
                    history.append(
                        {
                            "timestamp": created_at,
                            "event": "removed",
                            "commit_id": a_id,
                            "to_commit": b_id,
                            "details": item,
                        }
                    )

            for item in diff.get("modified_surfaces", []):
                if item.get("before_fingerprint") == fingerprint or item.get("after_fingerprint") == fingerprint:
                    history.append(
                        {
                            "timestamp": created_at,
                            "event": "modified",
                            "from_commit": a_id,
                            "to_commit": b_id,
                            "details": item,
                        }
                    )

        history.sort(key=lambda x: (x.get("timestamp") or "", x.get("event") or ""))
        return history


def _model_to_record(model: ModelGeometry, fingerprinted_faces: list[FingerprintedFace]) -> dict[str, Any]:
    return {
        "commit_id": model.commit_id,
        "step_path": model.step_path,
        "volume": model.volume,
        "bbox_min": list(model.bbox_min),
        "bbox_max": list(model.bbox_max),
        "faces": [
            {
                "index": item.face.index,
                "surface_type": item.face.surface_type,
                "fingerprint": item.fingerprint,
                "base_fingerprint": item.base_fingerprint,
                "payload": item.rounded_payload,
            }
            for item in fingerprinted_faces
        ],
    }


def _diff_to_record(diff: GeometryDiffResult) -> dict[str, Any]:
    return {
        "commit_a": diff.commit_a,
        "commit_b": diff.commit_b,
        "added_surfaces": diff.added_surfaces,
        "removed_surfaces": diff.removed_surfaces,
        "modified_surfaces": [asdict(item) for item in diff.modified_surfaces],
        "volume_before": diff.volume_before,
        "volume_after": diff.volume_after,
        "volume_delta": diff.volume_delta,
        "bbox_before": diff.bbox_before,
        "bbox_after": diff.bbox_after,
        "bbox_delta": diff.bbox_delta,
    }


def get_history(fingerprint: str, db_path: str | Path = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    """Convenience function to query a fingerprint evolution history."""
    return JsonDiffDatabase(db_path).get_history(fingerprint)
