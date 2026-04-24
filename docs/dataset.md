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

Full reports (13 GB outer ZIP, ~200 GB uncompressed):

https://drive.google.com/file/d/1ItUYKtr3hmjos8Hdd_e6rohuYSRgAAId/view

**Do NOT download the Reduced reports archive** (566 MB). That version
strips the behavioral telemetry this project depends on — no `info`, no
`network`, no `behavior.processes`, no timestamps. Confirmed by schema
inspection during Sprint 0.

### Archive structure (Russian doll)

The downloaded ZIP contains:
- `public_full_reports.zip` — inner ZIP with all 48,976 JSON reports
- `public_labels.csv` — metadata: `sha256, classification_family, classification_type, date`
- `ReadMe.md` — upstream documentation
- `__MACOSX/` — macOS metadata artifact, safe to delete

You only need to extract the OUTER ZIP. The filter script reads the inner
ZIP directly without extracting it, which keeps peak disk usage manageable.

## This project's sample

- **Task:** Binary malware family classification — Emotet vs Trickbot.
- **Sampling:** Stratified random sample of 2,000 Emotet + 2,000 Trickbot
  from the full dataset, seeded with `random.seed(42)` for reproducibility.
- **Why 4,000 and not more:** Full reports average ~4.3 MB each; taking
  all 14,429 Emotet + 4,202 Trickbot would be ~80 GB on disk and
  significantly class-imbalanced. 4,000 balanced samples exceeds the
  sprint plan's 2,000-sample minimum.
- **Why Emotet + Trickbot:** Both are banking trojans with historical
  interdependence (Emotet has been observed as a Trickbot dropper). Similar
  category, different code lineage — a harder and more realistic
  classification task than cross-category pairs would offer.

## Reproducing the filtered sample

1. Download the outer ZIP (13 GB) from the Drive link above.

2. Create a staging directory outside the repo — by default the script
   looks at `../malgraph-staging/` (a sibling of the repo). If you put it
   elsewhere, pass the path to the script or set `MALGRAPH_STAGING`.

3. Extract the outer ZIP into the staging directory. On Windows PowerShell:
```powershell
   Add-Type -AssemblyName System.IO.Compression.FileSystem
   [System.IO.Compression.ZipFile]::ExtractToDirectory(
       "$env:USERPROFILE\Downloads\Public_Avast_CTU_CAPEv2_Dataset_Full.zip",
       "..\malgraph-staging"
   )
```
   After this, your staging directory should contain
   `public_full_reports.zip`, `public_labels.csv`, and `ReadMe.md`.
   Do NOT extract the inner ZIP — the script reads it directly.

4. From the repo root, run:
```bash
   python scripts/filter_dataset_full.py
   # or with explicit staging path:
   python scripts/filter_dataset_full.py /path/to/staging
```

5. Output: 4,000 JSON reports in `data/raw/` plus a filtered
   `data/raw/labels.csv`.

6. (Optional) Delete the staging directory and outer ZIP to reclaim disk:
```powershell
   Remove-Item ..\malgraph-staging -Recurse -Force
```

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
