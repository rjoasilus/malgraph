"""
Tests for graph.builder.BehaviorGraph.

Organized into test classes by surface area:
  TestAddNode        - add_node primitive: idempotency, type-conflict
  TestAddEdge        - add_edge primitive: parallel edges, self-loops
  TestBuildFromEvents- bulk construction, missing-entity, type coverage
  TestFromProcessed  - on-disk loader against real Sprint 1 outputs
  TestToNetworkx     - MultiDiGraph round-trip
  TestInvariants     - determinism, orphan-by-design, edge case behavior

Sprint 1's parser tests (264) live alongside; this file adds Sprint 2's
graph-layer coverage on top.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from graph.builder import BehaviorGraph, MissingEntityError
from graph.types import EDGE_TYPES, NODE_TYPES


# --- helpers -----------------------------------------------------------------

def make_event(
    *, ord_: int, event_type: str, src: str, dst: str,
    timestamp: float | None = 0.0, metadata: dict | None = None,
    sample_id: str = "test_sample",
    timestamp_source: str = "absolute",
) -> dict:
    """Build an event dict matching schema v1.1 line shape."""
    return {
        "sample_id": sample_id,
        "ord": ord_,
        "event_type": event_type,
        "src": src,
        "dst": dst,
        "timestamp": timestamp,
        "timestamp_source": timestamp_source,
        "metadata": metadata or {},
    }


def make_entities(*pairs: tuple[str, str, str]) -> dict[str, dict]:
    """Build a sidecar dict from (entity_id, type, canonical) triples."""
    return {
        eid: {"id": eid, "type": etype, "canonical": canonical}
        for eid, etype, canonical in pairs
    }


# A "real-ish" minimal pair: one process spawns a file write.
PROC_A = ("proc_a_______", "process", "a.exe")
PROC_B = ("proc_b_______", "process", "b.exe")
FILE_X = ("file_x_______", "file", "c:/x.txt")


# --- TestAddNode -------------------------------------------------------------

class TestAddNode:
    def test_add_returns_int_node_id(self):
        g = BehaviorGraph()
        nid = g.add_node("e1__________", "process")
        assert isinstance(nid, int)
        assert nid == 0

    def test_add_is_idempotent_same_type(self):
        g = BehaviorGraph()
        n1 = g.add_node("e1__________", "process")
        n2 = g.add_node("e1__________", "process")
        assert n1 == n2
        assert g.n_nodes == 1

    def test_add_assigns_increasing_ids(self):
        g = BehaviorGraph()
        a = g.add_node("e1__________", "process")
        b = g.add_node("e2__________", "file")
        c = g.add_node("e3__________", "domain")
        assert (a, b, c) == (0, 1, 2)

    def test_add_rejects_type_conflict(self):
        g = BehaviorGraph()
        g.add_node("e1__________", "process")
        with pytest.raises(ValueError, match="reregistered"):
            g.add_node("e1__________", "file")

    def test_add_rejects_unknown_node_type(self):
        g = BehaviorGraph()
        with pytest.raises(ValueError, match="unknown node_type"):
            g.add_node("e1__________", "not_a_real_type")

    def test_adj_out_initialized_empty(self):
        g = BehaviorGraph()
        nid = g.add_node("e1__________", "process")
        assert g.adj_out[nid] == []


# --- TestAddEdge -------------------------------------------------------------

class TestAddEdge:
    def test_add_returns_int_edge_id(self):
        g = BehaviorGraph()
        s = g.add_node("e1__________", "process")
        d = g.add_node("e2__________", "file")
        eid = g.add_edge(s, d, "file_write", 1.0, 0, {})
        assert eid == 0

    def test_parallel_edges_preserved(self):
        """Two events same (src, dst, type) -> two distinct edges."""
        g = BehaviorGraph()
        s = g.add_node("e1__________", "process")
        d = g.add_node("e2__________", "registry_key")
        e1 = g.add_edge(s, d, "reg_read", 1.0, 0, {})
        e2 = g.add_edge(s, d, "reg_read", 2.0, 1, {})
        assert e1 != e2
        assert g.n_edges == 2
        assert g.adj_out[s] == [e1, e2]

    def test_self_loop_allowed(self):
        """A process modifying its own state is a real shape."""
        g = BehaviorGraph()
        n = g.add_node("e1__________", "process")
        eid = g.add_edge(n, n, "process_spawn", 0.0, 0, {})
        assert g.edges[eid].src == g.edges[eid].dst == n

    def test_rejects_unknown_edge_type(self):
        g = BehaviorGraph()
        s = g.add_node("e1__________", "process")
        d = g.add_node("e2__________", "file")
        with pytest.raises(ValueError, match="unknown edge_type"):
            g.add_edge(s, d, "not_a_real_edge", 0.0, 0, {})

    def test_metadata_stored_by_reference(self):
        """Metadata dict is the parser's; we shouldn't deep-copy."""
        g = BehaviorGraph()
        s = g.add_node("e1__________", "process")
        d = g.add_node("e2__________", "file")
        md = {"eid": 42}
        g.add_edge(s, d, "file_write", 0.0, 0, md)
        assert g.edges[0].metadata is md


# --- TestBuildFromEvents -----------------------------------------------------

class TestBuildFromEvents:
    def test_empty_events_produces_empty_graph(self):
        g = BehaviorGraph.build_from_events([], {})
        assert g.n_nodes == 0
        assert g.n_edges == 0

    def test_single_event(self):
        entities = make_entities(PROC_A, FILE_X)
        events = [make_event(
            ord_=0, event_type="file_write",
            src=PROC_A[0], dst=FILE_X[0],
        )]
        g = BehaviorGraph.build_from_events(events, entities)
        assert g.n_nodes == 2
        assert g.n_edges == 1
        assert g.edges[0].edge_type == "file_write"

    def test_missing_src_entity_raises(self):
        entities = make_entities(FILE_X)  # no PROC_A
        events = [make_event(
            ord_=0, event_type="file_write",
            src=PROC_A[0], dst=FILE_X[0],
        )]
        with pytest.raises(MissingEntityError, match="src="):
            BehaviorGraph.build_from_events(events, entities)

    def test_missing_dst_entity_raises(self):
        entities = make_entities(PROC_A)  # no FILE_X
        events = [make_event(
            ord_=0, event_type="file_write",
            src=PROC_A[0], dst=FILE_X[0],
        )]
        with pytest.raises(MissingEntityError, match="dst="):
            BehaviorGraph.build_from_events(events, entities)

    def test_node_type_taken_from_sidecar(self):
        """Even if event_type implies a kind, sidecar wins on entity type."""
        entities = make_entities(PROC_A, FILE_X)
        events = [make_event(
            ord_=0, event_type="file_write",
            src=PROC_A[0], dst=FILE_X[0],
        )]
        g = BehaviorGraph.build_from_events(events, entities)
        assert g.nodes[g.node_index[PROC_A[0]]].node_type == "process"
        assert g.nodes[g.node_index[FILE_X[0]]].node_type == "file"

    @pytest.mark.parametrize("event_type", sorted(EDGE_TYPES))
    def test_all_edge_types_accepted(self, event_type):
        entities = make_entities(PROC_A, FILE_X)
        events = [make_event(
            ord_=0, event_type=event_type,
            src=PROC_A[0], dst=FILE_X[0],
        )]
        g = BehaviorGraph.build_from_events(events, entities)
        assert g.edges[0].edge_type == event_type

    @pytest.mark.parametrize("node_type", sorted(NODE_TYPES))
    def test_all_node_types_accepted(self, node_type):
        entities = make_entities(
            PROC_A,
            ("ent_under_test", node_type, "canonical_name"),
        )
        events = [make_event(
            ord_=0, event_type="file_write",
            src=PROC_A[0], dst="ent_under_test",
        )]
        g = BehaviorGraph.build_from_events(events, entities)
        assert g.nodes[g.node_index["ent_under_test"]].node_type == node_type

    def test_null_timestamp_handled(self):
        """Events with timestamp_source='none' have timestamp=None."""
        entities = make_entities(PROC_A, FILE_X)
        events = [make_event(
            ord_=99, event_type="file_read",
            src=PROC_A[0], dst=FILE_X[0],
            timestamp=None, timestamp_source="none",
        )]
        g = BehaviorGraph.build_from_events(events, entities)
        assert g.edges[0].timestamp is None
        assert g.edges[0].ord == 99

    def test_metadata_preserved_on_edge(self):
        entities = make_entities(PROC_A, FILE_X)
        md = {"source_file": "abc123def456", "eid": 7}
        events = [make_event(
            ord_=0, event_type="file_copy",
            src=PROC_A[0], dst=FILE_X[0],
            metadata=md,
        )]
        g = BehaviorGraph.build_from_events(events, entities)
        assert g.edges[0].metadata == md


# --- TestFromProcessed -------------------------------------------------------

# The real sample we know exists from Sprint 1 backfill.
REAL_SAMPLE = "576352d72a627ae9cffc50abd3ca9930e92ec823724b4b5186a1dbd2df1d48a2"


@pytest.fixture(scope="module")
def real_graph() -> BehaviorGraph:
    if not Path(f"data/processed/{REAL_SAMPLE}.jsonl").is_file():
        pytest.skip("real sample not present; run scripts/run_parser_batch.py")
    return BehaviorGraph.from_processed(REAL_SAMPLE)


class TestFromProcessed:
    def test_loads_real_sample(self, real_graph):
        assert real_graph.sample_id == REAL_SAMPLE
        assert real_graph.n_edges == 110

    def test_node_types_subset_of_known(self, real_graph):
        seen = {n.node_type for n in real_graph.nodes}
        assert seen.issubset(NODE_TYPES)

    def test_edge_types_subset_of_known(self, real_graph):
        seen = {e.edge_type for e in real_graph.edges}
        assert seen.issubset(EDGE_TYPES)

    def test_missing_files_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            BehaviorGraph.from_processed("nonexistent_sample", tmp_path)


# --- TestToNetworkx ----------------------------------------------------------

class TestToNetworkx:
    def test_node_and_edge_counts_match(self, real_graph):
        nxg = real_graph.to_networkx()
        assert nxg.number_of_nodes() == real_graph.n_nodes
        assert nxg.number_of_edges() == real_graph.n_edges

    def test_is_multidigraph(self, real_graph):
        import networkx as nx
        nxg = real_graph.to_networkx()
        assert isinstance(nxg, nx.MultiDiGraph)

    def test_node_attrs_preserved(self, real_graph):
        nxg = real_graph.to_networkx()
        for nid, node in enumerate(real_graph.nodes):
            assert nxg.nodes[nid]["type"] == node.node_type
            assert nxg.nodes[nid]["entity_id"] == node.entity_id

    def test_edge_keys_are_edge_ids(self, real_graph):
        nxg = real_graph.to_networkx()
        # Every edge_id should appear exactly once as a key.
        seen_keys = set()
        for u, v, k in nxg.edges(keys=True):
            assert k not in seen_keys
            seen_keys.add(k)
        assert seen_keys == set(range(real_graph.n_edges))


# --- TestInvariants ----------------------------------------------------------

class TestInvariants:
    def test_deterministic_rebuild(self):
        """Same inputs -> byte-identical graph state."""
        g1 = BehaviorGraph.from_processed(REAL_SAMPLE)
        g2 = BehaviorGraph.from_processed(REAL_SAMPLE)
        assert g1.n_nodes == g2.n_nodes
        assert g1.n_edges == g2.n_edges
        # Node ordering identical (insertion-order under same input).
        assert [n.entity_id for n in g1.nodes] == [n.entity_id for n in g2.nodes]
        assert [n.node_type for n in g1.nodes] == [n.node_type for n in g2.nodes]
        # Edge ordering identical.
        e1 = [(e.src, e.dst, e.edge_type, e.ord) for e in g1.edges]
        e2 = [(e.src, e.dst, e.edge_type, e.ord) for e in g2.edges]
        assert e1 == e2

    def test_file_copy_source_file_is_metadata_only(self):
        """file_copy.metadata.source_file is NOT a node by design.

        This pins the orphan-by-design choice from the build pass: the
        third party in a 3-way file_copy relationship lives in metadata,
        not as a separate node. Sprint 3 features can recover it from
        metadata if needed.
        """
        # Construct a file_copy event whose source_file points to an
        # entity that is NOT in src or dst.
        entities = make_entities(
            PROC_A, FILE_X,
            ("source_file_id", "file", "c:/source.exe"),
        )
        events = [make_event(
            ord_=0, event_type="file_copy",
            src=PROC_A[0], dst=FILE_X[0],
            metadata={"source_file": "source_file_id"},
        )]
        g = BehaviorGraph.build_from_events(events, entities)
        # Only PROC_A and FILE_X are nodes; source_file_id is not.
        assert g.n_nodes == 2
        assert "source_file_id" not in g.node_index
        # But source_file_id is preserved in metadata.
        assert g.edges[0].metadata["source_file"] == "source_file_id"

    def test_real_sample_orphan_count(self, real_graph):
        """The known real sample has 78 sidecar entities, 77 graph nodes.

        The 1-entity gap is the file_copy source_file (the original
        sample exe, copied to a persistence location). Pinning this
        documents the design and catches accidental drift.
        """
        sidecar_path = Path(f"data/processed/{REAL_SAMPLE}.entities.jsonl")
        with sidecar_path.open(encoding="utf-8") as f:
            sidecar_count = sum(1 for line in f if line.strip())
        assert sidecar_count - real_graph.n_nodes == 1

    def test_out_edges_in_insertion_order(self):
        """Pre-sorted (timestamp, ord) input -> chronological adj_out."""
        entities = make_entities(PROC_A, FILE_X)
        events = [
            make_event(ord_=i, event_type="reg_read",
                       src=PROC_A[0], dst=FILE_X[0],
                       timestamp=float(i))
            for i in range(5)
        ]
        g = BehaviorGraph.build_from_events(events, entities)
        src = g.node_index[PROC_A[0]]
        out = g.out_edges(src)
        assert [e.ord for e in out] == [0, 1, 2, 3, 4]

    def test_repr_includes_counts(self):
        entities = make_entities(PROC_A, FILE_X)
        events = [make_event(
            ord_=0, event_type="file_write",
            src=PROC_A[0], dst=FILE_X[0],
        )]
        g = BehaviorGraph.build_from_events(
            events, entities, sample_id="abc",
        )
        r = repr(g)
        assert "abc" in r
        assert "nodes=2" in r
        assert "edges=1" in r