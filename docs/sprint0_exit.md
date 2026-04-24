# Sprint 0 Exit Review

**Dates:** April 2026 (week 0–1)
**Theme:** Build on rock, not sand.
**Status:** Complete.

## PDF exit criteria — confirmation

| Criterion | Status | Evidence |
|-----------|--------|----------|
| Fresh-clone + `pip install -r requirements.txt` + smoke test works | PASS | 105 pinned packages install cleanly on Python 3.12. Imports verified. |
| `docker build .` succeeds without errors | PASS | `malgraph:sprint0` image built in ~3 min; image size minimal thanks to `.dockerignore`. |
| Dataset source documented and at least one report visibly loaded | PASS | `docs/dataset.md` documents Avast-CTU CAPEv2. `ingest/load.py` prints full schema of loaded reports. |
| Repo committed and pushed | PENDING | Final push to GitHub is Step 8. |

## What was delivered

- Public repo layout matching PDF spec (`data/`, `ingest/`, `graph/`, `features/`, `models/`, `eval/`, `api/`, `experiments/`, `docs/`, `tests/`, plus top-level files).
- Python 3.12 virtual environment with pinned `requirements.txt` (pandas, networkx, pyarrow, pytest, jupyter, ipykernel and transitive deps).
- Minimal working Dockerfile on `python:3.10-slim`, with a `.dockerignore` that keeps build context under 11 KB.
- Dataset: 4,000 real CAPEv2 reports (2,000 Emotet + 2,000 Trickbot), stratified random sample from the Avast-CTU Public CAPEv2 dataset, seeded for reproducibility.
- `scripts/filter_dataset_full.py` — streaming ZIP filter that extracts only target samples without materializing the full 150+ GB dataset on disk.
- `ingest/load.py` — Sprint 0 smoke-test loader that opens any CAPE report and dumps its schema up to 2 levels deep.
- Documentation: `README.md`, `docs/dataset.md`, this file.

## What was NOT delivered (scope changes from PDF)

- **Binary malicious/benign framing replaced with family classification.** The
  Avast-CTU dataset is malicious-only, and acquiring a matched benign corpus
  would have introduced distributional mismatch (different sandbox config).
  Task was reframed as Emotet vs Trickbot binary family classification.
  Pipeline architecture, algorithms, and deliverables are unchanged.
- **Sample count scaled down from ≥2,000 "mixed" to 4,000 balanced.** The
  full-report size (~4.3 MB average) made full-corpus sampling disk-prohibitive
  on the development machine. 4,000 balanced samples exceeds the PDF's Sprint 3
  minimum while keeping disk footprint at ~17 GB.

## Key decisions to carry into Sprint 1

- **Schema:** CAPE full-report schema is richer than the PDF's original event
  spec assumed. Sprint 1 will map CAPE fields onto the PDF's event types as
  follows (draft — to be finalized in Sprint 1):
    - `process_spawn` <- `behavior.processtree` entries
    - `file_write` <- `behavior.summary.write_files` + enhanced events
    - `file_read` <- `behavior.summary.read_files` + enhanced events
    - `reg_write` <- `behavior.summary.write_keys`
    - `net_connect` <- `network.tcp` / `network.udp`
    - `dns_query` <- `network.dns` + `suricata.dns`
- **Labels:** Already filtered and stored in `data/raw/labels.csv` with
  columns `sha256, classification_family, classification_type, date`.
  No need to re-derive during Sprint 1 parsing.
- **Reproducibility:** RNG seed is `42` in the filter script. Same seed must
  be used for any train/val/test splits in Sprint 3 to keep the held-out
  test set consistent across sprints.

## Risks flagged for Sprint 1

- Some CAPE reports have very high cardinality in `behavior.enhanced`
  (seen: 298 events in a single Emotet sample). Sprint 1's parser must
  stream or iterate rather than load-all-and-transform.
- Trickbot samples in the observed data are consistently smaller than
  Emotet samples (~500 KB vs ~4 MB). Sprint 3 features that scale with
  raw size (e.g. "total event count") will correlate with label even
  without meaningful behavioral signal. Need to normalize or design
  size-invariant features.
