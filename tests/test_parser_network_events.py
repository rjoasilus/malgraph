"""
Tests for ingest.parser.extract_network_events.

Synthetic fixtures exercise sandbox-IP filtering, the beaconing
preservation design (no dedup of repeated connections), malformed
records, and IPv6 handling. Real-sample tests confirm the filter
handles c1f6f86's 184 sandbox connections correctly.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ingest.entities import EntityRegistry, EntityType
from ingest.events import Event, EventType, TimestampSource
from ingest.parser import (
    _OrdCounter,
    extract_network_events,
    parse_report_with_manifest,
)


REAL_SAMPLE = (
    Path("data/raw")
    / "c1f6f8635fd2dfa1440fe485d1ac0ad8b934f3779a4bb999028ce71d12ea5494.json"
)


# --- helpers ------------------------------------------------------------

def _run(tcp: list | None = None, udp: list | None = None) -> tuple[
    list[Event], EntityRegistry, dict,
]:
    """Build a minimal report with the given tcp/udp lists."""
    network: dict = {}
    if tcp is not None:
        network["tcp"] = tcp
    if udp is not None:
        network["udp"] = udp
    report = {"network": network}
    registry = EntityRegistry(sample_id="sid")
    root_actor_id = registry.register_process(pid=100, first_seen_ord=0)
    manifest: dict = {}
    events = extract_network_events(
        report=report, registry=registry, ord_counter=_OrdCounter(),
        sample_id="sid", root_actor_id=root_actor_id, manifest=manifest,
    )
    return events, registry, manifest


def _conn(dst: str, dport: int = 80, sport: int = 49732,
          time: float | None = 1.5, proto_src: str = "dst") -> dict:
    """Compact connection record. proto_src = 'dst' or 'ip' (key name)."""
    record: dict = {proto_src: dst, "dport": dport, "sport": sport}
    if time is not None:
        record["time"] = time
    return record


# --- empty / malformed --------------------------------------------------

class TestEmptyAndMalformed:
    def test_empty_report(self):
        report: dict = {}
        registry = EntityRegistry(sample_id="sid")
        root = registry.register_process(pid=100, first_seen_ord=0)
        events = extract_network_events(
            report=report, registry=registry, ord_counter=_OrdCounter(),
            sample_id="sid", root_actor_id=root, manifest={},
        )
        assert events == []

    def test_network_not_a_dict(self):
        report = {"network": "junk"}
        registry = EntityRegistry(sample_id="sid")
        root = registry.register_process(pid=100, first_seen_ord=0)
        events = extract_network_events(
            report=report, registry=registry, ord_counter=_OrdCounter(),
            sample_id="sid", root_actor_id=root, manifest={},
        )
        assert events == []

    def test_tcp_and_udp_both_missing(self):
        events, _, _ = _run()
        assert events == []

    def test_tcp_not_a_list(self):
        report = {"network": {"tcp": "junk"}}
        registry = EntityRegistry(sample_id="sid")
        root = registry.register_process(pid=100, first_seen_ord=0)
        events = extract_network_events(
            report=report, registry=registry, ord_counter=_OrdCounter(),
            sample_id="sid", root_actor_id=root, manifest={},
        )
        assert events == []

    def test_non_dict_record_tallied_malformed(self):
        events, _, manifest = _run(tcp=["not-a-dict", 42, None])  # type: ignore[list-item]
        assert events == []
        assert manifest["filters"]["net_connect_dropped_malformed"] == 3

    def test_missing_dst_field_tallied_malformed(self):
        events, _, manifest = _run(tcp=[
            {"dport": 80, "sport": 100, "time": 1.0},
        ])
        assert events == []
        assert manifest["filters"]["net_connect_dropped_malformed"] == 1

    def test_unparseable_ip_tallied_malformed(self):
        events, _, manifest = _run(tcp=[
            _conn("not-an-ip"),
            _conn(""),
        ])
        assert events == []
        assert manifest["filters"]["net_connect_dropped_malformed"] == 2


# --- sandbox IP filtering -----------------------------------------------

class TestSandboxFiltering:
    @pytest.mark.parametrize("ip", [
        "10.0.0.1",
        "172.23.1.3",          # actual c1f6f86 sandbox IP
        "192.168.1.1",
        "127.0.0.1",
        "169.254.1.1",
        "224.0.0.1",
        "255.255.255.255",
        "::1",
        "fe80::1",
    ])
    def test_sandbox_ips_dropped(self, ip):
        events, _, manifest = _run(tcp=[_conn(ip)])
        assert events == []
        assert manifest["filters"]["net_connect_dropped_private"] == 1

    @pytest.mark.parametrize("ip", [
        "8.8.8.8",
        "1.1.1.1",
        "93.184.216.34",       # example.com
        "2606:4700:4700::1111",
    ])
    def test_public_ips_emitted(self, ip):
        events, _, manifest = _run(tcp=[_conn(ip)])
        assert len(events) == 1
        assert manifest["filters"]["net_connect_dropped_private"] == 0

    def test_mixed_batch_partial_drop(self):
        events, _, manifest = _run(tcp=[
            _conn("172.23.1.3"),   # sandbox -> drop
            _conn("8.8.8.8"),       # public -> emit
            _conn("192.168.1.1"),   # sandbox -> drop
            _conn("1.1.1.1"),       # public -> emit
        ])
        assert len(events) == 2
        assert manifest["filters"]["net_connect_dropped_private"] == 2


# --- event shape --------------------------------------------------------

class TestEventShape:
    def test_event_type_is_net_connect(self):
        events, _, _ = _run(tcp=[_conn("8.8.8.8")])
        assert events[0].event_type is EventType.NET_CONNECT

    def test_dst_is_ip_entity(self):
        events, reg, _ = _run(tcp=[_conn("8.8.8.8")])
        assert reg.get(events[0].dst)["type"] == EntityType.IP.value
        assert reg.get(events[0].dst)["canonical"] == "8.8.8.8"

    def test_src_is_root_actor(self):
        events, reg, _ = _run(tcp=[_conn("8.8.8.8")])
        assert reg.get(events[0].src)["type"] == EntityType.PROCESS.value

    def test_metadata_protocol_tcp(self):
        events, _, _ = _run(tcp=[_conn("8.8.8.8")])
        assert events[0].metadata["protocol"] == "tcp"
        assert events[0].metadata["source"] == "network.tcp"

    def test_metadata_protocol_udp(self):
        events, _, _ = _run(udp=[_conn("8.8.8.8")])
        assert events[0].metadata["protocol"] == "udp"
        assert events[0].metadata["source"] == "network.udp"

    def test_metadata_includes_ports(self):
        events, _, _ = _run(tcp=[_conn("8.8.8.8", dport=443, sport=55555)])
        assert events[0].metadata["dport"] == 443
        assert events[0].metadata["sport"] == 55555

    def test_missing_ports_omitted_from_metadata(self):
        events, _, _ = _run(tcp=[{"dst": "8.8.8.8", "time": 1.0}])
        meta = events[0].metadata
        assert "dport" not in meta
        assert "sport" not in meta

    def test_non_int_ports_omitted_from_metadata(self):
        events, _, _ = _run(tcp=[{
            "dst": "8.8.8.8", "dport": "80", "sport": None, "time": 1.0,
        }])
        meta = events[0].metadata
        assert "dport" not in meta
        assert "sport" not in meta


# --- IP field alternative -----------------------------------------------

class TestIpFieldFallback:
    def test_ip_key_accepted_when_dst_missing(self):
        events, _, _ = _run(tcp=[_conn("8.8.8.8", proto_src="ip")])
        assert len(events) == 1

    def test_dst_takes_precedence_when_both_present(self):
        """If both 'dst' and 'ip' keys exist, 'dst' wins."""
        events, reg, _ = _run(tcp=[{
            "dst": "8.8.8.8", "ip": "1.1.1.1",
            "dport": 80, "time": 1.0,
        }])
        assert len(events) == 1
        assert reg.get(events[0].dst)["canonical"] == "8.8.8.8"


# --- timestamps ---------------------------------------------------------

class TestTimestamps:
    def test_time_field_is_relative(self):
        events, _, _ = _run(tcp=[_conn("8.8.8.8", time=4.27)])
        e = events[0]
        assert e.timestamp == 4.27
        assert e.timestamp_source is TimestampSource.RELATIVE

    def test_missing_time_yields_none(self):
        events, _, _ = _run(tcp=[_conn("8.8.8.8", time=None)])
        e = events[0]
        assert e.timestamp is None
        assert e.timestamp_source is TimestampSource.NONE

    def test_non_numeric_time_yields_none(self):
        events, _, _ = _run(tcp=[{
            "dst": "8.8.8.8", "dport": 80, "time": "not-a-float",
        }])
        assert events[0].timestamp is None
        assert events[0].timestamp_source is TimestampSource.NONE


# --- beaconing preservation (no dedup) ----------------------------------

class TestBeaconingPreservation:
    def test_repeated_same_dst_all_emitted(self):
        """Decision A from commit 12 review: repeated connections
        to same (dst, dport) are preserved, not deduplicated, so
        Sprint 3 can detect beaconing patterns."""
        events, reg, _ = _run(tcp=[
            _conn("8.8.8.8", dport=443, time=1.0),
            _conn("8.8.8.8", dport=443, time=2.0),
            _conn("8.8.8.8", dport=443, time=3.0),
        ])
        assert len(events) == 3
        # All point at the same IP entity (dedup at entity level).
        assert events[0].dst == events[1].dst == events[2].dst
        # But each event has a distinct timestamp.
        assert [e.timestamp for e in events] == [1.0, 2.0, 3.0]
        # And only one IP entity exists in the registry.
        assert reg.count_by_type().get("ip") == 1

    def test_different_ports_all_emitted(self):
        events, _, _ = _run(tcp=[
            _conn("8.8.8.8", dport=80),
            _conn("8.8.8.8", dport=443),
            _conn("8.8.8.8", dport=8080),
        ])
        assert len(events) == 3


# --- tcp and udp both --------------------------------------------------

class TestTcpAndUdp:
    def test_both_protocols_emit_in_order(self):
        events, _, _ = _run(
            tcp=[_conn("8.8.8.8", dport=443, time=1.0)],
            udp=[_conn("1.1.1.1", dport=53, time=0.5)],
        )
        # TCP is iterated before UDP per the extractor loop.
        assert len(events) == 2
        assert events[0].metadata["protocol"] == "tcp"
        assert events[1].metadata["protocol"] == "udp"


# --- determinism --------------------------------------------------------

class TestDeterminism:
    def test_same_input_yields_identical_events(self):
        tcp = [
            _conn("8.8.8.8", dport=443, time=1.0),
            _conn("1.1.1.1", dport=80, time=2.0),
        ]
        e1, r1, _ = _run(tcp=tcp)
        e2, r2, _ = _run(tcp=tcp)
        assert [e.to_dict() for e in e1] == [e.to_dict() for e in e2]
        assert r1.to_manifest() == r2.to_manifest()


# --- real sample --------------------------------------------------------

@pytest.mark.skipif(
    not REAL_SAMPLE.exists(),
    reason="real sample not present in data/raw/",
)
class TestRealSample:
    """Integration against c1f6f86. From sample inspection: 184 total
    TCP connections, 182 to the sandbox IP 172.23.1.3, and 2 to a
    real public destination (tested observationally)."""

    def test_c1f6f86_sandbox_drops(self):
        _, manifest = parse_report_with_manifest(REAL_SAMPLE)
        # Observed from the real sample: 184 tcp+udp destinations hit
        # RFC1918/loopback/etc sandbox infrastructure, 2 hit public IPs.
        assert manifest["filters"]["net_connect_dropped_private"] == 184
        assert manifest["filters"]["net_connect_dropped_malformed"] == 0

    def test_c1f6f86_public_connections_emitted(self):
        events, _ = parse_report_with_manifest(REAL_SAMPLE)
        net = [e for e in events if e.event_type is EventType.NET_CONNECT]
        assert len(net) == 2
        # Both connections share a single destination IP entity
        # (same public host, hit twice -> beaconing preserved).
        assert net[0].dst == net[1].dst

    def test_c1f6f86_net_connect_timestamps_relative(self):
        events, _ = parse_report_with_manifest(REAL_SAMPLE)
        net = [e for e in events if e.event_type is EventType.NET_CONNECT]
        for e in net:
            assert e.timestamp is not None
            # After parse_report's rebase step, timestamps are
            # relative to the earliest observed event. Source tag
            # still reflects the original RELATIVE parse.
            assert e.timestamp_source is TimestampSource.RELATIVE

    def test_c1f6f86_metadata_has_protocol_and_ports(self):
        events, _ = parse_report_with_manifest(REAL_SAMPLE)
        net = [e for e in events if e.event_type is EventType.NET_CONNECT]
        for e in net:
            assert e.metadata["protocol"] in ("tcp", "udp")
            # Both observed connections to the public host have
            # destination ports. Assert existence, not exact value.
            assert isinstance(e.metadata.get("dport"), int)

