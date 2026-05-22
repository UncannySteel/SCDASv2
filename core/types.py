from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Tuple

ChunkKey = Tuple[Any, Any, Any]  # (time_window, region, data_type)

@dataclass
class Chunk:
    chunk_id: int
    chunk_key: ChunkKey
    records: list[dict] = field(default_factory=list)
    region: Any = None            # true region of the chunk, used for geo-match scoring
    def __len__(self) -> int:
        return len(self.records)
