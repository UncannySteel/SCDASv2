"""Unit tests for core.pipeline.SCADSPipeline.

Verifies the wiring between Index, Cache, and SecureChunkStore.
Integration and concurrency tests live in separate modules.
Fixtures are local to this file.
"""
from __future__ import annotations

import pytest
from unittest.mock import patch

from core.cache import SCADSCache
from core.index import SmartIndex
from core.pipeline import SCADSPipeline
from core.security import AccessControl, AuditLogger, ChunkCrypto, SecureChunkStore
from core.segmenter import Segmenter, weblog_extractor


_RECORDS = [
    {"timestamp_hour": 3, "geo_region": "us-east", "log_level": "INFO", "id": 0},
    {"timestamp_hour": 3, "geo_region": "us-east", "log_level": "INFO", "id": 1},
    {"timestamp_hour": 4, "geo_region": "eu-west", "log_level": "ERROR", "id": 2},
]
_KEY_INFO = (3, "us-east", "INFO")
_KEY_ERR  = (4, "eu-west", "ERROR")
_ABSENT   = (99, "nowhere", "NONE")


def _build(records=None, grant_all=True):
    """Build a pipeline; access pipeline.store.audit / pipeline.store.crypto directly."""
    records = records or _RECORDS
    seg = Segmenter(weblog_extractor)
    chunks = seg.segment(records)

    index = SmartIndex()
    index.build(chunks)

    cache = SCADSCache(capacity=10)

    crypto = ChunkCrypto()
    ac = AccessControl()
    if grant_all:
        ac.grant("admin")

    # Build store; use store.audit (not a separate reference) to avoid the
    # falsy-empty-AuditLogger issue with `or` in SecureChunkStore.__init__.
    store = SecureChunkStore(crypto=crypto, access=ac)
    for chunk in chunks.values():
        store.store_chunk(chunk)

    chunk_regions = {k: c.region for k, c in chunks.items()}
    pipeline = SCADSPipeline(index, cache, store, chunk_regions)
    return pipeline


# ── Index miss ───────────────────────────────────────────────────────────────

def test_index_miss_returns_empty():
    pipeline = _build()
    assert pipeline.query("admin", _ABSENT) == []


def test_index_miss_does_not_audit():
    pipeline = _build()
    pipeline.query("admin", _ABSENT)
    assert len(pipeline.store.audit.entries) == 0


# ── Authorised access ────────────────────────────────────────────────────────

def test_authorised_returns_records():
    pipeline = _build()
    result = pipeline.query("admin", _KEY_INFO)
    assert len(result) == 2
    assert all(r["geo_region"] == "us-east" for r in result)


def test_audit_entry_written_on_grant():
    pipeline = _build()
    pipeline.query("admin", _KEY_INFO)
    entries = pipeline.store.audit.entries
    assert len(entries) == 1
    assert entries[0].granted is True
    assert entries[0].role == "admin"


# ── Unauthorised access ──────────────────────────────────────────────────────

def test_unauthorised_returns_empty():
    pipeline = _build(grant_all=False)
    assert pipeline.query("admin", _KEY_INFO) == []


def test_unauthorised_no_exception():
    pipeline = _build(grant_all=False)
    pipeline.query("admin", _KEY_INFO)  # must not raise


def test_audit_entry_written_on_denial():
    pipeline = _build(grant_all=False)
    pipeline.query("admin", _KEY_INFO)
    entries = pipeline.store.audit.entries
    assert len(entries) == 1
    assert entries[0].granted is False


def test_decrypt_not_called_on_denial():
    pipeline = _build(grant_all=False)
    with patch.object(pipeline.store.crypto, "decrypt") as mock_decrypt:
        pipeline.query("admin", _KEY_INFO)
    assert mock_decrypt.call_count == 0


# ── Cache behaviour ──────────────────────────────────────────────────────────

def test_first_query_is_cache_miss():
    pipeline = _build()
    misses_before = pipeline.cache.misses
    pipeline.query("admin", _KEY_INFO)
    assert pipeline.cache.misses == misses_before + 1


def test_second_query_is_cache_hit():
    pipeline = _build()
    pipeline.query("admin", _KEY_INFO)  # miss — populates cache
    hits_before = pipeline.cache.hits
    pipeline.query("admin", _KEY_INFO)  # hit
    assert pipeline.cache.hits == hits_before + 1


def test_query_region_forwarded_to_cache(monkeypatch):
    pipeline = _build()
    seen = []
    original_get = pipeline.cache.get

    def spy_get(chunk_key, query_region=None):
        seen.append(query_region)
        return original_get(chunk_key, query_region)

    monkeypatch.setattr(pipeline.cache, "get", spy_get)
    pipeline.query("admin", _KEY_INFO, query_region="us-east")
    assert seen == ["us-east"]


def test_chunk_region_stored_in_cache_on_miss():
    pipeline = _build()
    pipeline.query("admin", _KEY_INFO)
    # After a miss + put, the key must be in the cache.
    assert _KEY_INFO in pipeline.cache
