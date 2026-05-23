"""SQLite B-tree indexed baseline vs SCADS SmartIndex.

New addition to evaluation (§5 supplement):
  - SCADS: dict O(1) average lookup.
  - SQLite-indexed: B-tree index on (time_window, region, data_type).
  - SQLite-unindexed: same table WITHOUT index — sanity-check / full-scan.

Honest framing: if SQLite-indexed matches or beats SCADS on raw lookup latency,
this file reports it as-is.  The defensible SCADS contribution is not "fastest
lookup ever" — it is "co-designed index + cache + encryption, end-to-end."

Reports per dataset size in the KDD sweep {50k, 100k, 200k, 300k, 494021}:
  build_time_s, scads_latency_us, sqlite_indexed_latency_us,
  sqlite_unindexed_latency_us (each: mean, ci95, p50, p95, p99),
  sqlite_file_size_bytes, speedup_scads_vs_sqlite_indexed.

SQLite is stdlib only — no server required.

Also generates:
  new-results/fig5_scads_vs_sqlite_latency.png  (three-line log-scale)
  new-results/fig6_throughput_comparison.png    (bar chart at n=494,021)
  new-results/table_c_sql_baseline.md           (markdown table)
"""
from __future__ import annotations

import json
import os
import random
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

# Ensure project root is importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import data.kdd as kdd_loader
from benchmarks.stats import summarize, run_trials
from core.index import SmartIndex
from core.segmenter import Segmenter, kdd_extractor
from core.types import ChunkKey

RESULTS_DIR = Path(__file__).resolve().parents[1] / "results"
NEW_RESULTS_DIR = Path(__file__).resolve().parents[1] / "new-results"
RESULTS_DIR.mkdir(exist_ok=True)
NEW_RESULTS_DIR.mkdir(exist_ok=True)

SWEEP_SIZES = [50_000, 100_000, 200_000, 300_000, 494_021]
N_TRIALS = 7        # reduce to 5 if 494k exceeds 15 min (noted in JSON)
BASE_SEED = 42
MISS_FRACTION = 0.2
N_LOOKUPS = 200

SCOPE_CAVEAT = (
    "SCADS dict O(1) average lookup vs SQLite B-tree indexed point-lookup "
    "and SQLite full-scan (no index). In-process :memory: SQLite, single thread. "
    "Results do not generalise to a networked production RDBMS. "
    "Build time and SQLite file size are the honest cost of each approach."
)


# ---------------------------------------------------------------------------
# Build helpers
# ---------------------------------------------------------------------------

def _build_scads(records: list[dict]) -> tuple[SmartIndex, dict, list[ChunkKey]]:
    seg = Segmenter(kdd_extractor)
    chunks = seg.segment(records)
    idx = SmartIndex()
    idx.build(chunks)
    keys = list(chunks.keys())
    return idx, chunks, keys


def _build_chunk_rows(records: list[dict]) -> list[tuple]:
    """Return list of (time_window, region, data_type, chunk_id, n_records) tuples."""
    seg = Segmenter(kdd_extractor)
    chunks = seg.segment(records)
    rows = []
    for key, chunk in chunks.items():
        tw, region, dtype = key
        rows.append((tw, region, dtype, chunk.chunk_id, len(chunk)))
    return rows


_CREATE_TABLE = """
CREATE TABLE chunks (
    time_window INTEGER,
    region      INTEGER,
    data_type   TEXT,
    chunk_id    INTEGER,
    n_records   INTEGER
)
"""

_CREATE_INDEX = (
    "CREATE INDEX idx_chunk_key ON chunks (time_window, region, data_type)"
)

_SELECT = (
    "SELECT chunk_id FROM chunks "
    "WHERE time_window=? AND region=? AND data_type=?"
)


def _build_sqlite_inmem(rows: list[tuple], with_index: bool) -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.execute(_CREATE_TABLE)
    con.executemany("INSERT INTO chunks VALUES (?,?,?,?,?)", rows)
    con.commit()
    if with_index:
        con.execute(_CREATE_INDEX)
        con.commit()
    return con


def _build_sqlite_file(rows: list[tuple], with_index: bool, path: str) -> None:
    """Write a file-backed SQLite DB to measure on-disk size."""
    if os.path.exists(path):
        os.remove(path)
    con = sqlite3.connect(path)
    con.execute(_CREATE_TABLE)
    con.executemany("INSERT INTO chunks VALUES (?,?,?,?,?)", rows)
    con.commit()
    if with_index:
        con.execute(_CREATE_INDEX)
        con.commit()
    con.close()


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

def _scads_lookup(idx: SmartIndex, key: ChunkKey) -> list[int]:
    return idx.lookup(key)


def _sqlite_lookup(con: sqlite3.Connection, key: ChunkKey) -> list[int]:
    tw, region, dtype = key
    cur = con.execute(_SELECT, (tw, region, dtype))
    return [r[0] for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Per-size benchmark
# ---------------------------------------------------------------------------

def _bench_size(n: int, base_seed: int) -> dict:
    print(f"  [bench_baseline_sql] n={n:,} …", end=" ", flush=True)
    rng = random.Random(base_seed)

    result = kdd_loader.load(n=n, seed=base_seed)
    records = result["records"]
    is_synthetic = result["is_synthetic"]

    # --- Build SCADS ---
    t0 = time.perf_counter()
    idx, chunks, keys = _build_scads(records)
    scads_build_time_s = time.perf_counter() - t0
    mem_bytes = idx.memory_bytes()

    # --- Build chunk rows once, reused by both SQLite variants ---
    rows = _build_chunk_rows(records)

    # --- Build SQLite (indexed) ---
    t0 = time.perf_counter()
    con_indexed = _build_sqlite_inmem(rows, with_index=True)
    sqlite_indexed_build_time_s = time.perf_counter() - t0

    # --- Build SQLite (unindexed) ---
    con_unindexed = _build_sqlite_inmem(rows, with_index=False)

    # --- File-backed SQLite for on-disk size measurement ---
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        _build_sqlite_file(rows, with_index=True, path=db_path)
        sqlite_file_size_bytes = os.path.getsize(db_path)
    finally:
        try:
            os.remove(db_path)
        except OSError:
            pass

    # --- Query mix: hit keys + miss keys ---
    n_miss = max(1, int(N_LOOKUPS * MISS_FRACTION))
    n_hit = N_LOOKUPS - n_miss
    hit_keys = rng.choices(keys, k=n_hit)
    miss_keys = [(-1, -999, "__miss__")] * n_miss
    query_keys = hit_keys + miss_keys
    rng.shuffle(query_keys)

    def _trial_scads() -> None:
        for key in query_keys:
            _scads_lookup(idx, key)

    def _trial_sqlite_indexed() -> None:
        for key in query_keys:
            _sqlite_lookup(con_indexed, key)

    def _trial_sqlite_unindexed() -> None:
        for key in query_keys:
            _sqlite_lookup(con_unindexed, key)

    times_scads     = run_trials(_trial_scads,              N_TRIALS, base_seed)
    times_indexed   = run_trials(_trial_sqlite_indexed,     N_TRIALS, base_seed)
    times_unindexed = run_trials(_trial_sqlite_unindexed,   N_TRIALS, base_seed)

    def _to_us(times: list[float]) -> list[float]:
        return [t / N_LOOKUPS * 1e6 for t in times]

    s_scads     = summarize(_to_us(times_scads),     seed=base_seed)
    s_indexed   = summarize(_to_us(times_indexed),   seed=base_seed)
    s_unindexed = summarize(_to_us(times_unindexed), seed=base_seed)

    speedup = (
        s_indexed["mean"] / s_scads["mean"]
        if s_scads["mean"] > 0 else None
    )
    # Positive speedup > 1 means SCADS is faster; < 1 means SQLite-indexed is faster.
    # Reported honestly; no massaging.

    con_indexed.close()
    con_unindexed.close()

    label = "SCADS faster" if (speedup or 0) > 1 else "SQLite-indexed faster or tied"
    print(
        f"SCADS {s_scads['p50']:.2f} µs  "
        f"SQLite-idx {s_indexed['p50']:.2f} µs  "
        f"SQLite-noscan {s_unindexed['p50']:.2f} µs  "
        f"speedup(SCADS/idx)={speedup:.2f}× [{label}]"
    )

    return {
        "n": n,
        "is_synthetic": is_synthetic,
        "n_chunks": len(keys),
        "n_lookups_per_trial": N_LOOKUPS,
        "miss_fraction": MISS_FRACTION,
        "n_trials": N_TRIALS,
        "scads_build_time_s": scads_build_time_s,
        "scads_index_memory_bytes": mem_bytes,
        "sqlite_indexed_build_time_s": sqlite_indexed_build_time_s,
        "sqlite_file_size_bytes": sqlite_file_size_bytes,
        "scads_latency_us": s_scads,
        "sqlite_indexed_latency_us": s_indexed,
        "sqlite_unindexed_latency_us": s_unindexed,
        "speedup_scads_vs_sqlite_indexed": speedup,
        "honest_note": label,
    }


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------

def _save_fig(fig: plt.Figure, path: Path, caption: str) -> None:
    fig.text(
        0.5, -0.02, caption,
        ha="center", va="top", fontsize=7, style="italic",
        transform=fig.transFigure, wrap=True,
    )
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  -> {path.name}")


def _fig5_latency(sweep: list[dict], out: Path) -> None:
    """Three-line log-scale: median lookup latency vs dataset size."""
    sizes = [s["n"] for s in sweep]
    size_labels = [f"{n // 1000}k" if n < 494_021 else "494k" for n in sizes]
    scads_p50     = [s["scads_latency_us"]["p50"]            for s in sweep]
    idx_p50       = [s["sqlite_indexed_latency_us"]["p50"]   for s in sweep]
    unidx_p50     = [s["sqlite_unindexed_latency_us"]["p50"] for s in sweep]

    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.plot(size_labels, scads_p50,  marker="o", color="#2196F3", label="SCADS (dict O(1))")
    ax.plot(size_labels, idx_p50,    marker="s", color="#4CAF50", label="SQLite-indexed (B-tree)")
    ax.plot(size_labels, unidx_p50,  marker="^", color="#FF9800", label="SQLite-unindexed (full scan)")

    ax.set_yscale("log")
    ax.yaxis.set_major_formatter(mticker.ScalarFormatter())
    ax.set_xlabel("Dataset size (records)", fontsize=11)
    ax.set_ylabel("Median lookup latency (µs) — log scale", fontsize=11)
    ax.set_title("Figure 5 — SCADS vs SQLite indexed/unindexed lookup latency", fontweight="bold")
    ax.legend(fontsize=9)
    ax.grid(linestyle="--", alpha=0.4)

    caption = (
        "Median per-query latency (p50) over 7 seeded trials, 200 lookups/trial, "
        "20% miss queries. In-process :memory: SQLite. "
        "Does not generalise to a networked production RDBMS."
    )
    _save_fig(fig, out / "fig5_scads_vs_sqlite_latency.png", caption)


def _fig6_throughput(sweep: list[dict], out: Path) -> None:
    """Bar chart at n=494,021: queries/sec for each system."""
    entry = next((s for s in sweep if s["n"] == 494_021), sweep[-1])

    def _qps(stat: dict) -> float:
        mean_us = stat["mean"]
        if mean_us <= 0:
            return float("nan")
        return 1e6 / mean_us  # µs -> QPS

    qps_scads     = _qps(entry["scads_latency_us"])
    qps_indexed   = _qps(entry["sqlite_indexed_latency_us"])
    qps_unindexed = _qps(entry["sqlite_unindexed_latency_us"])

    labels = ["SCADS\n(dict O(1))", "SQLite-indexed\n(B-tree)", "SQLite-unindexed\n(full scan)"]
    values = [qps_scads, qps_indexed, qps_unindexed]
    colors = ["#2196F3", "#4CAF50", "#FF9800"]

    fig, ax = plt.subplots(figsize=(7, 4.5))
    bars = ax.bar(labels, values, color=colors, width=0.5)

    # Log scale if values span > 100×
    max_v = max(v for v in values if v == v)  # skip NaN
    min_v = min(v for v in values if v == v and v > 0)
    use_log = (max_v / min_v) > 100 if min_v > 0 else False
    if use_log:
        ax.set_yscale("log")
        ax.yaxis.set_major_formatter(mticker.ScalarFormatter())

    for bar, val in zip(bars, values):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() * 1.05,
            f"{val:,.0f}",
            ha="center", va="bottom", fontsize=9,
        )

    ax.set_ylabel("Queries per second" + (" (log scale)" if use_log else ""), fontsize=11)
    ax.set_xlabel("Index / storage engine", fontsize=11)
    ax.set_title(
        "Figure 6 — Throughput comparison at n = 494,021",
        fontweight="bold",
    )
    ax.grid(axis="y", linestyle="--", alpha=0.4)

    caption = (
        "QPS derived from mean per-query latency at n=494,021. "
        "In-process :memory: SQLite, single thread, 20% miss queries."
    )
    _save_fig(fig, out / "fig6_throughput_comparison.png", caption)


# ---------------------------------------------------------------------------
# Markdown table
# ---------------------------------------------------------------------------

def _table_c(sweep: list[dict], out: Path) -> None:
    lines = [
        "# Table C — SQLite Baseline: SCADS vs SQLite-indexed vs SQLite-unindexed",
        "",
        "| n | SCADS p50 (µs) | SQLite-idx p50 (µs) | SQLite-unidx p50 (µs) "
        "| SCADS p95 (µs) | SQLite-idx p95 (µs) "
        "| SCADS QPS | SQLite-idx QPS | SQLite idx build (s) "
        "| SQLite file size (MB) | Speedup (SCADS / SQLite-idx) |",
        "|----|----|----|----|----|----|----|----|----|----|----|",
    ]
    for s in sweep:
        n = s["n"]
        sc   = s["scads_latency_us"]
        idx  = s["sqlite_indexed_latency_us"]
        unidx = s["sqlite_unindexed_latency_us"]

        scads_qps = 1e6 / sc["mean"]   if sc["mean"]  > 0 else float("nan")
        idx_qps   = 1e6 / idx["mean"]  if idx["mean"] > 0 else float("nan")

        file_mb = s["sqlite_file_size_bytes"] / 1_048_576
        speedup = s["speedup_scads_vs_sqlite_indexed"]
        speedup_str = f"{speedup:.2f}×" if speedup is not None else "N/A"

        lines.append(
            f"| {n:,} "
            f"| {sc['p50']:.2f} "
            f"| {idx['p50']:.2f} "
            f"| {unidx['p50']:.2f} "
            f"| {sc['p95']:.2f} "
            f"| {idx['p95']:.2f} "
            f"| {scads_qps:,.0f} "
            f"| {idx_qps:,.0f} "
            f"| {s['sqlite_indexed_build_time_s']:.4f} "
            f"| {file_mb:.3f} "
            f"| {speedup_str} |"
        )

    lines += [
        "",
        "> **Note:** Speedup > 1× means SCADS is faster; < 1× means SQLite-indexed is faster.",
        "> In-process `:memory:` SQLite, single thread, 200 lookups/trial, 20% miss queries.",
        "> File size measured from a separate file-backed SQLite DB with the same index.",
    ]

    path = out / "table_c_sql_baseline.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  -> {path.name}")


# ---------------------------------------------------------------------------
# PIL validation
# ---------------------------------------------------------------------------

def _validate_png(path: Path) -> dict:
    """Return validation result dict."""
    result = {"path": str(path), "exists": False, "size_bytes": 0,
              "pil_ok": False, "dimensions": None, "passed": False}
    if not path.exists():
        return result
    result["exists"] = True
    result["size_bytes"] = path.stat().st_size
    if result["size_bytes"] < 5120:
        return result
    try:
        from PIL import Image
        img = Image.open(path)
        w, h = img.size
        result["dimensions"] = (w, h)
        img.verify()
        result["pil_ok"] = True
        result["passed"] = w >= 400 and h >= 300
    except Exception as exc:
        result["pil_error"] = str(exc)
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(base_seed: int = BASE_SEED) -> None:
    print("=== bench_baseline_sql: SCADS vs SQLite indexed vs unindexed ===")
    output: dict[str, Any] = {
        "benchmark": "baseline_sql",
        "seed": base_seed,
        "n_trials": N_TRIALS,
        "scope_caveat": SCOPE_CAVEAT,
        "sweep": [],
    }

    for n in SWEEP_SIZES:
        entry = _bench_size(n, base_seed)
        output["sweep"].append(entry)

    # Write JSON to both results/ and new-results/
    for dest in (RESULTS_DIR, NEW_RESULTS_DIR):
        out_path = dest / "baseline_sql.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2)
        print(f"  -> {out_path}")

    sweep = output["sweep"]

    # --- Figures ---
    print("\nGenerating figures …")
    _fig5_latency(sweep, NEW_RESULTS_DIR)
    _fig6_throughput(sweep, NEW_RESULTS_DIR)

    # --- Table ---
    print("\nGenerating markdown table …")
    _table_c(sweep, NEW_RESULTS_DIR)

    # --- Validation ---
    print("\nValidating PNGs …")
    pngs = [
        NEW_RESULTS_DIR / "fig5_scads_vs_sqlite_latency.png",
        NEW_RESULTS_DIR / "fig6_throughput_comparison.png",
    ]
    all_passed = True
    for p in pngs:
        v = _validate_png(p)
        status = "PASS" if v["passed"] else "FAIL"
        print(
            f"  {status}  {p.name}  "
            f"size={v['size_bytes']:,}B  dims={v['dimensions']}  pil_ok={v['pil_ok']}"
        )
        if not v["passed"]:
            all_passed = False

    output["validation"] = {
        str(p.name): _validate_png(p) for p in pngs
    }
    # Re-write JSON with validation block
    for dest in (RESULTS_DIR, NEW_RESULTS_DIR):
        out_path = dest / "baseline_sql.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2)

    if all_passed:
        print("\nAll PNG validations passed.")
    else:
        print("\nWARNING: one or more PNG validations FAILED.")

    print("\nDone.")


if __name__ == "__main__":
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else BASE_SEED
    run(base_seed=seed)
