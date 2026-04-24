"""
Filter the Avast-CTU staging dataset down to Emotet and Trickbot samples.
Copies matching reports from the staging area into data/raw/ and writes
a filtered labels.csv alongside them.

Usage:
    python scripts/filter_dataset.py
"""
from __future__ import annotations

import csv
import shutil
import sys
from pathlib import Path

# Config --------------------------------------------------------------
STAGING_ROOT = Path(r"C:\Users\rjoas\code\malgraph-staging")
REPORTS_DIR = STAGING_ROOT / "reports" / "public_small_reports"
LABELS_CSV = STAGING_ROOT / "public_labels.csv"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "data" / "raw"
FILTERED_LABELS = RAW_DIR / "labels.csv"

TARGET_FAMILIES = {"Emotet", "Trickbot"}
# ---------------------------------------------------------------------


def main() -> int:
    if not LABELS_CSV.exists():
        print(f"ERROR: labels file not found: {LABELS_CSV}", file=sys.stderr)
        return 1
    if not REPORTS_DIR.exists():
        print(f"ERROR: reports dir not found: {REPORTS_DIR}", file=sys.stderr)
        return 1

    RAW_DIR.mkdir(parents=True, exist_ok=True)

    # Pass 1: read labels, identify target samples
    target_rows = []
    with LABELS_CSV.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["classification_family"] in TARGET_FAMILIES:
                target_rows.append(row)

    print(f"Labels matched: {len(target_rows)}")
    by_family = {}
    for r in target_rows:
        by_family[r["classification_family"]] = by_family.get(r["classification_family"], 0) + 1
    for fam, n in sorted(by_family.items()):
        print(f"  {fam}: {n}")

    # Pass 2: copy matching report files
    copied = 0
    missing = 0
    total = len(target_rows)
    for i, row in enumerate(target_rows, 1):
        src = REPORTS_DIR / f"{row['sha256']}.json"
        dst = RAW_DIR / f"{row['sha256']}.json"
        if not src.exists():
            missing += 1
            continue
        if not dst.exists():
            shutil.copy2(src, dst)
        copied += 1
        if i % 500 == 0 or i == total:
            print(f"  progress: {i}/{total}")

    # Write filtered labels alongside the raw data
    with FILTERED_LABELS.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["sha256", "classification_family", "classification_type", "date"])
        writer.writeheader()
        writer.writerows(target_rows)

    print(f"\nCopied: {copied}")
    print(f"Missing source files: {missing}")
    print(f"Labels written to: {FILTERED_LABELS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
