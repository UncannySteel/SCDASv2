# `new-results/` — Complete Figure & Table Index

This folder contains **all** SCADS evaluation outputs: the original PoC figures (fig1–fig4) plus the three new baselines added in this round (fig5–fig10), all JSON data, all tables, and the two prose addenda for the paper.

## Figures

| File | Source benchmark | Caption |
|---|---|---|
| `fig1_index_speedup.png` | `benchmarks/bench_index.py` | SCADS vs pandas vs DuckDB lookup latency + SCADS speedup over pandas scan across the KDD size sweep (50k → 494k). |
| `fig2_cache_hitrate.png` | `benchmarks/bench_cache.py` | Cache hit-rate for SCADS / LRU / LFU / ARC / TinyLFU across {zipf, burst, temporal, uniform} workloads at capacity = 20. |
| `fig3_cache_capacity.png` | `benchmarks/bench_cache.py` | SCADS hit-rate vs cache capacity {20, 60, 200, 500} per workload. |
| `fig4_encryption_scopes.png` | `benchmarks/bench_security.py` | AES-256-GCM median + p95 latency for per-page / per-chunk / full-dataset scopes. |
| **`fig5_scads_vs_sqlite_latency.png`** (NEW) | `benchmarks/bench_baseline_sql.py` | SCADS vs SQLite-indexed vs SQLite-unindexed median lookup latency across the KDD size sweep — log scale. |
| **`fig6_throughput_comparison.png`** (NEW) | `benchmarks/bench_baseline_sql.py` | Queries/sec at n = 494,021: SCADS vs SQLite-indexed vs SQLite-unindexed. |
| **`fig7_cache_hitrate_vs_lru_lfu.png`** (NEW) | `benchmarks/bench_baseline_cache.py` | Hit-rate at capacity = 60: SCADS vs cachetools.LRU vs cachetools.LFU across {zipf, burst, temporal, uniform}. |
| **`fig8_cache_skew_sensitivity.png`** (NEW) | `benchmarks/bench_baseline_cache.py` | Hit-rate vs Zipf skew α ∈ {0.5, 0.8, 1.0, 1.2, 1.5} at capacity = 60 — shows where SCADS scoring wins. |
| **`fig9_write_throughput.png`** (NEW) | `benchmarks/bench_writes.py` | Steady-state insert throughput (records/sec) at n ∈ {10k, 100k, 494k}. |
| **`fig10_update_cost_breakdown.png`** (NEW) | `benchmarks/bench_writes.py` | Per-update latency broken down by component (decrypt / mutate / encrypt / index) — in-chunk vs cross-chunk update. |

## Tables

| File | Content |
|---|---|
| `table_a.txt` | Original Table A (index sweep). |
| `table_b.txt` | Original Table B (cache + security summary). |
| **`table_c_sql_baseline.md`** (NEW) | SCADS vs SQLite-indexed vs SQLite-unindexed: p50/p95 latency, QPS, build time, file size, speedup. |
| **`table_d_cache_comparison.md`** (NEW) | SCADS vs cachetools LRU/LFU across (capacity × workload): hit-rate, delta, lookup latency. |
| **`table_e_write_costs.md`** (NEW) | Insert throughput by size; in-chunk vs cross-chunk update latency; S3/S2 ratio. |

## Raw JSON

| File | Schema |
|---|---|
| `index.json` | bench_index sweep output (existing). |
| `cache.json` | bench_cache sweep output (existing). |
| `security.json` | bench_security scopes output (existing). |
| **`baseline_sql.json`** (NEW) | Per-size SCADS / SQLite-indexed / SQLite-unindexed latency stats. |
| **`baseline_cache.json`** (NEW) | Hit-rate per (capacity × workload × policy) including skew-sweep sub-block. |
| **`writes.json`** (NEW) | Insert throughput per size; S2/S3 latency + component breakdown. |

## Prose addenda (paper text)

- **`prose_distributed_integration.md`** — paragraph for Section IV: how SCADS chunk_keys map to Kafka / HDFS / Cassandra partition primitives. Establishes architectural compatibility without requiring a cluster benchmark.
- **`prose_threat_model.md`** — half-column threat model + informal IND-CPA argument by reference to AES-256-GCM. Sized for systems / data-management venues; flags formal-proof work as out of scope.

## Summary

See `SUMMARY.md` in this folder for the reviewer-objection → addressing-artifact mapping.
