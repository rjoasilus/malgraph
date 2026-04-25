"""
End-to-end parser tests — the deliverable file named in the Sprint 1 PDF.

Exercises parse_report_with_manifest against the failure modes the
sprint exit criteria call out:
- corrupt JSON
- missing expected fields
- non-UTF-8 bytes
- empty report
- oversize report

Per-extractor behavior is covered in test_parser_*.py; this file
consolidates the PDF-required end-to-end scenarios so the exit
review has a single file to point at.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ingest.events import EventType, SCHEMA_VERSION
from ingest.parser import parse_report, parse_report_with_manifest


# --- fixtures -----------------------------------------------------------

def _write(tmp: Path, name: str, data: bytes | str) -> Path:
    """Write a fixture file; return its path."""
    p = tmp / name
    if isinstance(data, str):
        p.write_text(data, encoding="utf-8")
    else:
        p.write_bytes(data)
    return p


# --- PDF-mandated failure modes ----------------------------------------

class TestCorruptJson:
    def test_truncated_json_captured_in_manifest(self, tmp_path: Path):
        p = _write(tmp_path, "bad.json", '{"behavior": {"processes": ')
        events, manifest = parse_report_with_manifest(p)
        assert events == []
        assert manifest["parse_ok"] is False
        assert manifest["parse_error"] is not None
        assert "JSONDecodeError" in manifest["parse_error"]

    def test_garbage_bytes_captured_in_manifest(self, tmp_path: Path):
        p = _write(tmp_path, "garbage.json", b"this isn't JSON at all")
        events, manifest = parse_report_with_manifest(p)
        assert events == []
        assert manifest["parse_ok"] is False

    def test_corrupt_json_does_not_raise(self, tmp_path: Path):
        """Critical invariant for the batch runner: a corrupt input
        produces a failure manifest, never an exception."""
        p = _write(tmp_path, "bad.json", "{{{not json}}}")
        # If this raises, the test fails loudly.
        events = parse_report(p)
        assert events == []


class TestMissingFields:
    def test_empty_report_produces_no_events(self, tmp_path: Path):
        p = _write(tmp_path, "empty.json", "{}")
        events, manifest = parse_report_with_manifest(p)
        assert events == []
        assert manifest["parse_ok"] is True

    def test_missing_behavior_key(self, tmp_path: Path):
        p = _write(tmp_path, "nobehav.json",
                   json.dumps({"info": {"started": "2021-01-01 00:00:00"}}))
        events, manifest = parse_report_with_manifest(p)
        assert events == []
        assert manifest["parse_ok"] is True

    def test_missing_event_type_fields_in_enhanced(self, tmp_path: Path):
        """Sprint 1 PDF wording: 'missing event_type fields'. In our
        schema event_type is derived from (event, object) in enhanced
        entries — entries missing those keys should be skipped, not
        crash the parser."""
        report = {"behavior": {"enhanced": [
            {"event": "read"},               # missing object
            {"object": "registry"},          # missing event
            {"data": {"regkey": "X"}},       # missing both
        ]}}
        p = _write(tmp_path, "malformed.json", json.dumps(report))
        events, manifest = parse_report_with_manifest(p)
        assert manifest["parse_ok"] is True
        # No enhanced events emitted — all three entries are malformed.
        assert not any(
            e.metadata.get("source") == "behavior.enhanced"
            for e in events
        )

    def test_missing_info_started(self, tmp_path: Path):
        """info.started is deliberately NOT used as the time anchor
        (it's unreliable in Avast-CTU). Absence should not affect
        parsing."""
        report = {"behavior": {"processtree": [{"pid": 100}]}}
        p = _write(tmp_path, "noinfo.json", json.dumps(report))
        events, manifest = parse_report_with_manifest(p)
        assert manifest["parse_ok"] is True
        assert len(events) == 1
        assert events[0].event_type is EventType.PROCESS_SPAWN


class TestNonUtf8Bytes:
    def test_latin1_bytes_in_strings(self, tmp_path: Path):
        """CAPE samples can contain non-UTF-8 bytes in file paths.
        Our loader opens with 'rb' and json.load decodes — json spec
        requires UTF-8 so invalid bytes would be a JSONDecodeError."""
        # Construct valid JSON with a latin-1 char encoded as UTF-8.
        # This should parse cleanly.
        report = {"behavior": {"processtree": [{
            "pid": 100, "parent_id": 1, "name": "café.exe",
        }]}}
        p = _write(tmp_path, "utf8.json", json.dumps(report))
        events, manifest = parse_report_with_manifest(p)
        assert manifest["parse_ok"] is True
        assert events[0].metadata["name"] == "café.exe"

    def test_invalid_utf8_bytes_captured_as_parse_error(
        self, tmp_path: Path,
    ):
        """Raw invalid UTF-8 in the file yields a JSONDecodeError,
        captured in the manifest. Does not crash."""
        # Write bytes that aren't valid UTF-8.
        p = _write(tmp_path, "badbytes.json",
                   b'{"behavior": {"name": "\xff\xfe not utf-8"}}')
        events, manifest = parse_report_with_manifest(p)
        assert events == []
        assert manifest["parse_ok"] is False
        assert "UnicodeDecodeError" in manifest["parse_error"] \
            or "JSONDecodeError" in manifest["parse_error"]


class TestOversize:
    def test_deeply_nested_processtree(self, tmp_path: Path):
        """30-deep processtree — well below Python recursion limit
        but a proxy for 'oversize report' in the PDF's sense."""
        node = {"pid": 1000}
        for i in range(29):
            node = {"pid": 999 - i, "children": [node]}
        report = {"behavior": {
            "processes": [{"process_id": p} for p in range(970, 1001)],
            "processtree": [node],
        }}
        p = _write(tmp_path, "deep.json", json.dumps(report))
        events, manifest = parse_report_with_manifest(p)
        assert manifest["parse_ok"] is True
        assert len(events) == 30

    def test_large_enhanced_list(self, tmp_path: Path):
        """10,000 enhanced entries — exercises the iteration path
        (Sprint 0 Risk #2: behavior.enhanced can be 300+ in heavy
        samples; we want comfort beyond that)."""
        entries = [
            {"event": "read", "object": "registry",
             "data": {"regkey": f"Key_{i}"}, "eid": i}
            for i in range(10_000)
        ]
        report = {"behavior": {"enhanced": entries}}
        p = _write(tmp_path, "large.json", json.dumps(report))
        events, manifest = parse_report_with_manifest(p)
        assert manifest["parse_ok"] is True
        assert manifest["event_counts"].get("reg_read") == 10_000


# --- missing file / path cases -----------------------------------------

class TestMissingFile:
    def test_nonexistent_path_captured_in_manifest(self, tmp_path: Path):
        p = tmp_path / "does_not_exist.json"
        events, manifest = parse_report_with_manifest(p)
        assert events == []
        assert manifest["parse_ok"] is False
        assert "FileNotFoundError" in manifest["parse_error"]


# --- public API contract ------------------------------------------------

class TestPublicApi:
    def test_parse_report_returns_list(self, tmp_path: Path):
        p = _write(tmp_path, "empty.json", "{}")
        assert parse_report(p) == []

    def test_parse_report_accepts_string_path(self, tmp_path: Path):
        p = _write(tmp_path, "empty.json", "{}")
        assert parse_report(str(p)) == []

    def test_parse_report_accepts_path_object(self, tmp_path: Path):
        p = _write(tmp_path, "empty.json", "{}")
        assert parse_report(Path(p)) == []

    def test_manifest_always_populated_with_core_fields(
        self, tmp_path: Path,
    ):
        """Every manifest — ok or fail — carries the contract fields."""
        ok_path = _write(tmp_path, "ok.json", "{}")
        bad_path = _write(tmp_path, "bad.json", "{not json}")
        required = {"sample_id", "path", "parse_ok", "parse_error",
                    "event_counts", "entity_counts", "filters"}
        for p in (ok_path, bad_path):
            _, manifest = parse_report_with_manifest(p)
            missing = required - manifest.keys()
            assert missing == set(), \
                f"manifest missing fields for {p.name}: {missing}"


# --- output invariants vs PDF schema -----------------------------------

class TestOutputSchema:
    def test_events_roundtrip_jsonl(self, tmp_path: Path):
        """Every emitted event serializes and re-validates — the
        property batch runner relies on."""
        report = {"behavior": {
            "processes": [{"process_id": 100}],
            "processtree": [{"pid": 100, "parent_id": 1}],
            "enhanced": [{
                "event": "read", "object": "registry",
                "data": {"regkey": "X"}, "eid": 1,
            }],
        }}
        p = _write(tmp_path, "smoke.json", json.dumps(report))
        events = parse_report(p)
        for e in events:
            line = e.to_json()
            roundtrip = json.loads(line)
            # Required keys present.
            for key in ("sample_id", "ord", "event_type", "src", "dst",
                        "timestamp_source", "metadata"):
                assert key in roundtrip

    def test_sample_id_derived_from_target_when_available(
        self, tmp_path: Path,
    ):
        sha = "a" * 64
        report = {
            "target": {"file": {"sha256": sha}},
            "behavior": {"processtree": [{"pid": 100, "parent_id": 1}]},
        }
        p = _write(tmp_path, "named.json", json.dumps(report))
        events, manifest = parse_report_with_manifest(p)
        assert manifest["sample_id"] == sha
        assert events[0].sample_id == sha

    def test_sample_id_falls_back_to_filename_stem(self, tmp_path: Path):
        p = _write(tmp_path, "fallback_id.json", "{}")
        _, manifest = parse_report_with_manifest(p)
        assert manifest["sample_id"] == "fallback_id"

    def test_schema_version_tag(self):
        assert SCHEMA_VERSION == "1.1"
