"""Encryption benchmark: per-page, per-chunk, full-dataset AES-256-GCM round-trips.

BUG 2 fix: the old benchmark encrypted only 5 records and called a 20×5 blob
"full dataset", producing a meaningless ratio. This benchmark times encryption
round-trips over:

  - per-page   : ~512 records (a sub-chunk page slice)
  - per-chunk  : ~1716 records (one KDD chunk, median size)
  - full dataset: all 494,021 records serialised into one blob

Three scopes shown side by side; median + p95 reported (BUG 2 fix).

Scope caveat: "AES-256-GCM in-process latency for payloads of 512 / ~1716 /
~494k records serialised as JSON. Wall-clock on a single thread; not a
throughput or hardware-AES benchmark."
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import data.kdd as kdd_loader
from benchmarks.stats import summarize
from core.security import ChunkCrypto
from core.segmenter import Segmenter, kdd_extractor

RESULTS_DIR = Path(__file__).resolve().parents[1] / "results"
RESULTS_DIR.mkdir(exist_ok=True)

# Target sizes (in records) for each scope.
PAGE_SIZE = 512
CHUNK_SIZE = 1716    # spec §7: ~1716 records per chunk (KDD median)
FULL_SIZE = 494_021  # spec §5 BUG 2 fix: real whole-dataset blob

N_TRIALS = 10        # median + p95 over 10 trials
BASE_SEED = 42

SCOPE_CAVEAT = (
    "AES-256-GCM in-process encrypt+decrypt round-trip latency for payloads of "
    f"~{PAGE_SIZE} (per-page), ~{CHUNK_SIZE} (per-chunk), and ~{FULL_SIZE:,} "
    "(full dataset) records serialised as compact JSON. "
    "Single-threaded wall-clock; not a throughput or hardware-AES benchmark. "
    "Numbers scale with JSON serialisation cost, not AES alone."
)


def _serialize(records: list[dict]) -> bytes:
    return json.dumps(records, separators=(",", ":")).encode("utf-8")


def _time_round_trip(crypto: ChunkCrypto, payload: bytes, n_trials: int) -> list[float]:
    """Return n_trials round-trip (encrypt + decrypt) times in seconds."""
    times: list[float] = []
    for _ in range(n_trials):
        t0 = time.perf_counter()
        blob = crypto.encrypt(payload)
        _ = crypto.decrypt(blob)
        times.append(time.perf_counter() - t0)
    return times


def run(base_seed: int = BASE_SEED) -> None:
    print("=== bench_security: AES-256-GCM round-trip (3 scopes) ===")

    # Load the full KDD dataset (real or synthetic).
    result = kdd_loader.load(n=FULL_SIZE, seed=base_seed)
    all_records = result["records"]
    is_synthetic = result["is_synthetic"]
    print(
        f"  Dataset: {len(all_records):,} records "
        f"({'synthetic' if is_synthetic else 'real KDD'})"
    )

    # Build a real chunk from segmented data to get a representative chunk payload.
    seg = Segmenter(kdd_extractor)
    chunks = seg.segment(all_records)
    # Pick the chunk closest to CHUNK_SIZE records.
    chunks_by_size = sorted(chunks.values(), key=lambda c: abs(len(c) - CHUNK_SIZE))
    representative_chunk = chunks_by_size[0]
    actual_chunk_size = len(representative_chunk)
    print(f"  Representative chunk: {actual_chunk_size} records "
          f"(target ~{CHUNK_SIZE})")

    # Build payloads.
    page_records = all_records[:PAGE_SIZE]
    chunk_records = representative_chunk.records
    full_records = all_records  # true whole-dataset blob (BUG 2 fix)

    page_payload  = _serialize(page_records)
    chunk_payload = _serialize(chunk_records)
    full_payload  = _serialize(full_records)

    print(
        f"  Payload sizes: page={len(page_payload):,}B  "
        f"chunk={len(chunk_payload):,}B  "
        f"full={len(full_payload):,}B"
    )

    crypto = ChunkCrypto()

    print(f"  Running {N_TRIALS} trials per scope …")
    page_times  = _time_round_trip(crypto, page_payload,  N_TRIALS)
    chunk_times = _time_round_trip(crypto, chunk_payload, N_TRIALS)
    full_times  = _time_round_trip(crypto, full_payload,  N_TRIALS)

    def _ms(times: list[float]) -> list[float]:
        return [t * 1000 for t in times]

    s_page  = summarize(_ms(page_times),  seed=base_seed)
    s_chunk = summarize(_ms(chunk_times), seed=base_seed)
    s_full  = summarize(_ms(full_times),  seed=base_seed)

    def _fmt(s: dict) -> str:
        return f"median={s['p50']:.3f}ms  p95={s['p95']:.3f}ms"

    print(f"  per-page  (~{PAGE_SIZE} rec): {_fmt(s_page)}")
    print(f"  per-chunk (~{actual_chunk_size} rec): {_fmt(s_chunk)}")
    print(f"  full      ({len(all_records):,} rec): {_fmt(s_full)}")

    output: dict[str, Any] = {
        "benchmark": "security",
        "seed": base_seed,
        "n_trials": N_TRIALS,
        "is_synthetic": is_synthetic,
        "scope_caveat": SCOPE_CAVEAT,
        "scopes": {
            "per_page": {
                "n_records": PAGE_SIZE,
                "payload_bytes": len(page_payload),
                "latency_ms": s_page,
            },
            "per_chunk": {
                "n_records": actual_chunk_size,
                "target_n_records": CHUNK_SIZE,
                "payload_bytes": len(chunk_payload),
                "latency_ms": s_chunk,
            },
            "full_dataset": {
                "n_records": len(all_records),
                "payload_bytes": len(full_payload),
                "latency_ms": s_full,
            },
        },
        "ratios": {
            "full_vs_chunk_median": (
                s_full["p50"] / s_chunk["p50"] if s_chunk["p50"] > 0 else None
            ),
            "chunk_vs_page_median": (
                s_chunk["p50"] / s_page["p50"] if s_page["p50"] > 0 else None
            ),
            "note": (
                "Ratios reflect serialisation + AES cost; "
                "per-chunk is the SCADS operating point."
            ),
        },
    }

    out_path = RESULTS_DIR / "security.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"  -> {out_path}")


if __name__ == "__main__":
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else BASE_SEED
    run(base_seed=seed)
