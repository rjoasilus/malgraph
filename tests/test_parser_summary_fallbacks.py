"""
Tests for ingest.parser.extract_summary_fallbacks.

The central invariant: summary entries that duplicate what
behavior.enhanced already emitted are dropped. Dedup is via
EntityRegistry.has_canonical, so tests pre-register entities to
simulate enhanced having fired, then assert the fallback skips.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ingest.entities import EntityRegistry, EntityType
from ingest.events import Event, EventType, TimestampSource
from ingest.parser import (
    _OrdCounter,
    extract_summary_fallbacks,
    parse_report_with_manifest,
)


REAL_SAMPLE = (
    Path("data/raw")
    / "c1f6f8635fd2dfa1440fe485d1ac0ad8b934f3779a4bb999028ce71d12ea5494.json"
)


# --- helpers ------------------------------------------------------------

def _run(summary: dict, pre_registered: dict | None = None) -> tuple[
    list[Event], EntityRegistry, dict,
]:
    """Harness: build a report with the given summary, optionally pre-
    register entities to simulate enhanced having fired first.

    pre_registered is a dict like {EntityType.FILE: ['c:/x.exe', ...],
                                   EntityType.REGISTRY_KEY: ['HKLM\\X']}.
    """
    report = {"behavior": {"summary": summary}}
    registry = EntityRegistry(sample_id="sid")
    root_actor_id = registry.register_process(pid=100, first_seen_ord=0)

    if pre_registered:
        for etype, canons in pre_registered.items():
            for c in canons:
                if etype is EntityType.FILE:
                    registry.register_file(c)
                elif etype is EntityType.REGISTRY_KEY:
                    registry.register_registry_key(c)
                elif etype is EntityType.NAMED_PIPE:
                    registry.register_named_pipe(c)
                else:
                    raise ValueError(f"unsupported pre_registered type: {etype}")

    manifest: dict = {}
    events = extract_summary_fallbacks(
        report=report,
        registry=registry,
        ord_counter=_OrdCounter(),
        sample_id="sid",
        root_actor_id=root_actor_id,
        manifest=manifest,
    )
    return events, registry, manifest


# --- empty / malformed --------------------------------------------------

class TestEmptyAndMalformed:
    def test_empty_report(self):
        events, _, _ = _run({})
        assert events == []

    def test_summary_not_a_dict(self):
        report = {"behavior": {"summary": "junk"}}
        registry = EntityRegistry(sample_id="sid")
        root = registry.register_process(pid=100, first_seen_ord=0)
        events = extract_summary_fallbacks(
            report=report, registry=registry, ord_counter=_OrdCounter(),
            sample_id="sid", root_actor_id=root, manifest={},
        )
        assert events == []

    def test_field_not_a_list_skipped(self):
        events, _, _ = _run({"read_files": "not-a-list"})
        assert events == []

    def test_empty_and_whitespace_strings_dropped(self):
        events, _, manifest = _run({
            "read_files": ["", "   ", "\t"],
            "read_keys": ["", None, 42],  # type: ignore[list-item]
        })
        assert events == []
        # 3 file + 3 reg = 6 malformed
        assert manifest["filters"]["summary_malformed_dropped"] == 6

    def test_non_string_entries_dropped(self):
        events, _, _ = _run({"read_files": [123, None, ["nested"], {}]})  # type: ignore[list-item]
        assert events == []


# --- file fallbacks -----------------------------------------------------

class TestFileFallbacks:
    def test_read_files_emits_file_read(self):
        events, reg, _ = _run({
            "read_files": ["C:\\Windows\\System32\\kernel32.dll"],
        })
        assert len(events) == 1
        e = events[0]
        assert e.event_type is EventType.FILE_READ
        assert e.timestamp is None
        assert e.timestamp_source is TimestampSource.NONE
        assert e.metadata["source"] == "behavior.summary.read_files"
        assert e.metadata["attribution"] == "summary_fallback"
        assert reg.get(e.dst)["type"] == EntityType.FILE.value
        assert reg.get(e.dst)["canonical"] == "c:/windows/system32/kernel32.dll"

    def test_write_files_emits_file_write(self):
        events, _, _ = _run({"write_files": ["C:\\Temp\\x.bin"]})
        assert events[0].event_type is EventType.FILE_WRITE
        assert events[0].metadata["source"] == "behavior.summary.write_files"

    def test_delete_files_emits_file_delete(self):
        events, _, _ = _run({"delete_files": ["C:\\gone.exe"]})
        assert events[0].event_type is EventType.FILE_DELETE
        assert events[0].metadata["source"] == "behavior.summary.delete_files"

    def test_named_pipe_in_summary_routed_correctly(self):
        events, reg, _ = _run({
            "write_files": ["\\Device\\NamedPipe\\srvsvc"],
        })
        assert len(events) == 1
        assert reg.get(events[0].dst)["type"] == EntityType.NAMED_PIPE.value

    def test_actor_is_root_process(self):
        """All fallback events attributed to root process."""
        events, reg, _ = _run({
            "read_files": ["C:\\a"], "write_files": ["C:\\b"],
        })
        src_types = {reg.get(e.src)["type"] for e in events}
        assert src_types == {EntityType.PROCESS.value}
        # And all point at the SAME process (the root one).
        assert len({e.src for e in events}) == 1


# --- registry fallbacks -------------------------------------------------

class TestRegistryFallbacks:
    def test_read_keys_emits_reg_read(self):
        events, reg, _ = _run({
            "read_keys": ["HKEY_LOCAL_MACHINE\\SOFTWARE\\X"],
        })
        assert len(events) == 1
        e = events[0]
        assert e.event_type is EventType.REG_READ
        assert e.metadata["source"] == "behavior.summary.read_keys"
        assert e.metadata["attribution"] == "summary_fallback"
        assert reg.get(e.dst)["type"] == EntityType.REGISTRY_KEY.value

    def test_write_keys_emits_reg_write(self):
        events, _, _ = _run({"write_keys": ["HKLM\\X"]})
        assert events[0].event_type is EventType.REG_WRITE

    def test_delete_keys_emits_reg_delete(self):
        events, _, _ = _run({"delete_keys": ["HKLM\\X"]})
        assert events[0].event_type is EventType.REG_DELETE

    def test_registry_canonicalization(self):
        """Same key different case -> one event, one entity."""
        events, _, _ = _run({"read_keys": [
            "hkey_local_machine\\SOFTWARE\\X",
            "HKEY_LOCAL_MACHINE\\SOFTWARE\\X",
        ]})
        # Both canonicalize to the same key; second registration is a no-op
        # and has_canonical returns True, so second is dedup-dropped.
        assert len(events) == 1


# --- catch-all keys ignored --------------------------------------------

class TestCatchAllKeysIgnored:
    def test_files_catchall_ignored(self):
        """behavior.summary.files is intentionally NOT mapped to events
        (design decision A from commit 11 review)."""
        events, reg, _ = _run({
            "files": ["C:\\anything.exe"],
        })
        assert events == []
        # No entity registered either (only the root process).
        assert reg.count_by_type() == {"process": 1}

    def test_keys_catchall_ignored(self):
        events, _, _ = _run({"keys": ["HKLM\\whatever"]})
        assert events == []


# --- dedup against enhanced --------------------------------------------

class TestDedupAgainstEnhanced:
    def test_file_already_registered_is_dedup_dropped(self):
        """Simulate enhanced firing first by pre-registering the file.
        Summary fallback should skip it."""
        events, _, manifest = _run(
            summary={"read_files": ["C:\\x.exe"]},
            pre_registered={EntityType.FILE: ["c:/x.exe"]},  # canonical form
        )
        assert events == []
        assert manifest["filters"]["summary_dedup_dropped"] == 1

    def test_registry_key_already_registered_is_dedup_dropped(self):
        events, _, manifest = _run(
            summary={"read_keys": ["HKLM\\X"]},
            pre_registered={EntityType.REGISTRY_KEY: ["HKLM\\X"]},
        )
        assert events == []
        assert manifest["filters"]["summary_dedup_dropped"] == 1

    def test_partial_dedup_partial_emit(self):
        """Some summary entries match enhanced (dedup), others don't (emit)."""
        events, _, manifest = _run(
            summary={"read_files": [
                "C:\\x.exe",  # pre-registered, dedup
                "C:\\y.exe",  # not pre-registered, emit
            ]},
            pre_registered={EntityType.FILE: ["c:/x.exe"]},
        )
        assert len(events) == 1
        assert manifest["filters"]["summary_dedup_dropped"] == 1
        assert manifest["filters"]["summary_malformed_dropped"] == 0

    def test_file_and_directory_are_distinct_for_dedup(self):
        """An entity pre-registered as DIRECTORY does NOT dedup a summary
        FILE entry with the same canonical path (they're different
        entity types — per schema.md decision B)."""
        # Pre-register as DIRECTORY only (simulating enhanced create,dir).
        registry = EntityRegistry(sample_id="sid")
        registry.register_process(pid=100, first_seen_ord=0)
        registry.register_directory("c:/temp/foo")

        report = {"behavior": {"summary": {
            "read_files": ["C:\\Temp\\foo"],  # same path, but file context
        }}}
        events = extract_summary_fallbacks(
            report=report, registry=registry, ord_counter=_OrdCounter(),
            sample_id="sid", root_actor_id="dummy", manifest={},
        )
        assert len(events) == 1  # emitted: DIR pre-reg doesn't block FILE
        assert registry.get(events[0].dst)["type"] == EntityType.FILE.value

    def test_named_pipe_dedup(self):
        """Named-pipe paths dedup against NAMED_PIPE entities, not FILE."""
        events, _, manifest = _run(
            summary={"read_files": ["\\Device\\NamedPipe\\srvsvc"]},
            pre_registered={EntityType.NAMED_PIPE: ["/device/namedpipe/srvsvc"]},
        )
        assert events == []
        assert manifest["filters"]["summary_dedup_dropped"] == 1


# --- event invariants ---------------------------------------------------

class TestEventInvariants:
    def test_no_timestamps(self):
        events, _, _ = _run({"read_files": ["C:\\x.exe"]})
        for e in events:
            assert e.timestamp is None
            assert e.timestamp_source is TimestampSource.NONE

    def test_attribution_tag_present(self):
        events, _, _ = _run({
            "read_files": ["C:\\a"], "write_keys": ["HKLM\\b"],
        })
        for e in events:
            assert e.metadata["attribution"] == "summary_fallback"

    def test_source_tag_names_the_exact_field(self):
        events, _, _ = _run({
            "read_files": ["C:\\a"],
            "write_files": ["C:\\b"],
            "delete_keys": ["HKLM\\c"],
        })
        sources = {e.metadata["source"] for e in events}
        assert sources == {
            "behavior.summary.read_files",
            "behavior.summary.write_files",
            "behavior.summary.delete_keys",
        }

    def test_ord_is_monotonic(self):
        events, _, _ = _run({
            "read_files": ["C:\\a", "C:\\b", "C:\\c"],
            "read_keys": ["HKLM\\x", "HKLM\\y"],
        })
        ords = [e.ord for e in events]
        assert ords == sorted(ords)
        assert ords == list(range(len(events)))


# --- determinism --------------------------------------------------------

class TestDeterminism:
    def test_same_input_yields_identical_events(self):
        summary = {
            "read_files": ["C:\\a.exe", "C:\\b.exe"],
            "write_keys": ["HKLM\\X", "HKLM\\Y"],
        }
        e1, r1, _ = _run(summary)
        e2, r2, _ = _run(summary)
        assert [e.to_dict() for e in e1] == [e.to_dict() for e in e2]
        assert r1.to_manifest() == r2.to_manifest()


# --- real sample --------------------------------------------------------

@pytest.mark.skipif(
    not REAL_SAMPLE.exists(),
    reason="real sample not present in data/raw/",
)
class TestRealSample:
    """Integration against c1f6f86. The sample's summary has 2 registry
    keys that behavior.enhanced already covered. Both should be
    dedup-dropped, resulting in 0 new summary events."""

    def test_c1f6f86_summary_dedup_count(self):
        _, manifest = parse_report_with_manifest(REAL_SAMPLE)
        assert manifest["filters"]["summary_dedup_dropped"] == 2
        assert manifest["filters"]["summary_malformed_dropped"] == 0

    def test_c1f6f86_event_count_unchanged_by_summary(self):
        """Summary fallbacks add 0 events because all summary entries
        deduplicated against enhanced."""
        events, _ = parse_report_with_manifest(REAL_SAMPLE)
        # Still 1 process_spawn + 2 reg_read + 3 module_load = 6.
        assert len(events) == 6

    def test_c1f6f86_no_summary_fallback_events_emitted(self):
        events, _ = parse_report_with_manifest(REAL_SAMPLE)
        fallbacks = [
            e for e in events
            if e.metadata.get("attribution") == "summary_fallback"
        ]
        assert fallbacks == []
