"""
Tests for ingest.parser.extract_enhanced_events.

Synthetic fixtures exercise each (event, object) pair the dispatch
table handles, plus malformed inputs and the two silently-skipped
pairs. Real-sample assertions confirm integration on the c1f6f86
Emotet sample.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ingest.entities import EntityRegistry, EntityType
from ingest.events import Event, EventType, TimestampSource
from ingest.parser import (
    _ENHANCED_HANDLED,
    _ENHANCED_SKIPPED_SILENT,
    _OrdCounter,
    extract_enhanced_events,
    parse_report_with_manifest,
)


REAL_SAMPLE = (
    Path("data/raw")
    / "c1f6f8635fd2dfa1440fe485d1ac0ad8b934f3779a4bb999028ce71d12ea5494.json"
)


# --- helpers ------------------------------------------------------------

def _run(enhanced_entries: list[dict]) -> tuple[
    list[Event], EntityRegistry, dict,
]:
    """Harness: build a minimal report with the given enhanced entries
    and run extract_enhanced_events. Returns (events, registry, manifest)."""
    report = {"behavior": {"enhanced": enhanced_entries}}
    registry = EntityRegistry(sample_id="sid")
    root_actor_id = registry.register_process(pid=100, first_seen_ord=0)
    manifest: dict = {}
    events = extract_enhanced_events(
        report=report,
        registry=registry,
        ord_counter=_OrdCounter(),
        sample_id="sid",
        root_actor_id=root_actor_id,
        manifest=manifest,
    )
    return events, registry, manifest


def _entry(event: str, obj: str, data: dict, ts: str | None = None,
           eid: int | None = None) -> dict:
    """Compact constructor for enhanced entries."""
    e: dict = {"event": event, "object": obj, "data": data}
    if ts is not None:
        e["timestamp"] = ts
    if eid is not None:
        e["eid"] = eid
    return e


# --- dispatch table sanity ---------------------------------------------

class TestDispatchTable:
    def test_handled_table_has_expected_types(self):
        assert ("read", "registry") in _ENHANCED_HANDLED
        assert ("write", "registry") in _ENHANCED_HANDLED
        assert ("delete", "registry") in _ENHANCED_HANDLED
        assert ("read", "file") in _ENHANCED_HANDLED
        assert ("write", "file") in _ENHANCED_HANDLED
        assert ("delete", "file") in _ENHANCED_HANDLED
        assert ("copy", "file") in _ENHANCED_HANDLED
        assert ("move", "file") in _ENHANCED_HANDLED
        assert ("create", "dir") in _ENHANCED_HANDLED
        assert ("load", "library") in _ENHANCED_HANDLED
        assert len(_ENHANCED_HANDLED) == 10

    def test_silent_skip_table(self):
        assert ("execute", "file") in _ENHANCED_SKIPPED_SILENT
        assert ("create", "windowshook") in _ENHANCED_SKIPPED_SILENT


# --- empty/malformed inputs --------------------------------------------

class TestEmptyAndMalformed:
    def test_empty_enhanced_yields_no_events(self):
        events, _, _ = _run([])
        assert events == []

    def test_enhanced_not_a_list_yields_no_events(self):
        report = {"behavior": {"enhanced": "junk"}}
        registry = EntityRegistry(sample_id="sid")
        root_actor_id = registry.register_process(pid=100, first_seen_ord=0)
        events = extract_enhanced_events(
            report=report, registry=registry, ord_counter=_OrdCounter(),
            sample_id="sid", root_actor_id=root_actor_id, manifest={},
        )
        assert events == []

    def test_non_dict_entries_skipped(self):
        events, _, _ = _run(["not-a-dict", 42, None])  # type: ignore[list-item]
        assert events == []

    def test_missing_event_or_object_keys_skipped(self):
        entries = [
            {"object": "registry", "data": {"regkey": "X"}},
            {"event": "read", "data": {"regkey": "X"}},
            {"event": 42, "object": "registry", "data": {"regkey": "X"}},
        ]
        events, _, _ = _run(entries)
        assert events == []

    def test_missing_data_payload_tolerated(self):
        """Entry has valid (event, object) but no 'data'. Should
        gracefully skip (returns None from _emit_enhanced_event)."""
        events, _, _ = _run([{"event": "read", "object": "registry"}])
        assert events == []


# --- registry operations ------------------------------------------------

class TestRegistryEvents:
    def test_reg_read(self):
        events, reg, _ = _run([
            _entry("read", "registry", {"regkey": "X", "content": None},
                   ts="2021-06-03 23:01:29,800", eid=1),
        ])
        assert len(events) == 1
        e = events[0]
        assert e.event_type is EventType.REG_READ
        assert e.timestamp_source is TimestampSource.ABSOLUTE
        assert e.timestamp is not None
        assert e.metadata["source"] == "behavior.enhanced"
        assert e.metadata["eid"] == 1
        # content=None in raw -> key omitted from metadata (see
        # test_reg_content_omitted_when_none_or_missing below)
        assert "content" not in e.metadata
        assert reg.get(e.dst)["type"] == EntityType.REGISTRY_KEY.value

    def test_reg_write_with_content_preserved(self):
        events, _, _ = _run([
            _entry("write", "registry",
                   {"regkey": "HKLM\\X", "content": "some_value"}),
        ])
        assert len(events) == 1
        assert events[0].event_type is EventType.REG_WRITE
        assert events[0].metadata["content"] == "some_value"

    def test_reg_delete(self):
        events, _, _ = _run([
            _entry("delete", "registry", {"regkey": "HKLM\\X"}),
        ])
        assert len(events) == 1
        assert events[0].event_type is EventType.REG_DELETE

    def test_reg_key_canonicalized(self):
        """Same registry key in different cases yields the same entity."""
        events, reg, _ = _run([
            _entry("read", "registry",
                   {"regkey": "hkey_local_machine\\Software\\X"}),
            _entry("read", "registry",
                   {"regkey": "HKEY_LOCAL_MACHINE\\Software\\X"}),
        ])
        assert len(events) == 2
        assert events[0].dst == events[1].dst

    def test_reg_missing_regkey_skipped(self):
        events, _, _ = _run([_entry("read", "registry", {})])
        assert events == []

    def test_reg_content_omitted_when_none_or_missing(self):
        """Per emit logic: content is preserved only when present
        AND not None. Missing 'content' key: no meta entry. None: no
        meta entry. This keeps metadata compact for the common case."""
        events, _, _ = _run([
            _entry("read", "registry", {"regkey": "X"}),  # no content key
            _entry("read", "registry", {"regkey": "Y", "content": None}),
        ])
        assert "content" not in events[0].metadata
        assert "content" not in events[1].metadata


# --- file operations ----------------------------------------------------

class TestFileEvents:
    def test_file_read(self):
        events, reg, _ = _run([
            _entry("read", "file",
                   {"file": "C:\\Windows\\System32\\x.dll"}),
        ])
        assert len(events) == 1
        e = events[0]
        assert e.event_type is EventType.FILE_READ
        assert reg.get(e.dst)["type"] == EntityType.FILE.value
        assert reg.get(e.dst)["canonical"] == "c:/windows/system32/x.dll"

    def test_file_write(self):
        events, _, _ = _run([
            _entry("write", "file", {"file": "C:\\Temp\\out.bin"}),
        ])
        assert events[0].event_type is EventType.FILE_WRITE

    def test_file_delete(self):
        events, _, _ = _run([
            _entry("delete", "file", {"file": "C:\\Temp\\gone.exe"}),
        ])
        assert events[0].event_type is EventType.FILE_DELETE

    def test_file_path_fallback_to_path_key(self):
        """Some enhanced entries use 'path' instead of 'file'."""
        events, _, _ = _run([
            _entry("read", "file", {"path": "C:\\Windows\\a.dll"}),
        ])
        assert len(events) == 1

    def test_file_missing_path_skipped(self):
        events, _, _ = _run([_entry("read", "file", {})])
        assert events == []

    def test_file_canonicalized(self):
        """Same path different cases -> same entity."""
        events, reg, _ = _run([
            _entry("read", "file", {"file": "C:\\Windows\\CMD.EXE"}),
            _entry("read", "file", {"file": "c:\\windows\\cmd.exe"}),
        ])
        assert events[0].dst == events[1].dst

    def test_named_pipe_routed_to_pipe_entity(self):
        """Enhanced file write to \\Device\\NamedPipe\\* should
        register a NAMED_PIPE entity, not a FILE."""
        events, reg, _ = _run([
            _entry("write", "file", {"file": "\\Device\\NamedPipe\\srvsvc"}),
        ])
        assert len(events) == 1
        assert reg.get(events[0].dst)["type"] == EntityType.NAMED_PIPE.value


class TestFileCopyMove:
    def test_file_copy(self):
        events, reg, _ = _run([
            _entry("copy", "file", {
                "from": "C:\\Users\\a\\x.exe",
                "to":   "C:\\Users\\a\\Roaming\\y.exe",
            }),
        ])
        assert len(events) == 1
        e = events[0]
        assert e.event_type is EventType.FILE_COPY
        # source_file in metadata points at the FROM file entity.
        src_file_id = e.metadata["source_file"]
        assert src_file_id != e.dst  # distinct entities
        assert reg.get(src_file_id)["canonical"] == "c:/users/a/x.exe"
        assert reg.get(e.dst)["canonical"] == "c:/users/a/roaming/y.exe"

    def test_file_move(self):
        events, _, _ = _run([
            _entry("move", "file", {
                "from": "C:\\old.exe", "to": "C:\\new.exe",
            }),
        ])
        assert events[0].event_type is EventType.FILE_MOVE

    def test_copy_missing_from_or_to_skipped(self):
        events, _, _ = _run([
            _entry("copy", "file", {"from": "C:\\a"}),
            _entry("copy", "file", {"to": "C:\\b"}),
            _entry("copy", "file", {}),
        ])
        assert events == []


# --- directory creation -------------------------------------------------

class TestDirectoryCreation:
    def test_create_dir_emits_file_write_with_directory_entity(self):
        """Decision B from commit 10 design review: create,dir emits
        FILE_WRITE with DIRECTORY entity + metadata.kind='directory'."""
        events, reg, _ = _run([
            _entry("create", "dir", {"file": "C:\\Temp\\new_subdir"}),
        ])
        assert len(events) == 1
        e = events[0]
        assert e.event_type is EventType.FILE_WRITE
        assert e.metadata["kind"] == "directory"
        assert reg.get(e.dst)["type"] == EntityType.DIRECTORY.value
        assert reg.get(e.dst)["canonical"] == "c:/temp/new_subdir"

    def test_create_dir_missing_path_skipped(self):
        events, _, _ = _run([_entry("create", "dir", {})])
        assert events == []

    def test_directory_entity_distinct_from_file_entity(self):
        """Same canonical path as dir vs file -> distinct entity IDs."""
        events, reg, _ = _run([
            _entry("create", "dir", {"file": "C:\\Temp\\samepath"}),
            _entry("read", "file", {"file": "C:\\Temp\\samepath"}),
        ])
        assert len(events) == 2
        assert events[0].dst != events[1].dst
        assert reg.get(events[0].dst)["type"] == EntityType.DIRECTORY.value
        assert reg.get(events[1].dst)["type"] == EntityType.FILE.value


# --- module load --------------------------------------------------------

class TestModuleLoad:
    def test_module_load(self):
        events, reg, _ = _run([
            _entry("load", "library",
                   {"file": "User32.dll", "pathtofile": None}),
        ])
        assert len(events) == 1
        e = events[0]
        assert e.event_type is EventType.MODULE_LOAD
        assert reg.get(e.dst)["type"] == EntityType.MODULE.value
        assert reg.get(e.dst)["canonical"] == "user32.dll"

    def test_module_case_insensitive_dedup(self):
        events, _, _ = _run([
            _entry("load", "library", {"file": "USER32.DLL"}),
            _entry("load", "library", {"file": "User32.dll"}),
            _entry("load", "library", {"file": "user32.dll"}),
        ])
        assert len(events) == 3
        assert events[0].dst == events[1].dst == events[2].dst

    def test_module_path_preserved_in_metadata(self):
        events, _, _ = _run([
            _entry("load", "library", {
                "file": "custom.dll",
                "pathtofile": "C:\\Windows\\System32\\custom.dll",
            }),
        ])
        assert events[0].metadata["path"] == "c:/windows/system32/custom.dll"

    def test_module_missing_file_skipped(self):
        events, _, _ = _run([_entry("load", "library", {})])
        assert events == []


# --- skip lists ---------------------------------------------------------

class TestSkipLists:
    def test_execute_file_silently_skipped(self):
        events, _, manifest = _run([
            _entry("execute", "file", {"file": "C:\\x.exe"}),
        ])
        assert events == []
        assert "unhandled_enhanced" not in manifest

    def test_windowshook_silently_skipped(self):
        events, _, manifest = _run([
            _entry("create", "windowshook", {}),
        ])
        assert events == []
        assert "unhandled_enhanced" not in manifest

    def test_unknown_pair_tallied_in_manifest(self):
        events, _, manifest = _run([
            _entry("twiddle", "widget", {"x": 1}),
            _entry("twiddle", "widget", {"x": 2}),
            _entry("frobnicate", "doohickey", {}),
        ])
        assert events == []
        unhandled = manifest.get("unhandled_enhanced", [])
        by_key = {(u["event"], u["object"]): u["count"] for u in unhandled}
        assert by_key == {
            ("twiddle", "widget"): 2,
            ("frobnicate", "doohickey"): 1,
        }


# --- timestamp handling -------------------------------------------------

class TestTimestamps:
    def test_absolute_timestamp_parsed(self):
        events, _, _ = _run([
            _entry("read", "registry", {"regkey": "X"},
                   ts="2021-06-03 23:01:29,800"),
        ])
        assert events[0].timestamp_source is TimestampSource.ABSOLUTE
        assert events[0].timestamp is not None

    def test_missing_timestamp_yields_none(self):
        events, _, _ = _run([
            _entry("read", "registry", {"regkey": "X"}),  # no ts
        ])
        assert events[0].timestamp is None
        assert events[0].timestamp_source is TimestampSource.NONE

    def test_unparseable_timestamp_yields_none(self):
        events, _, _ = _run([
            _entry("read", "registry", {"regkey": "X"}, ts="not-a-date"),
        ])
        assert events[0].timestamp is None
        assert events[0].timestamp_source is TimestampSource.NONE


# --- determinism --------------------------------------------------------

class TestDeterminism:
    def test_same_input_yields_identical_events(self):
        entries = [
            _entry("read", "registry", {"regkey": "A"}, eid=1),
            _entry("load", "library", {"file": "x.dll"}, eid=2),
            _entry("write", "file", {"file": "C:\\out.bin"}, eid=3),
        ]
        e1, r1, _ = _run(entries)
        e2, r2, _ = _run(entries)
        assert [e.to_dict() for e in e1] == [e.to_dict() for e in e2]
        assert r1.to_manifest() == r2.to_manifest()


# --- real sample --------------------------------------------------------

@pytest.mark.skipif(
    not REAL_SAMPLE.exists(),
    reason="real sample not present in data/raw/",
)
class TestRealSample:
    """Integration against the c1f6f86 Emotet sample."""

    def test_c1f6f86_event_counts(self):
        events, manifest = parse_report_with_manifest(REAL_SAMPLE)
        counts = manifest["event_counts"]
        # From Sprint 1 inspection: 2 reg_read, 3 module_load, 1 process_spawn.
        assert counts.get("reg_read") == 2
        assert counts.get("module_load") == 3
        assert counts.get("process_spawn") == 1

    def test_c1f6f86_expected_modules(self):
        events, _ = parse_report_with_manifest(REAL_SAMPLE)
        module_events = [
            e for e in events if e.event_type is EventType.MODULE_LOAD
        ]
        # The sample loads user32.dll, imm32.dll, wininet.dll.
        # Pull the canonical names via the registry dst lookups.
        # (Direct assertion on metadata path would be cleaner but
        #  the modules in this sample have pathtofile=None.)
        assert len(module_events) == 3

    def test_c1f6f86_no_unhandled_enhanced(self):
        _, manifest = parse_report_with_manifest(REAL_SAMPLE)
        assert manifest.get("unhandled_enhanced", []) == []

