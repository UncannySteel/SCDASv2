"""Shared statistical utilities for all SCADS benchmarks.

BUG 3 fix: every metric is reported as mean ± 95% CI over N seeded trials,
with p50/p95/p99.  A single seed=42 with no variance is not acceptable.
"""
from __future__ import annotations

import math
import random
import time
from typing import Any, Callable


def summarize(samples: list[float], seed: int = 0) -> dict:
    """Return descriptive statistics for a list of timing samples.

    Returns
    -------
    dict with keys: mean, ci95_low, ci95_high, p50, p95, p99, n, seed
    """
    n = len(samples)
    if n == 0:
        return {
            "mean": float("nan"), "ci95_low": float("nan"),
            "ci95_high": float("nan"), "p50": float("nan"),
            "p95": float("nan"), "p99": float("nan"),
            "n": 0, "seed": seed,
        }
    s = sorted(samples)
    mean = sum(s) / n

    # 95% CI via t-distribution (two-tailed); use z≈1.96 for n>=30, else
    # fall back to a conservative t table for small n.
    if n >= 2:
        variance = sum((x - mean) ** 2 for x in s) / (n - 1)
        std = math.sqrt(variance)
        # t critical values (two-tailed, 95%) for small n; z for large n.
        _t_table = {
            1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571,
            6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228,
            15: 2.131, 20: 2.086, 25: 2.060, 29: 2.045,
        }
        df = n - 1
        t_crit = _t_table.get(df) or (2.045 if df < 30 else 1.96)
        margin = t_crit * std / math.sqrt(n)
    else:
        margin = 0.0

    def percentile(p: float) -> float:
        idx = p / 100 * (n - 1)
        lo, hi = int(idx), min(int(idx) + 1, n - 1)
        frac = idx - lo
        return s[lo] * (1 - frac) + s[hi] * frac

    return {
        "mean": mean,
        "ci95_low": mean - margin,
        "ci95_high": mean + margin,
        "p50": percentile(50),
        "p95": percentile(95),
        "p99": percentile(99),
        "n": n,
        "seed": seed,
    }


def run_trials(fn: Callable[[], Any], n_trials: int, base_seed: int) -> list[float]:
    """Run fn() n_trials times, each with a deterministic seed offset.

    The function receives no seed argument — callers set up any RNG state
    before each call via a wrapper.  This helper provides the seeded outer
    loop and measures wall-clock time per trial.

    Returns a list of elapsed times in seconds.
    """
    times: list[float] = []
    for i in range(n_trials):
        # Each trial gets a reproducible seed so results can be regenerated.
        random.seed(base_seed + i)
        t0 = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t0)
    return times
