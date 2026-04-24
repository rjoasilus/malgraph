# MalGraph

Behavior-based malware family classification via graph modeling of sandbox telemetry.

Polymorphic malware mutates its code but not its behavior. MalGraph ingests
dynamic CAPEv2 sandbox reports, constructs typed behavior graphs (processes,
files, domains, registry keys), and classifies samples by malware family using
a hybrid of hand-engineered graph features and learned GNN embeddings. The
final artifact is a containerized FastAPI service with a scoring endpoint.

**Current task:** Binary family classification — **Emotet vs Trickbot** —
drawn from the Avast-CTU Public CAPEv2 dataset. Both are banking trojans,
chosen because they are behaviorally similar and historically interrelated
(Emotet has been observed dropping Trickbot), making the classification task
non-trivial. See `docs/dataset.md` for full provenance and reproduction steps.

## Status

| Sprint | Phase                          | Status       |
|--------|--------------------------------|--------------|
| 0      | Foundation & Environment       | Complete     |
| 1      | Telemetry Ingestion            | Not started  |
| 2      | Graph Construction             | Not started  |
| 3      | Features + Baseline ML         | Not started  |
| 4      | Graph Learning (GNN)           | Not started  |
| 5      | Adversarial Robustness         | Not started  |
| 6      | Deployment & Polish            | Not started  |

## Quickstart

Requires Python 3.10 or later (developed on 3.12). Requires Docker only if
you want to run containerized; native Python works for all sprints up through 5.

```bash
git clone git@github.com:rjoasilus/malgraph.git
cd malgraph
python -m venv .venv
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# macOS/Linux:
# source .venv/bin/activate
pip install -r requirements.txt
```

### Acquire the dataset

The reports themselves are not in the repo (~17 GB, gitignored). Full
acquisition and reproduction steps are in `docs/dataset.md`. Short version:

1. Download the Avast-CTU Public CAPEv2 **Full** reports archive from the
   upstream Drive link (see `docs/dataset.md`).
2. Extract the outer ZIP to a sibling staging directory (e.g. `../malgraph-staging/`).
3. Run the stratified sampling filter:

```bash
   python scripts/filter_dataset_full.py
```

   Default staging path is `../malgraph-staging/` relative to the repo root.
   Override with `python scripts/filter_dataset_full.py /path/to/staging`
   or via the `MALGRAPH_STAGING` environment variable.

4. Result: 4,000 JSON reports in `data/raw/` (2,000 Emotet + 2,000 Trickbot,
   stratified random sample seeded with `42`) plus a filtered `labels.csv`.

### Smoke-test the loader

```bash
python ingest/load.py                          # picks a random report
python ingest/load.py data/raw/<sha256>.json   # load a specific one
```

## Docker

```bash
docker build -t malgraph:sprint0 .
docker run --rm malgraph:sprint0
# Run the loader with host data mounted into the container:
docker run --rm -v ${PWD}/data:/app/data malgraph:sprint0 python ingest/load.py
```

## Architecture

Six-stage pipeline:
Raw sandbox JSON
|
v
[ Sprint 1 ] Telemetry ingestion     -> Normalized event stream
|
v
[ Sprint 2 ] Graph construction      -> Typed behavior graph
|
v
[ Sprint 3 ] Feature extraction + ML -> Baseline family classifier
|
v
[ Sprint 4 ] Graph learning (GNN)    -> Learned embedding + hybrid classifier
|
v
[ Sprint 5 ] Robustness testing      -> Degradation curves under perturbation
|
v
[ Sprint 6 ] Deployment              -> FastAPI /scan + Docker + CLI

## Repository layout
malgraph/
api/            # Sprint 6: FastAPI service
data/
raw/          # CAPE JSON reports (gitignored; see docs/dataset.md)
processed/    # Sprint 1 normalized event streams
docs/           # Architecture, schema, sprint reports
eval/           # Metrics and evaluation scripts
experiments/    # Sprint-dated notebooks
features/       # Sprint 3 feature extraction
graph/          # Sprint 2 graph builder
ingest/         # Sprint 1 parser; Sprint 0 smoke-test loader
models/         # Trained models (gitignored)
scripts/        # Reproducible setup scripts (dataset filter, etc.)
tests/          # Unit tests
Dockerfile
requirements.txt
README.md

## Context

Individual extension of a CSC-350 (Analysis of Algorithms) group project at
Rider University. Solo work in this repository; any shared group code would
live in a separate tree.

## License

TBD.
