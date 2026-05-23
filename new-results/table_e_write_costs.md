# Table E: SCADS Write Costs

## S1 — Insert Throughput

| Dataset size | records/sec (mean) | CI95 low | CI95 high | chunks/sec |
|--------------|--------------------|----------|-----------|------------|
| 10,000 | 167,012 | 165,998 | 168,025 | 9,619.9 |
| 100,000 | 226,708 | 213,510 | 239,906 | 743.6 |
| 494,021 | 233,779 | 226,757 | 240,801 | 175.1 |

## S2 — In-chunk Update Latency

| Metric | Value (µs) |
|--------|------------|
| Mean   | 2527.5 |
| CI95   | [1626.9, 3428.0] |
| p50    | 1662.2 |
| p95    | 2907.9 |
| p99    | 6890.4 |

## S3 — Cross-chunk Update Latency

| Metric | Value (µs) |
|--------|------------|
| Mean   | 4419.1 |
| CI95   | [3312.0, 5526.2] |
| p50    | 2311.4 |
| p95    | 4915.2 |
| p99    | 20781.8 |

## S3/S2 Latency Ratio

| Metric | Ratio |
|--------|-------|
| Mean latency (S3/S2) | 1.75x |

---

> **Framing:** SCADS is read-optimised; cross-chunk updates incur full re-encryption of both source and destination chunks. This is the known cost of chunk-granularity encryption.