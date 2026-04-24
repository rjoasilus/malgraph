"""
Canonicalization rules for Sprint 1 event entities.

Pure functions. No state. Each function takes a raw string (or timestamp)
from a CAPE report and returns a canonical form suitable for entity
hashing (see ingest/entities.py) or filtering decisions (see parser.py).

Covers:
- Windows file paths (incl. named-pipe and device-path routing)
- Registry keys
- Domains
- IP addresses (plus sandbox/private-range classification)
- CAPE timestamp strings (4 observed formats in the Avast-CTU corpus)

See docs/schema.md for the canonical forms this module emits.
"""
from __future__ import annotations

import ipaddress
import re
from datetime import datetime, timezone
from enum import Enum


class PathKind(str, Enum):
    """Result of classifying a Windows path string — routes callers to
    the right EntityType in entities.py."""

    FILE = "file"
    NAMED_PIPE = "named_pipe"


# --- filter lists -------------------------------------------------------

SANDBOX_DOMAINS: frozenset[str] = frozenset({
    "google-public-dns-a.google.com",  # CAPE liveness probe
})


_SANDBOX_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("224.0.0.0/4"),    # IPv4 multicast
    ipaddress.ip_network("::1/128"),        # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),       # IPv6 unique-local
    ipaddress.ip_network("fe80::/10"),      # IPv6 link-local
    ipaddress.ip_network("ff00::/8"),       # IPv6 multicast
)


# --- paths --------------------------------------------------------------

_NAMED_PIPE_PREFIX = "\\device\\namedpipe\\"
_HARDDISK_VOLUME1_PREFIX = "\\device\\harddiskvolume1\\"


def canonicalize_path(raw: str) -> tuple[PathKind, str]:
    """
    Normalize a Windows file path. Returns (kind, canonical).

    - lowercase
    - strip outer quotes and whitespace
    - route \\Device\\NamedPipe\\* to NAMED_PIPE kind
    - rewrite \\Device\\HarddiskVolume1\\ to c:/ (the Avast-CTU sandbox
      mounts a single volume; other HarddiskVolume<N> prefixes are left
      as-is — no reliable mapping without the live system)
    - backslashes -> forward slashes
    - trailing slash removed (except drive-letter root "x:/")

    Short-name expansion (PROGRA~1 -> "program files") is NOT attempted;
    we have no reliable expansion table. Paths with short names stay
    canonically distinct from their long forms — a known limitation.
    """
    s = raw.strip().strip('"').strip("'").lower()
    if not s:
        return PathKind.FILE, ""

    if s.startswith(_NAMED_PIPE_PREFIX):
        return PathKind.NAMED_PIPE, s.replace("\\", "/")

    if s.startswith(_HARDDISK_VOLUME1_PREFIX):
        s = "c:/" + s[len(_HARDDISK_VOLUME1_PREFIX):]

    s = s.replace("\\", "/")
    if len(s) > 3 and s.endswith("/"):
        s = s.rstrip("/")
    return PathKind.FILE, s


# --- domains ------------------------------------------------------------

def canonicalize_domain(raw: str) -> str:
    """Lowercase, strip leading/trailing dots and whitespace."""
    return raw.strip().strip(".").lower()


def is_sandbox_domain(raw: str) -> bool:
    """True if this domain is a known CAPE sandbox artifact
    (e.g. CAPE's DNS liveness probe). Expand SANDBOX_DOMAINS as
    the batch run surfaces more candidates."""
    return canonicalize_domain(raw) in SANDBOX_DOMAINS


# --- registry keys ------------------------------------------------------

_HKEY_PREFIX_RE = re.compile(r"^(hkey_[a-z_]+)(.*)$", re.IGNORECASE)


def canonicalize_registry_key(raw: str) -> str:
    """
    Normalize a Windows registry key string:
    - uppercase HKEY_* prefix (e.g. HKEY_LOCAL_MACHINE)
    - preserve native backslash separators (unlike file paths)
    - no trailing backslash
    """
    s = raw.strip()
    m = _HKEY_PREFIX_RE.match(s)
    if m:
        s = m.group(1).upper() + m.group(2)
    return s.rstrip("\\")


# --- IP addresses -------------------------------------------------------

def canonicalize_ip(raw: str) -> str | None:
    """
    Normalize IPv4/IPv6. Returns the canonical string form (leading-zero
    stripped, IPv6 compressed), or None if unparseable.
    """
    try:
        return str(ipaddress.ip_address(raw.strip()))
    except (ValueError, TypeError):
        return None


def is_sandbox_ip(raw: str) -> bool:
    """
    True if this IP is in a sandbox/private range we should NOT emit
    as a net_connect event:
    - RFC1918, loopback, link-local, multicast (IPv4 + IPv6)
    - broadcast 255.255.255.255
    - unparseable input (conservative: drop it)
    """
    s = raw.strip()
    if s == "255.255.255.255":
        return True
    try:
        addr = ipaddress.ip_address(s)
    except (ValueError, TypeError):
        return True
    return any(addr in net for net in _SANDBOX_NETWORKS)


# --- timestamps ---------------------------------------------------------

_TS_PATTERNS: tuple[str, ...] = (
    "%Y-%m-%dT%H:%M:%S.%f%z",   # "2021-07-22T22:57:04.995386+0000" (suricata)
    "%Y-%m-%dT%H:%M:%S%z",      # "2021-07-22T22:57:04+0000"
    "%Y-%m-%d %H:%M:%S.%f",     # after comma->period normalization
    "%Y-%m-%d %H:%M:%S",        # "2021-07-22 22:56:26" (info.started)
)


def parse_cape_timestamp(raw: object) -> datetime | None:
    """
    Parse any CAPE timestamp flavor in the Avast-CTU corpus. Returns a
    timezone-aware UTC datetime, or None on failure.

    Formats observed:
    - "2021-07-22 22:56:26"             (info.started, no millis)
    - "2021-06-03 23:01:29,362"         (enhanced / calls, comma-millis)
    - "2021-06-03 23:01:29.362"         (period-millis variant)
    - "2021-07-22T22:57:04.995386+0000" (suricata.dns, ISO-ish with tz)

    Non-string input (None, int, already-datetime, etc.) returns None —
    timestamps with no temporal anchor fall back to `ord` per schema.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    s = raw.strip().replace(",", ".")
    for pattern in _TS_PATTERNS:
        try:
            dt = datetime.strptime(s, pattern)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None
