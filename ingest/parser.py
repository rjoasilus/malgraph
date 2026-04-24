"""
CAPE report -> Event stream parser. Sprint 1 orchestration layer.

Public entry points:
- parse_report(path): returns list[Event]
- parse_report_with_manifest(path): returns (list[Event], manifest_dict)
  for batch runs that need per-sample counters.

Extractors for each event domain are free functions (extract_*). They're
stubs in this commit; real implementations land in the next commit group
one domain at a time.

Design choices recorded here:
- stdlib json.load (verified tolerable on up to 179 MB samples).
- Per-report timestamp anchor = min observed absolute timestamp across
  all emitted events, NOT info.started (see docs/schema.md).
- ord is assigned in parse order, then preserved through the final
  sort so (timestamp, ord) is a total order even when ties exist.
- Summary-fallback events use the sample's root process as src with
  metadata.attribution='summary_fallback'.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ingest.entities import EntityRegistry
from ingest.events import Event, EventType, TimestampSource


# --- public API ---------------------------------------------------------

def parse_report(path: str | Path) -> list[Event]:
    """Parse one CAPE JSON report into a normalized Event list."""
    events, _ = parse_report_with_manifest(path)
    return events


def parse_report_with_manifest(
    path: str | Path,
) -> tuple[list[Event], dict[str, Any]]:
    """
    Parse one CAPE report and return (events, manifest).

    manifest contains per-sample counters consumed by the batch runner
    for experiments/sprint1_stats.md: event counts by type, entity
    counts by type, filter-drop counts, parse errors, etc.
    """
    path = Path(path)
    manifest: dict[str, Any] = {
        "sample_id": path.stem,
        "path": str(path),
        "parse_ok": False,
        "parse_error": None,
        "event_counts": {},
        "entity_counts": {},
        "filters": {
            "net_connect_dropped_private": 0,
            "dns_dropped_sandbox": 0,
        },
        "unhandled_enhanced": [],
        "schema_version": None,
    }

    try:
        with path.open("rb") as f:
            report = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        manifest["parse_error"] = f"{type(e).__name__}: {e}"
        return [], manifest

    sample_id = _derive_sample_id(report, fallback=path.stem)
    manifest["sample_id"] = sample_id

    registry = EntityRegistry(sample_id=sample_id)
    root_pid = _root_pid(report)
    root_actor_id = registry.register_process(
        pid=root_pid, first_seen_ord=0, name=_root_name(report),
    )

    # Ordinal counter: every extractor uses next(ord_gen) to claim its slot.
    ord_counter = _OrdCounter()

    events: list[Event] = []

    # --- extractor dispatch (all stubs for now) -------------------------
    events += extract_process_spawns(
        report, registry, ord_counter, sample_id, manifest,
    )
    events += extract_enhanced_events(
        report, registry, ord_counter, sample_id,
        root_actor_id, manifest,
    )
    events += extract_summary_fallbacks(
        report, registry, ord_counter, sample_id,
        root_actor_id, manifest,
    )
    events += extract_network_events(
        report, registry, ord_counter, sample_id,
        root_actor_id, manifest,
    )
    events += extract_dns_events(
        report, registry, ord_counter, sample_id,
        root_actor_id, manifest,
    )

    # Rebase timestamps: min absolute becomes t=0 per docs/schema.md.
    events = _rebase_timestamps(events)

    # Sort by (timestamp_or_inf, ord) for a total order. Events with
    # timestamp=None sink to the end but retain their relative ord.
    events.sort(key=lambda e: (
        e.timestamp if e.timestamp is not None else float("inf"),
        e.ord,
    ))

    # Final counters.
    for e in events:
        manifest["event_counts"][e.event_type.value] = (
            manifest["event_counts"].get(e.event_type.value, 0) + 1
        )
    manifest["entity_counts"] = registry.count_by_type()
    manifest["parse_ok"] = True
    return events, manifest


# --- extractors (STUBS — filled in next commit group) -------------------

def extract_process_spawns(
    report: dict,
    registry: EntityRegistry,
    ord_counter: "_OrdCounter",
    sample_id: str,
    manifest: dict,
) -> list[Event]:
    """
    behavior.processtree -> process_spawn events.

    Walks the processtree depth-first. For each node, emits one
    process_spawn with src=parent, dst=child. Parent PIDs not seen in
    behavior.processes (typical for the sandbox launcher that spawned
    the root sample) are registered as EXTERNAL entities; the event is
    still emitted so Sprint 2's graph has a valid source node.

    No timestamps — processtree nodes in the Avast-CTU corpus don't
    carry reliable per-node timing. Ordering is via `ord`.
    """
    behavior = report.get("behavior") or {}
    tree = behavior.get("processtree") or []
    if not isinstance(tree, list):
        return []

    # Build the set of "internal" pids (those known to behavior.processes)
    # so we can flag parents that aren't in it.
    processes = behavior.get("processes") or []
    internal_pids: set[int] = set()
    if isinstance(processes, list):
        for p in processes:
            if isinstance(p, dict) and isinstance(p.get("process_id"), int):
                internal_pids.add(p["process_id"])

    events: list[Event] = []
    _walk_processtree(
        nodes=tree,
        parent_pid=None,
        registry=registry,
        ord_counter=ord_counter,
        sample_id=sample_id,
        internal_pids=internal_pids,
        out=events,
    )
    return events


def _walk_processtree(
    nodes: list,
    parent_pid: int | None,
    registry: EntityRegistry,
    ord_counter: "_OrdCounter",
    sample_id: str,
    internal_pids: set[int],
    out: list[Event],
) -> None:
    """Recursive helper for extract_process_spawns. Mutates `out`."""
    for node in nodes:
        if not isinstance(node, dict):
            continue
        pid = node.get("pid")
        if not isinstance(pid, int):
            continue  # unusable without a pid
        ppid = node.get("parent_id") if parent_pid is None else parent_pid
        name = node.get("name") or ""
        module_path = node.get("module_path") or ""

        ord_val = ord_counter.next()

        # Child is always a PROCESS entity in this sample.
        child_id = registry.register_process(
            pid=pid, first_seen_ord=ord_val, name=name,
        )

        # Parent: PROCESS if known in behavior.processes, EXTERNAL otherwise.
        parent_role = "internal"
        if isinstance(ppid, int) and ppid in internal_pids:
            parent_entity_id = registry.register_process(
                pid=ppid, first_seen_ord=ord_val,
            )
        elif isinstance(ppid, int):
            parent_entity_id = registry.register_external(
                f"sandbox_launcher_pid_{ppid}"
            )
            parent_role = "sandbox_launcher"
        else:
            parent_entity_id = registry.register_external(
                "sandbox_launcher_unknown"
            )
            parent_role = "sandbox_launcher"

        meta: dict = {
            "pid": pid,
            "parent_pid": ppid if isinstance(ppid, int) else None,
            "parent_role": parent_role,
            "source": "behavior.processtree",
        }
        if name:
            meta["name"] = name
        if module_path:
            meta["module_path"] = module_path

        out.append(Event(
            sample_id=sample_id,
            ord=ord_val,
            event_type=EventType.PROCESS_SPAWN,
            src=parent_entity_id,
            dst=child_id,
            timestamp=None,
            timestamp_source=TimestampSource.NONE,
            metadata=meta,
        ))

        # Recurse. children's parent is THIS node's pid.
        children = node.get("children")
        if isinstance(children, list):
            _walk_processtree(
                nodes=children,
                parent_pid=pid,
                registry=registry,
                ord_counter=ord_counter,
                sample_id=sample_id,
                internal_pids=internal_pids,
                out=out,
            )


def extract_enhanced_events(
    report: dict,
    registry: EntityRegistry,
    ord_counter: "_OrdCounter",
    sample_id: str,
    root_actor_id: str,
    manifest: dict,
) -> list[Event]:
    """behavior.enhanced -> file/reg/module events. STUB."""
    return []


def extract_summary_fallbacks(
    report: dict,
    registry: EntityRegistry,
    ord_counter: "_OrdCounter",
    sample_id: str,
    root_actor_id: str,
    manifest: dict,
) -> list[Event]:
    """behavior.summary.{read,write,delete}_{files,keys} -> fallback
    file/reg events that enhanced didn't already emit. STUB."""
    return []


def extract_network_events(
    report: dict,
    registry: EntityRegistry,
    ord_counter: "_OrdCounter",
    sample_id: str,
    root_actor_id: str,
    manifest: dict,
) -> list[Event]:
    """network.{tcp,udp} -> net_connect, filtered to non-private. STUB."""
    return []


def extract_dns_events(
    report: dict,
    registry: EntityRegistry,
    ord_counter: "_OrdCounter",
    sample_id: str,
    root_actor_id: str,
    manifest: dict,
) -> list[Event]:
    """suricata.dns preferred, network.dns fallback. STUB."""
    return []


# --- helpers ------------------------------------------------------------

class _OrdCounter:
    """Monotonic ord assigner. One per report parse."""
    __slots__ = ("_n",)

    def __init__(self) -> None:
        self._n = 0

    def next(self) -> int:
        n = self._n
        self._n += 1
        return n


def _derive_sample_id(report: dict, fallback: str) -> str:
    """Prefer target.file.sha256; fall back to the filename stem."""
    target = report.get("target") or {}
    f = target.get("file") or {}
    sha = f.get("sha256")
    if isinstance(sha, str) and len(sha) == 64:
        return sha.lower()
    return fallback


def _root_pid(report: dict) -> int:
    """First pid in behavior.processes; fallback 0."""
    procs = (report.get("behavior") or {}).get("processes") or []
    if procs and isinstance(procs[0], dict):
        pid = procs[0].get("process_id")
        if isinstance(pid, int):
            return pid
    return 0


def _root_name(report: dict) -> str:
    procs = (report.get("behavior") or {}).get("processes") or []
    if procs and isinstance(procs[0], dict):
        name = procs[0].get("process_name")
        if isinstance(name, str):
            return name
    return ""


def _rebase_timestamps(events: list[Event]) -> list[Event]:
    """
    Per docs/schema.md: per-report anchor is the minimum observed
    absolute/relative timestamp in the emitted events. Everything
    shifts so that minimum becomes 0.0. Events with timestamp=None
    pass through unchanged.

    This is a no-op when there are no events or no timed events.
    """
    timed = [e.timestamp for e in events if e.timestamp is not None]
    if not timed:
        return events
    anchor = min(timed)
    if anchor == 0.0:
        return events

    rebased: list[Event] = []
    for e in events:
        if e.timestamp is None:
            rebased.append(e)
        else:
            rebased.append(Event(
                sample_id=e.sample_id,
                ord=e.ord,
                event_type=e.event_type,
                src=e.src,
                dst=e.dst,
                timestamp=e.timestamp - anchor,
                timestamp_source=e.timestamp_source,
                metadata=e.metadata,
            ))
    return rebased

