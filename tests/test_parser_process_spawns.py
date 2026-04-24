"""
Tests for ingest.parser.extract_process_spawns.

Synthetic fixtures exercise edge cases (empty tree, nesting, malformed
nodes). One test asserts against a real CAPE sample to confirm the
extractor produces the expected shape on real data.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ingest.entities import EntityRegistry, EntityType
from ingest.events import Event, EventType, TimestampSource
from ingest.parser import (
    _OrdCounter,
    extract_process_spawns,
    parse_report_with_manifest,
)


REAL_SAMPLE = (
    Path("data/raw")
    / "c1f6f8635fd2dfa1440fe485d1ac0ad8b934f3779a4bb999028ce71d12ea5494.json"
)


# --- helpers ------------------------------------------------------------

def _run_extractor(report: dict, sample_id: str = "sid") -> tuple[
    list[Event], EntityRegistry,
]:
    """Common harness: run extract_process_spawns on a synthetic report."""
    registry = EntityRegistry(sample_id=sample_id)
    ord_counter = _OrdCounter()
    events = extract_process_spawns(
        report=report,
        registry=registry,
        ord_counter=ord_counter,
        sample_id=sample_id,
        manifest={},
    )
    return events, registry


# --- empty/malformed inputs --------------------------------------------

class TestEmptyAndMalformed:
    def test_empty_report_yields_no_events(self):
        events, reg = _run_extractor({})
        assert events == []
        assert len(reg) == 0

    def test_empty_behavior_yields_no_events(self):
        events, _ = _run_extractor({"behavior": {}})
        assert events == []

    def test_empty_processtree_yields_no_events(self):
        events, _ = _run_extractor({"behavior": {"processtree": []}})
        assert events == []

    def test_processtree_not_a_list_yields_no_events(self):
        events, _ = _run_extractor({"behavior": {"processtree": "junk"}})
        assert events == []

    def test_node_without_pid_skipped(self):
        report = {"behavior": {"processtree": [
            {"name": "no_pid.exe"},          # skipped
            {"pid": 100, "parent_id": 1, "name": "has_pid.exe"},
        ]}}
        events, _ = _run_extractor(report)
        assert len(events) == 1
        assert events[0].metadata["pid"] == 100

    def test_non_dict_node_skipped(self):
        report = {"behavior": {"processtree": [
            "not-a-dict",
            ["also", "not", "a", "dict"],
            {"pid": 100, "parent_id": 1},
        ]}}
        events, _ = _run_extractor(report)
        assert len(events) == 1


# --- parent role classification ----------------------------------------

class TestParentRole:
    def test_external_parent_when_pid_not_in_behavior_processes(self):
        """Classic sandbox-launcher case: root node's parent PID
        doesn't appear in behavior.processes."""
        report = {"behavior": {
            "processes": [{"process_id": 100}],
            "processtree": [
                {"pid": 100, "parent_id": 2312, "name": "sample.exe"},
            ],
        }}
        events, reg = _run_extractor(report)
        assert len(events) == 1
        e = events[0]
        assert e.metadata["parent_role"] == "sandbox_launcher"
        assert e.metadata["parent_pid"] == 2312
        # Source entity is the external placeholder, not a process.
        src_meta = reg.get(e.src)
        assert src_meta["type"] == EntityType.EXTERNAL.value

    def test_internal_parent_when_pid_is_in_behavior_processes(self):
        """Not realistic for a root node but possible for children in
        a tree. Here we construct a tree where the child's parent IS
        in behavior.processes."""
        report = {"behavior": {
            "processes": [
                {"process_id": 100}, {"process_id": 200},
            ],
            "processtree": [
                {"pid": 100, "parent_id": 200, "name": "child.exe"},
            ],
        }}
        events, reg = _run_extractor(report)
        assert len(events) == 1
        e = events[0]
        assert e.metadata["parent_role"] == "internal"
        src_meta = reg.get(e.src)
        assert src_meta["type"] == EntityType.PROCESS.value

    def test_missing_parent_id_becomes_sandbox_launcher_unknown(self):
        """Defensive case: processtree node with no parent_id at all."""
        report = {"behavior": {
            "processes": [{"process_id": 100}],
            "processtree": [{"pid": 100, "name": "orphan.exe"}],
        }}
        events, reg = _run_extractor(report)
        assert len(events) == 1
        assert events[0].metadata["parent_role"] == "sandbox_launcher"
        assert events[0].metadata["parent_pid"] is None


# --- recursion / nested trees ------------------------------------------

class TestNestedTree:
    def test_children_get_parent_from_containing_node(self):
        """children[].parent_id is IGNORED; parent is the enclosing node's
        pid. This matches CAPE's processtree structure."""
        report = {"behavior": {
            "processes": [
                {"process_id": 100}, {"process_id": 200},
                {"process_id": 300},
            ],
            "processtree": [{
                "pid": 100, "parent_id": 999, "name": "root.exe",
                "children": [
                    {"pid": 200, "parent_id": 100, "name": "mid.exe",
                     "children": [
                         {"pid": 300, "parent_id": 200, "name": "leaf.exe"},
                     ]},
                ],
            }],
        }}
        events, _ = _run_extractor(report)
        assert len(events) == 3

        # Event ordering: preorder DFS.
        assert [e.metadata["pid"] for e in events] == [100, 200, 300]
        assert [e.metadata["parent_pid"] for e in events] == [999, 100, 200]

    def test_multiple_root_processes(self):
        report = {"behavior": {
            "processes": [
                {"process_id": 100}, {"process_id": 200},
            ],
            "processtree": [
                {"pid": 100, "parent_id": 1, "name": "a.exe"},
                {"pid": 200, "parent_id": 1, "name": "b.exe"},
            ],
        }}
        events, _ = _run_extractor(report)
        assert len(events) == 2
        assert [e.metadata["pid"] for e in events] == [100, 200]

    def test_ord_is_monotonic(self):
        report = {"behavior": {
            "processes": [
                {"process_id": 100}, {"process_id": 200},
                {"process_id": 300},
            ],
            "processtree": [{
                "pid": 100, "parent_id": 1,
                "children": [
                    {"pid": 200, "children": [
                        {"pid": 300},
                    ]},
                ],
            }],
        }}
        events, _ = _run_extractor(report)
        ords = [e.ord for e in events]
        assert ords == sorted(ords)
        assert ords == list(range(len(events)))

    def test_deep_recursion_does_not_blow_stack(self):
        """Construct a 50-deep tree — well short of Python's default
        recursion limit, but confirms depth handling is sane."""
        node = {"pid": 1000}
        for i in range(49):
            node = {"pid": 999 - i, "children": [node]}
        report = {"behavior": {
            "processes": [{"process_id": p} for p in range(950, 1001)],
            "processtree": [{"pid": 949, "parent_id": 1, "children": [node]}],
        }}
        events, _ = _run_extractor(report)
        assert len(events) == 51


# --- event invariants ---------------------------------------------------

class TestEventInvariants:
    def test_all_events_are_process_spawn(self):
        report = {"behavior": {
            "processes": [{"process_id": 100}],
            "processtree": [{"pid": 100, "parent_id": 1}],
        }}
        events, _ = _run_extractor(report)
        for e in events:
            assert e.event_type is EventType.PROCESS_SPAWN

    def test_no_timestamps(self):
        """processtree doesn't carry reliable timing — timestamp=None,
        source=NONE. See docs/schema.md."""
        report = {"behavior": {
            "processes": [{"process_id": 100}],
            "processtree": [{"pid": 100, "parent_id": 1}],
        }}
        events, _ = _run_extractor(report)
        for e in events:
            assert e.timestamp is None
            assert e.timestamp_source is TimestampSource.NONE

    def test_metadata_source_tag(self):
        report = {"behavior": {
            "processes": [{"process_id": 100}],
            "processtree": [{"pid": 100, "parent_id": 1}],
        }}
        events, _ = _run_extractor(report)
        assert events[0].metadata["source"] == "behavior.processtree"

    def test_module_path_preserved_when_present(self):
        report = {"behavior": {
            "processes": [{"process_id": 100}],
            "processtree": [{
                "pid": 100, "parent_id": 1, "name": "x.exe",
                "module_path": "C:\\Users\\comp\\Temp\\x.exe",
            }],
        }}
        events, _ = _run_extractor(report)
        assert events[0].metadata["module_path"] == \
            "C:\\Users\\comp\\Temp\\x.exe"

    def test_module_path_omitted_when_absent(self):
        report = {"behavior": {
            "processes": [{"process_id": 100}],
            "processtree": [{"pid": 100, "parent_id": 1}],
        }}
        events, _ = _run_extractor(report)
        assert "module_path" not in events[0].metadata


# --- determinism --------------------------------------------------------

class TestDeterminism:
    def test_same_input_yields_identical_events(self):
        report = {"behavior": {
            "processes": [
                {"process_id": 100}, {"process_id": 200},
            ],
            "processtree": [{
                "pid": 100, "parent_id": 1,
                "children": [{"pid": 200, "parent_id": 100}],
            }],
        }}
        e1, r1 = _run_extractor(report, sample_id="s")
        e2, r2 = _run_extractor(report, sample_id="s")
        # Full field-by-field equality.
        assert [e.to_dict() for e in e1] == [e.to_dict() for e in e2]
        assert r1.to_manifest() == r2.to_manifest()


# --- real sample --------------------------------------------------------

@pytest.mark.skipif(
    not REAL_SAMPLE.exists(),
    reason="real sample not present in data/raw/",
)
class TestRealSample:
    """Asserts against the c1f6f86 Emotet sample actually on disk."""

    def test_c1f6f86_emits_exactly_one_spawn(self):
        events, manifest = parse_report_with_manifest(REAL_SAMPLE)
        spawns = [e for e in events if e.event_type is EventType.PROCESS_SPAWN]
        assert len(spawns) == 1

    def test_c1f6f86_spawn_has_sandbox_launcher_parent(self):
        events, _ = parse_report_with_manifest(REAL_SAMPLE)
        spawn = [e for e in events if e.event_type is EventType.PROCESS_SPAWN][0]
        assert spawn.metadata["parent_role"] == "sandbox_launcher"
        assert spawn.metadata["pid"] == 2116
        assert spawn.metadata["parent_pid"] == 2312
