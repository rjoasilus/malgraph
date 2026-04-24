"""
Tests for ingest.entities.

The core invariants under test:
1. ID stability within a sample — same input → same ID.
2. ID uniqueness across types — same canonical form, different type,
   produces different IDs.
3. Sample-scoping for processes — same PID in different samples
   produces different IDs.
4. Manifest ordering is stable and type-partitioned.
5. ID format: 12 lowercase hex characters.
"""
from __future__ import annotations

import re

import pytest

from ingest.entities import EntityRegistry, EntityType, ID_LENGTH


_HEX_ID_RE = re.compile(r"^[0-9a-f]{12}$")


# --- ID format ----------------------------------------------------------

class TestIdFormat:
    def test_id_length_constant_matches_hash_output(self):
        assert ID_LENGTH == 12

    def test_content_entity_id_is_12_hex(self):
        r = EntityRegistry(sample_id="s")
        eid = r.register_file("c:/x.exe")
        assert _HEX_ID_RE.match(eid), f"malformed id: {eid!r}"

    def test_process_id_is_12_hex(self):
        r = EntityRegistry(sample_id="s")
        eid = r.register_process(pid=100, first_seen_ord=0)
        assert _HEX_ID_RE.match(eid), f"malformed id: {eid!r}"


# --- ID stability within a sample ---------------------------------------

class TestStabilityWithinSample:
    def test_same_file_path_yields_same_id(self):
        r = EntityRegistry(sample_id="s")
        a = r.register_file("c:/windows/system32/cmd.exe")
        b = r.register_file("c:/windows/system32/cmd.exe")
        assert a == b

    def test_same_pid_yields_same_process_id_regardless_of_ord(self):
        """first_seen_ord is metadata, not part of the ID hash."""
        r = EntityRegistry(sample_id="s")
        a = r.register_process(pid=100, first_seen_ord=0)
        b = r.register_process(pid=100, first_seen_ord=99)
        assert a == b

    def test_first_registration_metadata_wins(self):
        """If the same process is registered twice, the first call's
        metadata (name, first_seen_ord) is retained."""
        r = EntityRegistry(sample_id="s")
        r.register_process(pid=100, first_seen_ord=0, name="first.exe")
        r.register_process(pid=100, first_seen_ord=99, name="second.exe")
        eid = r.register_process(pid=100, first_seen_ord=99, name="third.exe")
        meta = r.get(eid)
        assert meta["first_seen_ord"] == 0
        assert meta["canonical"] == "first.exe"

    @pytest.mark.parametrize("register_fn, inp", [
        ("register_file", "c:/x.exe"),
        ("register_directory", "c:/windows"),
        ("register_registry_key", "HKEY_LOCAL_MACHINE\\Software\\X"),
        ("register_domain", "example.com"),
        ("register_ip", "8.8.8.8"),
        ("register_named_pipe", "/device/namedpipe/srvsvc"),
        ("register_module", "user32.dll"),
        ("register_external", "sandbox_launcher"),
    ])
    def test_all_content_entities_stable_under_repeat(self, register_fn, inp):
        r = EntityRegistry(sample_id="s")
        fn = getattr(r, register_fn)
        assert fn(inp) == fn(inp)


# --- cross-type uniqueness ----------------------------------------------

class TestCrossTypeUniqueness:
    def test_same_canonical_different_type_distinct_ids(self):
        """A file named 'example.com' and a domain 'example.com' are
        different entities and must get different IDs."""
        r = EntityRegistry(sample_id="s")
        f = r.register_file("example.com")
        d = r.register_domain("example.com")
        assert f != d

    def test_file_vs_directory_distinct(self):
        r = EntityRegistry(sample_id="s")
        f = r.register_file("c:/windows")
        d = r.register_directory("c:/windows")
        assert f != d

    def test_registry_vs_file_distinct(self):
        r = EntityRegistry(sample_id="s")
        f = r.register_file("SOFTWARE\\Microsoft")
        k = r.register_registry_key("SOFTWARE\\Microsoft")
        assert f != k


# --- process sample-scoping ---------------------------------------------

class TestProcessSampleScoping:
    def test_same_pid_different_samples_distinct_ids(self):
        """PID 100 in sample A and PID 100 in sample B are different
        processes and must get different IDs."""
        ra = EntityRegistry(sample_id="sampleA")
        rb = EntityRegistry(sample_id="sampleB")
        pa = ra.register_process(pid=100, first_seen_ord=0)
        pb = rb.register_process(pid=100, first_seen_ord=0)
        assert pa != pb

    def test_different_pids_same_sample_distinct_ids(self):
        r = EntityRegistry(sample_id="s")
        a = r.register_process(pid=100, first_seen_ord=0)
        b = r.register_process(pid=200, first_seen_ord=1)
        assert a != b


# --- content-entity cross-sample stability ------------------------------

class TestContentCrossSampleStability:
    def test_same_file_path_across_samples_same_id(self):
        """Content-hashed entities are cross-sample stable by design —
        useful for cross-sample analysis in later sprints."""
        ra = EntityRegistry(sample_id="sampleA")
        rb = EntityRegistry(sample_id="sampleB")
        a = ra.register_file("c:/windows/system32/cmd.exe")
        b = rb.register_file("c:/windows/system32/cmd.exe")
        assert a == b

    def test_same_domain_across_samples_same_id(self):
        ra = EntityRegistry(sample_id="sampleA")
        rb = EntityRegistry(sample_id="sampleB")
        assert ra.register_domain("example.com") \
            == rb.register_domain("example.com")


# --- registry bookkeeping -----------------------------------------------

class TestRegistryBookkeeping:
    def test_len_counts_distinct_entities(self):
        r = EntityRegistry(sample_id="s")
        r.register_file("c:/a.exe")
        r.register_file("c:/b.exe")
        r.register_file("c:/a.exe")  # dup, no new entity
        r.register_domain("example.com")
        assert len(r) == 3

    def test_contains_reflects_registration(self):
        r = EntityRegistry(sample_id="s")
        eid = r.register_file("c:/a.exe")
        assert eid in r
        assert "deadbeef" * 3 not in r  # 24-char plausible-looking id, not registered

    def test_get_returns_metadata_or_none(self):
        r = EntityRegistry(sample_id="s")
        eid = r.register_file("c:/a.exe")
        meta = r.get(eid)
        assert meta is not None
        assert meta["type"] == EntityType.FILE.value
        assert meta["canonical"] == "c:/a.exe"
        assert r.get("000000000000") is None

    def test_count_by_type(self):
        r = EntityRegistry(sample_id="s")
        r.register_file("c:/a.exe")
        r.register_file("c:/b.exe")
        r.register_domain("example.com")
        r.register_process(pid=100, first_seen_ord=0)
        counts = r.count_by_type()
        assert counts == {"file": 2, "domain": 1, "process": 1}

    def test_empty_registry(self):
        r = EntityRegistry(sample_id="s")
        assert len(r) == 0
        assert r.count_by_type() == {}
        assert r.to_manifest() == []


# --- manifest ordering --------------------------------------------------

class TestManifestOrdering:
    def test_manifest_stable_across_insertion_order(self):
        """to_manifest() must return entities in a deterministic order
        regardless of the order they were registered in. This is the
        invariant that lets us diff two parser runs byte-for-byte."""
        r1 = EntityRegistry(sample_id="s")
        r1.register_file("c:/z.exe")
        r1.register_domain("example.com")
        r1.register_file("c:/a.exe")

        r2 = EntityRegistry(sample_id="s")
        r2.register_domain("example.com")
        r2.register_file("c:/a.exe")
        r2.register_file("c:/z.exe")

        assert r1.to_manifest() == r2.to_manifest()

    def test_manifest_grouped_by_type_then_canonical(self):
        r = EntityRegistry(sample_id="s")
        r.register_domain("z.example.com")
        r.register_domain("a.example.com")
        r.register_file("c:/z.exe")
        r.register_file("c:/a.exe")

        manifest = r.to_manifest()
        # All domains before all files (alphabetical by type).
        types_in_order = [row["type"] for row in manifest]
        assert types_in_order == ["domain", "domain", "file", "file"]
        # Within each type, alphabetical by canonical.
        canonicals = [row["canonical"] for row in manifest]
        assert canonicals == [
            "a.example.com", "z.example.com",
            "c:/a.exe", "c:/z.exe",
        ]

    def test_manifest_includes_all_fields(self):
        r = EntityRegistry(sample_id="s")
        eid = r.register_process(pid=100, first_seen_ord=5, name="root.exe")
        rows = r.to_manifest()
        assert len(rows) == 1
        row = rows[0]
        assert row["id"] == eid
        assert row["type"] == "process"
        assert row["canonical"] == "root.exe"
        assert row["pid"] == 100
        assert row["first_seen_ord"] == 5
