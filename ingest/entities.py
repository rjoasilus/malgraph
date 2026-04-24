"""
Stable entity IDs for the Sprint 1 event stream.

Two ID strategies:

- **Cross-sample entities** (files, domains, IPs, registry keys, named pipes,
  modules, directories): content hash of the canonical form.
  Same file path observed in two samples gets the same ID — useful for
  cross-sample analysis in later sprints ("which samples touched this key?").

- **Sample-scoped entities** (processes): hash of `(sample_id, pid)`.
  PIDs are sandbox-assigned and can repeat across samples; scoping by
  sample_id prevents cross-sample collisions.

ID length: 12 hex chars = 48 bits. Collision probability is negligible
at 4k-sample corpus scale.

Canonicalization is the caller's responsibility — register_* expects
already-canonical strings. See `ingest/canonicalize.py` (next commit).

PID reuse within a single report (rare in 60-240s sandbox runs) is not
currently disambiguated. If it shows up, we promote `first_seen_ord` into
the hash key.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EntityType(str, Enum):
    PROCESS = "process"
    FILE = "file"
    DIRECTORY = "directory"
    REGISTRY_KEY = "registry_key"
    DOMAIN = "domain"
    IP = "ip"
    NAMED_PIPE = "named_pipe"
    MODULE = "module"      # DLLs/libraries loaded by a process
    EXTERNAL = "external"  # sandbox launcher, unknown parent procs


ID_LENGTH = 12


def _hash12(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:ID_LENGTH]


@dataclass
class EntityRegistry:
    """
    Accumulates entities observed during a single report parse.
    One registry per sample. Also doubles as the parser's manifest of
    everything it touched -- `to_manifest()` dumps a stable-ordered view.
    """

    sample_id: str
    _entities: dict[str, dict[str, Any]] = field(default_factory=dict)

    # --- public registration API --------------------------------------------

    def register_file(self, canonical_path: str) -> str:
        return self._register_content(EntityType.FILE, canonical_path)

    def register_directory(self, canonical_path: str) -> str:
        return self._register_content(EntityType.DIRECTORY, canonical_path)

    def register_registry_key(self, canonical_key: str) -> str:
        return self._register_content(EntityType.REGISTRY_KEY, canonical_key)

    def register_domain(self, canonical_domain: str) -> str:
        return self._register_content(EntityType.DOMAIN, canonical_domain)

    def register_ip(self, ip: str) -> str:
        return self._register_content(EntityType.IP, ip)

    def register_named_pipe(self, pipe_path: str) -> str:
        return self._register_content(EntityType.NAMED_PIPE, pipe_path)

    def register_module(self, module_name: str) -> str:
        return self._register_content(EntityType.MODULE, module_name)

    def register_external(self, tag: str) -> str:
        """For actors outside the analysis scope (the CAPE launcher process
        that spawned the root sample, unknown parent PIDs, etc.). Still gets
        a stable ID so Sprint 2 edges have a valid source node."""
        return self._register_content(EntityType.EXTERNAL, tag)

    def register_process(
        self, pid: int, first_seen_ord: int, name: str = ""
    ) -> str:
        key = f"{self.sample_id}:{pid}"
        eid = _hash12(f"{EntityType.PROCESS.value}:{key}")
        if eid not in self._entities:
            self._entities[eid] = {
                "type": EntityType.PROCESS.value,
                "canonical": name,
                "pid": pid,
                "first_seen_ord": first_seen_ord,
            }
        return eid

    # --- read API -----------------------------------------------------------

    def get(self, eid: str) -> dict[str, Any] | None:
        return self._entities.get(eid)

    def __len__(self) -> int:
        return len(self._entities)

    def __contains__(self, eid: str) -> bool:
        return eid in self._entities

    def count_by_type(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for meta in self._entities.values():
            counts[meta["type"]] = counts.get(meta["type"], 0) + 1
        return counts

    def to_manifest(self) -> list[dict[str, Any]]:
        """
        Serializable list: one row per entity. Order is stable by
        (type, canonical) so diffing two runs is trivial.
        """
        return [
            {"id": eid, **meta}
            for eid, meta in sorted(
                self._entities.items(),
                key=lambda kv: (kv[1]["type"], str(kv[1].get("canonical", ""))),
            )
        ]

    # --- internals ----------------------------------------------------------

    def _register_content(self, etype: EntityType, canonical: str) -> str:
        eid = _hash12(f"{etype.value}:{canonical}")
        if eid not in self._entities:
            self._entities[eid] = {
                "type": etype.value,
                "canonical": canonical,
            }
        return eid
