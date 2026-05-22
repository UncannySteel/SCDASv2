"""Concurrency tests (GIL-bound single-process, spec §5 / §6).

Scope caveat (spec §2): Python threads share the CPython GIL; these tests
demonstrate that no partial state is exposed during cooperative scheduling.
This is NOT a true-parallelism or distributed-systems result.

Spec §6 requirements covered here:
  - 20 threads reading the same chunk get identical result sets.
  - Concurrent reads never return partial data.
  - Write-path concurrency is marked skip (PoC is read-heavy, spec §2).
"""
from __future__ import annotations

import threading

import pytest

from core.cache import SCADSCache
from core.index import SmartIndex
from core.pipeline import SCADSPipeline
from core.security import AccessControl, ChunkCrypto, SecureChunkStore
from core.segmenter import Segmenter, weblog_extractor


_N_RECORDS = 50
_RECORDS = [
    {"timestamp_hour": 10, "geo_region": "ap-east", "log_level": "INFO", "seq": i}
    for i in range(_N_RECORDS)
]
_TARGET_KEY = (10, "ap-east", "INFO")
_THREAD_COUNT = 20


def _build_pipeline() -> SCADSPipeline:
    seg = Segmenter(weblog_extractor)
    chunks = seg.segment(_RECORDS)

    index = SmartIndex()
    index.build(chunks)

    cache = SCADSCache(capacity=20)

    crypto = ChunkCrypto()
    ac = AccessControl()
    ac.grant("reader")
    store = SecureChunkStore(crypto=crypto, access=ac)
    for chunk in chunks.values():
        store.store_chunk(chunk)

    chunk_regions = {k: c.region for k, c in chunks.items()}
    return SCADSPipeline(index, cache, store, chunk_regions)


def test_concurrent_reads_identical_results():
    """20 threads reading the same chunk must all receive identical result sets.

    Scope: GIL-bound single-process. Not a true-parallelism result.
    """
    pipeline = _build_pipeline()
    results: list[list[dict] | None] = [None] * _THREAD_COUNT
    errors: list[Exception] = []

    def worker(idx: int) -> None:
        try:
            results[idx] = pipeline.query("reader", _TARGET_KEY)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(_THREAD_COUNT)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Exceptions in threads: {errors}"

    reference = results[0]
    assert reference is not None
    assert len(reference) == _N_RECORDS

    ref_seqs = frozenset(r["seq"] for r in reference)
    for idx, result in enumerate(results):
        assert result is not None, f"Thread {idx} returned None"
        seqs = frozenset(r["seq"] for r in result)
        assert seqs == ref_seqs, (
            f"Thread {idx} result differs: got {len(result)} records, "
            f"reference has {len(reference)}"
        )


def test_concurrent_reads_no_partial_data():
    """Concurrent reads must return all records or none — never a partial set.

    Scope: GIL-bound single-process; atomicity is a consequence of CPython's
    GIL serialising list construction, not a formal concurrency guarantee.
    """
    pipeline = _build_pipeline()
    record_counts: list[int] = []
    lock = threading.Lock()
    errors: list[Exception] = []

    def worker() -> None:
        try:
            result = pipeline.query("reader", _TARGET_KEY)
            with lock:
                record_counts.append(len(result))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(_THREAD_COUNT)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Exceptions in threads: {errors}"
    assert len(record_counts) == _THREAD_COUNT

    # Every thread must have received the complete record set (no partial reads).
    for idx, count in enumerate(record_counts):
        assert count == _N_RECORDS, (
            f"Thread {idx} received partial data: {count} records "
            f"(expected {_N_RECORDS}). "
            "Note: atomicity here is GIL-provided, not a formal guarantee."
        )


def test_concurrent_reads_all_audited():
    """Each concurrent query must generate exactly one audit entry (100% capture)."""
    pipeline = _build_pipeline()
    errors: list[Exception] = []

    def worker() -> None:
        try:
            pipeline.query("reader", _TARGET_KEY)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(_THREAD_COUNT)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Exceptions in threads: {errors}"
    assert len(pipeline.store.audit.entries) == _THREAD_COUNT, (
        f"Expected {_THREAD_COUNT} audit entries, "
        f"got {len(pipeline.store.audit.entries)}"
    )


@pytest.mark.skip(reason="Write-path concurrency not evaluated — PoC is read-heavy (spec §2)")
def test_concurrent_write_read():
    """Write-path concurrency is out of scope for this PoC (spec §2 / §11)."""
    pass
