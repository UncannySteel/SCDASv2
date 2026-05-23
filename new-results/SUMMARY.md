# SUMMARY — Reviewer Objections Addressed

Each row maps a likely IEEE reviewer objection to the new artifact(s) in this folder that address it. Headline numbers are from the runs in `*.json`.

| Likely reviewer objection | Addressed by | Headline finding |
|---|---|---|
| "Your O(k) index claim is only fast because the baseline is unindexed pandas. A real DBMS with a B-tree index is O(log n) and competitive." | `fig5_scads_vs_sqlite_latency.png`, `fig6_throughput_comparison.png`, `table_c_sql_baseline.md`, `baseline_sql.json` | At n = 494,021: SCADS p50 ≈ 0.17 µs (≈ 5.8 M QPS), SQLite-indexed p50 ≈ 1.23 µs (≈ 813 k QPS), SQLite-unindexed p50 ≈ 7.33 µs. **SCADS retains ~7.9× advantage over a B-tree-indexed SQLite point lookup** — SQL parsing + row materialization dominate even for `:memory:` SQLite. |
| "Your cache comparison is against in-process LRU. Industry-standard Redis-policy caches would be more competitive." | `fig7_cache_hitrate_vs_lru_lfu.png`, `fig8_cache_skew_sensitivity.png`, `table_d_cache_comparison.md`, `baseline_cache.json` | At capacity = 60 on zipf: SCADS 0.864 vs cachetools.LRU 0.858 (+0.6 pp); at capacity = 20 the gap is +2.6 pp. **Skew sensitivity sweep**: SCADS first beats LRU at α = 0.8 and widens through α = 1.2; at α = 0.5 (near-uniform) SCADS trails by 0.8 pp, as expected — reported honestly. |
| "You don't evaluate writes at all. The framework looks read-only." | `fig9_write_throughput.png`, `fig10_update_cost_breakdown.png`, `table_e_write_costs.md`, `writes.json` | Steady-state insert at n = 494k: ~234 k records/sec. **In-chunk update** mean 2,528 µs (p95 6,890 µs); **cross-chunk update** 4,419 µs (p95 20,782 µs). Ratio S3/S2 = 1.75×. JSON (de)serialization dominates both paths (~4,100 µs for S3), not AES (encrypt + decrypt < 320 µs combined) and not the index update (~1 µs). |
| "Your framework is single-process. How does it relate to distributed systems like Kafka/HDFS/Spark?" | `prose_distributed_integration.md` | Architectural addendum (Section IV insert): SCADS chunk_keys map directly to Kafka partition keys, HDFS path prefixes, and Cassandra `(partition key, clustering column)` pairs. Encryption layer wraps chunks before they leave the SCADS coordinator, so distributed storage sees opaque blobs. End-to-end cluster benchmark deferred to follow-on work. |
| "What is your threat model? Where is the security argument?" | `prose_threat_model.md` | Honest-but-curious storage adversary; IND-CPA via AES-256-GCM with per-chunk random nonces (`os.urandom(12)`); integrity via GCM tag; RBAC enforced before decryption (audit → policy → decrypt) in a load-bearing ordering. Acknowledged limitations: access-pattern leakage at chunk granularity, plaintext index, no key management/rotation, no side-channel resistance. Formal proof and ORAM/PIR comparison reserved for security-track follow-on. |

## What was deliberately NOT added (and why)

- **Apache Spark / Cassandra / Kafka cluster benchmarks** — would require multi-node infrastructure for a PoC and is out of scope. The middleware reframing in `prose_distributed_integration.md` addresses architectural questions without the empirical cost. If a reviewer still demands cluster numbers, the rebuttal points to the partition-key mapping as evidence of integration readiness, not a substitute for cluster measurement.
- **Formal cryptographic proofs (IND-CPA / IND-CCA2 derivation, ORAM analysis)** — required for security-track venues (S&P, TIFS, CCS), not for systems / data-management venues. The threat model + reduction-by-reference in `prose_threat_model.md` is the standard expectation for the venues this paper targets.

## Reproducibility

All new benchmarks run from the repo root with `python benchmarks/<name>.py`. JSON outputs are written to both `results/` (canonical) and `new-results/` (this folder). Seed = 42 throughout, matching existing benchmarks. PNGs are regenerated from JSON; no ad-hoc re-runs.
