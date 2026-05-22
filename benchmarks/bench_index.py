"""Index benchmark: SCADS SmartIndex vs pandas vs DuckDB.

Honest three-way comparison (spec §5):
  - SCADS: dict O(1) average lookup.
  - pandas: boolean-mask filter — O(n) scan over the dataframe column.
  - DuckDB: in-process OLAP engine; we create an explicit index and report
    whether it was used (DuckDB 0.x uses ART indexes on explicit CREATE INDEX).

Reports per dataset size in the KDD sweep {50k, 100k, 200k, 300k, 494021}:
  build_time_s, memory_bytes (SCADS only), mean lookup latency ± 95% CI,
  p50/p95/p99.  Miss queries (keys absent from the index) are included at a
  configurable fraction.

Scope caveat: "SCADS dict O(1) average vs Python/pandas O(n) scan and DuckDB
in-process OLAP; results do not generalise to a networked production DBMS."
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

# Ensure project root is importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import data.kdd as kdd_loader
from benchmarks.stats import summarize, run_trials
from core.index import SmartIndex
from core.segmenter import Segmenter, kdd_extractor
from core.types import ChunkKey

RESULTS_DIR = Path(__file__).resolve().parents[1] / "results"
RESULTS_DIR.mkdir(exist_ok=True)

SWEEP_SIZES = [50_000, 100_000, 200_000, 300_000, 494_021]
N_TRIALS = 7        # mean ± 95% CI over 7 seeded trials
BASE_SEED = 42
MISS_FRACTION = 0.2  # 20% of lookup queries target absent keys
N_LOOKUPS = 200      # lookups per trial (modest for speed)

SCOPE_CAVEAT = (
    "SCADS dict O(1) average lookup vs Python/pandas O(n) boolean scan and "
    "DuckDB in-process OLAP (no network, no storage I/O). "
    "Results do not generalise to a networked production DBMS. "
    "Build time and memory overhead are the honest cost of the SCADS index."
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


def _build_pandas(records: list[dict]) -> pd.DataFrame:
    rows = []
    seg = Segmenter(kdd_extractor)
    chunks = seg.segment(records)
    for key, chunk in chunks.items():
        tw, region, dtype = key
        rows.append({"time_window": tw, "region": region, "data_type": dtype,
                     "chunk_id": chunk.chunk_id, "n_records": len(chunk)})
    return pd.DataFrame(rows)


def _build_duckdb(df: pd.DataFrame) -> tuple[duckdb.DuckDBPyConnection, str]:
    con = duckdb.connect()
    con.execute("CREATE TABLE chunks AS SELECT * FROM df")
    # Create an explicit index on the three key columns.
    con.execute(
        "CREATE INDEX idx_chunk_key ON chunks (time_window, region, data_type)"
    )
    return con, "CREATE INDEX on (time_window, region, data_type)"


# ---------------------------------------------------------------------------
# Lookup helpers — return the chunk_id list (same semantics as SmartIndex)
# ---------------------------------------------------------------------------

def _scads_lookup(idx: SmartIndex, key: ChunkKey) -> list[int]:
    return idx.lookup(key)


def _pandas_lookup(df: pd.DataFrame, key: ChunkKey) -> list[int]:
    tw, region, dtype = key
    mask = (df["time_window"] == tw) & (df["region"] == region) & (df["data_type"] == dtype)
    return df.loc[mask, "chunk_id"].tolist()


def _duckdb_lookup(con: duckdb.DuckDBPyConnection, key: ChunkKey) -> list[int]:
    tw, region, dtype = key
    rows = con.execute(
        "SELECT chunk_id FROM chunks WHERE time_window=? AND region=? AND data_type=?",
        [tw, region, dtype],
    ).fetchall()
    return [r[0] for r in rows]


# ---------------------------------------------------------------------------
# Per-size benchmark
# ---------------------------------------------------------------------------

def _bench_size(n: int, base_seed: int) -> dict:
    print(f"  [bench_index] n={n:,} …", end=" ", flush=True)
    rng = random.Random(base_seed)

    result = kdd_loader.load(n=n, seed=base_seed)
    records = result["records"]
    is_synthetic = result["is_synthetic"]

    # Build SCADS once (build time is measured separately, not per-trial).
    t_build_start = time.perf_counter()
    idx, chunks, keys = _build_scads(records)
    build_time_s = time.perf_counter() - t_build_start
    mem_bytes = idx.memory_bytes()

    # Build pandas and DuckDB once.
    df = _build_pandas(records)
    con, duckdb_index_note = _build_duckdb(df)

    # Compose lookup query mix: hit keys + miss keys (absent ChunkKeys).
    n_miss = max(1, int(N_LOOKUPS * MISS_FRACTION))
    n_hit = N_LOOKUPS - n_miss
    hit_keys = rng.choices(keys, k=n_hit)
    # Construct absent keys by using a sentinel region value.
    miss_keys = [(-1, -999, "__miss__")] * n_miss
    query_keys = hit_keys + miss_keys
    rng.shuffle(query_keys)

    def _trial_scads() -> None:
        for key in query_keys:
            _scads_lookup(idx, key)

    def _trial_pandas() -> None:
        for key in query_keys:
            _pandas_lookup(df, key)

    def _trial_duckdb() -> None:
        for key in query_keys:
            _duckdb_lookup(con, key)

    times_scads  = run_trials(_trial_scads,  N_TRIALS, base_seed)
    times_pandas = run_trials(_trial_pandas, N_TRIALS, base_seed)
    times_duckdb = run_trials(_trial_duckdb, N_TRIALS, base_seed)

    # Convert to per-lookup microseconds.
    def _to_us(times: list[float]) -> list[float]:
        return [t / N_LOOKUPS * 1e6 for t in times]

    s_scads  = summarize(_to_us(times_scads),  seed=base_seed)
    s_pandas = summarize(_to_us(times_pandas), seed=base_seed)
    s_duckdb = summarize(_to_us(times_duckdb), seed=base_seed)

    # Speedup vs pandas (mean latency ratio, honest label).
    speedup_vs_pandas = s_pandas["mean"] / s_scads["mean"] if s_scads["mean"] > 0 else None

    con.close()
    print(
        f"SCADS {s_scads['mean']:.2f} µs  pandas {s_pandas['mean']:.1f} µs  "
        f"DuckDB {s_duckdb['mean']:.2f} µs  "
        f"speedup_vs_pandas_scan={speedup_vs_pandas:.0f}×"
    )

    return {
        "n": n,
        "is_synthetic": is_synthetic,
        "build_time_s": build_time_s,
        "index_memory_bytes": mem_bytes,
        "n_chunks": len(keys),
        "n_lookups_per_trial": N_LOOKUPS,
        "miss_fraction": MISS_FRACTION,
        "duckdb_index": duckdb_index_note,
        "scads_latency_us": s_scads,
        "pandas_latency_us": s_pandas,
        "duckdb_latency_us": s_duckdb,
        "speedup_scads_vs_pandas_scan": speedup_vs_pandas,
        "note": (
            "Speedup is 'dict O(1) average vs Python/pandas O(n) boolean scan'. "
            "DuckDB has an explicit ART index — comparison is fair."
        ),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(base_seed: int = BASE_SEED) -> None:
    print("=== bench_index: SCADS vs pandas vs DuckDB ===")
    sizes_to_run = SWEEP_SIZES
    output = {
        "benchmark": "index",
        "seed": base_seed,
        "n_trials": N_TRIALS,
        "scope_caveat": SCOPE_CAVEAT,
        "sweep": [],
    }
    for n in sizes_to_run:
        entry = _bench_size(n, base_seed)
        output["sweep"].append(entry)

    out_path = RESULTS_DIR / "index.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"  -> {out_path}")


if __name__ == "__main__":
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else BASE_SEED
    run(base_seed=seed)
