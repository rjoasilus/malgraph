# BehaviorGraph Construction Complexity

**Claim.** `BehaviorGraph.build_from_events(events, entities)` runs in
**O(|E|) time** and **O(|V| + |E|) space**, where `|E| = len(events)` is
the number of edges produced and `|V|` is the number of distinct entities
referenced as `src` or `dst` across those events.

This document is the theoretical half of Sprint 2's algorithmic spine.
The empirical validation lives in
[`experiments/sprint2_runtime.md`](../experiments/sprint2_runtime.md).
The two are independent: the proof establishes the asymptotic bound; the
empirical curve confirms the constant factor is well-behaved on real data.

## Algorithm under analysis

From `graph/builder.py`:

```python
@classmethod
def build_from_events(cls, events, entities, sample_id=""):
    g = cls(sample_id=sample_id)
    for event in events:
        src_id = event["src"]
        dst_id = event["dst"]
        src_meta = entities.get(src_id)
        dst_meta = entities.get(dst_id)
        if src_meta is None: raise MissingEntityError(...)
        if dst_meta is None: raise MissingEntityError(...)
        src_node = g.add_node(src_id, src_meta["type"])
        dst_node = g.add_node(dst_id, dst_meta["type"])
        g.add_edge(src_node, dst_node, event["event_type"],
                   event.get("timestamp"), event["ord"],
                   event.get("metadata", {}))
    return g
```

A single pass over `events`. Per iteration: two entity-dict lookups, two
`add_node` calls, one `add_edge` call. Errors short-circuit the loop with
a single comparison.

## Storage layout

```python
nodes:      list[NodeData]     # node_id -> NodeData
edges:      list[EdgeData]     # edge_id -> EdgeData
adj_out:    list[list[int]]    # node_id -> [edge_id, ...]
node_index: dict[str, int]     # entity_id -> node_id
```

Each event becomes exactly one `EdgeData` and at most two new
`NodeData`s. `node_index` is the only structure used for lookup; the
rest are append-only flat arrays.

## Assumptions

1. **Hash uniformity.** `entities` and `node_index` are Python dicts.
   Lookup, insertion, and membership tests are amortized O(1) under the
   standard hash-uniformity assumption. Keys are sha1-derived 12-character
   hex strings; collisions across 1.26M entities (corpus-wide) are well
   below the birthday bound for 48-bit hashes (≈16.7M).
2. **Pre-sorted input.** Sprint 1 guarantees events arrive in
   `(timestamp_or_inf, ord)` order. The builder does not sort; it
   preserves input order in `adj_out`. No O(|E| log |E|) work.
3. **Amortized list/dict growth.** Python `list.append` and `dict[k] = v`
   are amortized O(1) under doubling reallocation. The standard
   amortization argument applies.

## Time complexity

We bound the work per event, then multiply by `|E|`.

### Per-event work

For each `event` in `events`:

| Operation | Cost | Justification |
|---|---|---|
| `entities.get(src_id)` | O(1) amortized | dict lookup |
| `entities.get(dst_id)` | O(1) amortized | dict lookup |
| `add_node(src_id, ...)` | O(1) amortized | see below |
| `add_node(dst_id, ...)` | O(1) amortized | see below |
| `add_edge(...)` | O(1) amortized | see below |

#### `add_node(entity_id, node_type)`

```python
existing = self.node_index.get(entity_id)        # O(1)
if existing is not None:
    if self.nodes[existing].node_type != node_type:  # O(1)
        raise ValueError(...)
    return existing
node_id = len(self.nodes)                        # O(1)
self.nodes.append(NodeData(...))                 # O(1) amortized
self.adj_out.append([])                          # O(1) amortized
self.node_index[entity_id] = node_id             # O(1) amortized
return node_id
```

Every line is O(1) amortized. The idempotency check exits early on a
revisit and does no extra work.

#### `add_edge(src, dst, edge_type, timestamp, ord_, metadata)`

```python
edge_id = len(self.edges)                        # O(1)
self.edges.append(EdgeData(...))                 # O(1) amortized
self.adj_out[src_node_id].append(edge_id)        # O(1) amortized
return edge_id
```

Three operations, each amortized O(1).

### Total work

Let `T(event)` be the work for one iteration. From the table above,
`T(event) = O(1)` amortized. Summing across all events:
T_total = Σ T(event) for event in events
= Σ O(1)
= O(|events|)
= O(|E|)

Since each event produces exactly one edge, `|events| = |E|`.

**Conclusion: `build_from_events` is O(|E|) time.**

## Space complexity

We bound the size of each container after construction.

| Structure | Size | Justification |
|---|---|---|
| `nodes` | O(|V|) | one entry per distinct entity_id |
| `edges` | O(|E|) | one entry per event |
| `adj_out` | O(|V| + |E|) | |V| outer lists, |E| ints distributed across them |
| `node_index` | O(|V|) | one entry per distinct entity_id |

Per-element overhead is a Python-level constant (slotted dataclass for
`NodeData`/`EdgeData`; integer for `adj_out` entries; string + int pair
for `node_index`). The asymptotic bound is independent of these
constants.

Total: `O(|V|) + O(|E|) + O(|V| + |E|) + O(|V|) = O(|V| + |E|)`.

Because each event contributes at most two distinct entities, `|V| ≤ 2|E|`,
so `O(|V| + |E|) ⊆ O(|E|)`. Stating the bound as `O(|V| + |E|)` is more
informative when the graph is sparse and dense subsets matter (e.g.,
when reasoning about peak memory on samples with high entity reuse).

**Conclusion: `build_from_events` uses O(|V| + |E|) space.**

## What is NOT included in this bound

These are deliberate exclusions, called out so the bound is honest about
its scope.

1. **File I/O.** `from_processed` opens two files and JSON-decodes each
   line. That cost scales with the number of bytes on disk, which is
   roughly proportional to `|E|` but is dominated by I/O constants
   irrelevant to the algorithm. The bound is for `build_from_events`,
   which takes already-deserialized inputs.

2. **Entity-sidecar construction.** Sprint 1's parser builds the
   `EntityRegistry` that produces the sidecar. That cost is bounded
   in Sprint 1's analysis, not here.

3. **Visualization.** `to_networkx` materializes a `MultiDiGraph` and
   is itself O(|V| + |E|), but it is a separate method invoked on
   demand. Layout and rendering (in `graph/visualize.py`) have their
   own costs (`spring_layout` is O(|V|² · iterations); `kamada_kawai`
   is O(|V|³)). These do not bound graph *construction*.

4. **`MissingEntityError` paths.** If raised, the function exits in
   O(k) work where `k` is the number of events processed before the
   miss. The bound `O(|E|)` is the upper bound on the success path;
   error paths are strictly cheaper.

## Cross-reference: empirical validation

The bound above is asymptotic and ignores the constant factor. The
empirical curve in
[`experiments/sprint2_runtime.md`](../experiments/sprint2_runtime.md)
measures that constant on the full corpus:

- **Linear-fit slope:** 1.75 µs per event (R² = 0.980 across 4,000 samples)
- **Log-log slope:** 1.030 (1.0 = linear)
- **Memory slope:** 229 bytes per event (R² = 0.988 across 400 samples)

The log-log slope of 1.030 is the direct empirical signature of `k = 1`
in `time = c · n^k`. The R² values establish that `n_events` alone
explains 98%+ of variance in both time and memory — there is no hidden
super-linear term.

Together, the proof and the empirical curve close the algorithmic-spine
case: the bound is correct, and the constant factor is well-behaved on
real data at real scale.

## See also

- [`graph/builder.py`](../graph/builder.py) — the implementation
- [`tests/test_graph_builder.py`](../tests/test_graph_builder.py) — invariant tests including `test_deterministic_rebuild`
- [`experiments/sprint2_runtime.md`](../experiments/sprint2_runtime.md) — empirical curve
- [`scripts/build_all_graphs.py`](../scripts/build_all_graphs.py) — timing harness