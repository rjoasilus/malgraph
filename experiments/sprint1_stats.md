# Sprint 1 — Corpus-level parser statistics

Generated from `data/processed/manifest.jsonl`.
Regenerate with `python scripts/build_sprint1_stats.py`.

## Headline

- **Samples:** 4,000
- **Parsed OK:** 4,000 (100.0%)
- **Parse failures:** 0
- **Total events emitted:** 1,808,351
- **Total entities registered:** 1,256,808

**Label breakdown:**

- `Emotet`: 2,000
- `Trickbot`: 2,000

## Event-type distribution (4k corpus)

Per-sample stats compute against non-zero samples only; the
`samples` column shows how many samples emitted ≥1 of that
event type. Sprint exit criterion: no event type is 0% or 100%
unless by design.

| event_type | total | % total | samples | mean | median | p95 | max |
|---|---:|---:|---:|---:|---:|---:|---:|
| `reg_read` | 1,264,095 | 69.9% | 4,000 | 316.0 | 111.0 | 1,443 | 6,088 |
| `module_load` | 264,960 | 14.7% | 3,994 | 66.3 | 44.0 | 216 | 9,744 |
| `file_read` | 134,555 | 7.4% | 3,562 | 37.8 | 14.0 | 204 | 439 |
| `reg_write` | 90,650 | 5.0% | 1,961 | 46.2 | 14.0 | 218 | 410 |
| `file_write` | 14,571 | 0.8% | 2,935 | 5.0 | 2.0 | 8 | 328 |
| `process_spawn` | 13,583 | 0.8% | 4,000 | 3.4 | 3.0 | 7 | 22 |
| `net_connect` | 13,070 | 0.7% | 2,084 | 6.3 | 2.0 | 12 | 1,029 |
| `file_delete` | 6,139 | 0.3% | 2,414 | 2.5 | 2.0 | 6 | 8 |
| `file_copy` | 2,516 | 0.1% | 1,226 | 2.1 | 2.0 | 2 | 60 |
| `file_move` | 2,084 | 0.1% | 2,003 | 1.0 | 1.0 | 1 | 59 |
| `dns_query` | 1,851 | 0.1% | 612 | 3.0 | 2.0 | 7 | 9 |
| `reg_delete` | 277 | 0.0% | 45 | 6.2 | 8.0 | 16 | 16 |

## Entity-type distribution (4k corpus)

| entity_type | total | samples | mean | median | p95 | max |
|---|---:|---:|---:|---:|---:|---:|
| `registry_key` | 1,031,199 | 4,000 | 257.8 | 96.0 | 1,037 | 3,001 |
| `module` | 98,480 | 3,994 | 24.7 | 21.0 | 73 | 107 |
| `file` | 97,023 | 3,678 | 26.4 | 18.0 | 114 | 414 |
| `process` | 13,583 | 4,000 | 3.4 | 3.0 | 7 | 22 |
| `directory` | 5,946 | 2,922 | 2.0 | 2.0 | 3 | 10 |
| `external` | 5,123 | 4,000 | 1.3 | 1.0 | 2 | 2 |
| `ip` | 2,964 | 2,084 | 1.4 | 1.0 | 4 | 7 |
| `domain` | 1,851 | 612 | 3.0 | 2.0 | 7 | 9 |
| `named_pipe` | 639 | 638 | 1.0 | 1.0 | 1 | 2 |

## Family comparison — Risk #1 investigation

Sprint 0 flagged that Trickbot samples are consistently smaller
(~500 KB) than Emotet samples (~4 MB). Features that scale with
raw size will correlate with label spuriously. The table below
is the empirical check — per-event-type mean per sample for each
family. Look for types where the ratio Emotet:Trickbot is far
from 1; those are the ones feature engineering must normalize.

| event_type | mean (Emotet) | mean (Trickbot) | Emotet:Trickbot |
|---|---:|---:|---:|
| `reg_read` | 233.4 | 398.6 | 0.6x |
| `module_load` | 58.0 | 74.5 | 0.8x |
| `file_read` | 13.7 | 53.6 | 0.3x |
| `reg_write` | 43.4 | 1.9 | 22.9x |
| `file_write` | 1.9 | 5.4 | 0.3x |
| `process_spawn` | 3.6 | 3.2 | 1.1x |
| `net_connect` | 1.5 | 5.1 | 0.3x |
| `file_delete` | 2.2 | 0.9 | 2.4x |
| `file_copy` | 0.0 | 1.3 | 0.0x |
| `file_move` | 0.9 | 0.2 | 4.8x |
| `dns_query` | 0.0 | 0.9 | 0.0x |
| `reg_delete` | 0.0 | 0.1 | 0.0x |

## Filter effectiveness

Counters for events and entries dropped at parse time (sandbox
infrastructure noise, malformed records, enhanced↔summary dedup).
Totals across the whole corpus.

| filter | total dropped |
|---|---:|
| `dns_dropped_malformed` | 0 |
| `dns_dropped_sandbox` | 419 |
| `net_connect_dropped_malformed` | 0 |
| `net_connect_dropped_private` | 3,076,291 |
| `summary_dedup_dropped` | 1,030,685 |
| `summary_malformed_dropped` | 23 |

## Unhandled `behavior.enhanced` shapes across 4k corpus

Every `(event, object)` pair seen in `behavior.enhanced` that is
not in our dispatch table. Empty table = parser's dispatch is
complete for this corpus. Non-empty entries are candidates for
Sprint 3 feature exploration.

| event | object | total count | samples |
|---|---|---:|---:|
| `findwindow` | `windowname` | 1,719 | 628 |
| `delete` | `service` | 168 | 7 |
| `start` | `service` | 83 | 83 |
| `delete` | `dir` | 2 | 2 |

## Parse failures

_None. All samples parsed cleanly._
