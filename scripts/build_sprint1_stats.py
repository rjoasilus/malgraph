"""
Build experiments/sprint1_stats.md from data/processed/manifest.jsonl.

Reads the aggregate manifest emitted by run_parser_batch.py and
writes a markdown report suitable for the Sprint 1 exit review.

Usage:
    python scripts/build_sprint1_stats.py
    python scripts/build_sprint1_stats.py --manifest ... --out ...

Re-run any time the corpus changes. Deterministic output: same input
manifest -> byte-identical .md.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path


DEFAULT_MANIFEST = Path("data/processed/manifest.jsonl")
DEFAULT_OUT = Path("experiments/sprint1_stats.md")


def load_manifest(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _fmt_int(n: int) -> str:
    return f"{n:,}"


def _fmt_float(x: float, places: int = 1) -> str:
    return f"{x:,.{places}f}"


def _distribution_stats(values: list[float]) -> dict:
    """Summary stats for a list of numbers. Empty list -> zeros."""
    if not values:
        return {
            "n": 0, "sum": 0, "mean": 0.0, "median": 0.0,
            "p95": 0.0, "max": 0,
        }
    sorted_v = sorted(values)
    idx_p95 = max(0, int(round(0.95 * (len(sorted_v) - 1))))
    return {
        "n": len(sorted_v),
        "sum": sum(sorted_v),
        "mean": statistics.fmean(sorted_v),
        "median": statistics.median(sorted_v),
        "p95": sorted_v[idx_p95],
        "max": sorted_v[-1],
    }


def _section_headline(rows: list[dict]) -> list[str]:
    n_total = len(rows)
    n_ok = sum(1 for r in rows if r.get("parse_ok"))
    n_fail = n_total - n_ok
    total_events = sum(
        sum(r.get("event_counts", {}).values()) for r in rows
    )
    total_entities = sum(
        sum(r.get("entity_counts", {}).values()) for r in rows
    )
    labels = Counter(r.get("label") for r in rows)

    lines = [
        "## Headline",
        "",
        f"- **Samples:** {_fmt_int(n_total)}",
        f"- **Parsed OK:** {_fmt_int(n_ok)} ({n_ok / n_total:.1%})",
        f"- **Parse failures:** {_fmt_int(n_fail)}",
        f"- **Total events emitted:** {_fmt_int(total_events)}",
        f"- **Total entities registered:** {_fmt_int(total_entities)}",
        "",
        "**Label breakdown:**",
        "",
    ]
    for label, count in sorted(
        labels.items(),
        key=lambda kv: (-kv[1], str(kv[0])),
    ):
        label_str = label if label is not None else "(no label)"
        lines.append(f"- `{label_str}`: {_fmt_int(count)}")
    lines.append("")
    return lines


def _section_event_distribution(rows: list[dict]) -> list[str]:
    totals: Counter = Counter()
    per_sample: dict[str, list[int]] = defaultdict(list)
    for r in rows:
        counts = r.get("event_counts") or {}
        for etype, n in counts.items():
            totals[etype] += n
            per_sample[etype].append(n)
        # Include zeros for event types this sample didn't have —
        # important for honest mean/median.
        seen = set(counts.keys())
        # deferred: we only compute across-corpus per-type stats below;
        # zero-padding handled via _pad_zeros.

    grand_total = sum(totals.values())
    lines = [
        "## Event-type distribution (4k corpus)",
        "",
        "Per-sample stats compute against non-zero samples only; the",
        "`samples` column shows how many samples emitted ≥1 of that",
        "event type. Sprint exit criterion: no event type is 0% or 100%",
        "unless by design.",
        "",
        "| event_type | total | % total | samples | mean | median | p95 | max |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for etype in sorted(totals.keys(), key=lambda k: -totals[k]):
        stats = _distribution_stats(per_sample[etype])
        pct = (totals[etype] / grand_total) if grand_total else 0
        lines.append(
            f"| `{etype}` | {_fmt_int(totals[etype])} | "
            f"{pct:.1%} | {_fmt_int(stats['n'])} | "
            f"{_fmt_float(stats['mean'])} | "
            f"{_fmt_float(stats['median'])} | "
            f"{_fmt_int(int(stats['p95']))} | "
            f"{_fmt_int(int(stats['max']))} |"
        )
    lines.append("")
    return lines


def _section_entity_distribution(rows: list[dict]) -> list[str]:
    totals: Counter = Counter()
    per_sample: dict[str, list[int]] = defaultdict(list)
    for r in rows:
        counts = r.get("entity_counts") or {}
        for etype, n in counts.items():
            totals[etype] += n
            per_sample[etype].append(n)
    lines = [
        "## Entity-type distribution (4k corpus)",
        "",
        "| entity_type | total | samples | mean | median | p95 | max |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for etype in sorted(totals.keys(), key=lambda k: -totals[k]):
        stats = _distribution_stats(per_sample[etype])
        lines.append(
            f"| `{etype}` | {_fmt_int(totals[etype])} | "
            f"{_fmt_int(stats['n'])} | "
            f"{_fmt_float(stats['mean'])} | "
            f"{_fmt_float(stats['median'])} | "
            f"{_fmt_int(int(stats['p95']))} | "
            f"{_fmt_int(int(stats['max']))} |"
        )
    lines.append("")
    return lines


def _section_family_comparison(rows: list[dict]) -> list[str]:
    """Emotet vs Trickbot mean event counts — the Risk #1 investigation."""
    by_family: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        lbl = r.get("label")
        if lbl:
            by_family[lbl].append(r)

    families = sorted(by_family.keys())
    if len(families) < 2:
        return [
            "## Family comparison",
            "",
            "_Skipped: fewer than 2 labeled families in manifest._",
            "",
        ]

    # Collect all event types seen.
    event_types: set[str] = set()
    for r in rows:
        event_types.update((r.get("event_counts") or {}).keys())

    lines = [
        "## Family comparison — Risk #1 investigation",
        "",
        "Sprint 0 flagged that Trickbot samples are consistently smaller",
        "(~500 KB) than Emotet samples (~4 MB). Features that scale with",
        "raw size will correlate with label spuriously. The table below",
        "is the empirical check — per-event-type mean per sample for each",
        "family. Look for types where the ratio Emotet:Trickbot is far",
        "from 1; those are the ones feature engineering must normalize.",
        "",
    ]

    header_cells = ["event_type"] + [f"mean ({f})" for f in families]
    if "Emotet" in families and "Trickbot" in families:
        header_cells.append("Emotet:Trickbot")

    lines.append("| " + " | ".join(header_cells) + " |")
    lines.append("|" + "|".join(["---"] + ["---:" for _ in header_cells[1:]]) + "|")

    def _mean_for(family: str, etype: str) -> float:
        samples = by_family[family]
        if not samples:
            return 0.0
        return statistics.fmean(
            (r.get("event_counts") or {}).get(etype, 0) for r in samples
        )

    rows_to_sort: list[tuple[str, list[float]]] = []
    for etype in event_types:
        means = [_mean_for(f, etype) for f in families]
        rows_to_sort.append((etype, means))
    rows_to_sort.sort(key=lambda pair: -sum(pair[1]))

    for etype, means in rows_to_sort:
        cells = [f"`{etype}`"]
        for m in means:
            cells.append(_fmt_float(m))
        if "Emotet" in families and "Trickbot" in families:
            e_idx = families.index("Emotet")
            t_idx = families.index("Trickbot")
            e_mean = means[e_idx]
            t_mean = means[t_idx]
            if t_mean > 0:
                ratio_s = f"{e_mean / t_mean:.1f}x"
            elif e_mean > 0:
                ratio_s = "inf"
            else:
                ratio_s = "—"
            cells.append(ratio_s)
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    return lines


def _section_filter_effectiveness(rows: list[dict]) -> list[str]:
    totals: Counter = Counter()
    for r in rows:
        for k, v in (r.get("filters") or {}).items():
            if isinstance(v, int):
                totals[k] += v

    lines = [
        "## Filter effectiveness",
        "",
        "Counters for events and entries dropped at parse time (sandbox",
        "infrastructure noise, malformed records, enhanced↔summary dedup).",
        "Totals across the whole corpus.",
        "",
        "| filter | total dropped |",
        "|---|---:|",
    ]
    for k in sorted(totals.keys()):
        lines.append(f"| `{k}` | {_fmt_int(totals[k])} |")
    lines.append("")
    return lines


def _section_unhandled_enhanced(rows: list[dict]) -> list[str]:
    """Aggregate behavior.enhanced (event, object) pairs the dispatch
    table didn't handle. Important for deciding what to add in Sprint 3."""
    tally: Counter = Counter()
    samples_affected: Counter = Counter()
    for r in rows:
        unhandled = r.get("unhandled_enhanced") or []
        sample_keys: set[tuple[str, str]] = set()
        for entry in unhandled:
            key = (entry.get("event"), entry.get("object"))
            tally[key] += entry.get("count", 0)
            sample_keys.add(key)
        for key in sample_keys:
            samples_affected[key] += 1

    lines = [
        "## Unhandled `behavior.enhanced` shapes across 4k corpus",
        "",
        "Every `(event, object)` pair seen in `behavior.enhanced` that is",
        "not in our dispatch table. Empty table = parser's dispatch is",
        "complete for this corpus. Non-empty entries are candidates for",
        "Sprint 3 feature exploration.",
        "",
    ]

    if not tally:
        lines.append("_None. Dispatch table covers every observed pair._")
        lines.append("")
        return lines

    lines.append("| event | object | total count | samples |")
    lines.append("|---|---|---:|---:|")
    for key, count in sorted(tally.items(), key=lambda kv: -kv[1]):
        ev, obj = key
        lines.append(
            f"| `{ev}` | `{obj}` | {_fmt_int(count)} | "
            f"{_fmt_int(samples_affected[key])} |"
        )
    lines.append("")
    return lines


def _section_parse_failures(rows: list[dict]) -> list[str]:
    """If the corpus has any parse failures, list them."""
    fails = [r for r in rows if not r.get("parse_ok")]
    lines = ["## Parse failures", ""]
    if not fails:
        lines.append("_None. All samples parsed cleanly._")
        lines.append("")
        return lines
    lines.append("| sample_id | error |")
    lines.append("|---|---|")
    for r in fails[:50]:  # cap for readability
        err = r.get("parse_error") or ""
        err_short = err if len(err) < 120 else err[:117] + "..."
        lines.append(f"| `{r.get('sample_id', '?')}` | {err_short} |")
    if len(fails) > 50:
        lines.append("")
        lines.append(f"_...and {len(fails) - 50} more._")
    lines.append("")
    return lines


def build_report(rows: list[dict]) -> str:
    title = [
        "# Sprint 1 — Corpus-level parser statistics",
        "",
        "Generated from `data/processed/manifest.jsonl`.",
        "Regenerate with `python scripts/build_sprint1_stats.py`.",
        "",
    ]
    sections = (
        _section_headline(rows)
        + _section_event_distribution(rows)
        + _section_entity_distribution(rows)
        + _section_family_comparison(rows)
        + _section_filter_effectiveness(rows)
        + _section_unhandled_enhanced(rows)
        + _section_parse_failures(rows)
    )
    return "\n".join(title + sections)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    if not args.manifest.is_file():
        print(f"[ERROR] manifest not found: {args.manifest}", file=sys.stderr)
        return 1

    rows = load_manifest(args.manifest)
    if not rows:
        print(f"[ERROR] manifest is empty: {args.manifest}", file=sys.stderr)
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    report = build_report(rows)
    args.out.write_text(report, encoding="utf-8")
    print(f"[OK] wrote {args.out} ({len(report):,} chars from "
          f"{len(rows):,} manifest rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
