"""Layer 3 cache tests (spec §6): positive + negative / adversarial cases.

All fixtures are local to this file (conftest.py is owned by another module).
"""
from __future__ import annotations

import random

import pytest

from core.cache import (
    ARCCache,
    CachePolicy,
    LFUCache,
    LRUCache,
    SCADSCache,
    TinyLFUCache,
)

ALL_POLICIES = [SCADSCache, LRUCache, LFUCache, ARCCache, TinyLFUCache]
# Policies that always evict on insert (no admission/rejection door):
EVICTING_POLICIES = [SCADSCache, LRUCache, LFUCache, ARCCache]


def _key(region: str, i: int):
    return ("t", region, i)


# --------------------------------------------------------------------------- positive
@pytest.mark.parametrize("cls", ALL_POLICIES)
def test_interface_surface(cls):
    c = cls(4)
    assert isinstance(c, CachePolicy)
    assert c.hits == 0 and c.misses == 0
    assert c.hit_rate == 0.0
    assert _key("a", 1) not in c


@pytest.mark.parametrize("cls", ALL_POLICIES)
def test_repeat_access_is_hit(cls):
    c = cls(4)
    k = _key("us", 1)
    assert c.get(k, query_region="us") is False      # cold miss
    c.put(k, region="us")
    assert c.get(k, query_region="us") is True        # now resident -> hit
    assert c.hits == 1 and c.misses == 1


@pytest.mark.parametrize("cls", ALL_POLICIES)
def test_empty_get_is_miss(cls):
    c = cls(4)
    assert c.get(_key("us", 99), query_region="us") is False
    assert c.misses == 1 and c.hits == 0


def test_geo_matching_query_scores_higher():
    """SCADS: a query whose region matches the chunk scores higher than one that doesn't."""
    c = SCADSCache(4)
    match = _key("us", 1)
    miss = _key("eu", 2)
    c.put(match, region="us")
    c.put(miss, region="eu")
    c.get(match, query_region="us")     # geo match -> 1.0 term
    c.get(miss, query_region="asia")    # geo mismatch -> 0.2 term
    assert c.score(match) > c.score(miss)
    # exact formula check: both have access_count 1 (freq term 0.6*0.1=0.06)
    assert c.score(match) == pytest.approx(0.06 + 0.4 * 1.0)
    assert c.score(miss) == pytest.approx(0.06 + 0.4 * 0.2)


@pytest.mark.parametrize("cls", [SCADSCache, LRUCache, LFUCache, ARCCache])
def test_hit_rate_climbs_with_capacity(cls):
    keys = [_key("r%d" % (i % 5), i) for i in range(50)]
    weights = [1.0 / (i + 1) for i in range(50)]      # skewed (zipf-like) reuse
    rng = random.Random(7)
    seq = rng.choices(keys, weights=weights, k=2000)

    def run(capacity):
        c = cls(capacity)
        for k in seq:
            if not c.get(k, query_region=k[1]):
                c.put(k, region=k[1])
        return c.hit_rate

    small, mid, large = run(2), run(10), run(40)
    assert small < mid < large


# --------------------------------------------------------------------------- grace period
def test_grace_period_protects_freshly_inserted():
    """A freshly inserted (grace) frame survives the next eviction even if it
    scores lowest; a proven, higher-scoring frame is evicted instead."""
    c = SCADSCache(2)
    c.put("A", region="us")
    c.get("A", query_region="us")        # A proven (grace cleared, high score)
    c.put("B", region="eu")              # B fresh -> grace protected
    c.put("C", region="eu")              # eviction: only A is non-grace -> A evicted
    assert "A" not in c
    assert "B" in c
    assert "C" in c


def test_grace_does_not_deadlock_when_all_fresh():
    """If every candidate is still fresh, eviction must still proceed (not raise/hang)."""
    c = SCADSCache(2)
    c.put("A", region="us")
    c.put("B", region="eu")
    c.put("C", region="us")              # all fresh -> fall back, evict lowest score
    assert len(c) == 2
    assert "C" in c


# --------------------------------------------------------------------------- negative / pins
@pytest.mark.parametrize("cls", EVICTING_POLICIES)
def test_all_frames_pinned_raises_runtimeerror(cls):
    """All frames pinned + insertion pressure -> RuntimeError, never a silent hang."""
    c = cls(2)
    c.put("A", region="us")
    c.put("B", region="eu")
    c.pin("A")
    c.pin("B")
    with pytest.raises(RuntimeError):
        c.put("C", region="us")


@pytest.mark.parametrize("cls", EVICTING_POLICIES)
def test_pinned_frame_never_evicted_100_trials(cls):
    """Invariant: a pinned frame survives arbitrary eviction pressure (100 trials)."""
    for trial in range(100):
        rng = random.Random(trial)
        c = cls(3)
        c.put("PIN", region="hot")
        c.pin("PIN")
        for _ in range(60):
            i = rng.randint(0, 25)
            k = _key("r%d" % (i % 4), i)
            if not c.get(k, query_region=k[1]):
                c.put(k, region=k[1])
        assert "PIN" in c, f"pinned frame evicted on trial {trial}"


@pytest.mark.parametrize("cls", ALL_POLICIES)
def test_unpin_unknown_is_noop(cls):
    c = cls(4)
    c.unpin("never-seen")               # must not raise
    c.put("A", region="us")
    c.unpin("A")                        # unpinning an unpinned-but-present id: no-op
    c.unpin("A")                        # idempotent
    assert "A" in c


@pytest.mark.parametrize("cls", EVICTING_POLICIES)
def test_unpinning_allows_eviction_again(cls):
    c = cls(2)
    c.put("A", region="us")
    c.put("B", region="eu")
    c.pin("A")
    c.pin("B")
    with pytest.raises(RuntimeError):
        c.put("C", region="us")
    c.unpin("B")                        # free a frame
    c.put("C", region="us")            # now succeeds
    assert "A" in c                    # still pinned -> retained
    assert "C" in c


# --------------------------------------------------------------------------- SCADS vs LRU thesis
def test_scads_beats_lru_under_geo_skewed_capacity_constrained():
    """Central thesis: under a frequency- + geo-skewed, capacity-constrained
    workload, SCADS retains the hot geo-matching set better than LRU."""
    capacity = 10
    hot = [_key("hot", i) for i in range(8)]
    seq = []                            # (key, query_region) pairs
    # Warmup: the hot working set has been queried before (frequency + geo signal).
    for _ in range(15):
        for h in hot:
            seq.append((h, "hot"))
    cold_id = 0
    for _ in range(40):                 # measured rounds
        for h in hot:
            seq.append((h, "hot"))      # query from hot region -> geo match
        for _ in range(8):              # cold scan: distinct keys, non-matching region
            seq.append((_key("cold", 10_000 + cold_id), "hot"))
            cold_id += 1

    def run(cls):
        c = cls(capacity)
        for key, qr in seq:
            if not c.get(key, query_region=qr):
                c.put(key, region=key[1])
        return c.hit_rate

    scads_hr = run(SCADSCache)
    lru_hr = run(LRUCache)
    assert scads_hr > lru_hr


# --------------------------------------------------------------------------- ARC / TinyLFU are real
def test_arc_is_scan_resistant_vs_lru():
    """ARC should keep a frequently-reused set across a long cold scan better
    than LRU (the property ARC exists for)."""
    capacity = 10
    hot = [_key("h", i) for i in range(6)]
    seq = []
    for _ in range(12):                 # warmup the reused set
        seq.extend(hot)
    cold_id = 0
    for _ in range(30):
        for h in hot:
            seq.append(h)
        for _ in range(10):
            seq.append(_key("c", 50_000 + cold_id))
            cold_id += 1

    def run(cls):
        c = cls(capacity)
        for k in seq:
            if not c.get(k, query_region=k[1]):
                c.put(k, region=k[1])
        return c.hit_rate

    assert run(ARCCache) >= run(LRUCache)
    assert run(ARCCache) > 0.0


def test_tinylfu_admission_keeps_frequent_over_one_offs():
    """TinyLFU's frequency door should not let a flood of one-off keys evict a
    proven hot key."""
    c = TinyLFUCache(3)
    hot = _key("h", 1)
    # Build up frequency for the hot key.
    for _ in range(20):
        if not c.get(hot, query_region="h"):
            c.put(hot, region="h")
    # Flood with distinct one-off keys.
    for i in range(200):
        k = _key("c", 90_000 + i)
        if not c.get(k, query_region="c"):
            c.put(k, region="c")
    # The frequent key should still be resident.
    assert hot in c
    assert c.get(hot, query_region="h") is True


def test_tinylfu_repeated_access_climbs_hit_rate():
    c = TinyLFUCache(20)
    rng = random.Random(3)
    keys = [_key("r%d" % (i % 5), i) for i in range(30)]
    weights = [1.0 / (i + 1) for i in range(30)]
    seq = rng.choices(keys, weights=weights, k=1500)
    for k in seq:
        if not c.get(k, query_region=k[1]):
            c.put(k, region=k[1])
    assert c.hit_rate > 0.0
