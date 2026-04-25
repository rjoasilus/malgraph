"""
BehaviorGraph: a typed, directed multigraph built from a Sprint 1 event
stream.

Storage model: flat edge array + per-node out-edge lists.

    nodes:      list[NodeData]              indexed by node_id (int)
    edges:      list[EdgeData]              indexed by edge_id (int)
    adj_out:    list[list[int]]             node_id -> [edge_id, ...]
    node_index: dict[str, int]              entity_id -> node_id

Each event becomes exactly one edge. Parallel edges between the same
(src, dst) pair are preserved -- they carry distinct timestamps/ord
values and Sprint 3 features may want them. The PDF's BehaviorGraph
contract (add_node, add_edge, build_from_events, to_networkx) is
satisfied; storage is adjacency-list with consistent integer ID
mapping per the PDF's Sprint 2 design.

Build cost is O(|E|) time, O(|V| + |E|) space; see docs/complexity.md.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Optional

from graph.types import EdgeData, EDGE_TYPES, NODE_TYPES, NodeData


class MissingEntityError(KeyError):
    """An event referenced an entity_id absent from the sidecar.

    Raised by build_from_events when src or dst points to an unregistered
    entity. Loud-by-design: indicates a parser/sidecar mismatch (R2-3 in
    the Sprint 1 exit doc), not a recoverable edge case.
    """


class BehaviorGraph:
    """Directed multigraph of one sample's behavior.

    Construct via build_from_events(events, entities) for the pure path,
    or BehaviorGraph.from_processed(sample_id, processed_dir) to load
    the parser sidecars from disk.
    """

    __slots__ = ("sample_id", "nodes", "edges", "adj_out", "node_index")

    def __init__(self, sample_id: str = "") -> None:
        self.sample_id: str = sample_id
        self.nodes: list[NodeData] = []
        self.edges: list[EdgeData] = []
        self.adj_out: list[list[int]] = []
        self.node_index: dict[str, int] = {}

    # --- construction primitives -------------------------------------------

    def add_node(self, entity_id: str, node_type: str) -> int:
        """Idempotent: returns existing node_id if entity_id is known.

        First-write-wins on node_type. A subsequent add_node with the
        same entity_id but different node_type indicates registry
        corruption and is rejected.
        """
        existing = self.node_index.get(entity_id)
        if existing is not None:
            if self.nodes[existing].node_type != node_type:
                raise ValueError(
                    f"entity_id {entity_id!r} reregistered with "
                    f"{node_type!r}; previously {self.nodes[existing].node_type!r}"
                )
            return existing
        node_id = len(self.nodes)
        self.nodes.append(NodeData(entity_id=entity_id, node_type=node_type))
        self.adj_out.append([])
        self.node_index[entity_id] = node_id
        return node_id

    def add_edge(
        self,
        src_node_id: int,
        dst_node_id: int,
        edge_type: str,
        timestamp: Optional[float],
        ord_: int,
        metadata: dict,
    ) -> int:
        """Append an edge. No dedup: parallel edges are preserved."""
        edge_id = len(self.edges)
        self.edges.append(EdgeData(
            src=src_node_id,
            dst=dst_node_id,
            edge_type=edge_type,
            timestamp=timestamp,
            ord=ord_,
            metadata=metadata,
        ))
        self.adj_out[src_node_id].append(edge_id)
        return edge_id

    # --- bulk build --------------------------------------------------------

    @classmethod
    def build_from_events(
        cls,
        events: Iterable[dict],
        entities: dict[str, dict[str, Any]],
        sample_id: str = "",
    ) -> "BehaviorGraph":
        """Construct a graph from a Sprint 1 event stream + entity sidecar.

        events: iterable of dicts with keys sample_id, ord, event_type,
                src, dst, timestamp, timestamp_source, metadata.
                Pre-sorted by (timestamp_or_inf, ord) per Sprint 1 contract.
        entities: dict mapping entity_id -> {type, canonical, ...} -- the
                deserialized sidecar.

        Raises MissingEntityError if any event references an entity_id
        not present in `entities`.
        """
        g = cls(sample_id=sample_id)
        for event in events:
            src_id = event["src"]
            dst_id = event["dst"]
            src_meta = entities.get(src_id)
            dst_meta = entities.get(dst_id)
            if src_meta is None:
                raise MissingEntityError(
                    f"event ord={event.get('ord')} src={src_id!r} "
                    f"not in entity sidecar"
                )
            if dst_meta is None:
                raise MissingEntityError(
                    f"event ord={event.get('ord')} dst={dst_id!r} "
                    f"not in entity sidecar"
                )
            src_node = g.add_node(src_id, src_meta["type"])
            dst_node = g.add_node(dst_id, dst_meta["type"])
            g.add_edge(
                src_node_id=src_node,
                dst_node_id=dst_node,
                edge_type=event["event_type"],
                timestamp=event.get("timestamp"),
                ord_=event["ord"],
                metadata=event.get("metadata", {}),
            )
        return g

    @classmethod
    def from_processed(
        cls, sample_id: str, processed_dir: Path | str = "data/processed",
    ) -> "BehaviorGraph":
        """Load a graph from <processed_dir>/<sample_id>.{jsonl,entities.jsonl}."""
        processed_dir = Path(processed_dir)
        events_path = processed_dir / f"{sample_id}.jsonl"
        entities_path = processed_dir / f"{sample_id}.entities.jsonl"
        with events_path.open("r", encoding="utf-8") as f:
            events = [json.loads(line) for line in f if line.strip()]
        with entities_path.open("r", encoding="utf-8") as f:
            entities = {
                row["id"]: row
                for row in (json.loads(line) for line in f if line.strip())
            }
        return cls.build_from_events(events, entities, sample_id=sample_id)

    # --- read API ----------------------------------------------------------

    @property
    def n_nodes(self) -> int:
        return len(self.nodes)

    @property
    def n_edges(self) -> int:
        return len(self.edges)

    def out_edges(self, node_id: int) -> list[EdgeData]:
        """Edges leaving node_id, in insertion (chronological) order."""
        return [self.edges[eid] for eid in self.adj_out[node_id]]

    def to_networkx(self):
        """Export to a NetworkX MultiDiGraph for visualization/analysis.

        Lazy import: networkx is not required to construct or query the
        graph itself. Node attrs: type, entity_id. Edge attrs: edge_type,
        timestamp, ord, metadata. Edge keys are edge_ids (stable, unique).
        """
        import networkx as nx

        nxg = nx.MultiDiGraph(sample_id=self.sample_id)
        for node_id, node in enumerate(self.nodes):
            nxg.add_node(
                node_id, type=node.node_type, entity_id=node.entity_id,
            )
        for edge_id, edge in enumerate(self.edges):
            nxg.add_edge(
                edge.src, edge.dst, key=edge_id,
                edge_type=edge.edge_type,
                timestamp=edge.timestamp,
                ord=edge.ord,
                metadata=edge.metadata,
            )
        return nxg

    def __repr__(self) -> str:
        return (
            f"BehaviorGraph(sample_id={self.sample_id!r}, "
            f"nodes={self.n_nodes}, edges={self.n_edges})"
        )


# Re-export the canonical type sets so callers don't need two imports.
__all__ = [
    "BehaviorGraph",
    "MissingEntityError",
    "NODE_TYPES",
    "EDGE_TYPES",
]