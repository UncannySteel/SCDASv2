"""Layer 2 — Smart Index.

A dict-backed index mapping ``chunk_key -> [chunk_id, ...]``. Lookup is O(1)
average dictionary access; this single complexity label (O(1)) is used
consistently throughout the project.

Contract:
  - lookup of an ingested key returns its chunk id(s);
  - lookup of an absent key returns [] and never raises;
  - distinct chunks never share ids (guaranteed upstream by the Segmenter);
  - build time and memory footprint are recorded for honest comparison.
"""
from __future__ import annotations

import sys
import time
from typing import Dict, List

from core.types import Chunk, ChunkKey


class SmartIndex:
    """O(1) dict-backed chunk-key index."""

    def __init__(self) -> None:
        self._index: Dict[ChunkKey, List[int]] = {}
        self.build_time_s: float = 0.0

    def build(self, chunks: Dict[ChunkKey, Chunk]) -> None:
        """Build the index from a chunk map, recording wall-clock build time."""
        start = time.perf_counter()
        index: Dict[ChunkKey, List[int]] = {}
        for key, chunk in chunks.items():
            index.setdefault(key, []).append(chunk.chunk_id)
        self._index = index
        self.build_time_s = time.perf_counter() - start

    def lookup(self, key: ChunkKey) -> List[int]:
        """Return chunk ids for a key, or [] if absent. Never raises."""
        ids = self._index.get(key)
        if ids is None:
            return []
        return list(ids)

    def memory_bytes(self) -> int:
        """Approximate in-memory footprint of the index structure in bytes."""
        total = sys.getsizeof(self._index)
        for key, ids in self._index.items():
            total += sys.getsizeof(key)
            for component in key:
                total += sys.getsizeof(component)
            total += sys.getsizeof(ids)
            for chunk_id in ids:
                total += sys.getsizeof(chunk_id)
        return total

    def __len__(self) -> int:
        return len(self._index)
