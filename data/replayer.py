"""Real-trace workload replayer.

Loads a real Apache or Nginx access log (CSV or JSON-lines format) from a caller-supplied path
and replays it as a SCADS query stream (chunk_key tuples).

If no path is given or the file does not exist, the replayer is a no-op: iteration yields
nothing and the caller is notified via a single printed warning.

No data is synthetic here — the replayer only processes files it is explicitly pointed at.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any, Generator, Optional, Tuple

# Allow running as a script from inside the data/ directory.
_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.types import ChunkKey


# Default mapping of a parsed log row -> ChunkKey.
# Callers may supply their own extractor to adapt to different log schemas.
def _default_extractor(row: dict[str, Any]) -> ChunkKey:
    """Map a parsed log row to a (time_window, region, data_type) ChunkKey.

    Falls back to safe defaults when fields are absent.
    """
    try:
        hour = int(str(row.get("hour", row.get("timestamp_hour", 0))))
    except (ValueError, TypeError):
        hour = 0
    region = str(row.get("geo_region", row.get("region", "unknown")))
    method = str(row.get("method", row.get("request_method", "GET"))).upper()
    return (hour % 24, region, method)


def _parse_csv(path: Path) -> Generator[dict[str, Any], None, None]:
    with path.open(newline="", encoding="utf-8", errors="replace") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            yield dict(row)


def _parse_jsonlines(path: Path) -> Generator[dict[str, Any], None, None]:
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


class WorkloadReplayer:
    """Replays a real access log as a SCADS chunk-key query stream.

    Parameters
    ----------
    path:
        Path to the log file (CSV or JSON-lines).  Pass None or omit to create a no-op replayer.
    extractor:
        Optional callable(row: dict) -> ChunkKey.  Defaults to _default_extractor.
    max_rows:
        Maximum number of rows to replay (None = unlimited).
    """

    def __init__(
        self,
        path: Optional[str | Path] = None,
        extractor=None,
        max_rows: Optional[int] = None,
    ) -> None:
        self._path: Optional[Path] = Path(path) if path is not None else None
        self._extractor = extractor or _default_extractor
        self._max_rows = max_rows
        self._rows_replayed = 0

        if self._path is None:
            print("[replayer.py] No log path supplied — replayer is a no-op.")
        elif not self._path.exists():
            print(f"[replayer.py] Log file not found: {self._path} — replayer is a no-op.")
            self._path = None

    @property
    def is_active(self) -> bool:
        return self._path is not None

    def _row_stream(self) -> Generator[dict[str, Any], None, None]:
        if self._path is None:
            return
        suffix = self._path.suffix.lower()
        if suffix == ".csv":
            yield from _parse_csv(self._path)
        else:
            # Treat everything else (including .log, .txt, .json, .jsonl) as JSON-lines.
            yield from _parse_jsonlines(self._path)

    def replay(self) -> Generator[ChunkKey, None, None]:
        """Yield one ChunkKey per log entry.

        Stops after max_rows if set.  Yields nothing if no valid file was given.
        """
        count = 0
        for row in self._row_stream():
            if self._max_rows is not None and count >= self._max_rows:
                break
            try:
                key = self._extractor(row)
            except Exception:
                continue
            self._rows_replayed += 1
            count += 1
            yield key

    def __iter__(self) -> Generator[ChunkKey, None, None]:
        return self.replay()

    @property
    def rows_replayed(self) -> int:
        return self._rows_replayed


if __name__ == "__main__":
    # Self-check: no-op replayer yields nothing.
    replayer = WorkloadReplayer(path=None)
    keys = list(replayer.replay())
    assert keys == [], f"Expected empty replay, got {keys}"
    print(f"Replayer self-check OK: no-op replayer yielded {len(keys)} keys.")

    # Self-check: non-existent path.
    replayer2 = WorkloadReplayer(path="/nonexistent/access.log")
    keys2 = list(replayer2.replay())
    assert keys2 == [], f"Expected empty replay, got {keys2}"
    print(f"Replayer self-check OK: missing-file replayer yielded {len(keys2)} keys.")
