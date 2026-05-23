# Table D — Cache Policy Comparison

Rows: (capacity, workload). Columns: SCADS hit-rate, cachetools.LRU hit-rate, cachetools.LFU hit-rate, SCADS vs LRU delta (pp), mean lookup latency (µs).

| Capacity | Workload | SCADS HR | ct.LRU HR | ct.LFU HR | SCADS-LRU Δ (pp) | SCADS lat µs | ct.LRU lat µs | ct.LFU lat µs |
|----------|----------|----------|-----------|-----------|-----------------|--------------|---------------|---------------|
|       20 | zipf     | 0.646    | 0.620     | 0.653     | +2.6            | 2.37         | 0.63          | 0.72          |
|       20 | burst    | 0.992    | 0.992     | 0.992     | +0.0            | 0.23         | 0.33          | 0.33          |
|       20 | temporal | 0.609    | 0.807     | 0.496     | -19.8           | 2.79         | 0.45          | 1.09          |
|       20 | uniform  | 0.099    | 0.111     | 0.096     | -1.2            | 5.95         | 1.04          | 1.42          |
|       60 | zipf     | 0.864    | 0.858     | 0.863     | +0.6            | 2.65         | 0.39          | 0.51          |
|       60 | burst    | 1.000    | 1.000     | 1.000     | +0.0            | 0.12         | 0.27          | 0.31          |
|       60 | temporal | 0.972    | 0.982     | 0.965     | -1.0            | 0.67         | 0.47          | 0.49          |
|       60 | uniform  | 0.294    | 0.296     | 0.297     | -0.2            | 12.88        | 0.95          | 1.04          |
|      200 | zipf     | 1.000    | 1.000     | 1.000     | +0.0            | 0.17         | 0.26          | 0.36          |
|      200 | burst    | 1.000    | 1.000     | 1.000     | +0.0            | 0.12         | 0.24          | 0.31          |
|      200 | temporal | 1.000    | 1.000     | 1.000     | +0.0            | 0.14         | 0.32          | 0.45          |
|      200 | uniform  | 0.877    | 0.876     | 0.877     | +0.1            | 9.19         | 0.39          | 0.77          |
|      500 | zipf     | 1.000    | 1.000     | 1.000     | +0.0            | 0.15         | 0.26          | 0.42          |
|      500 | burst    | 1.000    | 1.000     | 1.000     | +0.0            | 0.13         | 0.24          | 0.31          |
|      500 | temporal | 1.000    | 1.000     | 1.000     | +0.0            | 0.15         | 0.31          | 0.38          |
|      500 | uniform  | 1.000    | 1.000     | 1.000     | +0.0            | 0.13         | 0.34          | 0.37          |

> ct = cachetools (cachetools)
> Δ values: positive = SCADS wins, negative = SCADS loses (expected on uniform/temporal).
> Redis: not benchmarked (unavailable).
> Scope: Hit-rate comparison of in-process eviction policies on seeded synthetic KDD-schema workloads with geo-correlated queries (locality_prob=0.80). cachetools.LRUCache and cachetools.LFUCache are faithful in-process implementations of Redis allkeys-lru and allkeys-lfu policies (cited as widely used Pythonic equivalents). This benchmark measures eviction-policy quality only, NOT network latency. SCADS is expected to win under Zipf + geo-skewed workloads and to tie or lose under uniform / temporal workloads — reported honestly. Results do not generalise to production buffer pools or distributed caches.
