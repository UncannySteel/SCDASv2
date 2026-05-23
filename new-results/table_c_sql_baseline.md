# Table C — SQLite Baseline: SCADS vs SQLite-indexed vs SQLite-unindexed

| n | SCADS p50 (µs) | SQLite-idx p50 (µs) | SQLite-unidx p50 (µs) | SCADS p95 (µs) | SQLite-idx p95 (µs) | SCADS QPS | SQLite-idx QPS | SQLite idx build (s) | SQLite file size (MB) | Speedup (SCADS / SQLite-idx) |
|----|----|----|----|----|----|----|----|----|----|----|
| 50,000 | 0.09 | 1.39 | 6.23 | 0.13 | 2.20 | 9,936,125 | 616,550 | 0.0041 | 0.027 | 16.12× |
| 100,000 | 0.09 | 1.20 | 6.56 | 0.13 | 1.34 | 10,108,303 | 815,471 | 0.0004 | 0.027 | 12.40× |
| 200,000 | 0.09 | 1.23 | 7.44 | 0.14 | 1.36 | 10,159,652 | 796,541 | 0.0008 | 0.027 | 12.75× |
| 300,000 | 0.09 | 1.19 | 7.31 | 0.14 | 1.37 | 10,294,118 | 812,348 | 0.0004 | 0.027 | 12.67× |
| 494,021 | 0.17 | 1.23 | 7.33 | 0.22 | 2.02 | 5,575,468 | 703,270 | 0.0004 | 0.027 | 7.93× |

> **Note:** Speedup > 1× means SCADS is faster; < 1× means SQLite-indexed is faster.
> In-process `:memory:` SQLite, single thread, 200 lookups/trial, 20% miss queries.
> File size measured from a separate file-backed SQLite DB with the same index.