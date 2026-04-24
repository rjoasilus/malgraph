# Dataset

## Source

**Avast-CTU Public CAPEv2 Dataset** — 48,976 malicious Windows samples
detonated in CAPEv2 sandboxes at the Czech Technical University AI Center,
in cooperation with Avast Software. Each sample has a full CAPEv2 JSON
report with behavioral telemetry (processes, process trees, API calls,
file operations, registry operations, network events) and static PE analysis.

- Upstream repository: https://github.com/avast/avast-ctu-cape-dataset
- Paper: Bosansky et al., "Avast-CTU Public CAPE Dataset" (arXiv:2209.03188)
- Collection period: July – September 2021

### Why this dataset

- **Real behavioral data, not synthetic.** Actual malware detonated in
  production-grade sandboxes.
- **Scale.** 48,976 samples — enough headroom to subsample without stress.
- **Rich schema.** Full reports include `info`, `behavior.processes`,
  `behavior.processtree`, `behavior.enhanced`, `network.{tcp,udp,dns,http}`,
  `signatures`, MITRE ATT&CK `ttps`. Supports the graph model defined in
  the sprint plan without schema gymnastics.
- **Multi-family labels.** Ten malware families, enabling binary or
  multi-class classification depending on project scope.

## License

Research use. Samples are distributed as execution logs only (no binaries).
Cite the upstream paper when publishing results:

```bibtex
@misc{avast-ctu-cape-dataset-2022,
  doi = {10.48550/ARXIV.2209.03188},
  url = {https://arxiv.org/abs/2209.03188},
  author = {Bosansky, Branislav and Kouba, Dominik and Manhal, Ondrej and
            Sick, Thorsten and Lisy, Viliam and Kroustek, Jakub and Somol, Petr},
  title = {Avast-CTU Public CAPE Dataset},
  publisher = {arXiv},
  year = {2022}
}
```

## Acquisition

Full reports (13 GB ZIP):

https://drive.google.com/file/d/1ItUYKtr3hmjos8Hdd_e6rohuYSRgAAId/view

The ZIP contains an inner `public_full_reports.zip` plus a `public_labels.csv`
metadata file and a ReadMe.

**Do not download the Reduced reports archive** (566 MB) — that version
strips the behavioral telemetry this project depends on (no `info`, no
`network`, no `behavior.processes`, no timestamps). Confirmed by schema
inspection during Sprint 0. See `docs/schema_notes.md` (forthcoming in
Sprint 1) for the full comparison.

## This project's sample

- **Task:** Binary malware family classification — Emotet vs Trickbot.
- **Sampling:** Stratified random sample of 2,000 Emotet + 2,000 Trickbot
  from the full dataset, seeded with `random.seed(42)` for reproducibility.
- **Why 4,000 and not more:** Path chosen during Sprint 0 to balance disk
  footprint (full reports average ~4.3 MB each) against ML corpus size.
  4,000 balanced samples exceeds the sprint plan's 2,000-sample minimum and
  avoids the class-imbalance problem of taking all 14,429 Emotet + 4,202
  Trickbot samples.
- **Why Emotet + Trickbot:** Both are banking trojans with historical
  interdependence (Emotet has been observed as a Trickbot dropper). Similar
  category, different code lineage — a harder and more realistic
  classification task than cross-category pairs would offer.

Reproducing the filtered sample:

```bash
# 1. Download the full archive to ~/Downloads (see link above)
# 2. Extract the outer ZIP into a staging directory:
#    e.g. C:\Users\<user>\code\malgraph-staging\
# 3. Update paths at the top of scripts/filter_dataset_full.py if needed
# 4. Run:
python scripts/filter_dataset_full.py
```

The script reads the staging ZIP by streaming, so peak disk usage stays
under the full uncompressed footprint (~150+ GB). Final output:
4,000 JSON reports in `data/raw/` plus a filtered `data/raw/labels.csv`.

## What is NOT in the repository

The 4,000 reports (~17 GB on disk) are gitignored. Anyone reproducing the
project needs to run the acquisition steps above. This is standard practice
for malware research datasets and respects the upstream licensing terms.

## Fallback sources (if Avast-CTU becomes unavailable)

1. MALVADA dataset — 30,000+ CAPEv2 traces, published in ScienceDirect
   (Fernández et al., 2025). Schema compatible.
2. Cuckoo/CAPE public report dumps on GitHub — smaller, inconsistent
   schemas; useful for schema reference but not as a primary corpus.
3. Self-detonation via local CAPEv2 instance + MalwareBazaar — high
   effort, not recommended unless primary sources fail.
