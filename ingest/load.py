"""
Sprint 0 smoke test: load a single CAPE sandbox report and inspect its structure.

This script is deliberately minimal. It proves the ingestion pipeline starts
working end-to-end: read JSON from disk, parse it, surface the schema shape
so Sprint 1 can design a proper parser against it.

Usage:
    python ingest/load.py data/raw/<sha256>.json

If no path is given, loads a random report from data/raw/ for convenience.
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path


def load_report(path: Path) -> dict:
    """Load a CAPE sandbox JSON report from disk."""
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def describe(obj, depth: int = 0, max_depth: int = 2, indent: str = "  ") -> None:
    """Walk a JSON tree and print its shape up to max_depth."""
    if depth > max_depth:
        return
    prefix = indent * depth
    if isinstance(obj, dict):
        for k, v in obj.items():
            type_name = type(v).__name__
            size = len(v) if hasattr(v, "__len__") and not isinstance(v, (str, bytes)) else "-"
            print(f"{prefix}{k}: {type_name} (len={size})")
            if isinstance(v, (dict, list)) and depth < max_depth:
                describe(v, depth + 1, max_depth, indent)
    elif isinstance(obj, list) and obj:
        print(f"{prefix}[0] (sample of {len(obj)}):")
        describe(obj[0], depth + 1, max_depth, indent)


def pick_random_report(raw_dir: Path) -> Path | None:
    """Return a random .json file from data/raw/, ignoring labels.csv and .gitkeep."""
    candidates = [p for p in raw_dir.glob("*.json")]
    return random.choice(candidates) if candidates else None


def main() -> int:
    if len(sys.argv) == 2:
        path = Path(sys.argv[1])
    elif len(sys.argv) == 1:
        raw_dir = Path(__file__).resolve().parent.parent / "data" / "raw"
        path = pick_random_report(raw_dir)
        if path is None:
            print(f"ERROR: no JSON reports found in {raw_dir}", file=sys.stderr)
            return 1
        print(f"(no path given, picked random report)\n")
    else:
        print("Usage: python ingest/load.py [path/to/report.json]", file=sys.stderr)
        return 1

    if not path.exists():
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        return 1

    size_kb = path.stat().st_size / 1024
    print(f"File:  {path.name}")
    print(f"Size:  {size_kb:.1f} KB")
    print()

    try:
        report = load_report(path)
    except json.JSONDecodeError as e:
        print(f"ERROR: invalid JSON: {e}", file=sys.stderr)
        return 1

    print(f"Top-level keys: {list(report.keys())}")
    print()
    print("Structure (depth=2):")
    describe(report, max_depth=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
