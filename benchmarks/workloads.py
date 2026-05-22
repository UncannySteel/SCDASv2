"""Query workload generators for SCADS cache benchmarks.

BUG 1 fix: the geo-correlated generator ties query_region to the accessed
chunk's *true* region with a tunable locality probability p.  Under the old
harness, query_region was drawn with random.choice(regions) independently of
the chunk, making geo_match pure noise and the cache's central thesis
unmeasurable.  This generator fixes that.

Workload types
--------------
zipf        — power-law skew (α=1.2); a small hot set dominates
burst       — repeated cycles: one key hammered for a burst window, then switch
temporal    — Gaussian walk over the key space, simulating a moving time window
uniform     — every key equally likely (cache-policy neutral)
geo_correlated — wraps any base generator; returns (chunk_key, query_region)
               pairs where query_region == chunk.region with prob p, else a
               random other region.
"""
from __future__ import annotations

import math
import random
from typing import Any, Iterator, List, Tuple

from core.types import ChunkKey


# ---------------------------------------------------------------------------
# Base generators — yield chunk keys only
# ---------------------------------------------------------------------------

def _zipf_weights(n: int, alpha: float = 1.2) -> list[float]:
    weights = [1.0 / (i ** alpha) for i in range(1, n + 1)]
    total = sum(weights)
    return [w / total for w in weights]


def zipf_generator(
    keys: list[ChunkKey],
    n_queries: int,
    rng: random.Random,
    alpha: float = 1.2,
) -> list[ChunkKey]:
    """Power-law skew: a small fraction of keys receives most accesses."""
    weights = _zipf_weights(len(keys), alpha)
    # random.choices accepts weights and is faster than a manual CDF loop.
    return rng.choices(keys, weights=weights, k=n_queries)


def burst_generator(
    keys: list[ChunkKey],
    n_queries: int,
    rng: random.Random,
    burst_len: int = 20,
) -> list[ChunkKey]:
    """Burst pattern: one key is hammered for burst_len accesses, then a new
    hot key is chosen at random, simulating a scan / event spike."""
    result: list[ChunkKey] = []
    while len(result) < n_queries:
        hot = rng.choice(keys)
        chunk = min(burst_len, n_queries - len(result))
        result.extend([hot] * chunk)
    return result


def temporal_generator(
    keys: list[ChunkKey],
    n_queries: int,
    rng: random.Random,
    window: int = 10,
) -> list[ChunkKey]:
    """Temporal locality: Gaussian walk over the key index, simulating a
    moving time window.  LRU should win under this pattern — report honestly."""
    n = len(keys)
    pos = rng.randint(0, n - 1)
    result: list[ChunkKey] = []
    sigma = max(1, window // 3)
    for _ in range(n_queries):
        offset = int(rng.gauss(0, sigma))
        pos = max(0, min(n - 1, pos + offset))
        result.append(keys[pos])
    return result


def uniform_generator(
    keys: list[ChunkKey],
    n_queries: int,
    rng: random.Random,
) -> list[ChunkKey]:
    """Uniform: every key equally likely.  Cache-policy neutral baseline."""
    return [rng.choice(keys) for _ in range(n_queries)]


# ---------------------------------------------------------------------------
# Geo-correlated wrapper — BUG 1 fix
# ---------------------------------------------------------------------------

def geo_correlated_queries(
    chunk_keys: list[ChunkKey],
    chunk_regions: dict[ChunkKey, Any],
    base_queries: list[ChunkKey],
    rng: random.Random,
    locality_prob: float = 0.8,
) -> list[Tuple[ChunkKey, Any]]:
    """Attach a query_region to each chunk_key access.

    With probability ``locality_prob`` the query_region equals the chunk's true
    region (geo_match=True for the SCADS scoring function).  Otherwise a random
    *other* region is chosen, injecting controlled noise.

    This is the BUG 1 fix: geo signal is deliberately correlated with chunk
    accesses so that the cache benchmark actually measures geographic locality,
    not random noise.

    Parameters
    ----------
    chunk_keys      : all possible chunk keys in the workload universe
    chunk_regions   : mapping chunk_key -> true region (from Chunk.region)
    base_queries    : sequence of chunk keys to access (from any generator)
    rng             : seeded RNG for reproducibility
    locality_prob   : probability that query_region == chunk.region

    Returns
    -------
    list of (chunk_key, query_region) pairs
    """
    all_regions = list({r for r in chunk_regions.values() if r is not None})
    if not all_regions:
        # No region info available; return None as query_region (no geo signal).
        return [(key, None) for key in base_queries]

    result: list[Tuple[ChunkKey, Any]] = []
    for key in base_queries:
        true_region = chunk_regions.get(key)
        if true_region is None or rng.random() >= locality_prob:
            # Either unknown region or the noise branch — pick any other region.
            others = [r for r in all_regions if r != true_region]
            query_region = rng.choice(others) if others else true_region
        else:
            query_region = true_region
        result.append((key, query_region))
    return result


# ---------------------------------------------------------------------------
# Convenience: build a complete workload dict for bench_cache
# ---------------------------------------------------------------------------

WORKLOAD_NAMES = ("zipf", "burst", "temporal", "uniform")


def build_workloads(
    keys: list[ChunkKey],
    chunk_regions: dict[ChunkKey, Any],
    n_queries: int,
    rng: random.Random,
    locality_prob: float = 0.8,
) -> dict[str, list[Tuple[ChunkKey, Any]]]:
    """Return a dict mapping workload name -> geo-correlated query list."""
    base: dict[str, list[ChunkKey]] = {
        "zipf":     zipf_generator(keys, n_queries, rng),
        "burst":    burst_generator(keys, n_queries, rng),
        "temporal": temporal_generator(keys, n_queries, rng),
        "uniform":  uniform_generator(keys, n_queries, rng),
    }
    out: dict[str, list[Tuple[ChunkKey, Any]]] = {}
    for name, queries in base.items():
        out[name] = geo_correlated_queries(
            keys, chunk_regions, queries, rng, locality_prob
        )
    return out
