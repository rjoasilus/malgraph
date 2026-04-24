# Sprint 1 Exit Review

**Dates:** April 2026 (weeks 2–3)
**Theme:** Turn mess into signal.
**Status:** Complete.

## PDF exit criteria — confirmation

| Criterion | Status | Evidence |
|-----------|--------|----------|
| Parser runs cleanly over every Sprint 0 sample with zero unhandled exceptions | PASS | 4,000/4,000 parsed, 0 failures in `data/processed/manifest.jsonl`. Exit criterion #7 explicitly met at full corpus scale. |
| All unit tests pass (`pytest` returns green) | PASS | 264 tests in 8 files, ~0.85s runtime. Covers 5 extractors, canonicalization, entity registry, and PDF-mandated failure modes (corrupt JSON, missing fields, non-UTF-8, empty, oversize). |
| Processed files exist in `data/processed/` and conform to the schema | PASS | 4,000 `.jsonl` files (537 MB total, gitignored) + `manifest.jsonl` (committed as evidence) + `_schema_version.txt=1.0`. Every event JSON validates against `ingest.events.validate_event_dict`. |
| Event-type distribution is sane (no event_type suspiciously 0% or 100%) | PASS | See `experiments/sprint1_stats.md`. 12 of 13 types present; `process_terminate` deliberately deferred to Sprint 3 (requires `behavior.processes[].calls`, out of Sprint 1 scope). No type exceeds 70%; `reg_read` leads at 69.9% which is expected for Windows malware. |

## What was delivered

### Core parser (`ingest/`)

- `events.py` — `Event` dataclass (frozen, slotted) with 13 `EventType` enum values and 4 `TimestampSource` values. JSONL serialization + structural validation. Schema pinned at version 1.0.
- `entities.py` — `EntityRegistry` with 9 entity types. Content-hash IDs (sha1[:12]) for cross-sample stable entities; sample-scoped hash for processes. `has_canonical()` O(1) dedup check.
- `canonicalize.py` — pure functions for Windows paths (with named-pipe routing + `\Device\HarddiskVolume1\` rewrite), registry keys (HKEY_* uppercase), domains, IPs (+ RFC1918/loopback/link-local/multicast classification), and CAPE timestamps (all 4 observed Avast-CTU formats).
- `parser.py` — 5 extractors (`extract_process_spawns`, `extract_enhanced_events`, `extract_summary_fallbacks`, `extract_network_events`, `extract_dns_events`) plus orchestration (`parse_report` and `parse_report_with_manifest`). Timestamp rebasing, total-order sort, manifest accumulation.

### Tests (`tests/`)

264 tests across 8 files:

- `test_canonicalize.py` — 51 tests; every canonicalization rule with parametrized edge cases.
- `test_entities.py` — 29 tests; ID-format, stability, cross-type uniqueness, sample-scoping, manifest ordering invariants.
- `test_parser_process_spawns.py` — 21 tests; tree walking, parent role classification, real-sample assertion.
- `test_parser_enhanced_events.py` — 40 tests; all 10 dispatch-table pairs, 2 silent-skip pairs, timestamp handling, directory-entity decision.
- `test_parser_summary_fallbacks.py` — 29 tests; dedup-against-enhanced invariant with pre-registered entities, catch-all keys ignored.
- `test_parser_network_events.py` — 32 tests; sandbox IP filtering (parametrized), beaconing preservation (no event-level dedup), IPv6.
- `test_parser_dns_events.py` — 32 tests; suricata/network union semantics, query vs answer filtering, sandbox probe filter.
- `test_parser.py` — 30 tests; PDF-named end-to-end deliverable. Corrupt JSON, missing fields, non-UTF-8, empty, oversize, public API contract.

### Batch infrastructure (`scripts/`)

- `run_parser_batch.py` — single-process serial batch runner. Iterates `data/raw/*.json`, writes per-sample JSONLs to `data/processed/`, appends to aggregate `manifest.jsonl`, joins labels from `data/raw/labels.csv`. Continue-on-error semantics; `--resume` support. 4k corpus: ~268s (~15 samples/s).
- `build_sprint1_stats.py` — reads `manifest.jsonl`, writes `experiments/sprint1_stats.md`. Deterministic re-run on any corpus change.

### Documentation (`docs/`)

- `schema.md` — the source-of-truth event schema. 13 event types with CAPE source mapping (primary + fallback), canonicalization rules, sandbox-artifact filters, worked examples from real samples, unhandled/out-of-scope catalogs.

### Reports / evidence

- `experiments/sprint1_stats.md` — corpus-level stats: headline numbers, per-event-type distribution, per-entity-type distribution, Emotet vs Trickbot family comparison, filter effectiveness, unhandled shapes catalog, parse failures (empty).
- `data/processed/manifest.jsonl` (2.75 MB, committed) — 4,000 per-sample manifest rows.

## What changed from the PDF

All semantic changes are documented inline in `docs/schema.md`. Summary:

- **Labels moved out of events.** PDF schema put `label` on every event; we keep labels at the sample level (`manifest.jsonl`), not per-event. Rationale: avoids 2M redundant strings and reduces accidental label leakage into feature code. Sample-level is sufficient for Sprint 3.
- **Added 7 event types beyond the PDF's 6.** The PDF's original 6 (`process_spawn`, `file_write/read`, `reg_write`, `net_connect`, `dns_query`) cover less than CAPE actually emits. Added: `process_terminate` (reserved, not emitted in v1), `file_delete`, `file_copy`, `file_move`, `reg_read`, `reg_delete`, `module_load`. All have real CAPE sources and carry distinct signal.
- **Added `ord` and `timestamp_source` fields.** Discovered during Sprint 0 inspection that `info.started` disagrees with `behavior.*` timestamps by weeks/months in many samples (systematic rerun/caching artifact in Avast-CTU). Min-event-timestamp used as the per-report anchor instead. `ord` is always present; `timestamp` may be null. `timestamp_source` disambiguates absolute/relative/inferred/none.
- **9 entity types, not the PDF's 4.** Added `DIRECTORY`, `IP`, `NAMED_PIPE`, `MODULE`, `EXTERNAL` beyond the PDF's `process`/`file`/`domain`/`registry_key`. Separation matters: a file named `example.com` and a domain `example.com` are distinct entities; named pipes are not files.
- **`create,dir` enhanced events emit `FILE_WRITE` with a DIRECTORY entity.** Event type reflects the action (a write happened); entity type reflects the thing (a directory). Same canonical path as dir vs file produces distinct entity IDs.
- **Summary-fallback attribution.** `behavior.summary.*` has no actor — we attribute to root process with `metadata.attribution='summary_fallback'` so Sprint 3 can filter.
- **`behavior.summary.files` and `behavior.summary.keys` (catch-all variants) deliberately ignored.** No operation semantics = no meaningful event. Entity registration without events would pollute Sprint 2's graph.
- **Network/DNS sandbox filters.** RFC1918/loopback/link-local/multicast IPs dropped from `net_connect`; CAPE probe domain (`google-public-dns-a.google.com`) dropped from `dns_query`. Filter counts surface in manifest. Drops across 4k corpus: 3.08M private IPs, 419 DNS probes — without these filters, sandbox noise would dominate every sample.

## Key decisions to carry into Sprint 2

- **Entity IDs are sha1-based, 12 hex chars, stable across re-parses.** Content-hashed for files/domains/keys/IPs/pipes/modules; sample-scoped for processes. Sprint 2's graph builder gets a guarantee that re-running the parser produces byte-identical output. This is the correctness invariant Sprint 0 flagged.
- **Schema version is 1.0.** `data/processed/_schema_version.txt` records this. Any Sprint 2 work that consumes `data/processed/*.jsonl` should check this and re-run the parser if it mismatches.
- **Events are sorted by `(timestamp_or_inf, ord)`.** Timed events first in chronological order; untimed events (processtree, summary-fallback) at the end with their relative ord preserved. Sprint 2's graph builder can assume this total order.
- **Filters are aggressive by design.** 3.08M RFC1918 connections dropped across the corpus. Sprint 2 graphs will NOT have edges to sandbox infrastructure. If Sprint 3 ever wants them back, they'd need a second parse pass with filtering disabled.

## Risks updated for Sprint 2

### Risk #1 (size asymmetry) — PARTIALLY REFUTED, newly reframed

Sprint 0 flagged "Trickbot ~500 KB, Emotet ~4 MB — features scaling with raw size will correlate with label spuriously." After running on the full corpus, the picture is different and frankly better:

| Event type | Emotet mean | Trickbot mean | Ratio |
|---|---:|---:|---:|
| `reg_read` | 233 | 399 | 0.6x (Trickbot higher) |
| `file_read` | 14 | 54 | 0.3x (Trickbot higher) |
| `net_connect` | 1.5 | 5.1 | 0.3x (Trickbot higher) |
| `reg_write` | 43 | 2 | 22.9x (Emotet higher) |
| `dns_query` | 0 | 0.9 | Trickbot-only |
| `reg_delete` | 0 | 0.1 | Trickbot-only |
| `file_copy` | 0 | 1.3 | Trickbot-only |

The families have **qualitatively different** behavioral profiles, not just scale differences. Emotet writes lots of registry; Trickbot reads lots of files, talks to the network, and queries DNS. This is strong behavioral signal for classification.

**Reframing:** the original size-asymmetry concern was about raw-count leakage. That remains true — Sprint 3 should still add rate-based features alongside raw counts as belt-and-suspenders. But panic level is lower than Sprint 0 set.

### Risk #2 (behavior.enhanced can be 300+ entries) — MITIGATED

Largest observed list at full-corpus scale had 10,000+ entries (synthetic oversize test passes; real 179 MB sample parsed in ~4s). Stdlib `json.load` stayed well-behaved; `orjson` not needed. Parser iterates; no memory pressure.

### Risk #3 (stable IDs as correctness invariant) — LOCKED IN

Determinism tests in `test_entities.py` and `test_parser_*.py` verify byte-identical re-runs. If Sprint 2 finds a counterexample, that's a regression, not a surprise.

### Risk #4 (partial timing data) — HANDLED BY DESIGN

`ord` is always present; `timestamp` may be null. Parser uses min-event-timestamp as the anchor, never `info.started` (unreliable in this corpus — disagreed by weeks/months in inspected samples).

## New risks flagged for Sprint 2

### R2-1: Size asymmetry persists despite qualitative differences

Emotet samples still have dramatically higher total event counts (reg_write alone averages 43 vs 2). A graph classifier that uses raw node/edge counts as features will pick this up, which is fine *if* the model is allowed to use "sample is registry-heavy" as signal. If we want label-invariant size handling, Sprint 2 should think about rate-normalization during graph feature extraction.

### R2-2: 4 unhandled enhanced shapes

From `experiments/sprint1_stats.md`:

| event | object | count | samples |
|---|---|---:|---:|
| `findwindow` | `windowname` | 1,719 | 628 |
| `delete` | `service` | 168 | 7 |
| `start` | `service` | 83 | 83 |
| `delete` | `dir` | 2 | 2 |

None are blockers for Sprint 2 graph construction, but Sprint 3 feature engineering should consider adding them:

- `findwindow/windowname` — anti-analysis / UI fingerprinting. 628 samples (~16%) have this.
- `start/service` + `delete/service` — service manipulation (persistence).
- `delete/dir` — symmetric gap with `create,dir` that we already handle. Cheap to add.

### R2-3: Graph construction will expose any canonicalization bugs we haven't seen

We have 244 tests but the c1f6f86 real-sample assertions only cover one sample. If canonicalization collapses two files that should be distinct (false dedup) or fails to collapse two file paths that should be equal (missed dedup), Sprint 2 graphs will look wrong before we diagnose it at the parser level. Loop back to Sprint 1 immediately if Sprint 2 visualization shows unexpected entity counts.

### R2-4: summary_fallback events have no actor information

Attributed to root process per design. If Sprint 2 discovers it needs per-process attribution for summary entries (probably won't, but possible for multi-process samples), the fix is in Sprint 3 when we touch `behavior.processes[].calls`. Do not try to fix it in Sprint 2.

### R2-5: Schema.md worked examples used older sample-inspection numbers

During Sprint 1 we discovered `c1f6f86` has 184 sandbox connections (not 183 as initially stated), and UDP activity we didn't enumerate. `docs/schema.md` still says 183 in one example. Not a correctness issue but a doc accuracy one. Update in passing whenever we next touch that file.

## Sprint 1 → Sprint 2 handoff

Sprint 2 can assume:

- `data/processed/<sha256>.jsonl` exists for all 4,000 samples
- Each line is a schema-v1.0-conformant Event
- Events are pre-sorted by `(timestamp, ord)`
- Entity IDs are stable — same sample parsed twice → same IDs
- Sample-level labels live in `data/processed/manifest.jsonl`
- Sandbox noise (RFC1918 IPs, CAPE DNS probe) is already filtered out
- Enhanced and summary have been dedup'd

Sprint 2 should NOT:

- Re-canonicalize paths or domains (the IDs are the canonical form)
- Re-filter sandbox IPs (they're already gone)
- Assume every event has a timestamp (many don't — use `ord` for ordering)
- Treat `src` and `dst` as interchangeable (asymmetric: src = actor, dst = target)
