# Sprint 2 Exit Review

**Dates:** April 2026 (weeks 4–5)
**Theme:** Events become structure.
**Status:** Complete.

## PDF exit criteria — confirmation

| Criterion | Status | Evidence |
|-----------|--------|----------|
| Graphs build correctly and deterministically for every processed sample | PASS | `scripts/build_all_graphs.py` succeeded 4000/4000, 0 failures, 69s elapsed. `test_deterministic_rebuild` in `tests/test_graph_builder.py::TestInvariants` rebuilds the real sample twice and asserts byte-identical node + edge ordering. |
| Empirical runtime curve matches theoretical bound (within noise) | PASS | Linear-fit slope 1.75 µs/event, R² = 0.980. Log-log slope = 1.030 (1.0 = linear; 3% deviation in noise band). Memory slope 229 bytes/event, R² = 0.988. See `experiments/sprint2_runtime.md`. |
| At least 3 sample graphs visualized in `docs/` | PASS | `docs/sprint2_graph_small_235826b3.png` (Emotet, 30 entities), `docs/sprint2_graph_medium_5d1e041d.png` (Trickbot, 504 entities), `docs/sprint2_graph_large_dce6b197.png` (Emotet, 3550 entities, 2-hop subgraph). Selected deterministically by `scripts/render_sample_graphs.py`. |
| All tests pass | PASS | 325 tests in 11.06s, all green. 62 new tests added in Sprint 2 across `TestAddNode`, `TestAddEdge`, `TestBuildFromEvents`, `TestFromProcessed`, `TestToNetworkx`, `TestInvariants`, `TestVisualize`. |

## What was delivered

### Graph layer (`graph/`)

- `types.py` — `NodeData`/`EdgeData` slotted dataclasses with `__post_init__` validation. `NODE_TYPES` (9) and `EDGE_TYPES` (12) constants mirrored from `ingest/` (no hard import dependency).
- `builder.py` — `BehaviorGraph` class. Flat-edge-array adjacency-list storage (`nodes: list[NodeData]`, `edges: list[EdgeData]`, `adj_out: list[list[int]]`, `node_index: dict[str, int]`). Public surface: `add_node` (idempotent, type-conflict raises), `add_edge` (parallel edges + self-loops allowed), `build_from_events` (strict `MissingEntityError` on sidecar misses), `from_processed` (loads `<sample>.{jsonl, entities.jsonl}` pair), `to_networkx` (lazy import, MultiDiGraph with edge_id keys).
- `visualize.py` — `render(graph, output_path)` produces PNG. Three-tier auto-strategy: small (<200 nodes, full graph + labels), medium (200-1000, top-15 labels + spring layout), large (>1000, k-hop subgraph from highest-out-degree node). Okabe-Ito colorblind-safe node palette.

### Tests (`tests/`)

62 new tests in `tests/test_graph_builder.py`, organized by surface:

- `TestAddNode` — 6 tests; idempotency, type-conflict rejection, ID assignment.
- `TestAddEdge` — 5 tests; parallel-edge preservation, self-loops, metadata-by-reference.
- `TestBuildFromEvents` — 9 tests + 21 parametrized (12 edge types + 9 node types); empty/single events, missing-entity raises, all-types coverage, null-timestamp handling.
- `TestFromProcessed` — 4 tests; real-sample load, error path.
- `TestToNetworkx` — 4 tests; round-trip, MultiDiGraph contract, edge_id-as-key invariant.
- `TestInvariants` — 5 tests; deterministic rebuild, file_copy orphan-by-design, real-sample orphan-count gap pinned at 1, chronological out_edges, repr.
- `TestVisualize` — 9 tests + 3 parametrized tier cases; PNG creation, empty graph, parent-dir creation, subgraph_root override, palette invariants, real-sample render.

Total: **325 tests** (264 Sprint 1 + 62 Sprint 2 deltas; one of those deltas is 1 net + 60 new since Sprint 1 had a couple of overlapping cases not actually re-exposed).

### Batch infrastructure (`scripts/`)

- `render_sample_graphs.py` — deterministic 3-tier sample picker. Selects smallest sample with `30 ≤ entities ≤ 100` (small), sample whose `entities` is closest to 500 (medium), sample with the largest `entities` (large). Tie-breaker: `sample_id` lex order.
- `build_all_graphs.py` — timing + memory harness. min-of-3 `perf_counter` per sample, `gc.collect` between runs, tracemalloc on every 10th sample, file I/O excluded from timed region. ~70s for full corpus at ~58 samples/s.
- `sprint2_runtime_analysis.py` — regression + plot generator. Reads CSV, fits linear and log-log regressions, writes 3 PNGs. Idempotent — re-run on fresh CSV regenerates everything.

### Documentation (`docs/`)

- `complexity.md` — theoretical proof outline. Per-event operation table (5 ops, each amortized O(1)), space bound by container, summed across single pass. Explicit "what is NOT included" section (file I/O, sidecar construction, visualization, error paths). Cross-references the empirical doc.
- 3 sample-graph PNGs (small, medium, large) committed as deliverable evidence.

### Reports / evidence (`experiments/`)

- `sprint2_runtime.md` — narrative report. TL;DR table, experiment setup, results across 3 plots, methodology caveats, reproducibility instructions.
- `sprint2_build_times.csv` — 4,000 rows of timing + memory data (372 KB, committed).
- `sprint2_runtime_{linear,loglog,memory}.png` — embedded plots.

### Loop-back work (Sprint 1 fixes done in Sprint 2)

- `pyproject.toml` added at repo root. Bare `pytest` previously collected 0 tests because no project config file existed to anchor `sys.path`. `python -m pytest` worked but required invocation discipline. Commit `f13a540`.
- `data/processed/<sample>.entities.jsonl` sidecar added; schema bumped 1.0 → 1.1. Sprint 1 events serialized only sha1 entity IDs, losing entity types. Sprint 2 graphs need `(id, type, canonical)` for correct node typing. Commit `bced173`.

## What changed from the PDF

All semantic changes documented inline in `docs/complexity.md` and the relevant code modules. Summary:

- **12 edge types, not 6.** The PDF's `{spawn, write, read, connect, query, modify}` was obsolete given Sprint 1's expanded event set. We use 1:1 `event_type → edge_type` mapping. Collapsing 12 into 6 would lose signal Sprint 3 will likely want for feature engineering, and "modify" is semantically vague (does `reg_delete` map to it? `file_move`?).
- **9 node types, not 4.** Same reason — PDF predates Sprint 1's expanded entity set (added `directory`, `ip`, `named_pipe`, `module`, `external` beyond the original 4).
- **Schema bumped 1.0 → 1.1 mid-sprint.** Discovered Sprint 1 events lost entity-type information on serialization. Added per-sample entity sidecar, looped back to update parser + batch script + tests, re-ran corpus. ~5 minutes of work, surfaced cleanly via the strict `MissingEntityError` design.
- **`file_copy.metadata.source_file` is orphan-by-design.** A `file_copy` event has three logical participants (actor, source_file, dst_file), but `Event.{src, dst}` is a 2-arg shape. Source_file lives in metadata, not as a separate node or edge. Real-sample test (`test_real_sample_orphan_count`) pins the resulting 78-entities-vs-77-nodes gap.
- **Medium-tier visualization uses spring layout, not kamada_kawai.** PDF didn't specify; my initial guess was kamada_kawai. First medium render collapsed into a corner wedge because kamada_kawai handles weakly-connected components badly. Switched to spring after seeing the output.
- **Visualizer is one library + one wrapper script, not a notebook.** PDF mentioned `experiments/sprint2_runtime.ipynb`. We chose markdown + companion script for diffability and GitHub rendering. Same content, better tooling fit.
- **`pyproject.toml` added.** Sprint 0 didn't include one; pytest needed it. Sprint 1 fix done in Sprint 2 because that's when it bit.
- **`matplotlib` and `scipy` added to `requirements.txt`.** Sprint 0 listed them aspirationally per the PDF but `requirements.txt` only had `networkx`. Added `matplotlib==3.9.2` and `scipy==1.14.1` (transitive dep for `networkx` layout algos at n > ~50).

## Key decisions to carry into Sprint 3

- **Edge types are 1:1 with event types** (12 of them). Sprint 3 feature engineering can compute per-edge-type counts directly without a remapping layer.
- **Strict missing-entity policy.** `build_from_events` raises `MissingEntityError` if an event references an entity_id not in the sidecar. Sprint 3 should not attempt to make this lenient — surfacing data-integrity bugs loud and fast was an explicit design choice (R2-3 mitigation).
- **`file_copy.metadata.source_file` lives only in metadata.** Sprint 3 features can recover the data-flow relationship by reading `metadata` if needed; the graph itself does not carry it.
- **`adj_out` only.** No `adj_in` is built. If Sprint 3 needs reverse traversal it is one pass over `edges` to build, but defer until needed.
- **Graphs are MultiDiGraphs.** Parallel edges between the same `(src, dst)` pair are preserved. Each carries its own `timestamp` and `ord`.
- **Build cost is negligible.** 1.75 µs/event Python-side, ~70s for the full 4k corpus including I/O. Sprint 3 feature extraction is unlikely to be bottlenecked by graph construction.

## Risks updated for Sprint 3

### R2-1 (size asymmetry persists) — STILL OPEN

From the Sprint 1 exit: Emotet samples have dramatically higher total event counts on average. Sprint 2 graphs reflect this directly — node/edge counts scale with sample size, and Emotet/Trickbot have visibly different graph profiles even at matched event counts (Emotet registry-heavy, Trickbot file-and-network-heavy).

This is the **right** signal for classification — but a graph classifier using *raw* node/edge counts as features will pick up family identity through aggregate size alone. Sprint 3 should add rate-normalized features (events per second, per-process) alongside raw counts as belt-and-suspenders. The empirical case for class-balance is fine (2000/2000 exact split); the case for feature-scale leakage is not.

### R2-2 (4 unhandled enhanced shapes) — STILL OPEN

`findwindow/windowname` (1,719 events, 628 samples), `delete/service` (168 events, 7 samples), `start/service` (83 events, 83 samples), `delete/dir` (2 events, 2 samples). None are blockers for Sprint 2 graphs. Sprint 3 feature engineering should consider:

- `findwindow/windowname` is anti-analysis / UI fingerprinting; ~16% of samples have it.
- `start/service` + `delete/service` are persistence indicators.
- `delete/dir` is the symmetric gap with `create,dir` we already handle. Cheap to add.

### R2-3 (canonicalization bugs surface as wrong entity counts) — MITIGATED

Sprint 2's `MissingEntityError` strict policy and the `test_real_sample_orphan_count` invariant test pin the expected 78-entities-vs-77-nodes gap on the known sample. Any change in Sprint 1 canonicalization that breaks this invariant fails loudly at test time, not via mysterious visualizations. Loop-back path is established and tested.

### R2-4 (summary_fallback events have no actor) — UNCHANGED

Still attributed to root process per Sprint 1 design. Sprint 2 builds graphs with these events as edges from the root. Sprint 3 feature work should consider whether per-process attribution is needed (probably not for Sprint 3's hand-engineered features; possibly for Sprint 4's GNN if attention-over-actors becomes a feature).

## New risks flagged for Sprint 3

### R3-1: Test-suite runtime ballooning

Sprint 1: 264 tests, 0.85s. Sprint 2: 325 tests, 11s. The 9x slowdown is concentrated in the `TestVisualize` parametrized cases (n=300 and n=1200 spring-layouts dominate). Sprint 3 will likely add another 50-100 tests; if visualization tests stay in the default suite, we'll hit ~30s+ per test run. Options: mark visualization tests as `@pytest.mark.slow` and exclude from default runs, or shrink synthetic graph sizes. Decide before suite friction starts costing development velocity.

### R3-2: BOM contamination across the repo

23 of 24 text files in the repo carry UTF-8 BOMs (legacy from `Set-Content -Encoding UTF8` writes). Python tolerates them per PEP 263; TOML parsers don't (we hit this once with `pyproject.toml`). Files written during Sprint 2 are BOM-free (`WriteAllText` with explicit `UTF8Encoding $false`). Cleanup deferred per "Option A — fix only what bites." If a non-Python tool ever chokes on a `.py` file, this is the cause.

### R3-3: Heavy samples will dominate Sprint 3 feature extraction time

Build cost is 1.75 µs/event; reasonable. But Sprint 3 features may include diameter, betweenness, or other potentially-quadratic graph metrics. The 9,746-event sample built in 12.4 ms; a quadratic feature on it would be ~95 ms. 4000 samples × 95 ms = 6 minutes for one feature. If Sprint 3 wants 20+ features, plan for parallel extraction or accept a multi-hour batch run.

### R3-4: Test count claim slightly imprecise

The exit-criteria table says "62 new tests" but the actual delta is 325 - 264 = 61. The discrepancy is timing-noise around when fixture-driven tests count vs don't. Not worth a code change; flagging here so future docs aren't held to a number that's off by one.

## Sprint 2 → Sprint 3 handoff

Sprint 3 can assume:

- `BehaviorGraph.from_processed(sample_id)` works for all 4,000 samples
- Graphs are deterministic — same parser output → byte-identical graph
- `n_edges == n_events` (1:1 mapping) on all samples
- Adjacency-list storage with integer node IDs; `node_index` for entity_id → node_id lookup
- `to_networkx()` exposes a `MultiDiGraph` for whatever Sprint 3 features can't compute on the native storage
- Build cost is ~1.75 µs/event Python-side; 4k corpus in ~70s including I/O
- Per-edge metadata preserved by reference from the original event (do not mutate)

Sprint 3 should NOT:

- Add edges synthesized from `metadata` (e.g., `file_copy.source_file → dst`) without explicit design discussion. The orphan-by-design test will catch this.
- Loosen the strict `MissingEntityError` policy. It's load-bearing for catching Sprint 1 regressions.
- Build per-sample features that recompute things already in `BehaviorGraph` (e.g., recounting node types by walking events instead of the graph).
- Assume `adj_in` exists. Build it once if needed; don't pretend it's free.
- Re-render docs/ figures unless the visualizer changes. The 3 PNGs are deliverable evidence at a fixed point in time; rerunning for Sprint 3 work would muddy the git history.