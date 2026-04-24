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

from ingest.canonicalize import (
    PathKind,
    canonicalize_domain,
    canonicalize_ip,
    canonicalize_path,
    canonicalize_registry_key,
    is_sandbox_domain,
    is_sandbox_ip,
    parse_cape_timestamp,
)
from ingest.entities import EntityRegistry, EntityType
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


# Dispatch table: (event, object) tuple -> handler name.
# Handlers are local closures inside extract_enhanced_events.
_ENHANCED_HANDLED: frozenset = frozenset({
    ("read", "registry"), ("write", "registry"), ("delete", "registry"),
    ("read", "file"), ("write", "file"), ("delete", "file"),
    ("copy", "file"), ("move", "file"),
    ("create", "dir"),
    ("load", "library"),
})
_ENHANCED_SKIPPED_SILENT: frozenset = frozenset({
    ("execute", "file"),        # overlaps with processtree-sourced spawn
    ("create", "windowshook"),  # niche, deferred
})


def extract_enhanced_events(
    report: dict,
    registry: EntityRegistry,
    ord_counter: "_OrdCounter",
    sample_id: str,
    root_actor_id: str,
    manifest: dict,
) -> list[Event]:
    """
    behavior.enhanced -> file/registry/module events.

    behavior.enhanced has no per-event PID, so actor attribution is
    always the sample's root process (root_actor_id). Timestamps are
    parsed via parse_cape_timestamp and converted to epoch seconds;
    the parent parse_report() rebases these to sample-local time.

    Unhandled (event, object) pairs are counted in
    manifest['unhandled_enhanced'] for post-batch review.
    """
    behavior = report.get("behavior") or {}
    enhanced = behavior.get("enhanced") or []
    if not isinstance(enhanced, list):
        return []

    events: list[Event] = []
    unhandled: dict[tuple[str, str], int] = {}

    for entry in enhanced:
        if not isinstance(entry, dict):
            continue
        ev_name = entry.get("event")
        ev_object = entry.get("object")
        if not isinstance(ev_name, str) or not isinstance(ev_object, str):
            continue
        key = (ev_name, ev_object)

        if key in _ENHANCED_SKIPPED_SILENT:
            continue
        if key not in _ENHANCED_HANDLED:
            unhandled[key] = unhandled.get(key, 0) + 1
            continue

        data = entry.get("data")
        if not isinstance(data, dict):
            data = {}

        # Timestamp: epoch seconds (rebased later by parse_report).
        ts_raw = entry.get("timestamp")
        dt = parse_cape_timestamp(ts_raw)
        if dt is not None:
            ts = dt.timestamp()
            ts_source = TimestampSource.ABSOLUTE
        else:
            ts = None
            ts_source = TimestampSource.NONE

        emitted = _emit_enhanced_event(
            key=key, data=data, entry=entry,
            registry=registry, ord_counter=ord_counter,
            sample_id=sample_id, root_actor_id=root_actor_id,
            timestamp=ts, timestamp_source=ts_source,
        )
        if emitted is not None:
            events.append(emitted)

    # Roll unhandled tallies into the manifest.
    if unhandled:
        bucket = manifest.setdefault("unhandled_enhanced", [])
        for (ev, obj), count in sorted(unhandled.items()):
            bucket.append({"event": ev, "object": obj, "count": count})

    return events


def _emit_enhanced_event(
    key: tuple[str, str],
    data: dict,
    entry: dict,
    registry: EntityRegistry,
    ord_counter: "_OrdCounter",
    sample_id: str,
    root_actor_id: str,
    timestamp: float | None,
    timestamp_source: TimestampSource,
) -> Event | None:
    """
    Map one enhanced entry to exactly one Event. Returns None if the
    entry's data payload is too malformed to construct an entity
    (e.g. missing path/regkey). The drop is silent by design — these
    are rare and not worth inflating the manifest for.
    """
    ev_name, ev_object = key
    meta: dict = {"source": "behavior.enhanced"}
    eid = entry.get("eid")
    if isinstance(eid, int):
        meta["eid"] = eid

    # --- registry operations -------------------------------------------
    if ev_object == "registry":
        regkey_raw = data.get("regkey")
        if not isinstance(regkey_raw, str) or not regkey_raw:
            return None
        dst_id = registry.register_registry_key(
            canonicalize_registry_key(regkey_raw)
        )
        content = data.get("content")
        if content is not None:
            # Content can be any JSON value; preserve without assumption.
            meta["content"] = content
        event_type = {
            "read": EventType.REG_READ,
            "write": EventType.REG_WRITE,
            "delete": EventType.REG_DELETE,
        }[ev_name]
        return _build_event(
            sample_id, ord_counter, event_type, root_actor_id, dst_id,
            timestamp, timestamp_source, meta,
        )

    # --- module load ---------------------------------------------------
    if ev_name == "load" and ev_object == "library":
        fname = data.get("file")
        if not isinstance(fname, str) or not fname:
            return None
        module_name = fname.strip().lower()
        dst_id = registry.register_module(module_name)
        path_to = data.get("pathtofile")
        if isinstance(path_to, str) and path_to:
            _, canon = canonicalize_path(path_to)
            if canon:
                meta["path"] = canon
        return _build_event(
            sample_id, ord_counter, EventType.MODULE_LOAD,
            root_actor_id, dst_id,
            timestamp, timestamp_source, meta,
        )

    # --- directory creation -------------------------------------------
    if ev_name == "create" and ev_object == "dir":
        path_raw = data.get("file") or data.get("path")
        if not isinstance(path_raw, str) or not path_raw:
            return None
        _, canon = canonicalize_path(path_raw)
        if not canon:
            return None
        dst_id = registry.register_directory(canon)
        meta["kind"] = "directory"
        return _build_event(
            sample_id, ord_counter, EventType.FILE_WRITE,
            root_actor_id, dst_id,
            timestamp, timestamp_source, meta,
        )

    # --- file copy / move (two-entity) --------------------------------
    if ev_name in ("copy", "move") and ev_object == "file":
        src_path_raw = data.get("from")
        dst_path_raw = data.get("to")
        if not isinstance(src_path_raw, str) or not src_path_raw:
            return None
        if not isinstance(dst_path_raw, str) or not dst_path_raw:
            return None
        src_kind, src_canon = canonicalize_path(src_path_raw)
        dst_kind, dst_canon = canonicalize_path(dst_path_raw)
        if not src_canon or not dst_canon:
            return None
        src_file_id = _register_pathlike(registry, src_kind, src_canon)
        dst_file_id = _register_pathlike(registry, dst_kind, dst_canon)
        meta["source_file"] = src_file_id
        event_type = EventType.FILE_COPY if ev_name == "copy" \
            else EventType.FILE_MOVE
        return _build_event(
            sample_id, ord_counter, event_type,
            root_actor_id, dst_file_id,
            timestamp, timestamp_source, meta,
        )

    # --- file read / write / delete -----------------------------------
    if ev_object == "file":
        path_raw = data.get("file") or data.get("path")
        if not isinstance(path_raw, str) or not path_raw:
            return None
        kind, canon = canonicalize_path(path_raw)
        if not canon:
            return None
        dst_id = _register_pathlike(registry, kind, canon)
        event_type = {
            "read": EventType.FILE_READ,
            "write": EventType.FILE_WRITE,
            "delete": EventType.FILE_DELETE,
        }[ev_name]
        return _build_event(
            sample_id, ord_counter, event_type, root_actor_id, dst_id,
            timestamp, timestamp_source, meta,
        )

    return None  # defensive; key was in _ENHANCED_HANDLED so shouldn't hit


def _register_pathlike(
    registry: EntityRegistry, kind: PathKind, canonical: str,
) -> str:
    """Route a canonicalized path to the right registry method."""
    if kind is PathKind.NAMED_PIPE:
        return registry.register_named_pipe(canonical)
    return registry.register_file(canonical)


def _build_event(
    sample_id: str,
    ord_counter: "_OrdCounter",
    event_type: EventType,
    src: str,
    dst: str,
    timestamp: float | None,
    timestamp_source: TimestampSource,
    metadata: dict,
) -> Event:
    return Event(
        sample_id=sample_id,
        ord=ord_counter.next(),
        event_type=event_type,
        src=src,
        dst=dst,
        timestamp=timestamp,
        timestamp_source=timestamp_source,
        metadata=metadata,
    )


# Mapping of summary field -> (event_type, entity kind).
# Only these six keys produce fallback events. The catch-all
# behavior.summary.files and .keys are deliberately ignored
# (no operation semantics; see commit design review).
_SUMMARY_FILE_MAP: dict[str, EventType] = {
    "read_files": EventType.FILE_READ,
    "write_files": EventType.FILE_WRITE,
    "delete_files": EventType.FILE_DELETE,
}
_SUMMARY_REG_MAP: dict[str, EventType] = {
    "read_keys": EventType.REG_READ,
    "write_keys": EventType.REG_WRITE,
    "delete_keys": EventType.REG_DELETE,
}


def extract_summary_fallbacks(
    report: dict,
    registry: EntityRegistry,
    ord_counter: "_OrdCounter",
    sample_id: str,
    root_actor_id: str,
    manifest: dict,
) -> list[Event]:
    """
    behavior.summary.{read,write,delete}_{files,keys} -> fallback events.

    Deduplication: if the canonical path/key is already registered in
    `registry` under the matching entity type, the summary entry is
    skipped (enhanced already covered it). This is an O(1) check per
    entry via EntityRegistry.has_canonical.

    Attribution: src = root_actor_id (per A/A design decision); all
    events tagged metadata.attribution='summary_fallback' so Sprint 3
    feature code can filter them if desired.

    No timestamps: summary is a flat list with no timing information.
    timestamp=None, timestamp_source=NONE.

    Empty / whitespace / non-string entries are silently dropped;
    counts surface in manifest['filters'].
    """
    summary = (report.get("behavior") or {}).get("summary") or {}
    if not isinstance(summary, dict):
        return []

    events: list[Event] = []
    dedup_dropped = 0
    malformed_dropped = 0

    # --- files ---------------------------------------------------------
    for field, event_type in _SUMMARY_FILE_MAP.items():
        entries = summary.get(field) or []
        if not isinstance(entries, list):
            continue
        for raw in entries:
            if not isinstance(raw, str) or not raw.strip():
                malformed_dropped += 1
                continue
            kind, canon = canonicalize_path(raw)
            if not canon:
                malformed_dropped += 1
                continue
            # Dedup: named pipes and files are different entity types,
            # so check whichever the canonical path routes to.
            etype = (EntityType.NAMED_PIPE if kind is PathKind.NAMED_PIPE
                     else EntityType.FILE)
            if registry.has_canonical(etype, canon):
                dedup_dropped += 1
                continue
            dst_id = _register_pathlike(registry, kind, canon)
            events.append(_build_event(
                sample_id=sample_id,
                ord_counter=ord_counter,
                event_type=event_type,
                src=root_actor_id,
                dst=dst_id,
                timestamp=None,
                timestamp_source=TimestampSource.NONE,
                metadata={
                    "source": f"behavior.summary.{field}",
                    "attribution": "summary_fallback",
                },
            ))

    # --- registry keys -------------------------------------------------
    for field, event_type in _SUMMARY_REG_MAP.items():
        entries = summary.get(field) or []
        if not isinstance(entries, list):
            continue
        for raw in entries:
            if not isinstance(raw, str) or not raw.strip():
                malformed_dropped += 1
                continue
            canon = canonicalize_registry_key(raw)
            if not canon:
                malformed_dropped += 1
                continue
            if registry.has_canonical(EntityType.REGISTRY_KEY, canon):
                dedup_dropped += 1
                continue
            dst_id = registry.register_registry_key(canon)
            events.append(_build_event(
                sample_id=sample_id,
                ord_counter=ord_counter,
                event_type=event_type,
                src=root_actor_id,
                dst=dst_id,
                timestamp=None,
                timestamp_source=TimestampSource.NONE,
                metadata={
                    "source": f"behavior.summary.{field}",
                    "attribution": "summary_fallback",
                },
            ))

    # --- bookkeeping --------------------------------------------------
    filters = manifest.setdefault("filters", {})
    filters["summary_dedup_dropped"] = (
        filters.get("summary_dedup_dropped", 0) + dedup_dropped
    )
    filters["summary_malformed_dropped"] = (
        filters.get("summary_malformed_dropped", 0) + malformed_dropped
    )

    return events


def extract_network_events(
    report: dict,
    registry: EntityRegistry,
    ord_counter: "_OrdCounter",
    sample_id: str,
    root_actor_id: str,
    manifest: dict,
) -> list[Event]:
    """
    network.tcp + network.udp -> net_connect events.

    Filter: destinations in RFC1918 / loopback / link-local / multicast
    / broadcast are dropped (sandbox infrastructure noise, not malware
    behavior). Count surfaces in manifest.filters.net_connect_dropped_private.

    No dedup: repeated connections to (dst, dport, protocol) are
    preserved with distinct timestamps so Sprint 3 can detect
    beaconing patterns.

    Actor: root_actor_id (CAPE doesn't provide per-connection pid in
    this schema). Timestamp: 'time' field is already sandbox-relative
    seconds -> source=RELATIVE. parse_report's rebase step will
    adjust relative to earliest observed event.
    """
    network = report.get("network") or {}
    if not isinstance(network, dict):
        return []

    events: list[Event] = []
    dropped_private = 0
    dropped_malformed = 0

    for proto in ("tcp", "udp"):
        entries = network.get(proto) or []
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                dropped_malformed += 1
                continue

            # Destination IP — accept 'dst' first, 'ip' second.
            dst_raw = entry.get("dst") or entry.get("ip")
            if not isinstance(dst_raw, str) or not dst_raw.strip():
                dropped_malformed += 1
                continue

            canon_ip = canonicalize_ip(dst_raw)
            if canon_ip is None:
                dropped_malformed += 1
                continue

            # Drop sandbox infrastructure destinations.
            if is_sandbox_ip(canon_ip):
                dropped_private += 1
                continue

            # Timestamp: network.{tcp,udp}[i].time is float seconds
            # already relative to sandbox start.
            t_raw = entry.get("time")
            if isinstance(t_raw, (int, float)):
                ts = float(t_raw)
                ts_source = TimestampSource.RELATIVE
            else:
                ts = None
                ts_source = TimestampSource.NONE

            dst_id = registry.register_ip(canon_ip)

            meta: dict = {
                "source": f"network.{proto}",
                "protocol": proto,
            }
            dport = entry.get("dport")
            if isinstance(dport, int):
                meta["dport"] = dport
            sport = entry.get("sport")
            if isinstance(sport, int):
                meta["sport"] = sport

            events.append(_build_event(
                sample_id=sample_id,
                ord_counter=ord_counter,
                event_type=EventType.NET_CONNECT,
                src=root_actor_id,
                dst=dst_id,
                timestamp=ts,
                timestamp_source=ts_source,
                metadata=meta,
            ))

    filters = manifest.setdefault("filters", {})
    filters["net_connect_dropped_private"] = (
        filters.get("net_connect_dropped_private", 0) + dropped_private
    )
    filters["net_connect_dropped_malformed"] = (
        filters.get("net_connect_dropped_malformed", 0) + dropped_malformed
    )

    return events


def extract_dns_events(
    report: dict,
    registry: EntityRegistry,
    ord_counter: "_OrdCounter",
    sample_id: str,
    root_actor_id: str,
    manifest: dict,
) -> list[Event]:
    """
    suricata.dns + network.dns -> dns_query events.

    Source preference: suricata.dns is primary (has timestamps).
    network.dns is union-supplement: any domain not already covered
    by a suricata event gets a network.dns fallback event with
    timestamp=None.

    suricata.dns emits only for entries with type=="query"; answer
    records are silently skipped (their domain will have already
    appeared in a query). Design decision A: one dns_query event
    per user-level query, not one per protocol message.

    Filters:
    - is_sandbox_domain drops CAPE's own DNS liveness probes
      (e.g. google-public-dns-a.google.com). Count surfaces in
      manifest.filters.dns_dropped_sandbox.
    - Non-string / empty / whitespace domains dropped as malformed.

    No event-level dedup: repeated queries to the same domain are
    preserved with distinct timestamps so Sprint 3 can detect
    repeated-resolution / C2-beaconing patterns. Entity-level dedup
    still applies (one domain entity per canonical domain).
    """
    network = report.get("network") or {}
    suricata = report.get("suricata") or {}
    if not isinstance(network, dict):
        network = {}
    if not isinstance(suricata, dict):
        suricata = {}

    events: list[Event] = []
    dropped_sandbox = 0
    dropped_malformed = 0

    # Track which domains were emitted from suricata so network.dns
    # can supplement rather than duplicate. Entity-level check (via
    # registry.has_canonical) doesn't work here because both sources
    # register DOMAIN entities — we need to track at the event level.
    suricata_domains: set[str] = set()

    # --- suricata.dns (primary) ---------------------------------------
    sur_entries = suricata.get("dns") or []
    if isinstance(sur_entries, list):
        for entry in sur_entries:
            if not isinstance(entry, dict):
                dropped_malformed += 1
                continue
            if entry.get("type") != "query":
                continue  # silently skip answer records

            domain_raw = entry.get("rrname")
            if not isinstance(domain_raw, str) or not domain_raw.strip():
                dropped_malformed += 1
                continue
            canon = canonicalize_domain(domain_raw)
            if not canon:
                dropped_malformed += 1
                continue
            if is_sandbox_domain(canon):
                dropped_sandbox += 1
                continue

            # Timestamp via parse_cape_timestamp.
            dt = parse_cape_timestamp(entry.get("timestamp"))
            if dt is not None:
                ts = dt.timestamp()
                ts_source = TimestampSource.ABSOLUTE
            else:
                ts = None
                ts_source = TimestampSource.NONE

            dst_id = registry.register_domain(canon)
            suricata_domains.add(canon)

            meta: dict = {"source": "suricata.dns"}
            rrtype = entry.get("rrtype")
            if isinstance(rrtype, str):
                meta["query_type"] = rrtype

            events.append(_build_event(
                sample_id=sample_id,
                ord_counter=ord_counter,
                event_type=EventType.DNS_QUERY,
                src=root_actor_id,
                dst=dst_id,
                timestamp=ts,
                timestamp_source=ts_source,
                metadata=meta,
            ))

    # --- network.dns (fallback / supplement) --------------------------
    net_entries = network.get("dns") or []
    if isinstance(net_entries, list):
        for entry in net_entries:
            if not isinstance(entry, dict):
                dropped_malformed += 1
                continue
            domain_raw = entry.get("request")
            if not isinstance(domain_raw, str) or not domain_raw.strip():
                dropped_malformed += 1
                continue
            canon = canonicalize_domain(domain_raw)
            if not canon:
                dropped_malformed += 1
                continue
            if is_sandbox_domain(canon):
                dropped_sandbox += 1
                continue
            if canon in suricata_domains:
                continue  # already covered by a suricata event

            dst_id = registry.register_domain(canon)

            meta: dict = {"source": "network.dns"}
            qtype = entry.get("type")
            if isinstance(qtype, str):
                meta["query_type"] = qtype

            events.append(_build_event(
                sample_id=sample_id,
                ord_counter=ord_counter,
                event_type=EventType.DNS_QUERY,
                src=root_actor_id,
                dst=dst_id,
                timestamp=None,
                timestamp_source=TimestampSource.NONE,
                metadata=meta,
            ))

    filters = manifest.setdefault("filters", {})
    filters["dns_dropped_sandbox"] = (
        filters.get("dns_dropped_sandbox", 0) + dropped_sandbox
    )
    filters["dns_dropped_malformed"] = (
        filters.get("dns_dropped_malformed", 0) + dropped_malformed
    )

    return events


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









