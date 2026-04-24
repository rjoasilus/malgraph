"""
Stream-filter the Avast-CTU FULL reports ZIP down to a stratified sample
of Emotet + Trickbot samples. Reads the ZIP directly without extracting
everything first, so peak disk usage stays reasonable.

Usage:
    python scripts/filter_dataset_full.py
"""
from __future__ import annotations

import csv
import random
import sys
import zipfile
from pathlib import Path

# Config --------------------------------------------------------------
STAGING_ROOT = Path(r"C:\Users\rjoas\code\malgraph-staging")
REPORTS_ZIP = STAGING_ROOT / "public_full_reports.zip"
LABELS_CSV = STAGING_ROOT / "public_labels.csv"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "data" / "raw"
FILTERED_LABELS = RAW_DIR / "labels.csv"

TARGET_FAMILIES = {"Emotet", "Trickbot"}
SAMPLES_PER_FAMILY = 2000
RANDOM_SEED = 42  # reproducible sampling
# ---------------------------------------------------------------------


def main() -> int:
    if not REPORTS_ZIP.exists():
        print(f"ERROR: reports ZIP not found: {REPORTS_ZIP}", file=sys.stderr)
        return 1
    if not LABELS_CSV.exists():
        print(f"ERROR: labels CSV not found: {LABELS_CSV}", file=sys.stderr)
        return 1

    random.seed(RANDOM_SEED)
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    # Clear any existing files (the reduced reports from earlier)
    existing = list(RAW_DIR.glob("*.json"))
    if existing:
        print(f"Removing {len(existing)} existing JSON files from data/raw/...")
        for p in existing:
            p.unlink()

    # Pass 1: read labels, bucket by family
    by_family: dict[str, list[dict]] = {fam: [] for fam in TARGET_FAMILIES}
    with LABELS_CSV.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            fam = row["classification_family"]
            if fam in TARGET_FAMILIES:
                by_family[fam].append(row)

    # Stratified sample
    sampled: list[dict] = []
    for fam in sorted(TARGET_FAMILIES):
        pool = by_family[fam]
        if len(pool) < SAMPLES_PER_FAMILY:
            print(f"WARNING: only {len(pool)} {fam} samples available, taking all")
            sampled.extend(pool)
        else:
            sampled.extend(random.sample(pool, SAMPLES_PER_FAMILY))
        fam_count = min(len(pool), SAMPLES_PER_FAMILY)
        print(f"  {fam}: sampling {fam_count} from {len(pool)}")

    target_sha256s = {row["sha256"] for row in sampled}
    print(f"Total target samples: {len(target_sha256s)}")
    print()

    # Pass 2: stream the ZIP, extract only matching entries
    print(f"Opening ZIP: {REPORTS_ZIP.name} ...")
    extracted = 0
    missing = 0
    target_filenames = {f"{sha}.json" for sha in target_sha256s}

    with zipfile.ZipFile(REPORTS_ZIP, "r") as zf:
        # Build a map of basename -> full path within the ZIP
        name_map = {Path(n).name: n for n in zf.namelist() if n.endswith(".json")}
        print(f"ZIP contains {len(name_map)} JSON entries total")
        print()

        for i, sha in enumerate(sorted(target_sha256s), 1):
            fname = f"{sha}.json"
            if fname not in name_map:
                missing += 1
                continue
            zip_path = name_map[fname]
            dest = RAW_DIR / fname
            with zf.open(zip_path) as src, dest.open("wb") as dst:
                # chunk copy to handle large files
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    dst.write(chunk)
            extracted += 1
            if i % 200 == 0 or i == len(target_sha256s):
                print(f"  progress: {i}/{len(target_sha256s)}")

    # Write sampled labels CSV
    with FILTERED_LABELS.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["sha256", "classification_family", "classification_type", "date"]
        )
        writer.writeheader()
        writer.writerows(sampled)

    print()
    print(f"Extracted: {extracted}")
    print(f"Missing from ZIP: {missing}")
    print(f"Labels written to: {FILTERED_LABELS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
