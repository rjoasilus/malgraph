# Event Schema — MalGraph Sprint 1

**Version:** 1.0 (draft — to be finalized after parser batch run over the 4,000-sample corpus)
**Applies to:** `ingest/parser.py` output, `data/processed/<sha256>.jsonl`

---

## Design decisions

**1. Labels live outside events.** Sample-level labels (Emotet | Trickbot) are stored in `data/processed/manifest.jsonl`, keyed by `sample_id`. They are **not** stored on each event. With ~500–1,000 events per sample × 4,000 samples, redundant per-event labels would inflate disk and invite accidental label leakage into feature code.

**2. Timestamps are relative to the earliest observed event in the report.** Not to `info.started`. In the Avast-CTU corpus, `info.started` disagrees with `behavior.*` timestamps by weeks or months in many samples (observed: 49-day and 6-month gaps across two inspected reports — systematic rerun/caching artifact). Using min-event-timestamp as the per-report anchor is self-consistent and immune to this.

**3. `ord` is always present. `timestamp` may be null.** Some CAPE sources (notably `behavior.summary.*`) provide events without timing. `ord` is the monotonic parse-order fallback for deterministic ordering.

**4. Entity IDs are stable across re-parses.** Files / domains / keys / IPs / pipes / modules / directories use `sha1(type:canonical_form)[:12]`. Processes use `sha1(sample_id:pid)[:12]`. See `ingest/entities.py`.

**5. `src` / `dst` invariant:** across all event types, `src` is the acting process (actor), `dst` is the target. Deviations are encoded in `metadata` (e.g. `file_copy` has `dst` = destination file, `metadata.source_file` = source file).

---

## Event record

```json
{
  "sample_id": "c1f6f8635fd2dfa1440fe485d1ac0ad8b934f3779a4bb999028ce71d12ea5494",
  "ord": 1,
  "event_type": "reg_read",
  "src": "7a4b9c3d2e1f",
  "dst": "9f8e7d6c5b4a",
  "timestamp": 0.031,
  "timestamp_source": "absolute",
  "metadata": {"content": null, "source": "behavior.enhanced"}
}
```

Field reference:

| Field | Type | Notes |
|---|---|---|
| `sample_id` | str | SHA-256 of the original sample. |
| `ord` | int ≥ 0 | Monotonic within-sample ordinal. Always present. |
| `event_type` | str | One of the 13 types below. |
| `src` | str | Entity ID of the actor (usually a process). |
| `dst` | str | Entity ID of the target. |
| `timestamp` | float \| null | Seconds since first event in this report. Null if unknown. |
| `timestamp_source` | str | `absolute` \| `relative` \| `inferred` \| `none`. |
| `metadata` | object | Event-specific context. Always present; may be empty. |

---

## Event types

13 types, grouped by domain:

| Type | Primary CAPE source | Fallback |
|------|---------------------|----------|
| `process_spawn` | `behavior.processtree` (walked depth-first) | `behavior.processes[].parent_id` for orphans |
| `process_terminate` | API calls: `NtTerminateProcess` (future work — `calls` is Sprint 3) | *(not emitted in Sprint 1 v1)* |
| `file_read` | `behavior.enhanced` (`event=read`, `object=file`) | `behavior.summary.read_files` |
| `file_write` | `behavior.enhanced` (`event=write`, `object=file`); also `create,dir` emits `file_write` with `dst` = DIRECTORY entity and `metadata.kind="directory"` | `behavior.summary.write_files` |
| `file_delete` | `behavior.enhanced` (`event=delete`, `object=file`) | `behavior.summary.delete_files` |
| `file_copy` | `behavior.enhanced` (`event=copy`, `object=file`) | — |
| `file_move` | `behavior.enhanced` (`event=move`, `object=file`) | — |
| `reg_read` | `behavior.enhanced` (`event=read`, `object=registry`) | `behavior.summary.read_keys` |
| `reg_write` | `behavior.enhanced` (`event=write`, `object=registry`) | `behavior.summary.write_keys` |
| `reg_delete` | `behavior.enhanced` (`event=delete`, `object=registry`) | `behavior.summary.delete_keys` |
| `net_connect` | `network.tcp` + `network.udp`, filtered to non-RFC1918 destinations | — |
| `dns_query` | `suricata.dns` when populated; else `network.dns` | union when disjoint |
| `module_load` | `behavior.enhanced` (`event=load`, `object=library`) | — |

**Dedup rule across primary/fallback sources.** Key: `(src, dst, event_type)`. Keep the primary-sourced event (which carries a real timestamp); drop any fallback match. Log drop counts in the per-sample parse manifest.

**`process_terminate` note.** CAPE does not emit a clean termination event in `behavior.enhanced`; the signal lives in `behavior.processes[].calls` (e.g. `NtTerminateProcess`). Since `calls` is deferred to Sprint 3, Sprint 1 v1 does **not** emit `process_terminate`. The enum entry exists for forward compatibility.

---

## Worked examples

All examples below use the sample `c1f6f8635fd2dfa1440fe485d1ac0ad8b934f3779a4bb999028ce71d12ea5494` unless noted.

### reg_read (from `behavior.enhanced`)

Raw CAPE input:
```json
{
  "event": "read", "object": "registry", "eid": 1,
  "timestamp": "2021-06-03 23:01:29,800",
  "data": {"regkey": "DisableUserModeCallbackFilter", "content": null}
}
```

Normalized:
```json
{
  "sample_id": "c1f6f86...",
  "ord": 1,
  "event_type": "reg_read",
  "src": "<root_process_id>",
  "dst": "<registry_key_id>",
  "timestamp": 0.000,
  "timestamp_source": "absolute",
  "metadata": {"content": null, "source": "behavior.enhanced"}
}
```

Entity `<registry_key_id>` is `sha1("registry_key:disableusermodecallbackfilter")[:12]`.

### module_load

Raw:
```json
{
  "event": "load", "object": "library",
  "timestamp": "2021-06-03 23:01:29,831",
  "data": {"file": "User32.dll", "pathtofile": null, "moduleaddress": "0x00000000"}
}
```

Normalized:
```json
{
  "event_type": "module_load",
  "src": "<root_process_id>",
  "dst": "<module_id>",
  "timestamp": 0.031,
  "timestamp_source": "absolute",
  "metadata": {"source": "behavior.enhanced"}
}
```

Module canonical form is the lowercased DLL name (`user32.dll`).

### file_copy (from sample `f07cd6c...`)

Raw:
```json
{
  "event": "copy", "object": "file", "eid": 85,
  "data": {
    "from": "C:\\Users\\comp\\AppData\\Local\\Temp\\F07CD6CE....exe",
    "to":   "C:\\Users\\comp\\AppData\\Roaming\\googroup\\G08DE7DF....exe"
  }
}
```

Normalized:
```json
{
  "event_type": "file_copy",
  "src": "<actor_process_id>",
  "dst": "<dst_file_id>",
  "metadata": {
    "source_file": "<src_file_id>",
    "source": "behavior.enhanced"
  }
}
```

Both files registered separately in the entity table.

### net_connect — filtered (from sample `c1f6f86...`)

All 183 TCP connections in this sample go to `172.23.1.3` (RFC1918 sandbox infrastructure). **None are emitted.** Parser records:
manifest.net_connect_dropped_private: 183
manifest.net_connect_emitted: 0

This prevents sandbox-topology noise from dominating the event stream and spuriously correlating with sample size → label.

---

## Canonicalization rules

Applied before entity registration. Full implementation in `ingest/canonicalize.py` (later commit).

**Paths:**
- Lowercase.
- Backslashes → forward slashes.
- `\Device\NamedPipe\*` → routed to `NAMED_PIPE` entity type (not `FILE`).
- `\Device\HarddiskVolumeN\...` → normalized to drive-letter form where mappable.
- Short names (`PROGRA~1`) → expanded when a long form is known.
- Environment variables (`%APPDATA%`, `%TEMP%`) → left as-is (already canonical).

**Domains:** lowercase, strip trailing dot.

**Registry keys:** uppercase HKEY prefix, consistent separator (backslash), no trailing backslash.

**IPs:** canonical IPv4/IPv6 string form (no leading zeros, lowercase hex).

---

## Sandbox-artifact filters

**Network filters** (applied before emitting `net_connect`):
- Drop destinations in RFC1918: `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`.
- Drop loopback `127.0.0.0/8`, link-local `169.254.0.0/16`, multicast `224.0.0.0/4`, broadcast `255.255.255.255`.

**Domain filters** (applied before emitting `dns_query`):
- `google-public-dns-a.google.com` — CAPE liveness probe.
- Additional entries to be catalogued during the Sprint 1 batch run; suspects logged to manifest.

---

## Unhandled / skipped CAPE shapes (as of 2026-04-24)

Observed in samples but **not** emitted; counted in the per-report parse manifest:

- `(create, windowshook)` — rare, niche, defer.
- `(execute, file)` — overlaps with processtree-sourced `process_spawn`; primary is processtree.
- `behavior.processes[].calls` — deferred to Sprint 3 feature extraction in entirety.
- `behavior.anomaly` — not currently mapped to event types.
- `curtain` — PowerShell-specific module content; Sprint 3 feature candidate.

---

## Out of scope for Sprint 1

These CAPE fields are reserved for later sprints:

- `ttps` — MITRE ATT&CK technique IDs. Sprint 3 feature input.
- `signatures` — CAPE pre-computed behavioral signatures. Sprint 3.
- `strings` — extracted strings. Not currently planned.
- `dropped`, `procdump`, `procmemory` — artifact bundles. Not used.

