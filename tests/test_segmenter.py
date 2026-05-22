"""Layer 1 segmentation tests — positive and negative (spec section 6)."""
from __future__ import annotations

from core.segmenter import (
    Segmenter,
    kdd_extractor,
    taxi_extractor,
    weblog_extractor,
)
from core.types import Chunk


# --- positive: exact chunk counts -------------------------------------------

def test_kdd_exact_chunk_count(kdd_records):
    seg = Segmenter(kdd_extractor)
    chunks = seg.segment(kdd_records)
    # (0,0,tcp), (1,0,tcp), (0,1,udp), (5,3,icmp)
    assert len(chunks) == 4
    assert chunks[(0, 0, "tcp")] is not None
    assert len(chunks[(0, 0, "tcp")]) == 3  # records 0,1,4 (src 24 -> hour 0)


def test_taxi_exact_chunk_count(taxi_records):
    seg = Segmenter(taxi_extractor)
    chunks = seg.segment(taxi_records)
    assert len(chunks) == 3
    assert len(chunks[(8, 1, "street_hail")]) == 3  # incl. pickup_hour 32 % 24


def test_weblog_exact_chunk_count(weblog_records):
    seg = Segmenter(weblog_extractor)
    chunks = seg.segment(weblog_records)
    assert len(chunks) == 3
    assert len(chunks[(1, "eu-west", "INFO")]) == 2  # explicit hour + epoch-derived


# --- positive: no record loss on round-trip ---------------------------------

def test_roundtrip_no_record_loss(kdd_records):
    seg = Segmenter(kdd_extractor)
    chunks = seg.segment(kdd_records)
    restored = seg.reassemble(chunks)
    assert len(restored) == len(kdd_records)
    # every original record is present (order within chunks preserved)
    for rec in kdd_records:
        assert rec in restored


def test_reassemble_accepts_iterable_of_chunks(taxi_records):
    seg = Segmenter(taxi_extractor)
    chunks = seg.segment(taxi_records)
    restored = seg.reassemble(list(chunks.values()))
    assert len(restored) == len(taxi_records)


# --- positive: every record lands in exactly one chunk ----------------------

def test_every_record_in_exactly_one_chunk(weblog_records):
    seg = Segmenter(weblog_extractor)
    chunks = seg.segment(weblog_records)
    total = sum(len(c) for c in chunks.values())
    assert total == len(weblog_records)


# --- positive: partial last chunk handled -----------------------------------

def test_partial_last_chunk():
    # 7 records: one key gets 4, another gets 3 (a partial / smaller final chunk).
    records = [{"src_bytes": 0, "dst_bytes": 0, "protocol_type": "tcp"} for _ in range(4)]
    records += [{"src_bytes": 1, "dst_bytes": 0, "protocol_type": "tcp"} for _ in range(3)]
    seg = Segmenter(kdd_extractor)
    chunks = seg.segment(records)
    sizes = sorted(len(c) for c in chunks.values())
    assert sizes == [3, 4]
    assert sum(sizes) == 7


# --- positive: region component set on chunk --------------------------------

def test_chunk_region_is_key_region_component(taxi_records):
    seg = Segmenter(taxi_extractor)
    chunks = seg.segment(taxi_records)
    for key, chunk in chunks.items():
        assert chunk.region == key[1]


# --- positive: isolated id namespaces ---------------------------------------

def test_isolated_id_namespaces(kdd_records, taxi_records):
    seg_a = Segmenter(kdd_extractor)
    seg_b = Segmenter(taxi_extractor)
    chunks_a = seg_a.segment(kdd_records)
    chunks_b = seg_b.segment(taxi_records)
    # within each run, ids are unique and form a 0..n-1 namespace
    ids_a = sorted(c.chunk_id for c in chunks_a.values())
    ids_b = sorted(c.chunk_id for c in chunks_b.values())
    assert ids_a == list(range(len(chunks_a)))
    assert ids_b == list(range(len(chunks_b)))
    assert len(set(ids_a)) == len(ids_a)


# --- negative: empty input --------------------------------------------------

def test_empty_input_returns_empty():
    seg = Segmenter(kdd_extractor)
    chunks = seg.segment([])
    assert chunks == {}
    assert seg.reassemble(chunks) == []


# --- negative: malformed record ingests without schema crash ----------------

def test_malformed_record_no_crash():
    # Records missing fields / with wrong types must not raise; they fall back
    # to sentinel key components and still ingest.
    records = [
        {},                                          # totally empty
        {"src_bytes": "not-a-number", "dst_bytes": None},  # bad types
        {"protocol_type": "tcp"},                    # partial
    ]
    seg = Segmenter(kdd_extractor)
    chunks = seg.segment(records)
    assert sum(len(c) for c in chunks.values()) == 3
    restored = seg.reassemble(chunks)
    assert len(restored) == 3


def test_malformed_weblog_record_no_crash():
    seg = Segmenter(weblog_extractor)
    chunks = seg.segment([{"geo_region": "x"}, {"timestamp": "bad"}])
    assert sum(len(c) for c in chunks.values()) == 2
