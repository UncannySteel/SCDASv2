"""Generate all publication figures from results/*.json.

Each figure is written to results/ as a PNG.  All numbers come from the JSON
files produced by the benchmark run — no ad-hoc re-runs (BUG 3 fix).

Figures produced
----------------
fig1_index_speedup.png
    SCADS vs pandas speedup across the KDD dataset-size sweep.
fig2_cache_hitrate.png
    Cache hit-rate for SCADS / LRU / LFU / ARC / TinyLFU across
    {Zipf, burst, temporal, uniform} workloads at capacity=20 (most
    discriminating point).
fig3_cache_capacity.png
    SCADS hit-rate vs capacity {20, 60, 200, 500} for each workload.
fig4_encryption_scopes.png
    AES-256-GCM median + p95 latency for per-page / per-chunk /
    full-dataset scopes.
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np


POLICIES = ["SCADS", "LRU", "LFU", "ARC", "TinyLFU"]
WORKLOADS = ["zipf", "burst", "temporal", "uniform"]
CAPACITIES = [20, 60, 200, 500]

SCOPE_CAVEAT_INDEX = (
    "Dict O(1) avg lookup vs Python/pandas O(n) scan.\n"
    "Does not generalise to a networked production DBMS."
)
SCOPE_CAVEAT_CACHE = (
    "In-process eviction policies, geo-correlated queries (locality_prob=0.80).\n"
    "Does not generalise to production buffer pools or distributed caches."
)
SCOPE_CAVEAT_SECURITY = (
    "AES-256-GCM in-process round-trip, single thread.\n"
    "Includes JSON serialisation cost; not a hardware-AES throughput benchmark."
)


def _save(fig: plt.Figure, path: Path, caption: str) -> None:
    fig.text(
        0.5, -0.02, caption,
        ha="center", va="top", fontsize=7, style="italic",
        transform=fig.transFigure, wrap=True,
    )
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  -> {path.name}")


# ---------------------------------------------------------------------------
# Figure 1 — Index speedup
# ---------------------------------------------------------------------------

def fig_index_speedup(data: dict, out: Path) -> None:
    sweep = data["sweep"]
    sizes = [s["n"] for s in sweep]
    speedups = [s["speedup_scads_vs_pandas_scan"] for s in sweep]
    scads_p50 = [s["scads_latency_us"]["p50"] for s in sweep]
    pandas_p50 = [s["pandas_latency_us"]["p50"] for s in sweep]
    duckdb_p50 = [s["duckdb_latency_us"]["p50"] for s in sweep]

    fig, (ax_lat, ax_sp) = plt.subplots(1, 2, figsize=(10, 4))

    size_labels = [f"{n//1000}k" for n in sizes]
    x = np.arange(len(sizes))
    w = 0.25

    ax_lat.bar(x - w, scads_p50, w, label="SCADS", color="#2196F3")
    ax_lat.bar(x,      pandas_p50, w, label="pandas", color="#FF9800")
    ax_lat.bar(x + w,  duckdb_p50, w, label="DuckDB (indexed)", color="#4CAF50")
    ax_lat.set_xticks(x)
    ax_lat.set_xticklabels(size_labels)
    ax_lat.set_ylabel("Median lookup latency (µs)")
    ax_lat.set_xlabel("Dataset size")
    ax_lat.set_title("Lookup latency — median (p50)")
    ax_lat.legend(fontsize=8)
    ax_lat.set_yscale("log")
    ax_lat.yaxis.set_major_formatter(mticker.ScalarFormatter())

    ax_sp.plot(size_labels, speedups, marker="o", color="#2196F3")
    ax_sp.set_ylabel("Speedup vs pandas (×)")
    ax_sp.set_xlabel("Dataset size")
    ax_sp.set_title("SCADS speedup over pandas scan")
    ax_sp.yaxis.set_major_formatter(mticker.ScalarFormatter())
    ax_sp.grid(axis="y", linestyle="--", alpha=0.5)

    fig.suptitle("Figure 1 — Index lookup performance", fontweight="bold")
    _save(fig, out / "fig1_index_speedup.png", SCOPE_CAVEAT_INDEX)


# ---------------------------------------------------------------------------
# Figure 2 — Cache hit-rate at capacity=20 (most discriminating)
# ---------------------------------------------------------------------------

def fig_cache_hitrate(data: dict, out: Path) -> None:
    results = data["results"]
    cap_key = "20"

    policies = POLICIES
    workloads = WORKLOADS
    n_wl = len(workloads)
    n_pol = len(policies)
    x = np.arange(n_wl)
    w = 0.15

    colors = ["#2196F3", "#FF9800", "#4CAF50", "#9C27B0", "#F44336"]

    fig, ax = plt.subplots(figsize=(9, 4))
    for i, (pol, col) in enumerate(zip(policies, colors)):
        means = [results[cap_key][wl][pol]["hit_rate"]["mean"] for wl in workloads]
        offset = (i - n_pol / 2 + 0.5) * w
        ax.bar(x + offset, means, w, label=pol, color=col)

    ax.set_xticks(x)
    ax.set_xticklabels([w.capitalize() for w in workloads])
    ax.set_ylabel("Mean hit-rate")
    ax.set_ylim(0, 1.15)
    ax.set_xlabel("Workload")
    ax.set_title("Figure 2 — Cache hit-rate by policy (capacity = 20)", fontweight="bold")
    ax.legend(fontsize=8, loc="upper right")
    ax.grid(axis="y", linestyle="--", alpha=0.4)

    _save(fig, out / "fig2_cache_hitrate.png", SCOPE_CAVEAT_CACHE)


# ---------------------------------------------------------------------------
# Figure 3 — SCADS hit-rate vs capacity for each workload
# ---------------------------------------------------------------------------

def fig_cache_capacity(data: dict, out: Path) -> None:
    results = data["results"]
    caps = CAPACITIES
    workloads = WORKLOADS

    colors = ["#2196F3", "#FF9800", "#4CAF50", "#9C27B0"]
    markers = ["o", "s", "^", "D"]

    fig, ax = plt.subplots(figsize=(7, 4))
    for wl, col, mk in zip(workloads, colors, markers):
        means = [results[str(c)][wl]["SCADS"]["hit_rate"]["mean"] for c in caps]
        ax.plot(caps, means, marker=mk, color=col, label=wl.capitalize())

    ax.set_xlabel("Cache capacity (chunks)")
    ax.set_ylabel("SCADS mean hit-rate")
    ax.set_title("Figure 3 — SCADS hit-rate vs cache capacity", fontweight="bold")
    ax.legend(fontsize=8)
    ax.set_ylim(0, 1.05)
    ax.grid(linestyle="--", alpha=0.4)

    _save(fig, out / "fig3_cache_capacity.png", SCOPE_CAVEAT_CACHE)


# ---------------------------------------------------------------------------
# Figure 4 — Encryption scopes (median + p95)
# ---------------------------------------------------------------------------

def fig_encryption_scopes(data: dict, out: Path) -> None:
    scopes_data = data["scopes"]

    labels = ["per-page\n(~512 rec)", "per-chunk\n(~1,716 rec)", "full dataset\n(~494k rec)"]
    keys = ["per_page", "per_chunk", "full_dataset"]

    medians = [scopes_data[k]["latency_ms"]["p50"] for k in keys]
    p95s    = [scopes_data[k]["latency_ms"]["p95"] for k in keys]
    means   = [scopes_data[k]["latency_ms"]["mean"] for k in keys]

    x = np.arange(len(keys))
    w = 0.3

    fig, ax = plt.subplots(figsize=(7, 4))
    bars_med = ax.bar(x - w / 2, medians, w, label="Median (p50)", color="#2196F3")
    bars_p95 = ax.bar(x + w / 2, p95s,    w, label="p95",          color="#FF9800", alpha=0.85)

    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("AES-256-GCM round-trip (ms)")
    ax.set_xlabel("Encryption scope")
    ax.set_title("Figure 4 — Encryption latency by scope (median + p95)", fontweight="bold")
    ax.legend(fontsize=8)
    ax.set_yscale("log")
    ax.yaxis.set_major_formatter(mticker.ScalarFormatter())
    ax.grid(axis="y", linestyle="--", alpha=0.4)

    _save(fig, out / "fig4_encryption_scopes.png", SCOPE_CAVEAT_SECURITY)


# ---------------------------------------------------------------------------
# Public entry-point
# ---------------------------------------------------------------------------

def generate(results_dir: Path | None = None) -> None:
    if results_dir is None:
        results_dir = Path(__file__).resolve().parents[1] / "results"
    results_dir = Path(results_dir)

    index_data    = json.loads((results_dir / "index.json").read_text())
    cache_data    = json.loads((results_dir / "cache.json").read_text())
    security_data = json.loads((results_dir / "security.json").read_text())

    fig_index_speedup(index_data, results_dir)
    fig_cache_hitrate(cache_data, results_dir)
    fig_cache_capacity(cache_data, results_dir)
    fig_encryption_scopes(security_data, results_dir)


if __name__ == "__main__":
    import sys
    rd = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    generate(results_dir=rd)
