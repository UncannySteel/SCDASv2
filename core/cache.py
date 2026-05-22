"""Layer 3 — Context-Aware Cache and baseline eviction policies.

One common :class:`CachePolicy` interface backs five policies:

  * ``SCADSCache``  — frequency + geographic-match scoring (the SCADS contribution)
  * ``LRUCache``    — least-recently-used
  * ``LFUCache``    — least-frequently-used
  * ``ARCCache``    — Adaptive Replacement Cache (Megiddo & Modha, 2003)
  * ``TinyLFUCache``— frequency-sketch admission over an LRU main cache

Scope (honest labeling): these are in-process simulations that operate on chunk
keys to compare *hit rates* under skewed, geo-correlated workloads. They model
eviction behaviour only — not a production buffer pool, and not concurrency.
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Any, Optional

from core.types import ChunkKey


class CachePolicy:
    """Common interface plus shared pin / hit bookkeeping for every policy."""

    def __init__(self, capacity: int):
        if capacity <= 0:
            raise ValueError("capacity must be a positive integer")
        self.capacity = capacity
        self.hits = 0
        self.misses = 0
        self._pinned: set = set()

    # ------------------------------------------------------------------ interface
    def get(self, chunk_key: ChunkKey, query_region: Any = None) -> bool:
        """Return True on hit (and record the access), False on miss."""
        raise NotImplementedError

    def put(self, chunk_key: ChunkKey, region: Any = None) -> None:
        """Insert ``chunk_key`` (its true ``region`` used for geo-match scoring)."""
        raise NotImplementedError

    def __contains__(self, chunk_key: ChunkKey) -> bool:
        raise NotImplementedError

    def __len__(self) -> int:
        raise NotImplementedError

    # ------------------------------------------------------------------ shared
    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0

    def pin(self, chunk_key: ChunkKey) -> None:
        self._pinned.add(chunk_key)

    def unpin(self, chunk_key: ChunkKey) -> None:
        # Unpin of an unknown / never-pinned id is a no-op (spec §6).
        self._pinned.discard(chunk_key)


# ---------------------------------------------------------------------------
# Frame-backed policies (SCADS, LRU, LFU) share storage + eviction scaffolding.
# ---------------------------------------------------------------------------
class _Frame:
    __slots__ = ("region", "access_count", "last_query_region", "grace", "recency")

    def __init__(self, region: Any, recency: int):
        self.region = region
        self.access_count = 0
        self.last_query_region: Any = None
        self.grace = True          # cold-start guard, cleared after first survival/access
        self.recency = recency


class _FrameCache(CachePolicy):
    """Single-tier cache; subclasses only choose the eviction victim."""

    def __init__(self, capacity: int):
        super().__init__(capacity)
        self._frames: "OrderedDict[Any, _Frame]" = OrderedDict()
        self._clock = 0

    def __contains__(self, chunk_key: ChunkKey) -> bool:
        return chunk_key in self._frames

    def __len__(self) -> int:
        return len(self._frames)

    def get(self, chunk_key: ChunkKey, query_region: Any = None) -> bool:
        self._clock += 1
        f = self._frames.get(chunk_key)
        if f is None:
            self.misses += 1
            return False
        f.access_count += 1
        f.recency = self._clock
        f.last_query_region = query_region
        f.grace = False           # proven by an access — no longer needs the guard
        self.hits += 1
        return True

    def put(self, chunk_key: ChunkKey, region: Any = None) -> None:
        self._clock += 1
        f = self._frames.get(chunk_key)
        if f is not None:
            if region is not None:
                f.region = region
            f.recency = self._clock
            return
        if len(self._frames) >= self.capacity:
            self._evict()
        self._frames[chunk_key] = _Frame(region, self._clock)

    def _evict(self) -> None:
        candidates = [k for k in self._frames if k not in self._pinned]
        if not candidates:
            raise RuntimeError("cache full and all frames pinned: no evictable frame")
        # Grace: freshly inserted frames are exempt from this eviction if any
        # non-fresh candidate exists. Grace lasts exactly one eviction event.
        non_grace = [k for k in candidates if not self._frames[k].grace]
        pool = non_grace if non_grace else candidates
        victim = self._select_victim(pool)
        del self._frames[victim]
        for k in candidates:
            if k != victim:
                self._frames[k].grace = False

    def _select_victim(self, pool: list) -> Any:
        raise NotImplementedError


class LRUCache(_FrameCache):
    def _select_victim(self, pool: list) -> Any:
        return min(pool, key=lambda k: self._frames[k].recency)


class LFUCache(_FrameCache):
    def _select_victim(self, pool: list) -> Any:
        return min(pool, key=lambda k: (self._frames[k].access_count,
                                        self._frames[k].recency))


class SCADSCache(_FrameCache):
    """Context-aware cache: score = a*freq + b*geo_match (a=0.6, b=0.4)."""

    ALPHA = 0.6
    BETA = 0.4

    def _frame_score(self, f: _Frame) -> float:
        freq = min(f.access_count / 10.0, 1.0)
        geo = 1.0 if (f.last_query_region is not None
                      and f.last_query_region == f.region) else 0.2
        return self.ALPHA * freq + self.BETA * geo

    def score(self, chunk_key: ChunkKey) -> float:
        """Public score accessor (used by tests / ablation)."""
        return self._frame_score(self._frames[chunk_key])

    def _select_victim(self, pool: list) -> Any:
        # Lowest score evicted first; oldest recency breaks ties.
        return min(pool, key=lambda k: (self._frame_score(self._frames[k]),
                                        self._frames[k].recency))


# ---------------------------------------------------------------------------
# ARC — Adaptive Replacement Cache (Megiddo & Modha, FAST 2003).
# Real implementation: T1/T2 resident lists, B1/B2 ghost lists, adaptive p.
# ---------------------------------------------------------------------------
class ARCCache(CachePolicy):
    def __init__(self, capacity: int):
        super().__init__(capacity)
        self.c = capacity
        self.p = 0
        self.t1: "OrderedDict[Any, bool]" = OrderedDict()   # recent, seen once
        self.t2: "OrderedDict[Any, bool]" = OrderedDict()   # frequent, seen >= 2
        self.b1: "OrderedDict[Any, bool]" = OrderedDict()   # ghost of T1
        self.b2: "OrderedDict[Any, bool]" = OrderedDict()   # ghost of T2
        self._region: dict = {}

    def __contains__(self, chunk_key: ChunkKey) -> bool:
        return chunk_key in self.t1 or chunk_key in self.t2

    def __len__(self) -> int:
        return len(self.t1) + len(self.t2)

    def get(self, chunk_key: ChunkKey, query_region: Any = None) -> bool:
        if chunk_key in self.t1:
            del self.t1[chunk_key]
            self.t2[chunk_key] = True          # promote: seen >= 2
            self.hits += 1
            return True
        if chunk_key in self.t2:
            self.t2.move_to_end(chunk_key)
            self.hits += 1
            return True
        self.misses += 1
        return False

    def put(self, chunk_key: ChunkKey, region: Any = None) -> None:
        if region is not None:
            self._region[chunk_key] = region

        if chunk_key in self.t1 or chunk_key in self.t2:
            self.t1.pop(chunk_key, None)
            self.t2.pop(chunk_key, None)
            self.t2[chunk_key] = True
            return

        if chunk_key in self.b1:                       # Case II — ghost hit in B1
            self.p = min(self.c, self.p + max(1, len(self.b2) // max(1, len(self.b1))))
            self._replace(chunk_key)
            del self.b1[chunk_key]
            self.t2[chunk_key] = True
            return

        if chunk_key in self.b2:                       # Case III — ghost hit in B2
            self.p = max(0, self.p - max(1, len(self.b1) // max(1, len(self.b2))))
            self._replace(chunk_key)
            del self.b2[chunk_key]
            self.t2[chunk_key] = True
            return

        # Case IV — brand-new key, manage list sizes then insert into T1.
        l1 = len(self.t1) + len(self.b1)
        if l1 == self.c:
            if len(self.t1) < self.c:
                self.b1.popitem(last=False)            # drop LRU ghost of B1
                self._replace(chunk_key)
            else:                                      # B1 empty: evict from T1
                victim = self._pop_lru_unpinned(self.t1)
                if victim is None:
                    raise RuntimeError(
                        "cache full and all frames pinned: no evictable frame")
        else:
            total = len(self.t1) + len(self.t2) + len(self.b1) + len(self.b2)
            if total >= self.c:
                if total == 2 * self.c:
                    self.b2.popitem(last=False)
                self._replace(chunk_key)
        self.t1[chunk_key] = True

    def _replace(self, incoming: ChunkKey) -> None:
        prefer_t1 = bool(self.t1) and (
            len(self.t1) > self.p
            or (incoming in self.b2 and len(self.t1) == self.p)
        )
        if prefer_t1:
            victim = self._pop_lru_unpinned(self.t1)
            if victim is not None:
                self.b1[victim] = True
                return
            victim = self._pop_lru_unpinned(self.t2)
            if victim is None:
                raise RuntimeError(
                    "cache full and all frames pinned: no evictable frame")
            self.b2[victim] = True
        else:
            victim = self._pop_lru_unpinned(self.t2)
            if victim is not None:
                self.b2[victim] = True
                return
            victim = self._pop_lru_unpinned(self.t1)
            if victim is None:
                raise RuntimeError(
                    "cache full and all frames pinned: no evictable frame")
            self.b1[victim] = True

    def _pop_lru_unpinned(self, od: "OrderedDict[Any, bool]") -> Optional[Any]:
        for key in od:                                 # LRU -> MRU order
            if key not in self._pinned:
                del od[key]
                return key
        return None


# ---------------------------------------------------------------------------
# TinyLFU — frequency-sketch admission policy over an LRU main cache.
# Real implementation: Count-Min sketch with aging gates admission.
# ---------------------------------------------------------------------------
class _CountMinSketch:
    def __init__(self, width: int = 512, depth: int = 4, seed: int = 1):
        self.width = width
        self.depth = depth
        self.table = [[0] * width for _ in range(depth)]
        self._seeds = [seed * 131 + i * 977 + 7 for i in range(depth)]
        self.total = 0
        self.sample = width * 8                        # reset window for aging

    def _idx(self, key: Any, row: int) -> int:
        return hash((self._seeds[row], key)) % self.width

    def increment(self, key: Any) -> None:
        self.total += 1
        for i in range(self.depth):
            self.table[i][self._idx(key, i)] += 1
        if self.total >= self.sample:
            self._age()

    def estimate(self, key: Any) -> int:
        return min(self.table[i][self._idx(key, i)] for i in range(self.depth))

    def _age(self) -> None:
        for i in range(self.depth):
            row = self.table[i]
            for j in range(self.width):
                row[j] >>= 1
        self.total >>= 1


class TinyLFUCache(CachePolicy):
    def __init__(self, capacity: int):
        super().__init__(capacity)
        self._main: "OrderedDict[Any, bool]" = OrderedDict()
        self._region: dict = {}
        self._sketch = _CountMinSketch()

    def __contains__(self, chunk_key: ChunkKey) -> bool:
        return chunk_key in self._main

    def __len__(self) -> int:
        return len(self._main)

    def get(self, chunk_key: ChunkKey, query_region: Any = None) -> bool:
        self._sketch.increment(chunk_key)
        if chunk_key in self._main:
            self._main.move_to_end(chunk_key)
            self.hits += 1
            return True
        self.misses += 1
        return False

    def put(self, chunk_key: ChunkKey, region: Any = None) -> None:
        self._sketch.increment(chunk_key)
        if region is not None:
            self._region[chunk_key] = region
        if chunk_key in self._main:
            self._main.move_to_end(chunk_key)
            return
        if len(self._main) < self.capacity:
            self._main[chunk_key] = True
            return
        # Full: pick the LRU unpinned victim, then apply the admission filter.
        victim = None
        for k in self._main:
            if k not in self._pinned:
                victim = k
                break
        if victim is None:
            raise RuntimeError("cache full and all frames pinned: no evictable frame")
        # Admit the candidate only if it is estimated at least as frequent as the
        # victim it would replace (the TinyLFU "door"); otherwise reject it.
        if self._sketch.estimate(chunk_key) >= self._sketch.estimate(victim):
            del self._main[victim]
            self._main[chunk_key] = True
