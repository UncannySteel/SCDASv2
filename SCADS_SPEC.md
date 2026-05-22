# SCADS — Proof-of-Concept Build Specification

**Purpose of this file:** a self-contained brief to (re)build the SCADS proof-of-concept
correctly in a fresh session. It captures (1) the paper's core idea, (2) what the PoC must
implement, (3) the measurement mistakes to avoid, (4) the test suite, and (5) acceptance
criteria. Read top to bottom before writing code.

> **Framing rule that governs everything below:** the numbers from the prototype are real but
> must be *correctly labeled*. We never inflate. A speedup is "dictionary lookup vs. Python
> linear scan," not "vs. a production database." Honesty is a feature of this PoC, not a
> caveat bolted on at the end.

---

## 1. The Idea in One Paragraph

SCADS (Scalable Chunk-Aware Data Segmentation) is a four-layer framework for secure,
low-latency access to large heterogeneous datasets. It treats four concerns that are usually
designed in isolation — segmentation, indexing, caching, encryption — as **co-designed,
mutually-aware layers**:

1. **Segmentation** splits records into 3D chunks `(time_window × region × data_type)`,
   shrinking the search space before any I/O.
2. **Smart Index** maps `chunk_key → chunk location` in a dictionary, turning O(n) full scans
   into O(k) chunk resolution.
3. **Context-Aware Cache** scores chunks by frequency *and* geographic match instead of plain
   recency, so it outperforms LRU/LFU under skewed, geo-correlated workloads.
4. **Lightweight Security** encrypts only the chunks a query touches (per-chunk AES-256),
   gated by RBAC checked *before* decryption, with an audit log of every access.

The thesis: co-design produces combined performance + security that bolt-together stacks
cannot, because each layer respects the others' boundaries (encryption scope = query scope =
cache unit = index unit).

---

## 2. Honest Positioning (what the PoC proves vs. does not)

The PoC **is**:
- A proof-of-concept that validates the **complexity claims** (O(1)/O(k) lookup beats O(n) scan).
- A correct implementation of per-chunk AES-256 + RBAC + audit logging.
- A valid ablation of the α/β cache scoring parameters.
- A demonstration of **bounded data exposure per query** (compartmentalization).

The PoC **is not**:
- A benchmark against production engines (Postgres/Spark) — baselines are in-process only.
- A distributed-systems result (single process, GIL-bound concurrency).
- A formal security proof (no key management, no side-channel analysis).
- A write/update-workload evaluation (read-heavy analytical queries only).

Every claim in the writeup must carry a one-line scope caveat:
*"This advantage holds under [condition] and does not hold under [condition]."*

---

## 3. Architecture & Layer Contracts

Build these as independent, testable modules. Each layer has an explicit contract.

### Layer 1 — Segmentation
- **Input:** list of records (dicts).
- **Output:** chunks keyed by `chunk_key = (time_window, region, data_type)`.
- **Pluggable extractors** per dataset (time/geo/type → key components). No single hardcoded schema.
- **Published chunk_key recipes** (must match the writeup exactly):
  - KDD (network): `(src_bytes-derived time bucket, dst_bytes-derived region, protocol_type → type)`
  - NYC Taxi (geo): `(pickup_hour % 24, borough_id, trip_type)`
  - Web Logs (events): `(timestamp_hour % 24, geo_region, log_level)`
- **Contract:** every record lands in exactly one chunk; no records lost across
  segment→reassemble; partial last chunk handled.

### Layer 2 — Smart Index
- **Structure:** `dict{chunk_key: chunk_id_or_list}`.
- **Lookup:** O(1) average dictionary access. (Pick one complexity label — O(1) — and use it
  consistently everywhere; do not say O(1) in one place and O(k) in another.)
- **Contract:** lookup of an ingested key returns its chunk(s); lookup of an absent key returns
  empty, never raises; distinct chunks never share IDs.
- **Must record:** index build time and index memory footprint (needed for honest comparison).

### Layer 3 — Context-Aware Cache
- **Scoring function (exact):**
  `Score = α · min(access_count / 10, 1.0) + β · (geo_match ? 1.0 : 0.2)`
  with `α + β = 1`, default `α=0.6, β=0.4`.
- **Grace period:** newly inserted chunks are exempt from immediate eviction (cold-start guard).
- **Baselines to compare against:** LRU, LFU, **ARC** (Megiddo & Modha 2003), **TinyLFU**.
  ARC and TinyLFU are the modern expected baselines — include both or do not claim them.
- **Adaptive variant** (online α/β tuning): may exist in code but is **future work**, not evaluated.

### Layer 4 — Lightweight Security
- **Per-chunk AES-256** (AES-GCM via `cryptography.hazmat`).
- **RBAC check happens BEFORE decryption** — this ordering is a load-bearing claim and must be
  test-provable (see §6, mock test asserting `decrypt.call_count == 0` on denial).
- **AuditLogger** records every access (granted or denied); audit cannot be bypassed by any
  data-touching code path.
- **Contract:** unauthorized role → empty result, no exception, no decrypt call; tampered
  ciphertext → raises (InvalidTag); wrong key → raises.

### End-to-End Pipeline
`Index → Cache → Fetch → AccessControl/Audit → Decrypt → Return`
Each query resolves to a minimal chunk before any I/O or decryption.

---

## 4. Datasets

- **KDD** (real): 10% subset, 494,021 records. Network intrusion data; columns documented in the
  KDD handoff. This is the primary real dataset.
- **NYC Taxi** (synthetic, up to 100k): geo-correlated trips mapped to 5 boroughs. *If feasible,
  also support the real TLC CSV and validate the synthetic generator against it
  (goodness-of-fit) — state any divergence.*
- **Web Logs** (synthetic, up to 100k): timestamps, ~10 GeoIP regions, log levels.
- **Scalability sweep:** KDD subset sizes {50k, 100k, 200k, 300k, 494,021}.

Synthetic data is acceptable for a PoC **only if disclosed** and, where possible, validated
against a real distribution.

---

## 5. Benchmarks — and the THREE bugs the old PoC had (do not repeat)

> These three are the difference between a credible PoC and one a reviewer can dismantle.

### BUG 1 — Cache benchmark randomized the geo signal (most important)
The old harness drew the query region with `random.choice(regions)` *independently of the chunk
being accessed*, so `geo_match` was pure noise and the cache's central thesis was never actually
measured.
**Required fix:** the query region must be **correlated with the accessed chunk's true region**
with a tunable locality probability (e.g. with prob p the query region == chunk region; else a
random other region). The whole point is to measure geo-locality, so the benchmark must contain
geo-locality.

### BUG 2 — Encryption baseline was fake
The old code encrypted only `chunk.records[:5]` and a "full dataset" of just 20×5 records, then
plotted a misleading ratio the paper itself disowned.
**Required fix:** time encryption round-trips over the **full chunk payload** and a **true
whole-dataset blob**. Report three scopes side by side:
- per-page (~512 records, if paging exists)
- per-chunk (~1,716 records)
- full dataset (~494k records)
Report median + p95. Any number from the old fake baseline is invalid and must be regenerated.

### BUG 3 — Single seed, no variance, figures disagreed with text
One fixed `seed=42`, single trial, and committed figures showed different numbers than the prose
(e.g. fig said 4787× while text said 38,000×) because figures and tables came from different runs.
**Required fix:**
- Parameterize the seed; run **N trials** per metric; report **mean ± 95% CI** and **p50/p95/p99**.
- Generate **every figure from the same run** that produces the reported number, so figures and
  text always agree. No stale artifacts.

### Index benchmark — honest three-way comparison
Compare SCADS against **pandas** and **DuckDB** (in-process OLAP), not just a Python list scan.
- Give DuckDB an index too (or clearly report it has none) — do not trade one unfair baseline
  for another.
- Report SCADS index **build time and memory** alongside lookup time.
- Include a fraction of **miss queries** (keys not in the index), not only guaranteed hits.
- Honest expected outcome: SCADS crushes pandas; may or may not beat DuckDB on raw latency. If it
  loses to DuckDB, the framing becomes *"comparable lookup latency while adding per-chunk
  encryption + RBAC that DuckDB does not provide natively"* — a stronger, more credible paper.

### Workload sensitivity
Run cache benchmarks across **Zipf, burst, temporal, uniform** distributions and capacities
**{20, 60, 200, 500}** chunks. Report SCADS / LRU / LFU / ARC / TinyLFU side by side. Expect:
SCADS dominates under capacity-constrained + geo-skewed; ties at well-provisioned capacity; LRU
wins under temporal — **report this honestly**.

### Concurrency
1, 5, 10, 25, 50 threads. Report throughput + p95. Frame as **GIL-bound single-process**, not a
true concurrency result.

### Real traces (strong optional)
Wire a `WorkloadReplayer` that loads a real Apache/Nginx access log (CSV/JSON) and replays it.
Reporting even one real trace removes a major threat to validity.

---

## 6. Test Suite (positive + negative)

The old PoC had a pipeline with timing but **no real assertions**. Build a `pytest` suite. The
**negative tests are the most valuable part** — they convert "we implemented X" into "we proved X."

### Positive (happy path)
```
Segmentation:  exact chunk counts; no record loss on round-trip; partial last chunk; isolated ID namespaces
Index:         lookup returns ingested chunks; distinct chunks don't share IDs
Cache:         repeat access → hit; geo-matching query scores matching region higher; hit rate climbs with capacity
Security:      authorized role returns correct records; every query writes an audit entry;
               per-page decrypt < per-chunk decrypt < full-dataset decrypt
```

### Negative (failure / adversarial — REQUIRED)
```
Segmentation:  empty chunk → []; absent chunk_key → [] (not KeyError); malformed record ingests without schema crash
Cache:         all frames pinned → RuntimeError (no silent hang); pinned page never evicted under pressure (100 trials);
               unpin of unknown id → no-op
Security:      unauthorized role → [] (zero records, no exception)
               decrypt NEVER called on RBAC failure  ← mock-assert decrypt.call_count == 0
               tampered ciphertext → raises InvalidTag (not garbage)
               wrong key → raises
               audit log captures 100% of accesses (count before/after every data path)
Concurrency:   20 threads reading same chunk get identical result sets
               concurrent dirty write + read never returns partial data
```

Run with coverage: `pytest tests/ --cov=core --cov-report=term-missing`.

---

## 7. Two Tables That Need No New Experiments

### Table A — Security properties (literature + math only)
| Property | Full-AES TDE | Per-Chunk (SCADS) | Per-Page (ext.) |
|---|---|---|---|
| Encryption scope | Entire DB | Query chunk only | Query page only |
| Decryption cost | O(n) | O(k) | O(\|page\|) |
| Data exposed / query | 100% | ~0.35% (1716/494021) | ~0.10% (512/494021) |
| Blast radius on breach | Full dataset | 1 chunk | 1 page (965× less) |
| RBAC granularity | Table-level | Chunk-level | Page-level |
| Audit logging | Optional | Built-in | Built-in |

### Table B — Security verification (from negative tests; binary pass/fail)
| Property | Test type | Result |
|---|---|---|
| Unauthorized role returns empty | Negative | PASS |
| Decrypt not called on RBAC failure | Mock | PASS |
| Tampered ciphertext raises | Negative | PASS |
| Wrong key cannot decrypt | Negative | PASS |
| Pinned page survives eviction | Invariant (100/100) | PASS |
| Audit captures 100% of accesses | Positive | PASS |

Binary pass/fail properties are harder to attack in review than any timing number.

---

## 8. Paging Extension — FUTURE WORK ONLY

A buffer-pool / page-granularity layer (fixed frames, pin/unpin, dirty bit, page-level encrypt)
was *designed* but **not benchmarked**. Present it as architecture + complexity derivation only:

```
T_query = O(1) directory + O(1) pool check + O(|page|) decrypt-on-miss
Exposure/query = page_size / total_records = 512 / 494021 = 0.10%
Blast radius   = total_records / page_size = 965× less than full-dataset encryption
```

Do **not** present paging as an experimental result unless you actually benchmark it.

---

## 9. Suggested Layout & Environment

```
scads/
├── core/
│   ├── segmenter.py        # Layer 1
│   ├── index.py            # Layer 2
│   ├── cache.py            # Layer 3 (SCADS, LRU, LFU, ARC, TinyLFU)
│   ├── security.py         # Layer 4 (AES-256, RBAC, AuditLogger)
│   └── pipeline.py         # end-to-end
├── data/                   # loaders: kdd / taxi / weblog (+ real-trace replayer)
├── benchmarks/             # bench_index (vs DuckDB/pandas), bench_cache (vs ARC/TinyLFU), bench_security
├── analysis/               # generate_figures.py — figures from the SAME run as reported numbers
└── tests/                  # positive + negative (conftest, per-layer, concurrency)
```

Environment:
```
pip install duckdb pandas numpy faker cryptography pytest pytest-benchmark pytest-mock matplotlib
```
Pin versions in `requirements.txt` and record the seed used for every reported number.

---

## 10. Acceptance Criteria (definition of done)

```
□ All four layers implemented as independent, testable modules with the contracts in §3
□ Cache benchmark uses geo-CORRELATED queries (BUG 1 fixed)
□ Encryption benchmark times full chunk + true full dataset, 3 scopes, median+p95 (BUG 2 fixed)
□ Every metric reported as mean ± 95% CI over N seeded trials, with p50/p95/p99 (BUG 3 fixed)
□ Every figure regenerated from the same run as its reported number (no stale artifacts)
□ Index benchmark compares SCADS vs pandas vs DuckDB, with build time + memory + miss queries
□ Cache benchmark includes LRU, LFU, ARC, TinyLFU across 4 workloads × 4 capacities
□ pytest suite passes: positive AND negative cases, including mock-asserted "no decrypt on denial"
□ Security properties table (A) and verification table (B) produced
□ Paging kept as future-work design + complexity only (not benchmarked)
□ Every reported result carries a one-line scope caveat
□ Numbers correctly labeled: "vs. Python linear scan," never "vs. production DBMS"
```

---

## 11. What NOT to Do

- Do not compare raw timings to Postgres/MySQL/Spark from a Python prototype.
- Do not claim the speedup is general — it is "dict O(1) lookup vs. list O(n) scan."
- Do not present paging as an experimental contribution without benchmarks.
- Do not claim cryptographic guarantees beyond what's implemented (no key mgmt / side-channel).
- Do not lower or remove correct numbers — relabel them.
- Do not let figures and prose come from different runs.
