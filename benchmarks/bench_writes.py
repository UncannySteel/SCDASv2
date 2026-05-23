"""Write/update microbenchmark for SCADS.

Fills the evaluation gap flagged in the paper's future-work section: the existing
benchmarks cover READ paths only. This file quantifies three write scenarios:

  S1 — Steady-state insert throughput (segment + index + encrypt-and-store)
  S2 — In-chunk update (cheap path: decrypt, mutate, re-encrypt; index unchanged)
  S3 — Cross-chunk update (expensive path: update source chunk, update dest chunk,
       update SmartIndex if a new chunk key was created)

Honest positioning: SCADS is read-optimised. Cross-chunk updates incur full
re-encryption of both source and destination chunks. This benchmark reports that
cost plainly.

Whitebox access: S2/S3 access store._blobs and store.crypto directly for
component-level timing. This is intentional for benchmarking and is documented here.
"""
from __future__ import annotations

import json
import math
import random
import sys
import time
from pathlib import Path
from typing import Any

# Ensure project root is on the path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
from PIL import Image

import data.kdd as kdd_loader
from benchmarks.stats import summarize
from core.index import SmartIndex
from core.security import AccessControl, SecureChunkStore
from core.segmenter import Segmenter, kdd_extractor

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

N_TRIALS = 5
BASE_SEED = 42

S1_SIZES = [10_000, 100_000, 494_021]
# n=10_000 is not in the loader's valid set; use synthetic generation approach.
# The loader only accepts {50000, 100000, 200000, 300000, 494021}.
# For 10k we synthesise directly.
_LOADER_VALID = {50_000, 100_000, 200_000, 300_000, 494_021}

N_UPDATES = 1_000  # S2 and S3: 1,000 random updates at n=100_000 baseline

SCOPE_CAVEAT = (
    "S1: end-to-end insert throughput (segment + SmartIndex build + AES-256-GCM "
    "encrypt-and-store per chunk). S2: per-update latency for in-chunk updates "
    "(decrypt one chunk, mutate one record, re-encrypt). S3: per-update latency "
    "for cross-chunk updates (decrypt chunk A, remove record, re-encrypt A, "
    "decrypt/create chunk B, insert record, re-encrypt B, update SmartIndex). "
    "Single-threaded wall-clock; synthetic or real KDD Cup 1999 (10 pct subset) data."
)

RESULTS_DIR = Path(__file__).resolve().parents[1] / "results"
NEW_RESULTS_DIR = Path(__file__).resolve().parents[1] / "new-results"
RESULTS_DIR.mkdir(exist_ok=True)
NEW_RESULTS_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _synthetic_records(n: int, seed: int) -> list[dict]:
    """Generate n synthetic KDD-schema records using the kdd loader's private helper."""
    import data.kdd as _kdd
    rng = random.Random(seed)
    return [_kdd._synthetic_record(rng) for _ in range(n)]


def _load_records(n: int, seed: int) -> tuple[list[dict], bool]:
    """Load records, returning (records, is_synthetic)."""
    if n in _LOADER_VALID:
        result = kdd_loader.load(n=n, seed=seed)
        return result["records"], result["is_synthetic"]
    # Fallback: generate synthetic for non-standard sizes.
    print(
        f"  [DISCLOSURE] n={n} not in loader valid set; "
        f"generating synthetic (seed={seed})."
    )
    return _synthetic_records(n, seed), True


def _build_system(records: list[dict]) -> tuple[dict, SmartIndex, SecureChunkStore]:
    """Segment + index + encrypt-and-store. Returns (chunks, index, store)."""
    seg = Segmenter(kdd_extractor)
    chunks = seg.segment(records)

    index = SmartIndex()
    index.build(chunks)

    access = AccessControl()
    access.grant("bench_role")  # wildcard — RBAC does not interfere
    store = SecureChunkStore(access=access)
    for key, chunk in chunks.items():
        store.store_records(key, chunk.records)

    return chunks, index, store


def _percentile(s: list[float], p: float) -> float:
    n = len(s)
    if n == 0:
        return float("nan")
    idx = p / 100.0 * (n - 1)
    lo, hi = int(idx), min(int(idx) + 1, n - 1)
    frac = idx - lo
    return s[lo] * (1 - frac) + s[hi] * frac


def _us_stats(times_us: list[float]) -> dict:
    s = sorted(times_us)
    mean = sum(s) / len(s) if s else float("nan")
    n = len(s)
    if n >= 2:
        variance = sum((x - mean) ** 2 for x in s) / (n - 1)
        std = math.sqrt(variance)
        _t_table = {
            1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571,
            6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228,
        }
        df = n - 1
        t_crit = _t_table.get(df, 1.96 if df >= 30 else 2.045)
        margin = t_crit * std / math.sqrt(n)
    else:
        margin = 0.0
    return {
        "mean": mean,
        "ci95_low": mean - margin,
        "ci95_high": mean + margin,
        "p50": _percentile(s, 50),
        "p95": _percentile(s, 95),
        "p99": _percentile(s, 99),
    }


# ---------------------------------------------------------------------------
# S1 — Steady-state insert throughput
# ---------------------------------------------------------------------------

def bench_s1(sizes: list[int], n_trials: int, base_seed: int) -> list[dict]:
    """Measure end-to-end insert throughput (records/sec and chunks/sec)."""
    results = []
    for n in sizes:
        print(f"\n  S1: n={n:,} …")
        # Reduce trials for largest size if needed (time budget guard).
        trials = n_trials if n < 400_000 else min(n_trials, 3)
        if trials < n_trials:
            print(f"    (reducing to {trials} trials for n={n:,} to stay within time budget)")

        recs_per_sec_samples: list[float] = []
        chunks_per_sec_samples: list[float] = []
        n_chunks_seen: list[int] = []

        for trial in range(trials):
            records, is_syn = _load_records(n, base_seed + trial)
            t0 = time.perf_counter()
            chunks, index, store = _build_system(records)
            elapsed = time.perf_counter() - t0
            recs_per_sec_samples.append(n / elapsed)
            n_chunks_seen.append(len(chunks))
            chunks_per_sec_samples.append(len(chunks) / elapsed)

        s_rps = summarize(recs_per_sec_samples, seed=base_seed)
        s_cps = summarize(chunks_per_sec_samples, seed=base_seed)

        print(
            f"    records/sec: {s_rps['mean']:,.0f} ± CI95 "
            f"[{s_rps['ci95_low']:,.0f}, {s_rps['ci95_high']:,.0f}]"
        )
        print(f"    chunks/sec:  {s_cps['mean']:,.1f}")

        results.append({
            "n": n,
            "n_trials": trials,
            "n_chunks_mean": sum(n_chunks_seen) / len(n_chunks_seen),
            "records_per_sec_mean": s_rps["mean"],
            "records_per_sec_ci95_low": s_rps["ci95_low"],
            "records_per_sec_ci95_high": s_rps["ci95_high"],
            "records_per_sec_p50": s_rps["p50"],
            "records_per_sec_p95": s_rps["p95"],
            "records_per_sec_p99": s_rps["p99"],
            "chunks_per_sec_mean": s_cps["mean"],
            "chunks_per_sec_ci95_low": s_cps["ci95_low"],
            "chunks_per_sec_ci95_high": s_cps["ci95_high"],
        })

    return results


# ---------------------------------------------------------------------------
# S2 — In-chunk update (cheap path)
# ---------------------------------------------------------------------------

def bench_s2(
    n_base: int,
    n_updates: int,
    base_seed: int,
) -> tuple[dict, dict]:
    """
    Time n_updates in-chunk record mutations.

    Component breakdown per update:
      (a) AES decrypt
      (b) record mutation (duration field)
      (c) AES encrypt
      (d) index update (none for in-chunk — explicitly 0)

    Returns (latency_stats_us, breakdown_us).
    """
    print(f"\n  S2: building baseline (n={n_base:,}) …")
    records, _ = _load_records(n_base, base_seed)
    chunks, index, store = _build_system(records)

    chunk_keys = list(store._blobs.keys())
    rng = random.Random(base_seed)

    total_us: list[float] = []
    dec_us: list[float] = []
    mut_us: list[float] = []
    enc_us: list[float] = []
    idx_us: list[float] = []

    print(f"  S2: running {n_updates} in-chunk updates …")
    for _ in range(n_updates):
        key = rng.choice(chunk_keys)
        blob = store._blobs[key]

        t_start = time.perf_counter()

        # (a) AES decrypt
        t0 = time.perf_counter()
        pt = store.crypto.decrypt(blob)
        t1 = time.perf_counter()
        dec_us.append((t1 - t0) * 1e6)

        # (b) deserialize + mutate one record's duration field
        t0 = time.perf_counter()
        rec_list = json.loads(pt.decode("utf-8"))
        if rec_list:
            rec_list[0]["duration"] = rng.randint(0, 58329)
        payload = json.dumps(rec_list, separators=(",", ":")).encode("utf-8")
        t1 = time.perf_counter()
        mut_us.append((t1 - t0) * 1e6)

        # (c) AES encrypt
        t0 = time.perf_counter()
        new_blob = store.crypto.encrypt(payload)
        t1 = time.perf_counter()
        enc_us.append((t1 - t0) * 1e6)

        # Store back (part of total but no separate index step needed)
        store._blobs[key] = new_blob

        # (d) index update — none for in-chunk
        idx_us.append(0.0)

        total_us.append((time.perf_counter() - t_start) * 1e6)

    lat = _us_stats(total_us)
    breakdown = {
        "decrypt": sum(dec_us) / len(dec_us),
        "mutate": sum(mut_us) / len(mut_us),
        "encrypt": sum(enc_us) / len(enc_us),
        "index": 0.0,
    }

    print(
        f"    mean={lat['mean']:.1f}µs  p50={lat['p50']:.1f}µs  "
        f"p95={lat['p95']:.1f}µs  p99={lat['p99']:.1f}µs"
    )
    print(
        f"    breakdown: dec={breakdown['decrypt']:.1f}µs  "
        f"mut={breakdown['mutate']:.1f}µs  "
        f"enc={breakdown['encrypt']:.1f}µs  idx=0.0µs"
    )
    return lat, breakdown


# ---------------------------------------------------------------------------
# S3 — Cross-chunk update (expensive path)
# ---------------------------------------------------------------------------

def bench_s3(
    n_base: int,
    n_updates: int,
    base_seed: int,
) -> tuple[dict, dict]:
    """
    Time n_updates cross-chunk record moves (delete from chunk A, insert into chunk B).

    Simulated by:
      1. Pick random chunk A. Decrypt, remove first record, re-encrypt A.
      2. Pick a different random chunk B (the destination). Decrypt, insert
         the removed record, re-encrypt B. Store back.
      3. Time index update: if the destination chunk_key is new, insert into
         index._index directly (whitebox). In this simulation both A and B are
         pre-existing chunks, so the index update is a no-op lookup but we still
         time it to show the boundary cost.

    Component breakdown per update:
      (a) AES decrypt (both chunks, summed)
      (b) record mutation (remove + insert)
      (c) AES encrypt (both chunks, summed)
      (d) index update (SmartIndex lookup/insert)

    Returns (latency_stats_us, breakdown_us).
    """
    print(f"\n  S3: building baseline (n={n_base:,}) …")
    records, _ = _load_records(n_base, base_seed)
    chunks, index, store = _build_system(records)

    chunk_keys = list(store._blobs.keys())
    if len(chunk_keys) < 2:
        raise RuntimeError("Need at least 2 chunks for S3 cross-chunk updates.")

    rng = random.Random(base_seed + 100)

    total_us: list[float] = []
    dec_us: list[float] = []
    mut_us: list[float] = []
    enc_us: list[float] = []
    idx_us: list[float] = []

    print(f"  S3: running {n_updates} cross-chunk updates …")
    for _ in range(n_updates):
        # Pick two distinct chunks.
        key_a = rng.choice(chunk_keys)
        key_b = rng.choice(chunk_keys)
        while key_b == key_a:
            key_b = rng.choice(chunk_keys)

        t_start = time.perf_counter()

        # (a) AES decrypt — chunk A
        t0 = time.perf_counter()
        blob_a = store._blobs[key_a]
        pt_a = store.crypto.decrypt(blob_a)
        # AES decrypt — chunk B
        blob_b = store._blobs[key_b]
        pt_b = store.crypto.decrypt(blob_b)
        t1 = time.perf_counter()
        dec_us.append((t1 - t0) * 1e6)

        # (b) Remove first record from A, insert into B
        t0 = time.perf_counter()
        recs_a = json.loads(pt_a.decode("utf-8"))
        recs_b = json.loads(pt_b.decode("utf-8"))
        if recs_a:
            moved = recs_a.pop(0)
            recs_b.append(moved)
        payload_a = json.dumps(recs_a, separators=(",", ":")).encode("utf-8")
        payload_b = json.dumps(recs_b, separators=(",", ":")).encode("utf-8")
        t1 = time.perf_counter()
        mut_us.append((t1 - t0) * 1e6)

        # (c) AES encrypt — both chunks
        t0 = time.perf_counter()
        new_blob_a = store.crypto.encrypt(payload_a)
        new_blob_b = store.crypto.encrypt(payload_b)
        t1 = time.perf_counter()
        enc_us.append((t1 - t0) * 1e6)

        # Store both blobs back.
        store._blobs[key_a] = new_blob_a
        store._blobs[key_b] = new_blob_b

        # (d) Index update — both chunks already exist, but we time the lookup/confirm.
        t0 = time.perf_counter()
        _ = index.lookup(key_a)
        _ = index.lookup(key_b)
        # If key_b were new (not pre-existing), we would do:
        #   index._index[key_b] = [max(max(v) for v in index._index.values()) + 1]
        # That whitebox path is documented but not exercised here since both
        # keys pre-exist. The measured latency is the dict lookup cost.
        t1 = time.perf_counter()
        idx_us.append((t1 - t0) * 1e6)

        total_us.append((time.perf_counter() - t_start) * 1e6)

    lat = _us_stats(total_us)
    breakdown = {
        "decrypt": sum(dec_us) / len(dec_us),
        "mutate": sum(mut_us) / len(mut_us),
        "encrypt": sum(enc_us) / len(enc_us),
        "index": sum(idx_us) / len(idx_us),
    }

    print(
        f"    mean={lat['mean']:.1f}µs  p50={lat['p50']:.1f}µs  "
        f"p95={lat['p95']:.1f}µs  p99={lat['p99']:.1f}µs"
    )
    print(
        f"    breakdown: dec={breakdown['decrypt']:.1f}µs  "
        f"mut={breakdown['mutate']:.1f}µs  "
        f"enc={breakdown['encrypt']:.1f}µs  "
        f"idx={breakdown['index']:.3f}µs"
    )
    return lat, breakdown


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------

def _validate_png(path: Path, min_bytes: int = 5_000, min_w: int = 400, min_h: int = 300) -> bool:
    """Return True if the PNG exists, is large enough, and PIL verifies it."""
    if not path.exists():
        return False
    if path.stat().st_size < min_bytes:
        return False
    try:
        img = Image.open(path)
        img.verify()
        # Re-open to get dimensions (verify() closes the file pointer).
        img2 = Image.open(path)
        w, h = img2.size
        return w >= min_w and h >= min_h
    except Exception:
        return False


def fig9_write_throughput(s1_results: list[dict], out_path: Path) -> None:
    """Bar chart: insert throughput by dataset size (log scale y-axis)."""
    labels = [f"{r['n'] // 1000}k" for r in s1_results]
    means = [r["records_per_sec_mean"] for r in s1_results]
    ci_low = [r["records_per_sec_mean"] - r["records_per_sec_ci95_low"] for r in s1_results]
    ci_high = [r["records_per_sec_ci95_high"] - r["records_per_sec_mean"] for r in s1_results]

    fig, ax = plt.subplots(figsize=(6, 4))
    x = range(len(labels))
    bars = ax.bar(x, means, yerr=[ci_low, ci_high], capsize=5,
                  color="#4472C4", edgecolor="black", linewidth=0.7,
                  error_kw={"elinewidth": 1.2, "ecolor": "black"})
    ax.set_yscale("log")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels)
    ax.set_xlabel("Dataset size (records)", fontsize=11)
    ax.set_ylabel("Records / second (log scale)", fontsize=11)
    ax.set_title("Fig 9: SCADS Insert Throughput (S1)\nSegment + Index + Encrypt-and-Store",
                 fontsize=11)
    ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.grid(axis="y", linestyle="--", alpha=0.5)
    plt.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def fig10_update_cost_breakdown(
    s2_breakdown: dict,
    s3_breakdown: dict,
    out_path: Path,
) -> None:
    """Stacked bar chart: per-component update latency for S2 and S3."""
    components = ["decrypt", "mutate", "encrypt", "index"]
    colors = ["#ED7D31", "#A9D18E", "#4472C4", "#FFC000"]
    labels = ["In-chunk update (S2)", "Cross-chunk update (S3)"]

    s2_vals = [s2_breakdown[c] for c in components]
    s3_vals = [s3_breakdown[c] for c in components]

    fig, ax = plt.subplots(figsize=(6, 5))
    x = [0, 1]
    bottoms = [0.0, 0.0]
    bar_handles = []
    for i, (comp, color) in enumerate(zip(components, colors)):
        vals = [s2_vals[i], s3_vals[i]]
        bars = ax.bar(x, vals, bottom=bottoms, color=color,
                      edgecolor="black", linewidth=0.6, label=comp.capitalize())
        bar_handles.append(bars)
        bottoms = [bottoms[j] + vals[j] for j in range(2)]

    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=10)
    ax.set_ylabel("Time per update (µs)", fontsize=11)
    ax.set_title(
        "Fig 10: Per-Update Cost Breakdown\nIn-chunk (S2) vs Cross-chunk (S3)",
        fontsize=11,
    )
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(axis="y", linestyle="--", alpha=0.5)

    # Annotate total on top of each bar.
    for xi, total in zip(x, bottoms):
        ax.text(xi, total + total * 0.01, f"{total:.1f}µs",
                ha="center", va="bottom", fontsize=9, fontweight="bold")

    plt.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Table E
# ---------------------------------------------------------------------------

def write_table_e(
    s1_results: list[dict],
    s2_lat: dict,
    s3_lat: dict,
    out_path: Path,
) -> None:
    """Write markdown table_e_write_costs.md."""
    lines = [
        "# Table E: SCADS Write Costs",
        "",
        "## S1 — Insert Throughput",
        "",
        "| Dataset size | records/sec (mean) | CI95 low | CI95 high | chunks/sec |",
        "|--------------|--------------------|----------|-----------|------------|",
    ]
    for r in s1_results:
        lines.append(
            f"| {r['n']:,} | {r['records_per_sec_mean']:,.0f} "
            f"| {r['records_per_sec_ci95_low']:,.0f} "
            f"| {r['records_per_sec_ci95_high']:,.0f} "
            f"| {r['chunks_per_sec_mean']:,.1f} |"
        )

    ratio = s3_lat["mean"] / s2_lat["mean"] if s2_lat["mean"] > 0 else float("nan")

    lines += [
        "",
        "## S2 — In-chunk Update Latency",
        "",
        "| Metric | Value (µs) |",
        "|--------|------------|",
        f"| Mean   | {s2_lat['mean']:.1f} |",
        f"| CI95   | [{s2_lat['ci95_low']:.1f}, {s2_lat['ci95_high']:.1f}] |",
        f"| p50    | {s2_lat['p50']:.1f} |",
        f"| p95    | {s2_lat['p95']:.1f} |",
        f"| p99    | {s2_lat['p99']:.1f} |",
        "",
        "## S3 — Cross-chunk Update Latency",
        "",
        "| Metric | Value (µs) |",
        "|--------|------------|",
        f"| Mean   | {s3_lat['mean']:.1f} |",
        f"| CI95   | [{s3_lat['ci95_low']:.1f}, {s3_lat['ci95_high']:.1f}] |",
        f"| p50    | {s3_lat['p50']:.1f} |",
        f"| p95    | {s3_lat['p95']:.1f} |",
        f"| p99    | {s3_lat['p99']:.1f} |",
        "",
        "## S3/S2 Latency Ratio",
        "",
        "| Metric | Ratio |",
        "|--------|-------|",
        f"| Mean latency (S3/S2) | {ratio:.2f}x |",
        "",
        "---",
        "",
        "> **Framing:** SCADS is read-optimised; cross-chunk updates incur full "
        "re-encryption of both source and destination chunks. This is the known "
        "cost of chunk-granularity encryption.",
    ]

    out_path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(base_seed: int = BASE_SEED) -> None:
    print("=== bench_writes: SCADS write/update microbenchmark ===")
    print(f"  N_TRIALS={N_TRIALS}, BASE_SEED={base_seed}, N_UPDATES={N_UPDATES}")

    # ------------------------------------------------------------------
    # S1 — Insert throughput
    # ------------------------------------------------------------------
    print("\n--- S1: Steady-state insert throughput ---")
    s1_results = bench_s1(S1_SIZES, N_TRIALS, base_seed)

    # ------------------------------------------------------------------
    # S2 — In-chunk update
    # ------------------------------------------------------------------
    print("\n--- S2: In-chunk update (cheap path) ---")
    s2_lat, s2_breakdown = bench_s2(100_000, N_UPDATES, base_seed)

    # ------------------------------------------------------------------
    # S3 — Cross-chunk update
    # ------------------------------------------------------------------
    print("\n--- S3: Cross-chunk update (expensive path) ---")
    s3_lat, s3_breakdown = bench_s3(100_000, N_UPDATES, base_seed)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    ratio = s3_lat["mean"] / s2_lat["mean"] if s2_lat["mean"] > 0 else float("nan")
    print(f"\n  S3/S2 mean latency ratio: {ratio:.2f}x")

    dominant_s3 = max(s3_breakdown, key=lambda k: s3_breakdown[k])
    print(f"  Dominant S3 component: {dominant_s3} ({s3_breakdown[dominant_s3]:.1f}µs)")

    # ------------------------------------------------------------------
    # Build JSON result
    # ------------------------------------------------------------------
    output: dict[str, Any] = {
        "benchmark": "writes",
        "seed": base_seed,
        "n_updates": N_UPDATES,
        "scope_caveat": SCOPE_CAVEAT,
        "insert_throughput": s1_results,
        "in_chunk_update_us": s2_lat,
        "cross_chunk_update_us": s3_lat,
        "in_chunk_breakdown_us": s2_breakdown,
        "cross_chunk_breakdown_us": s3_breakdown,
        "s3_s2_ratio": ratio,
        "dominant_s3_component": dominant_s3,
    }

    for out_dir in [RESULTS_DIR, NEW_RESULTS_DIR]:
        json_path = out_dir / "writes.json"
        with open(json_path, "w") as f:
            json.dump(output, f, indent=2)
        print(f"  -> {json_path}")

    # ------------------------------------------------------------------
    # Figures
    # ------------------------------------------------------------------
    fig9_path = NEW_RESULTS_DIR / "fig9_write_throughput.png"
    fig10_path = NEW_RESULTS_DIR / "fig10_update_cost_breakdown.png"

    print("\n  Generating fig9_write_throughput.png …")
    fig9_write_throughput(s1_results, fig9_path)

    print("  Generating fig10_update_cost_breakdown.png …")
    fig10_update_cost_breakdown(s2_breakdown, s3_breakdown, fig10_path)

    # ------------------------------------------------------------------
    # Table E
    # ------------------------------------------------------------------
    table_path = NEW_RESULTS_DIR / "table_e_write_costs.md"
    write_table_e(s1_results, s2_lat, s3_lat, table_path)
    print(f"  -> {table_path}")

    # ------------------------------------------------------------------
    # Self-validation
    # ------------------------------------------------------------------
    print("\n--- Self-validation ---")
    png_paths = [fig9_path, fig10_path]
    all_ok = True
    for p in png_paths:
        ok = _validate_png(p)
        size_kb = p.stat().st_size / 1024 if p.exists() else 0
        if ok:
            img = Image.open(p)
            w, h = img.size
            print(f"  OK  {p.name}: {size_kb:.1f}KB  {w}x{h}px")
        else:
            print(f"  FAIL {p.name}: size={size_kb:.1f}KB — attempting regeneration …")
            # Attempt regeneration.
            if "fig9" in p.name:
                fig9_write_throughput(s1_results, p)
            else:
                fig10_update_cost_breakdown(s2_breakdown, s3_breakdown, p)
            ok2 = _validate_png(p)
            print(f"  {'OK (regen)' if ok2 else 'FAILED (regen)'} {p.name}")
            if not ok2:
                all_ok = False

    print(f"\n  All PNG validations passed: {all_ok}")
    print("\n=== bench_writes complete ===")


if __name__ == "__main__":
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else BASE_SEED
    run(base_seed=seed)
