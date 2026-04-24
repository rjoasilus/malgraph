"""
Tests for ingest.parser.extract_dns_events.

Core behaviors under test:
- suricata.dns is primary (has timestamps), network.dns supplements
  only domains not already in suricata.
- suricata type=="answer" records are silently skipped.
- Sandbox-probe domain filter drops CAPE's liveness probe.
- No event-level dedup (beaconing preservation), but union-level
  dedup between suricata and network.dns.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ingest.entities import EntityRegistry, EntityType
from ingest.events import Event, EventType, TimestampSource
from ingest.parser import (
    _OrdCounter,
    extract_dns_events,
    parse_report_with_manifest,
)


REAL_SAMPLE = (
    Path("data/raw")
    / "c1f6f8635fd2dfa1440fe485d1ac0ad8b934f3779a4bb999028ce71d12ea5494.json"
)


# --- helpers ------------------------------------------------------------

def _run(
    suricata_dns: list | None = None,
    network_dns: list | None = None,
) -> tuple[list[Event], EntityRegistry, dict]:
    """Build a minimal report with the given suricata/network DNS lists."""
    report: dict = {}
    if suricata_dns is not None:
        report["suricata"] = {"dns": suricata_dns}
    if network_dns is not None:
        report["network"] = {"dns": network_dns}
    registry = EntityRegistry(sample_id="sid")
    root_actor_id = registry.register_process(pid=100, first_seen_ord=0)
    manifest: dict = {}
    events = extract_dns_events(
        report=report, registry=registry, ord_counter=_OrdCounter(),
        sample_id="sid", root_actor_id=root_actor_id, manifest=manifest,
    )
    return events, registry, manifest


def _sur_query(rrname: str, rrtype: str = "A",
               timestamp: str | None = None) -> dict:
    """Compact suricata.dns query record."""
    rec: dict = {"type": "query", "rrname": rrname, "rrtype": rrtype}
    if timestamp is not None:
        rec["timestamp"] = timestamp
    return rec


def _sur_answer(rrname: str, rrtype: str = "A") -> dict:
    """Compact suricata.dns answer record (should be skipped)."""
    return {"type": "answer", "rrname": rrname, "rrtype": rrtype}


def _net_query(request: str, qtype: str = "A") -> dict:
    """Compact network.dns record."""
    return {"request": request, "type": qtype}


# --- empty / malformed --------------------------------------------------

class TestEmptyAndMalformed:
    def test_empty_report(self):
        report: dict = {}
        registry = EntityRegistry(sample_id="sid")
        root = registry.register_process(pid=100, first_seen_ord=0)
        events = extract_dns_events(
            report=report, registry=registry, ord_counter=_OrdCounter(),
            sample_id="sid", root_actor_id=root, manifest={},
        )
        assert events == []

    def test_suricata_not_a_dict(self):
        report = {"suricata": "junk"}
        registry = EntityRegistry(sample_id="sid")
        root = registry.register_process(pid=100, first_seen_ord=0)
        events = extract_dns_events(
            report=report, registry=registry, ord_counter=_OrdCounter(),
            sample_id="sid", root_actor_id=root, manifest={},
        )
        assert events == []

    def test_network_not_a_dict(self):
        report = {"network": "junk"}
        registry = EntityRegistry(sample_id="sid")
        root = registry.register_process(pid=100, first_seen_ord=0)
        events = extract_dns_events(
            report=report, registry=registry, ord_counter=_OrdCounter(),
            sample_id="sid", root_actor_id=root, manifest={},
        )
        assert events == []

    def test_suricata_dns_not_a_list(self):
        report = {"suricata": {"dns": "junk"}}
        registry = EntityRegistry(sample_id="sid")
        root = registry.register_process(pid=100, first_seen_ord=0)
        events = extract_dns_events(
            report=report, registry=registry, ord_counter=_OrdCounter(),
            sample_id="sid", root_actor_id=root, manifest={},
        )
        assert events == []

    def test_non_dict_records_malformed(self):
        events, _, manifest = _run(
            suricata_dns=["not-a-dict", 42],
            network_dns=[None],
        )
        assert events == []
        assert manifest["filters"]["dns_dropped_malformed"] == 3

    def test_missing_domain_fields_malformed(self):
        events, _, manifest = _run(
            suricata_dns=[{"type": "query", "rrtype": "A"}],  # no rrname
            network_dns=[{"type": "A"}],                        # no request
        )
        assert events == []
        assert manifest["filters"]["dns_dropped_malformed"] == 2

    def test_empty_domain_malformed(self):
        events, _, manifest = _run(
            suricata_dns=[_sur_query(""), _sur_query("   ")],
            network_dns=[_net_query(""), _net_query("\t")],
        )
        assert events == []
        assert manifest["filters"]["dns_dropped_malformed"] == 4


# --- suricata type filtering -------------------------------------------

class TestSuricataTypeFiltering:
    def test_query_emitted(self):
        events, _, _ = _run(suricata_dns=[_sur_query("example.com")])
        assert len(events) == 1
        assert events[0].event_type is EventType.DNS_QUERY

    def test_answer_silently_skipped(self):
        """Design decision A: answer records duplicate the query's
        domain — skip them without tally."""
        events, _, manifest = _run(suricata_dns=[_sur_answer("example.com")])
        assert events == []
        # Not counted as malformed or sandbox-dropped.
        assert manifest["filters"]["dns_dropped_malformed"] == 0
        assert manifest["filters"]["dns_dropped_sandbox"] == 0

    def test_missing_type_treated_as_not_query(self):
        """Defensive: suricata entry without 'type' key is not a query
        we should emit."""
        events, _, _ = _run(suricata_dns=[
            {"rrname": "example.com", "rrtype": "A"},
        ])
        assert events == []

    def test_mixed_query_and_answer(self):
        events, _, _ = _run(suricata_dns=[
            _sur_query("a.example.com"),
            _sur_answer("a.example.com"),
            _sur_query("b.example.com"),
            _sur_answer("b.example.com"),
        ])
        assert len(events) == 2


# --- suricata timestamps ------------------------------------------------

class TestSuricataTimestamps:
    def test_suricata_timestamp_parsed_absolute(self):
        events, _, _ = _run(suricata_dns=[
            _sur_query("example.com",
                       timestamp="2021-07-22T22:57:04.995386+0000"),
        ])
        e = events[0]
        assert e.timestamp is not None
        assert e.timestamp_source is TimestampSource.ABSOLUTE

    def test_missing_timestamp_yields_none(self):
        events, _, _ = _run(suricata_dns=[_sur_query("example.com")])
        e = events[0]
        assert e.timestamp is None
        assert e.timestamp_source is TimestampSource.NONE


# --- network.dns fallback ----------------------------------------------

class TestNetworkDnsFallback:
    def test_network_dns_only_emits_without_timestamp(self):
        events, _, _ = _run(network_dns=[_net_query("example.com")])
        assert len(events) == 1
        e = events[0]
        assert e.event_type is EventType.DNS_QUERY
        assert e.timestamp is None
        assert e.timestamp_source is TimestampSource.NONE
        assert e.metadata["source"] == "network.dns"

    def test_network_dns_preserves_query_type(self):
        events, _, _ = _run(network_dns=[_net_query("x.com", qtype="AAAA")])
        assert events[0].metadata["query_type"] == "AAAA"


# --- union semantics between suricata and network ----------------------

class TestUnionSemantics:
    def test_domain_in_both_emitted_once_from_suricata(self):
        """suricata takes precedence: if a domain appears in both,
        only the suricata event is emitted (has timestamp)."""
        events, _, _ = _run(
            suricata_dns=[_sur_query("example.com",
                                     timestamp="2021-07-22T22:57:04+0000")],
            network_dns=[_net_query("example.com")],
        )
        assert len(events) == 1
        assert events[0].metadata["source"] == "suricata.dns"
        assert events[0].timestamp is not None

    def test_domain_only_in_network_emitted_as_fallback(self):
        events, _, _ = _run(
            suricata_dns=[_sur_query("a.example.com")],
            network_dns=[_net_query("b.example.com")],  # not in suricata
        )
        assert len(events) == 2
        sources = {e.metadata["source"] for e in events}
        assert sources == {"suricata.dns", "network.dns"}

    def test_union_case_insensitive(self):
        """Dedup is at canonicalized-domain level, so case differences
        don't cause double emit."""
        events, _, _ = _run(
            suricata_dns=[_sur_query("Example.COM")],
            network_dns=[_net_query("example.com")],
        )
        assert len(events) == 1
        assert events[0].metadata["source"] == "suricata.dns"


# --- sandbox domain filter ---------------------------------------------

class TestSandboxDomainFilter:
    def test_cape_probe_dropped_from_suricata(self):
        events, _, manifest = _run(suricata_dns=[
            _sur_query("google-public-dns-a.google.com"),
        ])
        assert events == []
        assert manifest["filters"]["dns_dropped_sandbox"] == 1

    def test_cape_probe_dropped_from_network_dns(self):
        events, _, manifest = _run(network_dns=[
            _net_query("google-public-dns-a.google.com"),
        ])
        assert events == []
        assert manifest["filters"]["dns_dropped_sandbox"] == 1

    def test_cape_probe_dropped_case_insensitive(self):
        events, _, manifest = _run(suricata_dns=[
            _sur_query("GOOGLE-PUBLIC-DNS-A.GOOGLE.COM"),
        ])
        assert events == []
        assert manifest["filters"]["dns_dropped_sandbox"] == 1


# --- beaconing preservation --------------------------------------------

class TestBeaconingPreservation:
    def test_repeated_query_all_emitted(self):
        """Repeated queries to same domain -> multiple events (for
        beaconing / retry-pattern detection), single domain entity."""
        events, reg, _ = _run(suricata_dns=[
            _sur_query("c2.evil.com", timestamp="2021-07-22T22:57:01+0000"),
            _sur_query("c2.evil.com", timestamp="2021-07-22T22:57:02+0000"),
            _sur_query("c2.evil.com", timestamp="2021-07-22T22:57:03+0000"),
        ])
        assert len(events) == 3
        # All point at the same domain entity.
        assert events[0].dst == events[1].dst == events[2].dst
        # Distinct timestamps preserved.
        assert len({e.timestamp for e in events}) == 3
        assert reg.count_by_type().get("domain") == 1


# --- event shape -------------------------------------------------------

class TestEventShape:
    def test_dst_is_domain_entity(self):
        events, reg, _ = _run(suricata_dns=[_sur_query("example.com")])
        assert reg.get(events[0].dst)["type"] == EntityType.DOMAIN.value
        assert reg.get(events[0].dst)["canonical"] == "example.com"

    def test_domain_canonicalized(self):
        events, reg, _ = _run(suricata_dns=[
            _sur_query("Example.COM."),   # uppercase + trailing dot
            _sur_query("example.com"),
        ])
        # Should canonicalize to the same entity, so second entry
        # is dedup'd at the suricata-domains level (one event).
        # Actually: both are emitted because both are suricata queries
        # and we don't dedup events from within suricata itself —
        # only cross-source.
        assert len(events) == 2
        assert events[0].dst == events[1].dst

    def test_src_is_root_actor(self):
        events, reg, _ = _run(suricata_dns=[_sur_query("example.com")])
        assert reg.get(events[0].src)["type"] == EntityType.PROCESS.value

    def test_metadata_query_type_from_rrtype(self):
        events, _, _ = _run(suricata_dns=[_sur_query("x.com", rrtype="MX")])
        assert events[0].metadata["query_type"] == "MX"

    def test_metadata_source_tag(self):
        events, _, _ = _run(
            suricata_dns=[_sur_query("a.com")],
            network_dns=[_net_query("b.com")],
        )
        by_source = {e.metadata["source"] for e in events}
        assert by_source == {"suricata.dns", "network.dns"}


# --- ord monotonicity ---------------------------------------------------

class TestOrdMonotonic:
    def test_ord_monotonic_across_sources(self):
        """ord increments across both suricata and network entries."""
        events, _, _ = _run(
            suricata_dns=[_sur_query("a.com"), _sur_query("b.com")],
            network_dns=[_net_query("c.com")],
        )
        ords = [e.ord for e in events]
        assert ords == sorted(ords)
        assert ords == list(range(len(events)))


# --- determinism -------------------------------------------------------

class TestDeterminism:
    def test_same_input_yields_identical_events(self):
        sur = [
            _sur_query("a.com", timestamp="2021-07-22T22:57:01+0000"),
            _sur_query("b.com", timestamp="2021-07-22T22:57:02+0000"),
        ]
        net = [_net_query("c.com")]
        e1, r1, _ = _run(suricata_dns=sur, network_dns=net)
        e2, r2, _ = _run(suricata_dns=sur, network_dns=net)
        assert [e.to_dict() for e in e1] == [e.to_dict() for e in e2]
        assert r1.to_manifest() == r2.to_manifest()


# --- real sample --------------------------------------------------------

@pytest.mark.skipif(
    not REAL_SAMPLE.exists(),
    reason="real sample not present in data/raw/",
)
class TestRealSample:
    """c1f6f86's DNS activity is the CAPE probe only — should be
    filtered out, zero emitted events."""

    def test_c1f6f86_probe_dropped(self):
        _, manifest = parse_report_with_manifest(REAL_SAMPLE)
        assert manifest["filters"]["dns_dropped_sandbox"] == 1
        assert manifest["filters"]["dns_dropped_malformed"] == 0

    def test_c1f6f86_no_dns_events_emitted(self):
        events, _ = parse_report_with_manifest(REAL_SAMPLE)
        dns = [e for e in events if e.event_type is EventType.DNS_QUERY]
        assert dns == []

    def test_c1f6f86_no_domain_entities_registered(self):
        """No non-sandbox DNS in the sample -> no domain entities."""
        _, manifest = parse_report_with_manifest(REAL_SAMPLE)
        assert "domain" not in manifest["entity_counts"]
