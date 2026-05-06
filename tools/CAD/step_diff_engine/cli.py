from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .database import JsonDiffDatabase
from .diff_engine import compare_models
from .step_parser import StepParseError, parse_step_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="STEP geometry diff engine")
    sub = parser.add_subparsers(dest="command", required=True)

    compare = sub.add_parser("compare", help="Compare two STEP commits")
    compare.add_argument("--step-a", required=True, help="Path to first STEP file")
    compare.add_argument("--step-b", required=True, help="Path to second STEP file")
    compare.add_argument("--commit-a", required=True, help="Commit id for step-a")
    compare.add_argument("--commit-b", required=True, help="Commit id for step-b")
    compare.add_argument("--db", default=None, help="JSON DB path")
    compare.add_argument("--metadata", default=None, help="Optional metadata JSON string")
    compare.add_argument("--output", default=None, help="Optional output JSON file for diff")
    compare.add_argument("--digits", type=int, default=6, help="Rounding digits for fingerprints")

    history = sub.add_parser("history", help="Get history for fingerprint")
    history.add_argument("fingerprint", help="Fingerprint hash")
    history.add_argument("--db", default=None, help="JSON DB path")

    return parser


def _parse_metadata(metadata_raw: str | None) -> dict[str, Any]:
    if not metadata_raw:
        return {}
    try:
        payload = json.loads(metadata_raw)
    except json.JSONDecodeError as exc:
        raise ValueError("--metadata must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("--metadata JSON must be an object")
    return payload


def _diff_to_json_ready(diff_obj) -> dict[str, Any]:
    data = asdict(diff_obj)
    return data


def run_compare(args: argparse.Namespace) -> int:
    metadata = _parse_metadata(args.metadata)

    model_a = parse_step_file(args.step_a, commit_id=args.commit_a)
    model_b = parse_step_file(args.step_b, commit_id=args.commit_b)
    diff = compare_models(model_a, model_b, digits=args.digits)

    db = JsonDiffDatabase(args.db) if args.db else JsonDiffDatabase()
    db.append_comparison(model_a=model_a, model_b=model_b, diff=diff, metadata=metadata)

    payload = _diff_to_json_ready(diff)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    else:
        print(json.dumps(payload, indent=2))

    return 0


def run_history(args: argparse.Namespace) -> int:
    db = JsonDiffDatabase(args.db) if args.db else JsonDiffDatabase()
    history = db.get_history(args.fingerprint)
    print(json.dumps(history, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "compare":
            return run_compare(args)
        if args.command == "history":
            return run_history(args)
        parser.error(f"Unknown command: {args.command}")
        return 2
    except StepParseError as exc:
        parser.exit(status=1, message=f"STEP parse error: {exc}\n")
    except Exception as exc:
        parser.exit(status=1, message=f"Error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
