"""Integration tests: full end-to-end pipeline over a small segmented+encrypted dataset.

Covers:
  - Happy path: authorised role gets correct records.
  - Unauthorised path: denied role gets [] with an audit entry and no decrypt call.
  - Absent key: index miss short-circuits before any security layer.
"""
from __future__ import annotations

import pytest
from unittest.mock import patch

from core.cache import SCADSCache
from core.index import SmartIndex
from core.pipeline import SCADSPipeline
from core.security import AccessControl, AuditLogger, ChunkCrypto, SecureChunkStore
from core.segmenter import Segmenter, weblog_extractor
from core.types import ChunkKey


# Small but non-trivial dataset: two distinct chunks.
_DATASET = [
    {"timestamp_hour": 6, "geo_region": "us-east", "log_level": "INFO",  "id": 1},
    {"timestamp_hour": 6, "geo_region": "us-east", "log_level": "INFO",  "id": 2},
    {"timestamp_hour": 6, "geo_region": "us-east", "log_level": "INFO",  "id": 3},
    {"timestamp_hour": 7, "geo_region": "eu-west", "log_level": "ERROR", "id": 4},
    {"timestamp_hour": 7, "geo_region": "eu-west", "log_level": "ERROR", "id": 5},
]
_TARGET: ChunkKey = (6, "us-east", "INFO")
_OTHER:  ChunkKey = (7, "eu-west", "ERROR")
_ABSENT: ChunkKey = (99, "void", "NONE")

_ALLOW_ROLE = "analyst"
_DENY_ROLE  = "guest"


def _build_pipeline() -> SCADSPipeline:
    """Build a full pipeline; access security internals via pipeline.store.*."""
    seg = Segmenter(weblog_extractor)
    chunks = seg.segment(_DATASET)

    index = SmartIndex()
    index.build(chunks)

    cache = SCADSCache(capacity=10)

    crypto = ChunkCrypto()
    ac = AccessControl()
    ac.grant(_ALLOW_ROLE)  # wildcard: may read any chunk

    # Construct store directly; reference store.audit rather than a separate
    # variable to avoid the falsy-empty-AuditLogger pitfall in __init__.
    store = SecureChunkStore(crypto=crypto, access=ac)
    for chunk in chunks.values():
        store.store_chunk(chunk)

    chunk_regions = {k: c.region for k, c in chunks.items()}
    return SCADSPipeline(index, cache, store, chunk_regions)


# ── Happy path ───────────────────────────────────────────────────────────────

class TestHappyPath:
    def test_returns_correct_record_count(self):
        pipeline = _build_pipeline()
        result = pipeline.query(_ALLOW_ROLE, _TARGET)
        assert len(result) == 3

    def test_returns_correct_records(self):
        pipeline = _build_pipeline()
        result = pipeline.query(_ALLOW_ROLE, _TARGET)
        ids = {r["id"] for r in result}
        assert ids == {1, 2, 3}

    def test_records_have_correct_region(self):
        pipeline = _build_pipeline()
        result = pipeline.query(_ALLOW_ROLE, _TARGET)
        assert all(r["geo_region"] == "us-east" for r in result)

    def test_audit_entry_written_and_granted(self):
        pipeline = _build_pipeline()
        pipeline.query(_ALLOW_ROLE, _TARGET)
        entries = pipeline.store.audit.entries
        assert len(entries) == 1
        entry = entries[0]
        assert entry.role == _ALLOW_ROLE
        assert entry.chunk_key == _TARGET
        assert entry.granted is True

    def test_second_query_is_cache_hit(self):
        pipeline = _build_pipeline()
        pipeline.query(_ALLOW_ROLE, _TARGET)
        hits_before = pipeline.cache.hits
        pipeline.query(_ALLOW_ROLE, _TARGET)
        assert pipeline.cache.hits == hits_before + 1

    def test_two_distinct_chunks_return_different_records(self):
        pipeline = _build_pipeline()
        r1 = pipeline.query(_ALLOW_ROLE, _TARGET)
        r2 = pipeline.query(_ALLOW_ROLE, _OTHER)
        assert {r["id"] for r in r1} == {1, 2, 3}
        assert {r["id"] for r in r2} == {4, 5}


# ── Unauthorised path ────────────────────────────────────────────────────────

class TestUnauthorisedPath:
    def test_denied_role_returns_empty(self):
        pipeline = _build_pipeline()
        result = pipeline.query(_DENY_ROLE, _TARGET)
        assert result == []

    def test_denied_role_no_exception(self):
        pipeline = _build_pipeline()
        pipeline.query(_DENY_ROLE, _TARGET)  # must not raise

    def test_audit_entry_written_on_denial(self):
        pipeline = _build_pipeline()
        pipeline.query(_DENY_ROLE, _TARGET)
        assert len(pipeline.store.audit.entries) == 1

    def test_audit_entry_not_granted(self):
        pipeline = _build_pipeline()
        pipeline.query(_DENY_ROLE, _TARGET)
        assert pipeline.store.audit.entries[0].granted is False

    def test_audit_captures_denied_role_name(self):
        pipeline = _build_pipeline()
        pipeline.query(_DENY_ROLE, _TARGET)
        assert pipeline.store.audit.entries[0].role == _DENY_ROLE

    def test_decrypt_never_called_on_denial(self):
        """LOAD-BEARING: decrypt must not be called when RBAC denies access."""
        pipeline = _build_pipeline()
        with patch.object(pipeline.store.crypto, "decrypt") as mock_decrypt:
            result = pipeline.query(_DENY_ROLE, _TARGET)
        assert result == []
        assert mock_decrypt.call_count == 0


# ── Absent key ───────────────────────────────────────────────────────────────

class TestAbsentKey:
    def test_absent_key_returns_empty(self):
        pipeline = _build_pipeline()
        assert pipeline.query(_ALLOW_ROLE, _ABSENT) == []

    def test_absent_key_no_audit_entry(self):
        """Index miss short-circuits before the security layer; nothing audited."""
        pipeline = _build_pipeline()
        pipeline.query(_ALLOW_ROLE, _ABSENT)
        assert len(pipeline.store.audit.entries) == 0

    def test_absent_key_no_decrypt(self):
        pipeline = _build_pipeline()
        with patch.object(pipeline.store.crypto, "decrypt") as mock_decrypt:
            pipeline.query(_ALLOW_ROLE, _ABSENT)
        assert mock_decrypt.call_count == 0
