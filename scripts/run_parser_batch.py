"""
Batch runner for the Sprint 1 parser.

Iterates raw CAPE reports from data/raw/, runs parse_report_with_manifest
on each, writes:

- data/processed/<sample_id>.jsonl   — normalized event stream per sample
- data/processed/manifest.jsonl      — one aggregate manifest row per sample

Usage:
    python scripts/run_parser_batch.py
    python scripts/run_parser_batch.py --limit 10
    python scripts/run_parser_batch.py --raw-dir data/raw \
                                       --out-dir data/processed \
                                       --labels data/raw/labels.csv
    python scripts/run_parser_batch.py --resume    # skip already-parsed

Errors in a single sample do NOT stop the batch; they are captured in
the manifest with parse_ok=False and a parse_error string. Exit code is
0 on clean completion, 1 only if the runner itself crashes (not if
individual samples fail).

Single-process by design for Sprint 1 — deterministic output ordering,
easy to debug, acceptable throughput for ~4k samples on modern hardware.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from typing import Iterator

# Make the repo root importable when invoked as `python scripts/...`.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from ingest.events import SCHEMA_VERSION
from ingest.parser import parse_report_with_manifest


DEFAULT_RAW = Path("data/raw")
DEFAULT_OUT = Path("data/processed")
DEFAULT_LABELS = Path("data/raw/labels.csv")
PROGRESS_EVERY = 50


def load_labels(path: Path) -> dict[str, dict[str, str]]:
    """
    Load labels.csv into {sha256: row_dict}. Missing file returns {}
    with a warning — lets batch run on a subset without labels.
    """
    if not path.is_file():
        print(f"[WARN] labels file not found: {path}", file=sys.stderr)
        return {}
    labels: dict[str, dict[str, str]] = {}
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sha = (row.get("sha256") or "").strip().lower()
            if sha:
                labels[sha] = row
    return labels


def discover_samples(raw_dir: Path) -> list[Path]:
    """All .json files in raw_dir, sorted for deterministic ordering."""
    return sorted(raw_dir.glob("*.json"))


def already_parsed(sample_id: str, out_dir: Path) -> bool:
    """True if both the per-sample JSONL exists and a manifest row exists."""
    return (out_dir / f"{sample_id}.jsonl").is_file()


def write_events_jsonl(
    events_iter: Iterator, out_path: Path,
) -> None:
    """Write events one per line. Atomic via tmp + rename."""
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for event in events_iter:
            f.write(event.to_json())
            f.write("\n")
    tmp.replace(out_path)


def write_entities_sidecar(entities: list[dict], out_path: Path) -> None:
    """Write per-entity rows one per line. Atomic via tmp + rename."""
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for entity in entities:
            f.write(json.dumps(entity, separators=(",", ":"), ensure_ascii=False))
            f.write("\n")
    tmp.replace(out_path)


def append_manifest(row: dict, manifest_path: Path) -> None:
    """Append one manifest row as a JSONL line."""
    with manifest_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, separators=(",", ":"), ensure_ascii=False))
        f.write("\n")


def run_batch(
    raw_dir: Path,
    out_dir: Path,
    labels_path: Path,
    limit: int | None,
    resume: bool,
) -> int:
    """
    Returns a shell exit code: 0 on normal completion (even with some
    sample-level parse failures), 1 if a fatal runner error occurs.
    """
    if not raw_dir.is_dir():
        print(f"[ERROR] raw-dir not found: {raw_dir}", file=sys.stderr)
        return 1
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = out_dir / "manifest.jsonl"
    schema_file = out_dir / "_schema_version.txt"

    # Refresh on a fresh (non-resume) run so stale outputs don't linger.
    if not resume:
        manifest_path.unlink(missing_ok=True)
    schema_file.write_text(SCHEMA_VERSION + "\n", encoding="utf-8")

    labels = load_labels(labels_path)
    samples = discover_samples(raw_dir)
    if limit is not None:
        samples = samples[:limit]

    if not samples:
        print(f"[WARN] no .json files found in {raw_dir}", file=sys.stderr)
        return 0

    n_total = len(samples)
    n_ok = 0
    n_fail = 0
    n_skipped = 0
    t_start = time.time()
    print(f"[INFO] parsing {n_total} samples from {raw_dir}")

    for i, path in enumerate(samples, start=1):
        sample_id = path.stem
        if resume and already_parsed(sample_id, out_dir):
            n_skipped += 1
            continue

        try:
            events, manifest = parse_report_with_manifest(path)
        except Exception as e:  # last-resort safety net
            n_fail += 1
            row = {
                "sample_id": sample_id,
                "path": str(path),
                "parse_ok": False,
                "parse_error": f"runner-level: {type(e).__name__}: {e}",
                "event_counts": {},
                "entity_counts": {},
                "filters": {},
                "schema_version": SCHEMA_VERSION,
            }
            _attach_label(row, sample_id, labels)
            append_manifest(row, manifest_path)
            _progress(i, n_total, n_ok, n_fail, n_skipped, t_start)
            continue

        if manifest.get("parse_ok"):
            write_events_jsonl(
                iter(events), out_dir / f"{sample_id}.jsonl",
            )
            write_entities_sidecar(
                manifest.get("entities", []),
                out_dir / f"{sample_id}.entities.jsonl",
            )
            n_ok += 1
        else:
            n_fail += 1

        # entities live in a per-sample sidecar; don't duplicate them
        # into the aggregate manifest.jsonl.
        manifest.pop("entities", None)
        manifest["schema_version"] = SCHEMA_VERSION
        _attach_label(manifest, sample_id, labels)
        append_manifest(manifest, manifest_path)
        _progress(i, n_total, n_ok, n_fail, n_skipped, t_start)

    elapsed = time.time() - t_start
    print(
        f"[DONE] total={n_total} ok={n_ok} fail={n_fail} "
        f"skipped={n_skipped} elapsed={elapsed:.1f}s "
        f"({n_total / elapsed:.1f} samples/s)"
    )
    print(f"[DONE] events -> {out_dir}/<sample>.jsonl")
    print(f"[DONE] manifest -> {manifest_path}")
    return 0


def _attach_label(
    row: dict, sample_id: str, labels: dict[str, dict[str, str]],
) -> None:
    """Inject family label from labels.csv into the manifest row."""
    label_row = labels.get(sample_id.lower())
    if label_row is None:
        row["label"] = None
    else:
        row["label"] = label_row.get("classification_family")
        ctype = label_row.get("classification_type")
        if ctype:
            row["label_type"] = ctype


def _progress(
    i: int, total: int, n_ok: int, n_fail: int, n_skipped: int,
    t_start: float,
) -> None:
    if i % PROGRESS_EVERY == 0 or i == total:
        elapsed = time.time() - t_start
        rate = i / elapsed if elapsed > 0 else 0
        eta = (total - i) / rate if rate > 0 else 0
        print(
            f"[{i}/{total}] ok={n_ok} fail={n_fail} "
            f"skipped={n_skipped} "
            f"rate={rate:.1f}/s eta={eta:.0f}s"
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    ap.add_argument("--limit", type=int, default=None,
                    help="Process only the first N samples (for smoke).")
    ap.add_argument("--resume", action="store_true",
                    help="Skip samples that already have output JSONL.")
    args = ap.parse_args()

    return run_batch(
        raw_dir=args.raw_dir,
        out_dir=args.out_dir,
        labels_path=args.labels,
        limit=args.limit,
        resume=args.resume,
    )


if __name__ == "__main__":
    sys.exit(main())
