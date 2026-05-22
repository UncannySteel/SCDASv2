# SCADS — Scalable Chunk-Aware Data Segmentation (Proof of Concept)

SCADS is a four-layer co-designed framework for secure, low-latency access to large
heterogeneous datasets. The four layers — segmentation, smart index, context-aware cache,
and lightweight per-chunk encryption — are designed to share the same unit boundaries so
that each layer's decisions reinforce the others.

---

## What this PoC proves — and what it does not

### The PoC **is**
- A proof-of-concept validating the **complexity claims**: O(1) dictionary lookup vs O(n)
  linear scan over a Python/pandas structure.
- A correct implementation of per-chunk **AES-256-GCM** + **RBAC** + **audit logging**.
- A valid ablation of the α/β cache scoring parameters across four workload types.
- A demonstration of **bounded data exposure per query** (~0.35% per-chunk,
  ~0.10% per-page) through compartmentalized decryption.

### The PoC **is not**
- A benchmark against production engines (Postgres/Spark/MySQL) — all baselines are
  in-process only.
- A distributed-systems result — this is a single-process, GIL-bound implementation.
- A formal security proof — there is no key-management infrastructure, no side-channel
  analysis, and no hardware security module integration.
- A write/update-workload evaluation — all benchmarks are read-heavy analytical queries.

Every reported number carries a scope caveat that specifies the condition under which it
holds and the condition under which it does not.

---

## Architecture

```
 Record stream
      |
      v
 Layer 1 — Segmenter         split records into chunks keyed by (time_window, region, data_type)
      |
      v
 Layer 2 — SmartIndex        dict{chunk_key -> chunk_id}  O(1) average lookup
      |
      v
 Layer 3 — Context-Aware Cache   score = α·freq + β·geo_match  (α=0.6, β=0.4)
      |                           baselines: LRU, LFU, ARC, TinyLFU
      v
 Layer 4 — Lightweight Security  RBAC check → AES-256-GCM decrypt → AuditLogger
      |
      v
 Query result (minimal chunk, bounded exposure)
```

Each layer exports a contract-defined interface so it can be tested in isolation.

---

## Installation

Requires **Python 3.13**.

```bash
pip install -r requirements.txt
```

Pinned versions are in [`requirements.txt`](requirements.txt).

---

## Running the tests

```bash
pytest tests/ -q
```

With coverage:

```bash
pytest tests/ --cov=core --cov-report=term-missing
```

The test suite covers positive (happy-path) and negative (adversarial) cases, including:

- Mock-asserted proof that `decrypt` is **never called** on RBAC denial.
- Tampered ciphertext raises `InvalidTag` (not silently returns garbage).
- All-frames-pinned pressure raises `RuntimeError` (no silent hang).
- Concurrent reads return identical result sets across 20 threads.

---

## Reproducing reported numbers and figures

```bash
python analysis/run_all.py
```

This single command, with a single recorded seed, runs all three benchmarks, writes
`results/*.json`, then generates figures and tables from those same files. Figures and
prose numbers are therefore always in sync (BUG 3 fix from prior PoC iteration).

To use a different seed:

```bash
python analysis/run_all.py 123
```

Figures are written to `results/fig*.png`; tables to `results/table_*.txt`.

---

## Datasets

| Dataset | Status | Records | Notes |
|---------|--------|---------|-------|
| KDD Cup 1999 (10%) | **Real** (if present in `data/raw/`) | 494,021 | Primary dataset. Download `kddcup.data_10_percent` from the UCI ML Repository and place it in `data/raw/`. Falls back to disclosed synthetic data if the file is absent. |
| NYC Taxi | **Synthetic** (disclosed) | up to 100k | Geo-correlated trips over 5 boroughs. If a real TLC CSV/Parquet is placed in `data/raw/`, the loader runs a scipy KS goodness-of-fit check and prints any divergence. |
| Web Logs | **Synthetic** (disclosed) | up to 100k | Timestamps, ~10 GeoIP regions, log levels. Fully seeded; no real file required. |

All synthetic generators use fixed seeds and print a disclosure line at load time. The
`is_synthetic` flag is recorded in every `results/*.json` file alongside the seed used.

---

## Benchmark scope caveats

| Benchmark | Headline number | Scope caveat |
|-----------|----------------|--------------|
| Index | ~2,500–3,000× speedup over pandas at 494k records | Dict O(1) avg lookup vs Python/pandas O(n) boolean scan. DuckDB has an explicit ART index. Does **not** generalise to a networked production DBMS. |
| Cache | SCADS hit-rate 64.6% vs LRU 62.0% at capacity=20, Zipf | In-process eviction policies, geo-correlated queries (locality\_prob=0.80). LRU is expected to win under temporal workloads — reported honestly. |
| Encryption | Per-chunk median 0.41 ms; full-dataset median 389 ms | AES-256-GCM in-process round-trip including JSON serialisation, single thread. Not a hardware-AES throughput benchmark. |

---

## Future work — Paging extension (not benchmarked)

A buffer-pool / page-granularity layer was designed but **not benchmarked**. It is
presented as an architectural extension and complexity derivation only:

```
T_query  = O(1) directory + O(1) pool check + O(|page|) decrypt-on-miss
Exposure = page_size / total_records = 512 / 494,021 = 0.10%
Blast radius = 965× less than full-dataset encryption
```

This projection will appear in the complexity table (Table A). Experimental results for
the paging layer will require a separate benchmarking study.

---

## Acceptance checklist (spec §10)

- [x] All four layers implemented as independent, testable modules with the contracts in §3
- [x] Cache benchmark uses geo-CORRELATED queries (BUG 1 fixed)
- [x] Encryption benchmark times full chunk + true full dataset, 3 scopes, median+p95 (BUG 2 fixed)
- [x] Every metric reported as mean ± 95% CI over N seeded trials, with p50/p95/p99 (BUG 3 fixed)
- [x] Every figure regenerated from the same run as its reported number (no stale artifacts)
- [x] Index benchmark compares SCADS vs pandas vs DuckDB, with build time + memory + miss queries
- [x] Cache benchmark includes LRU, LFU, ARC, TinyLFU across 4 workloads × 4 capacities
- [x] pytest suite passes: positive AND negative cases, including mock-asserted "no decrypt on denial"
- [x] Security properties table (A) and verification table (B) produced
- [x] Paging kept as future-work design + complexity only (not benchmarked)
- [x] Every reported result carries a one-line scope caveat
- [x] Numbers correctly labeled: "vs. Python linear scan," never "vs. production DBMS"
