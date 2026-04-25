"""
Generate plots for experiments/sprint2_runtime.md from
experiments/sprint2_build_times.csv.

Outputs:
    experiments/sprint2_runtime_linear.png    n_events vs build_time
    experiments/sprint2_runtime_loglog.png    same on log-log scales
    experiments/sprint2_runtime_memory.png    n_events vs peak_memory_kb

Linear regressions and slopes are printed to stdout for inclusion in
the narrative. The script is idempotent: re-run it after a fresh CSV
to regenerate everything.
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

CSV_PATH = Path("experiments/sprint2_build_times.csv")
OUT_DIR = Path("experiments")

# Family colors. Matches the spirit of graph/visualize.py's palette
# discipline (Okabe-Ito-ish, colorblind-safe).
FAMILY_COLORS = {
    "Emotet":   "#0072B2",  # blue
    "Trickbot": "#D55E00",  # vermillion
    "":         "#999999",  # unlabeled / fallback
}


def main() -> int:
    if not CSV_PATH.is_file():
        print(f"[ERROR] {CSV_PATH} not found; run scripts/build_all_graphs.py first",
              file=sys.stderr)
        return 1

    df = pd.read_csv(CSV_PATH)
    print(f"[INFO] loaded {len(df)} rows")
    print(f"[INFO] families: {df['label'].fillna('').value_counts().to_dict()}")

    df["label_filled"] = df["label"].fillna("")
    df["us_per_event"] = df["build_time_s"] * 1e6 / df["n_events"]

    # --- Plot 1: linear scatter -------------------------------------------
    fig, ax = plt.subplots(figsize=(10, 6.5), dpi=120)
    for family, group in df.groupby("label_filled"):
        ax.scatter(
            group["n_events"], group["build_time_s"] * 1000,
            s=8, alpha=0.5, color=FAMILY_COLORS.get(family, "#999999"),
            label=f"{family or 'unlabeled'} (n={len(group)})",
        )
    # Linear regression on full corpus.
    coeffs = np.polyfit(df["n_events"], df["build_time_s"] * 1000, 1)
    xs = np.linspace(df["n_events"].min(), df["n_events"].max(), 100)
    ys = coeffs[0] * xs + coeffs[1]
    ax.plot(xs, ys, "k--", linewidth=1, alpha=0.6,
            label=f"linear fit: y = {coeffs[0]:.4f}x + {coeffs[1]:.3f}")

    # R^2
    y_actual = df["build_time_s"].to_numpy() * 1000
    y_pred = coeffs[0] * df["n_events"].to_numpy() + coeffs[1]
    ss_res = ((y_actual - y_pred) ** 2).sum()
    ss_tot = ((y_actual - y_actual.mean()) ** 2).sum()
    r2 = 1 - ss_res / ss_tot

    ax.set_xlabel("n_events (= |E|)")
    ax.set_ylabel("build time (ms)")
    ax.set_title(
        f"BehaviorGraph build time vs problem size\n"
        f"4,000 samples, min-of-3 timing, R² = {r2:.4f}"
    )
    ax.legend(fontsize=8, loc="upper left")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    out1 = OUT_DIR / "sprint2_runtime_linear.png"
    fig.savefig(out1, bbox_inches="tight")
    plt.close(fig)
    print(f"[OK] {out1}")
    print(f"     slope = {coeffs[0]:.4f} ms/event ({coeffs[0]*1000:.2f} us/event)")
    print(f"     intercept = {coeffs[1]:.4f} ms")
    print(f"     R² = {r2:.6f}")

    # --- Plot 2: log-log --------------------------------------------------
    fig, ax = plt.subplots(figsize=(10, 6.5), dpi=120)
    for family, group in df.groupby("label_filled"):
        ax.scatter(
            group["n_events"], group["build_time_s"] * 1000,
            s=8, alpha=0.5, color=FAMILY_COLORS.get(family, "#999999"),
            label=f"{family or 'unlabeled'}",
        )
    # Log-log fit on samples with n>=50 to avoid timer-noise distortion.
    big = df[df["n_events"] >= 50]
    log_x = np.log10(big["n_events"])
    log_y = np.log10(big["build_time_s"] * 1000)
    log_coeffs = np.polyfit(log_x, log_y, 1)
    xs = np.logspace(np.log10(big["n_events"].min()),
                     np.log10(big["n_events"].max()), 100)
    ys = (10 ** log_coeffs[1]) * (xs ** log_coeffs[0])
    ax.plot(xs, ys, "k--", linewidth=1, alpha=0.6,
            label=f"power-law fit (n>=50): slope = {log_coeffs[0]:.4f}")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("n_events (log scale)")
    ax.set_ylabel("build time, ms (log scale)")
    ax.set_title(
        f"Log-log: build time vs problem size\n"
        f"slope ≈ 1.0 confirms O(|E|) linear scaling"
    )
    ax.legend(fontsize=8, loc="upper left")
    ax.grid(alpha=0.3, which="both")
    fig.tight_layout()
    out2 = OUT_DIR / "sprint2_runtime_loglog.png"
    fig.savefig(out2, bbox_inches="tight")
    plt.close(fig)
    print(f"[OK] {out2}")
    print(f"     log-log slope = {log_coeffs[0]:.4f} (1.0 = linear)")

    # --- Plot 3: memory ---------------------------------------------------
    mem = df.dropna(subset=["peak_memory_kb"])
    mem = mem[mem["peak_memory_kb"] != ""].copy()
    mem["peak_memory_kb"] = mem["peak_memory_kb"].astype(float)
    print(f"[INFO] memory-measured samples: {len(mem)}")

    fig, ax = plt.subplots(figsize=(10, 6.5), dpi=120)
    for family, group in mem.groupby("label_filled"):
        ax.scatter(
            group["n_events"], group["peak_memory_kb"],
            s=12, alpha=0.6, color=FAMILY_COLORS.get(family, "#999999"),
            label=f"{family or 'unlabeled'} (n={len(group)})",
        )
    mem_coeffs = np.polyfit(mem["n_events"], mem["peak_memory_kb"], 1)
    xs = np.linspace(mem["n_events"].min(), mem["n_events"].max(), 100)
    ys = mem_coeffs[0] * xs + mem_coeffs[1]
    ax.plot(xs, ys, "k--", linewidth=1, alpha=0.6,
            label=f"linear fit: y = {mem_coeffs[0]:.3f}x + {mem_coeffs[1]:.1f}")

    y_actual = mem["peak_memory_kb"].to_numpy()
    y_pred = mem_coeffs[0] * mem["n_events"].to_numpy() + mem_coeffs[1]
    ss_res = ((y_actual - y_pred) ** 2).sum()
    ss_tot = ((y_actual - y_actual.mean()) ** 2).sum()
    mem_r2 = 1 - ss_res / ss_tot

    ax.set_xlabel("n_events (= |E|)")
    ax.set_ylabel("peak memory (KB)")
    ax.set_title(
        f"Peak build memory vs problem size\n"
        f"every 10th sample (n={len(mem)}), R² = {mem_r2:.4f}"
    )
    ax.legend(fontsize=8, loc="upper left")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    out3 = OUT_DIR / "sprint2_runtime_memory.png"
    fig.savefig(out3, bbox_inches="tight")
    plt.close(fig)
    print(f"[OK] {out3}")
    print(f"     slope = {mem_coeffs[0]:.3f} KB/event")
    print(f"     intercept = {mem_coeffs[1]:.2f} KB")
    print(f"     R² = {mem_r2:.6f}")

    return 0


if __name__ == "__main__":
    sys.exit(main())