"""End-to-end SCADS pipeline.

Wires Layer 2 (Index) -> Layer 3 (Cache) -> Layer 4 (SecureChunkStore) into a
single query entry point.

Scope (spec §2 / §5): this is a single-process, GIL-bound implementation. Thread
safety relies on Python's GIL serialising bytecode execution; this is not a
distributed-systems or true-parallelism result.
"""
from __future__ import annotations

from typing import Any

from core.cache import CachePolicy
from core.index import SmartIndex
from core.security import SecureChunkStore
from core.types import ChunkKey


class SCADSPipeline:
    """Single query entry point combining all four SCADS layers.

    Query flow: Index lookup -> Cache tracking -> SecureChunkStore fetch.
    SecureChunkStore enforces the load-bearing ordering internally:
        audit -> RBAC check -> (deny: [], no decrypt) -> decrypt -> return.
    """

    def __init__(
        self,
        index: SmartIndex,
        cache: CachePolicy,
        store: SecureChunkStore,
        chunk_regions: dict[ChunkKey, Any] | None = None,
    ) -> None:
        self.index = index
        self.cache = cache
        self.store = store
        # Maps chunk_key -> true region for geo-match scoring in the cache.
        self._chunk_regions: dict[ChunkKey, Any] = chunk_regions or {}

    def query(
        self,
        role: str,
        chunk_key: ChunkKey,
        query_region: Any = None,
    ) -> list[dict]:
        """Return records for ``chunk_key`` if ``role`` is authorised.

        Returns [] on index miss, access denial, or missing chunk.
        Never raises for normal denial; propagates InvalidTag on tamper.

        Step 1 — Index (O(1) lookup): resolve chunk_key to chunk ids.
                 Return [] immediately on index miss (chunk does not exist).
        Step 2 — Cache: record this access for eviction-score bookkeeping.
        Step 3 — Fetch (SecureChunkStore): audit -> RBAC -> decrypt -> records.
        Step 4 — Populate cache on miss so future accesses score as hits.
        """
        # Step 1: Index — O(1) dict lookup; [] if the key was never ingested.
        if not self.index.lookup(chunk_key):
            return []

        # Step 2: Cache — track access (hit/miss) before the fetch.
        hit = self.cache.get(chunk_key, query_region)

        # Step 3: Fetch — the load-bearing security ordering lives here.
        records = self.store.fetch(role, chunk_key)

        # Step 4: Populate cache on miss; region used for geo-match scoring.
        if not hit:
            region = self._chunk_regions.get(chunk_key)
            self.cache.put(chunk_key, region)

        return records
