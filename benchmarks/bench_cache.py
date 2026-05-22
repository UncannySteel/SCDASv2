"""Cache benchmark: SCADS vs LRU vs LFU vs ARC vs TinyLFU.

Workloads: Zipf, burst, temporal, uniform — each with geo-correlated queries
(BUG 1 fix).  Capacities: {20, 60, 200, 500}.

Honest expected outcomes (report as-is, do not suppress):
  - SCADS wins under capacity-constrained + geo-skewed (Zipf) workloads.
  - LRU wins or ties under temporal workloads.
  - At large capacity (500) most policies converge.

Scope caveat: "Hit-rate comparison of in-process eviction policies on seeded
synthetic KDD-schema workloads. Results do not generalise to production buffer
pools or distributed caches."
"""
from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import data.kdd as kdd_loader
from benchmarks.stats import summarize
from benchmarks.workloads import build_workloads
from core.cache import ARCCache, LFUCache, LRUCache, SCADSCache, TinyLFUCache
from core.segmenter import Segmenter, kdd_extractor
from core.types import ChunkKey

RESULTS_DIR = Path(__file__).resolve().parents[1] / "results"
RESULTS_DIR.mkdir(exist_ok=True)

CAPACITIES = [20, 60, 200, 500]
N_QUERIES = 500
N_RECORDS = 50_000    # small subset — bench finishes in seconds
N_TRIALS = 5
BASE_SEED = 42
LOCALITY_PROB = 0.8   # geo-correlated: 80% chance query_region == chunk.region

SCOPE_CAVEAT = (
    "Hit-rate comparison of in-process eviction policies on seeded synthetic "
    "KDD-schema workloads with geo-correlated queries (locality_prob=0.80). "
    "LRU is expected to win under temporal workloads — reported honestly. "
    "Results do not generalise to production buffer pools or distributed caches."
)

POLICY_CLASSES = {
    "SCADS":    SCADSCache,
    "LRU":      LRUCache,
    "LFU":      LFUCache,
    "ARC":      ARCCache,
    "TinyLFU":  TinyLFUCache,
}


def _run_policy(policy_cls, capacity: int, queries: list, chunk_regions: dict) -> dict:
    """Run one policy over a query list; return hit_rate and timing samples."""
    cache = policy_cls(capacity)
    # Pre-populate cache with the first `capacity` unique keys.
    seen = []
    for key, _ in queries:
        if key not in seen:
            region = chunk_regions.get(key)
            cache.put(key, region)
            seen.append(key)
        if len(seen) >= capacity:
            break

    hit_count = 0
    t0 = time.perf_counter()
    for key, query_region in queries:
        hit = cache.get(key, query_region)
        if not hit:
            region = chunk_regions.get(key)
            cache.put(key, region)
        hit_count += int(hit)
    elapsed = time.perf_counter() - t0

    total = len(queries)
    return {
        "hit_rate": hit_count / total if total else 0.0,
        "hits": hit_count,
        "misses": total - hit_count,
        "elapsed_s": elapsed,
    }


def run(base_seed: int = BASE_SEED) -> None:
    print("=== bench_cache: SCADS / LRU / LFU / ARC / TinyLFU ===")

    result_kdd = kdd_loader.load(n=N_RECORDS, seed=base_seed)
    records = result_kdd["records"]

    seg = Segmenter(kdd_extractor)
    chunks = seg.segment(records)
    keys = list(chunks.keys())
    chunk_regions = {k: c.region for k, c in chunks.items()}

    print(f"  Dataset: {N_RECORDS:,} records -> {len(keys)} chunks")

    output: dict[str, Any] = {
        "benchmark": "cache",
        "seed": base_seed,
        "n_trials": N_TRIALS,
        "n_queries": N_QUERIES,
        "n_records": N_RECORDS,
        "locality_prob": LOCALITY_PROB,
        "scope_caveat": SCOPE_CAVEAT,
        "results": {},
    }

    for cap in CAPACITIES:
        output["results"][str(cap)] = {}
        for wl_name in ("zipf", "burst", "temporal", "uniform"):
            policy_results: dict[str, Any] = {}
            for policy_name, policy_cls in POLICY_CLASSES.items():
                hit_rates: list[float] = []
                elapsed_samples: list[float] = []
                for trial in range(N_TRIALS):
                    trial_rng = random.Random(base_seed + trial * 31 + cap)
                    wl_dict = build_workloads(
                        keys, chunk_regions, N_QUERIES, trial_rng, LOCALITY_PROB
                    )
                    queries = wl_dict[wl_name]
                    res = _run_policy(policy_cls, cap, queries, chunk_regions)
                    hit_rates.append(res["hit_rate"])
                    elapsed_samples.append(res["elapsed_s"])

                hr_stats = summarize(hit_rates, seed=base_seed)
                lat_stats = summarize(
                    [t / N_QUERIES * 1e6 for t in elapsed_samples], seed=base_seed
                )
                policy_results[policy_name] = {
                    "hit_rate": hr_stats,
                    "latency_us_per_query": lat_stats,
                }
                print(
                    f"  cap={cap:3d} {wl_name:8s} {policy_name:8s} "
                    f"hit_rate={hr_stats['mean']:.3f} "
                    f"(p50={hr_stats['p50']:.3f})"
                )

            output["results"][str(cap)][wl_name] = policy_results

    out_path = RESULTS_DIR / "cache.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"  -> {out_path}")


if __name__ == "__main__":
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else BASE_SEED
    run(base_seed=seed)
