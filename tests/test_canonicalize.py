"""
Tests for ingest.canonicalize.

Pure-function tests. Every canonicalization rule in docs/schema.md
gets at least one assertion here. If schema rules change, these
tests change in the same commit.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

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


# --- path canonicalization ----------------------------------------------

class TestCanonicalizePath:
    def test_basic_lowercase_and_slash_conversion(self):
        kind, p = canonicalize_path("C:\\Users\\Comp\\Temp\\X.EXE")
        assert kind is PathKind.FILE
        assert p == "c:/users/comp/temp/x.exe"

    def test_named_pipe_routed_to_pipe_kind(self):
        kind, p = canonicalize_path("\\Device\\NamedPipe\\srvsvc")
        assert kind is PathKind.NAMED_PIPE
        assert p == "/device/namedpipe/srvsvc"

    def test_harddiskvolume1_rewritten_to_drive_letter(self):
        kind, p = canonicalize_path(
            "\\Device\\HarddiskVolume1\\Users\\comp\\x.txt"
        )
        assert kind is PathKind.FILE
        assert p == "c:/users/comp/x.txt"

    def test_harddiskvolume_other_number_left_alone(self):
        """Only HarddiskVolume1 is rewritten; higher-numbered volumes
        have no reliable drive-letter mapping."""
        kind, p = canonicalize_path(
            "\\Device\\HarddiskVolume3\\Users\\comp\\x.txt"
        )
        assert kind is PathKind.FILE
        # Leaves the device path as-is (only lowercased, slashes flipped).
        assert p == "/device/harddiskvolume3/users/comp/x.txt"

    def test_outer_quotes_stripped(self):
        _, p = canonicalize_path('"C:\\Users\\comp\\x.exe"')
        assert p == "c:/users/comp/x.exe"

    def test_trailing_slash_stripped(self):
        _, p = canonicalize_path("C:\\Users\\comp\\")
        assert p == "c:/users/comp"

    def test_drive_root_trailing_slash_preserved(self):
        _, p = canonicalize_path("C:\\")
        # 3 chars = "c:/" which is a root — don't strip to "c:" (ambiguous)
        assert p == "c:/"

    def test_empty_input_yields_empty(self):
        kind, p = canonicalize_path("")
        assert kind is PathKind.FILE
        assert p == ""

    def test_whitespace_only_yields_empty(self):
        _, p = canonicalize_path("   \t  ")
        assert p == ""

    def test_same_input_canonicalizes_to_same_output(self):
        """Determinism: stable-ID invariant for Sprint 2 graphs."""
        a = canonicalize_path("C:\\Windows\\System32\\cmd.exe")
        b = canonicalize_path("c:\\WINDOWS\\system32\\CMD.EXE")
        assert a == b


# --- domain canonicalization --------------------------------------------

class TestCanonicalizeDomain:
    def test_lowercase(self):
        assert canonicalize_domain("Example.COM") == "example.com"

    def test_strip_trailing_dot(self):
        assert canonicalize_domain("example.com.") == "example.com"

    def test_strip_whitespace(self):
        assert canonicalize_domain("  example.com  ") == "example.com"


class TestIsSandboxDomain:
    def test_cape_dns_probe_flagged(self):
        assert is_sandbox_domain("google-public-dns-a.google.com") is True

    def test_cape_dns_probe_flagged_case_insensitive(self):
        assert is_sandbox_domain("GOOGLE-PUBLIC-DNS-A.google.com") is True

    def test_ordinary_domain_not_flagged(self):
        assert is_sandbox_domain("example.com") is False
        assert is_sandbox_domain("malicious-c2.evil") is False


# --- registry key canonicalization --------------------------------------

class TestCanonicalizeRegistryKey:
    def test_hkey_prefix_uppercased(self):
        k = canonicalize_registry_key("hkey_local_machine\\SOFTWARE\\Test")
        assert k == "HKEY_LOCAL_MACHINE\\SOFTWARE\\Test"

    def test_trailing_backslash_stripped(self):
        k = canonicalize_registry_key("HKEY_CURRENT_USER\\Software\\X\\")
        assert k == "HKEY_CURRENT_USER\\Software\\X"

    def test_body_casing_preserved(self):
        """Below the HKEY_* prefix, casing is left alone — Windows
        registry keys are case-insensitive at lookup time but some
        tooling preserves case. We don't lowercase the body to avoid
        collapsing distinct-at-the-source values."""
        k = canonicalize_registry_key(
            "HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Windows"
        )
        assert k == "HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Windows"

    def test_no_hkey_prefix_passthrough(self):
        """Short-form keys without HKEY prefix (as seen in
        behavior.enhanced) pass through with trailing-slash stripping."""
        k = canonicalize_registry_key("DisableUserModeCallbackFilter")
        assert k == "DisableUserModeCallbackFilter"


# --- IP canonicalization and classification -----------------------------

class TestCanonicalizeIp:
    def test_ipv4_passthrough(self):
        assert canonicalize_ip("8.8.8.8") == "8.8.8.8"

    def test_ipv4_leading_zero_stripped(self):
        # '010.0.0.1' is rejected outright by the ipaddress module
        # in py3.10+ (leading-zero ambiguity). Accept None.
        assert canonicalize_ip("010.0.0.1") is None

    def test_ipv6_compressed(self):
        assert canonicalize_ip("2001:0db8:0000:0000:0000:0000:0000:0001") \
            == "2001:db8::1"

    def test_garbage_returns_none(self):
        assert canonicalize_ip("not-an-ip") is None
        assert canonicalize_ip("") is None


class TestIsSandboxIp:
    @pytest.mark.parametrize("ip", [
        "10.0.0.1",
        "172.16.0.1",
        "172.23.1.3",        # the actual sandbox IP in sample c1f6f86
        "172.31.255.255",
        "192.168.1.1",
        "127.0.0.1",
        "169.254.0.1",
        "224.0.0.1",
        "255.255.255.255",
        "::1",
        "fe80::1",
        "fc00::1",
    ])
    def test_private_and_sandbox_ranges_flagged(self, ip):
        assert is_sandbox_ip(ip) is True

    @pytest.mark.parametrize("ip", [
        "8.8.8.8",
        "1.1.1.1",
        "172.15.255.255",   # just outside RFC1918
        "172.32.0.0",       # just outside RFC1918
        "2606:4700:4700::1111",
    ])
    def test_public_ips_not_flagged(self, ip):
        assert is_sandbox_ip(ip) is False

    def test_unparseable_conservatively_flagged_as_sandbox(self):
        """Unparseable input is dropped (treated as sandbox) to avoid
        emitting garbage IPs as net_connect events."""
        assert is_sandbox_ip("not-an-ip") is True
        assert is_sandbox_ip("") is True


# --- timestamp parsing --------------------------------------------------

class TestParseCapeTimestamp:
    def test_info_started_format_no_millis(self):
        dt = parse_cape_timestamp("2021-07-22 22:56:26")
        assert dt == datetime(2021, 7, 22, 22, 56, 26, tzinfo=timezone.utc)

    def test_enhanced_format_comma_millis(self):
        dt = parse_cape_timestamp("2021-06-03 23:01:29,362")
        assert dt == datetime(
            2021, 6, 3, 23, 1, 29, 362000, tzinfo=timezone.utc,
        )

    def test_period_millis_variant(self):
        dt = parse_cape_timestamp("2021-06-03 23:01:29.362")
        assert dt == datetime(
            2021, 6, 3, 23, 1, 29, 362000, tzinfo=timezone.utc,
        )

    def test_suricata_iso_with_tz(self):
        dt = parse_cape_timestamp("2021-07-22T22:57:04.995386+0000")
        assert dt == datetime(
            2021, 7, 22, 22, 57, 4, 995386, tzinfo=timezone.utc,
        )

    def test_all_formats_produce_tz_aware_utc(self):
        """All four observed formats converge on tz-aware UTC. That
        invariant is what makes min()/subtraction safe in parser.py."""
        samples = [
            "2021-07-22 22:56:26",
            "2021-06-03 23:01:29,362",
            "2021-06-03 23:01:29.362",
            "2021-07-22T22:57:04.995386+0000",
        ]
        for s in samples:
            dt = parse_cape_timestamp(s)
            assert dt is not None
            assert dt.tzinfo is not None
            assert dt.utcoffset().total_seconds() == 0

    def test_none_input(self):
        assert parse_cape_timestamp(None) is None

    def test_empty_string(self):
        assert parse_cape_timestamp("") is None
        assert parse_cape_timestamp("   ") is None

    def test_non_string_input(self):
        assert parse_cape_timestamp(1234567890) is None
        assert parse_cape_timestamp(4.271466970443726) is None

    def test_unparseable_string(self):
        assert parse_cape_timestamp("not a timestamp") is None
        assert parse_cape_timestamp("2021-99-99") is None
