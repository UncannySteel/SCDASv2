"""Layer 2 smart-index tests — positive and negative (spec section 6)."""
from __future__ import annotations

from core.index import SmartIndex
from core.segmenter import Segmenter, kdd_extractor, weblog_extractor


# --- positive: lookup returns ingested chunks -------------------------------

def test_lookup_returns_ingested_chunk_ids(kdd_records):
    seg = Segmenter(kdd_extractor)
    chunks = seg.segment(kdd_records)
    index = SmartIndex()
    index.build(chunks)
    for key, chunk in chunks.items():
        assert index.lookup(key) == [chunk.chunk_id]


def test_index_len_matches_chunk_count(weblog_records):
    seg = Segmenter(weblog_extractor)
    chunks = seg.segment(weblog_records)
    index = SmartIndex()
    index.build(chunks)
    assert len(index) == len(chunks)


# --- positive: distinct chunks never share ids ------------------------------

def test_distinct_chunks_dont_share_ids(kdd_records):
    seg = Segmenter(kdd_extractor)
    chunks = seg.segment(kdd_records)
    index = SmartIndex()
    index.build(chunks)
    seen = set()
    for key in chunks:
        for chunk_id in index.lookup(key):
            assert chunk_id not in seen
            seen.add(chunk_id)


# --- positive: build time and memory recorded -------------------------------

def test_build_time_and_memory_recorded(kdd_records):
    seg = Segmenter(kdd_extractor)
    chunks = seg.segment(kdd_records)
    index = SmartIndex()
    index.build(chunks)
    assert index.build_time_s >= 0.0
    assert index.memory_bytes() > 0


# --- negative: absent key returns [] (never raises) -------------------------

def test_absent_key_returns_empty_not_keyerror(kdd_records):
    seg = Segmenter(kdd_extractor)
    chunks = seg.segment(kdd_records)
    index = SmartIndex()
    index.build(chunks)
    # key that was never ingested
    assert index.lookup((99, 99, "nonexistent")) == []


def test_lookup_on_empty_index_returns_empty():
    index = SmartIndex()
    index.build({})
    assert index.lookup((0, 0, "tcp")) == []
    assert len(index) == 0


# --- negative: returned list is a copy (mutation isolation) -----------------

def test_returned_list_is_isolated_copy(kdd_records):
    seg = Segmenter(kdd_extractor)
    chunks = seg.segment(kdd_records)
    index = SmartIndex()
    index.build(chunks)
    key = next(iter(chunks))
    ids = index.lookup(key)
    ids.append(123456)
    # mutating the returned list must not corrupt the index
    assert index.lookup(key) != ids
