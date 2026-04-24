"""
Type definitions for the BehaviorGraph layer.

Edge types are the 12 active event types from Sprint 1's schema v1.0.
Node types are the 9 entity types from Sprint 1's EntityRegistry.

The PDF's original 6-edge-type list is obsolete — Sprint 1 expanded the
event/entity sets and we keep a 1:1 mapping from event_type to edge_type
to preserve signal for Sprint 3 feature engineering. See the schema-
deviation notes in docs/schema.md.

Constants are mirrored from ingest/ rather than imported so the graph
layer has no hard dependency on the parser. tests/test_graph_builder.py
asserts that NODE_TYPES and EDGE_TYPES stay in sync with the parser's
active type sets.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# 9 entity types from ingest/entities.py.
NODE_TYPES: frozenset[str] = frozenset({
    "process",
    "file",
    "directory",
    "registry_key",
    "domain",
    "ip",
    "named_pipe",
    "module",
    "external",
})

# 12 active event types from ingest/events.py.
# process_terminate is reserved (schema slot 13) but not emitted in v1.0
# and is intentionally absent here.
EDGE_TYPES: frozenset[str] = frozenset({
    "process_spawn",
    "file_read",
    "file_write",
    "file_delete",
    "file_copy",
    "file_move",
    "reg_read",
    "reg_write",
    "reg_delete",
    "net_connect",
    "dns_query",
    "module_load",
})


@dataclass(slots=True)
class NodeData:
    """A vertex in a BehaviorGraph.

    entity_id is the sha1[:12] hex ID assigned by Sprint 1's EntityRegistry:
    content-hashed for files/domains/keys/IPs/pipes/modules, sample-scoped
    for processes. Stable across re-parses.
    """
    entity_id: str
    node_type: str

    def __post_init__(self) -> None:
        if self.node_type not in NODE_TYPES:
            raise ValueError(
                f"unknown node_type {self.node_type!r}; "
                f"expected one of {sorted(NODE_TYPES)}"
            )


@dataclass(slots=True)
class EdgeData:
    """A directed edge in a BehaviorGraph.

    src and dst are integer node IDs (indices into BehaviorGraph.nodes).
    edge_type is the originating event_type (1:1 mapping with EDGE_TYPES).
    timestamp may be None for events with timestamp_source='none'; ord is
    always present, and events are pre-sorted by (timestamp_or_inf, ord).
    metadata is the original event metadata dict, retained by reference --
    do not mutate downstream.
    """
    src: int
    dst: int
    edge_type: str
    timestamp: Optional[float]
    ord: int
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.edge_type not in EDGE_TYPES:
            raise ValueError(
                f"unknown edge_type {self.edge_type!r}; "
                f"expected one of {sorted(EDGE_TYPES)}"
            )
