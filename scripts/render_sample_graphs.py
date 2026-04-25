"""
Render 3 representative BehaviorGraphs to docs/ for the Sprint 2
deliverable.

Selection rules (deterministic, derived from data/processed/manifest.jsonl):
    small   : smallest sample with 30 <= |entities| <= 100
    medium  : sample whose |entities| is closest to 500
    large   : sample with the largest |entities|
Ties broken by sample_id lex order.

Output:
    docs/sprint2_graph_small_<sha8>.png
    docs/sprint2_graph_medium_<sha8>.png
    docs/sprint2_graph_large_<sha8>.png

The wrapper exists so 'rerun the renders' is one command. The library
work is in graph/visualize.py; this script just picks samples.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make repo root importable when run as `python scripts/...`.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from graph.builder import BehaviorGraph
from graph.visualize import render


DEFAULT_MANIFEST = Path("data/processed/manifest.jsonl")
DEFAULT_PROCESSED = Path("data/processed")
DEFAULT_OUT = Path("docs")


def load_manifest(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    # Only successfully-parsed samples; others have no usable graph.
    return [r for r in rows if r.get("parse_ok")]


def total_entities(row: dict) -> int:
    return sum((row.get("entity_counts") or {}).values())


def pick_small(rows: list[dict]) -> dict:
    eligible = [r for r in rows if 30 <= total_entities(r) <= 100]
    if not eligible:
        raise RuntimeError(
            "no samples with 30 <= entities <= 100; corpus may be unusual"
        )
    return min(eligible, key=lambda r: (total_entities(r), r["sample_id"]))


def pick_medium(rows: list[dict], target: int = 500) -> dict:
    eligible = [r for r in rows if 200 <= total_entities(r) <= 1000]
    if not eligible:
        raise RuntimeError(
            "no samples in medium tier (200..1000 entities)"
        )
    return min(
        eligible,
        key=lambda r: (abs(total_entities(r) - target), r["sample_id"]),
    )


def pick_large(rows: list[dict]) -> dict:
    eligible = [r for r in rows if total_entities(r) > 1000]
    if not eligible:
        # Fallback: take whatever the largest is, even if not >1000.
        return max(rows, key=lambda r: (total_entities(r), r["sample_id"]))
    return max(eligible, key=lambda r: (total_entities(r), r["sample_id"]))


def render_pick(
    row: dict, tier_name: str,
    processed_dir: Path, out_dir: Path,
) -> Path:
    sid = row["sample_id"]
    label = row.get("label") or "unlabeled"
    n_ent = total_entities(row)
    g = BehaviorGraph.from_processed(sid, processed_dir)
    out_path = out_dir / f"sprint2_graph_{tier_name}_{sid[:8]}.png"
    title = (
        f"sprint 2 graph - {tier_name} tier\n"
        f"sample={sid[:12]}... label={label} entities={n_ent}"
    )
    return render(g, out_path, title=title)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    ap.add_argument("--processed-dir", type=Path, default=DEFAULT_PROCESSED)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    if not args.manifest.is_file():
        print(f"[ERROR] manifest not found: {args.manifest}", file=sys.stderr)
        return 1
    args.out_dir.mkdir(parents=True, exist_ok=True)

    rows = load_manifest(args.manifest)
    if not rows:
        print("[ERROR] no parse_ok=True rows in manifest", file=sys.stderr)
        return 1
    print(f"[INFO] {len(rows)} samples available")

    picks = [
        ("small", pick_small(rows)),
        ("medium", pick_medium(rows)),
        ("large", pick_large(rows)),
    ]
    for tier, row in picks:
        print(
            f"[PICK] {tier:6s} sample={row['sample_id'][:12]}... "
            f"label={row.get('label') or '-':10s} "
            f"entities={total_entities(row)}"
        )

    for tier, row in picks:
        out = render_pick(row, tier, args.processed_dir, args.out_dir)
        print(f"[RENDER] {tier:6s} -> {out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())