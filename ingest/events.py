"""
Normalized event schema for MalGraph Sprint 1.

An Event is the unit of sandbox behavior after parsing. Event streams feed
the Sprint 2 graph builder.

Stability guarantees:
- `ord` is monotonic within a sample and stable across re-parses.
- `timestamp` is seconds relative to the earliest observed event in the
  sample, not to `info.started` (unreliable in the Avast-CTU corpus).
- `src` and `dst` are canonical entity IDs from `EntityRegistry`.

Sample-level labels (Emotet | Trickbot) live in
`data/processed/manifest.jsonl`, NOT on Event objects. See docs/schema.md
for rationale.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class EventType(str, Enum):
    PROCESS_SPAWN = "process_spawn"
    PROCESS_TERMINATE = "process_terminate"
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    FILE_DELETE = "file_delete"
    FILE_COPY = "file_copy"
    FILE_MOVE = "file_move"
    REG_READ = "reg_read"
    REG_WRITE = "reg_write"
    REG_DELETE = "reg_delete"
    NET_CONNECT = "net_connect"
    DNS_QUERY = "dns_query"
    MODULE_LOAD = "module_load"


class TimestampSource(str, Enum):
    """How the event's timestamp was obtained."""

    ABSOLUTE = "absolute"   # parsed from a real datetime string (enhanced, calls)
    RELATIVE = "relative"   # given as float seconds offset (network.tcp.time)
    INFERRED = "inferred"   # interpolated from neighbors
    NONE = "none"           # no timing information available


SCHEMA_VERSION = "1.1"


@dataclass(frozen=True, slots=True)
class Event:
    sample_id: str
    ord: int
    event_type: EventType
    src: str
    dst: str
    timestamp: float | None = None
    timestamp_source: TimestampSource = TimestampSource.NONE
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSONL-serializable dict. Enums flattened to their string values."""
        d = asdict(self)
        d["event_type"] = self.event_type.value
        d["timestamp_source"] = self.timestamp_source.value
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"), ensure_ascii=False)


def validate_event_dict(d: dict[str, Any]) -> None:
    """
    Minimal structural check for a dict loaded back from JSONL.
    Raises ValueError on schema violations. Not a substitute for real
    validation (we can add jsonschema later if needed).
    """
    required = {"sample_id", "ord", "event_type", "src", "dst",
                "timestamp_source", "metadata"}
    missing = required - d.keys()
    if missing:
        raise ValueError(f"Event missing required fields: {missing}")
    if d["event_type"] not in {e.value for e in EventType}:
        raise ValueError(f"Unknown event_type: {d['event_type']}")
    if d["timestamp_source"] not in {s.value for s in TimestampSource}:
        raise ValueError(f"Unknown timestamp_source: {d['timestamp_source']}")
    if not isinstance(d["ord"], int) or d["ord"] < 0:
        raise ValueError(f"Invalid ord: {d['ord']!r}")
    ts = d.get("timestamp")
    if ts is not None and not isinstance(ts, (int, float)):
        raise ValueError(f"Invalid timestamp type: {type(ts).__name__}")
