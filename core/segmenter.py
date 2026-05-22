"""Layer 1 — Segmentation.

Splits a flat list of records into 3D chunks keyed by
``chunk_key = (time_window, region, data_type)``. Each dataset supplies its own
pluggable extractor; there is no single hardcoded schema.

Published chunk_key recipes (must match the writeup, spec section 3):
  - KDD (network):  (src_bytes-derived time bucket, dst_bytes-derived region, protocol_type)
  - NYC Taxi (geo): (pickup_hour % 24, borough_id, trip_type)
  - Web Logs:       (timestamp_hour % 24, geo_region, log_level)

Contract: every record lands in exactly one chunk; no records are lost across
segment -> reassemble; a partial last chunk is handled naturally (chunk sizes are
data-driven, not fixed). Malformed records still ingest without a schema crash:
extractors coerce defensively and fall back to sentinel components.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Iterable

from core.types import Chunk, ChunkKey

# Number of derived region buckets used by the KDD recipe (dst_bytes -> region).
KDD_REGION_BUCKETS = 8


def _safe_int(value: Any, default: int = 0) -> int:
    """Coerce a value to int, never raising on malformed input."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class Segmenter:
    """Segment records into chunks using a pluggable key extractor."""

    def __init__(self, extractor: Callable[[dict], ChunkKey]):
        self.extractor = extractor

    def segment(self, records: list[dict]) -> Dict[ChunkKey, Chunk]:
        """Group records into chunks. Each record lands in exactly one chunk.

        chunk_id values form an isolated namespace per call (0..n-1), so ids from
        one segmentation run never collide within that run.
        """
        chunks: Dict[ChunkKey, Chunk] = {}
        next_id = 0
        for record in records:
            key = self.extractor(record)
            chunk = chunks.get(key)
            if chunk is None:
                # region component of the key is the chunk's true region.
                chunk = Chunk(chunk_id=next_id, chunk_key=key, records=[], region=key[1])
                chunks[key] = chunk
                next_id += 1
            chunk.records.append(record)
        return chunks

    def reassemble(self, chunks: Iterable[Chunk] | Dict[ChunkKey, Chunk]) -> list[dict]:
        """Flatten chunks back into a record list with no record loss."""
        if isinstance(chunks, dict):
            iterable: Iterable[Chunk] = chunks.values()
        else:
            iterable = chunks
        out: list[dict] = []
        for chunk in iterable:
            out.extend(chunk.records)
        return out


def kdd_extractor(record: dict) -> ChunkKey:
    """KDD recipe: time bucket from src_bytes, region from dst_bytes, type=protocol_type."""
    src_bytes = _safe_int(record.get("src_bytes", 0))
    dst_bytes = _safe_int(record.get("dst_bytes", 0))
    protocol = record.get("protocol_type", "unknown")
    time_window = src_bytes % 24
    region = dst_bytes % KDD_REGION_BUCKETS
    return (time_window, region, protocol)


def taxi_extractor(record: dict) -> ChunkKey:
    """NYC Taxi recipe: (pickup_hour % 24, borough_id, trip_type)."""
    pickup_hour = _safe_int(record.get("pickup_hour", 0))
    borough_id = record.get("borough_id", None)
    trip_type = record.get("trip_type", "unknown")
    time_window = pickup_hour % 24
    return (time_window, borough_id, trip_type)


def weblog_extractor(record: dict) -> ChunkKey:
    """Web Logs recipe: (timestamp_hour % 24, geo_region, log_level).

    Accepts an explicit ``timestamp_hour`` or derives the hour from an epoch
    ``timestamp`` if only that is present.
    """
    if "timestamp_hour" in record:
        hour = _safe_int(record.get("timestamp_hour", 0))
    else:
        epoch = _safe_int(record.get("timestamp", 0))
        hour = (epoch // 3600) % 24
    geo_region = record.get("geo_region", None)
    log_level = record.get("log_level", "unknown")
    time_window = hour % 24
    return (time_window, geo_region, log_level)
