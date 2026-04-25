"""
Build a BehaviorGraph for every parsed sample and record per-sample
timing + memory. Output CSV feeds experiments/sprint2_runtime.md, which
validates the theoretical O(|E|) bound from docs/complexity.md.

Don't conflate the two: this script measures empirical wall-clock vs
problem size; the proof in docs/complexity.md is independent. The PDF
caveat is explicit -- both are required.

Output: experiments/sprint2_build_times.csv with columns:
    sample_id       full sha256
    label           from manifest (Emotet/Trickbot/None)
    n_events        len(events)
    n_nodes         g.n_nodes (after build)
    n_edges         g.n_edges (== n_events for our 1:1 mapping)
    build_time_s    min over 3 runs (perf_counter)
    peak_memory_kb  tracemalloc peak, only every 10th sample (else blank)

Methodology notes:
- Build time is min-of-3 to suppress GC/OS jitter. Mean is misleading
  on cold-cache first runs; min approximates the steady-state cost.
- Memory measured every 10th sample. tracemalloc adds non-trivial
  per-allocation overhead and would distort timing if always-on.
  400 memory points across 4k samples is plenty for a regression.
- Samples that fail to load (missing sidecar, corrupt jsonl) are
  logged to stderr and skipped, not fatal. Sprint 1 produced 4000/4000
  parse-ok, so this is defensive only.

Usage:
    python scripts/build_all_graphs.py
    python scripts/build_all_graphs.py --limit 100
    python scripts/build_all_graphs.py --memory-every 1   # heavy
"""
from __future__ import annotations

import argparse
import csv
import gc
import json
import sys
import time
import tracemalloc
from pathlib import Path

# Repo-root import shim (same pattern as scripts/run_parser_batch.py).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from graph.builder import BehaviorGraph


DEFAULT_MANIFEST = Path("data/processed/manifest.jsonl")
DEFAULT_PROCESSED = Path("data/processed")
DEFAULT_OUT = Path("experiments/sprint2_build_times.csv")
PROGRESS_EVERY = 100
TIMING_RUNS = 3
MEMORY_EVERY = 10


def load_manifest(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as f:
        return [
            json.loads(line) for line in f
            if line.strip() and json.loads(line).get("parse_ok")
        ]


def load_inputs(
    sample_id: str, processed_dir: Path,
) -> tuple[list[dict], dict[str, dict]]:
    """Load events + entity sidecar from disk. Pure I/O, excluded from
    build timing -- we want to measure the algorithm, not file reads."""
    events_path = processed_dir / f"{sample_id}.jsonl"
    entities_path = processed_dir / f"{sample_id}.entities.jsonl"
    with events_path.open("r", encoding="utf-8") as f:
        events = [json.loads(line) for line in f if line.strip()]
    with entities_path.open("r", encoding="utf-8") as f:
        entities = {
            row["id"]: row
            for row in (json.loads(line) for line in f if line.strip())
        }
    return events, entities


def time_build(events: list[dict], entities: dict, runs: int) -> float:
    """Return min wall-clock over `runs` builds. Discards results."""
    best = float("inf")
    for _ in range(runs):
        # Force GC before each run so collection cost doesn't bleed in.
        gc.collect()
        t0 = time.perf_counter()
        BehaviorGraph.build_from_events(events, entities)
        elapsed = time.perf_counter() - t0
        if elapsed < best:
            best = elapsed
    return best


def measure_memory(events: list[dict], entities: dict) -> int:
    """Peak allocation in KB during a single build under tracemalloc."""
    gc.collect()
    tracemalloc.start()
    BehaviorGraph.build_from_events(events, entities)
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return peak_bytes // 1024


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    ap.add_argument("--processed-dir", type=Path, default=DEFAULT_PROCESSED)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--limit", type=int, default=None,
                    help="Only first N samples (smoke-test).")
    ap.add_argument("--timing-runs", type=int, default=TIMING_RUNS)
    ap.add_argument("--memory-every", type=int, default=MEMORY_EVERY,
                    help="Measure peak memory every Nth sample. "
                         "1 = always (slow), large = sparse.")
    args = ap.parse_args()

    if not args.manifest.is_file():
        print(f"[ERROR] manifest not found: {args.manifest}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)

    rows = load_manifest(args.manifest)
    if args.limit is not None:
        rows = rows[: args.limit]
    n_total = len(rows)
    print(f"[INFO] {n_total} samples to build")
    print(f"[INFO] timing: min over {args.timing_runs} runs")
    print(f"[INFO] memory: every {args.memory_every}th sample")

    n_ok = 0
    n_fail = 0
    t_start = time.time()
    with args.out.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "sample_id", "label",
            "n_events", "n_nodes", "n_edges",
            "build_time_s", "peak_memory_kb",
        ])
        for i, row in enumerate(rows, start=1):
            sid = row["sample_id"]
            label = row.get("label") or ""
            try:
                events, entities = load_inputs(sid, args.processed_dir)
                build_time = time_build(events, entities, args.timing_runs)
                peak_kb = ""
                if i % args.memory_every == 0:
                    peak_kb = measure_memory(events, entities)
                # One more build to get final n_nodes/n_edges. The
                # timing build's graph was discarded; this isn't free
                # but it's a single build and gives the actual sizes.
                g = BehaviorGraph.build_from_events(events, entities)
                writer.writerow([
                    sid, label,
                    len(events), g.n_nodes, g.n_edges,
                    f"{build_time:.6f}", peak_kb,
                ])
                n_ok += 1
            except Exception as e:
                n_fail += 1
                print(
                    f"[SKIP] {sid[:12]}... {type(e).__name__}: {e}",
                    file=sys.stderr,
                )

            if i % PROGRESS_EVERY == 0 or i == n_total:
                elapsed = time.time() - t_start
                rate = i / elapsed if elapsed > 0 else 0
                eta = (n_total - i) / rate if rate > 0 else 0
                print(
                    f"[{i}/{n_total}] ok={n_ok} fail={n_fail} "
                    f"rate={rate:.1f}/s eta={eta:.0f}s"
                )

    elapsed = time.time() - t_start
    print(
        f"[DONE] total={n_total} ok={n_ok} fail={n_fail} "
        f"elapsed={elapsed:.1f}s ({n_total / elapsed:.1f} samples/s)"
    )
    print(f"[DONE] -> {args.out}")
    return 0 if n_fail == 0 else 0  # don't fail-exit on per-sample skips


if __name__ == "__main__":
    sys.exit(main())