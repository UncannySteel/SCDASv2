"""Layer 4 security tests (spec §6). Negative/adversarial tests are the most valuable.

Fixtures are local to this file by design (do not depend on conftest.py).
"""

from __future__ import annotations

import time

import pytest
from cryptography.exceptions import InvalidTag

from core.security import (
    AccessControl,
    AuditLogger,
    ChunkCrypto,
    SecureChunkStore,
)

CHUNK_KEY = (0, "us-east", "tcp")
OTHER_KEY = (1, "us-west", "udp")
ADMIN = "analyst"
INTRUDER = "guest"


def _records(n: int) -> list[dict]:
    return [{"id": i, "src": "10.0.0.%d" % (i % 255), "bytes": i * 7} for i in range(n)]


@pytest.fixture
def store():
    crypto = ChunkCrypto()
    access = AccessControl({ADMIN: [CHUNK_KEY, OTHER_KEY]})
    audit = AuditLogger()
    s = SecureChunkStore(crypto=crypto, access=access, audit=audit)
    s.store_records(CHUNK_KEY, _records(20))
    s.store_records(OTHER_KEY, _records(5))
    return s


# --- Positive ---------------------------------------------------------------

def test_authorized_role_returns_correct_records(store):
    out = store.fetch(ADMIN, CHUNK_KEY)
    assert out == _records(20)


def test_every_query_writes_an_audit_entry(store):
    before = len(store.audit)
    store.fetch(ADMIN, CHUNK_KEY)
    store.fetch(INTRUDER, CHUNK_KEY)
    assert len(store.audit) == before + 2
    last = store.audit.entries[-1]
    assert last.role == INTRUDER and last.granted is False


def test_audit_records_granted_flag(store):
    store.fetch(ADMIN, CHUNK_KEY)
    granted_entry = store.audit.entries[-1]
    assert granted_entry.role == ADMIN
    assert granted_entry.granted is True
    assert granted_entry.action == "fetch"


def test_decrypt_timing_page_lt_chunk_lt_dataset():
    """Timing sanity: per-page < per-chunk < full-dataset decrypt cost."""
    crypto = ChunkCrypto()
    page = crypto.encrypt(b"x" * (512 * 64))            # ~ a page
    chunk = crypto.encrypt(b"x" * (1716 * 64))          # ~ a chunk
    dataset = crypto.encrypt(b"x" * (494021 * 8))       # ~ full dataset blob

    def median_decrypt(blob, reps=5):
        samples = []
        for _ in range(reps):
            t0 = time.perf_counter()
            crypto.decrypt(blob)
            samples.append(time.perf_counter() - t0)
        samples.sort()
        return samples[len(samples) // 2]

    t_page = median_decrypt(page)
    t_chunk = median_decrypt(chunk)
    t_dataset = median_decrypt(dataset)
    assert t_page < t_chunk < t_dataset


# --- Negative / adversarial (REQUIRED) --------------------------------------

def test_unauthorized_role_returns_empty_no_exception(store):
    out = store.fetch(INTRUDER, CHUNK_KEY)
    assert out == []  # zero records, no exception


def test_decrypt_never_called_on_rbac_failure(mocker):
    crypto = ChunkCrypto()
    access = AccessControl({ADMIN: [CHUNK_KEY]})
    s = SecureChunkStore(crypto=crypto, access=access, audit=AuditLogger())
    s.store_records(CHUNK_KEY, _records(10))

    spy = mocker.spy(s.crypto, "decrypt")
    out = s.fetch(INTRUDER, CHUNK_KEY)

    assert out == []
    assert spy.call_count == 0  # LOAD-BEARING: no decrypt on denial
    # ...but the attempt was still audited.
    assert len(s.audit) == 1
    assert s.audit.entries[-1].granted is False


def test_decrypt_called_exactly_once_on_grant(mocker):
    crypto = ChunkCrypto()
    access = AccessControl({ADMIN: [CHUNK_KEY]})
    s = SecureChunkStore(crypto=crypto, access=access, audit=AuditLogger())
    s.store_records(CHUNK_KEY, _records(10))

    spy = mocker.spy(s.crypto, "decrypt")
    s.fetch(ADMIN, CHUNK_KEY)
    assert spy.call_count == 1


def test_tampered_ciphertext_raises_invalid_tag():
    crypto = ChunkCrypto()
    blob = bytearray(crypto.encrypt(b"sensitive payload"))
    blob[-1] ^= 0x01  # flip a bit in the GCM tag
    with pytest.raises(InvalidTag):
        crypto.decrypt(bytes(blob))


def test_tampered_nonce_raises_invalid_tag():
    crypto = ChunkCrypto()
    blob = bytearray(crypto.encrypt(b"sensitive payload"))
    blob[0] ^= 0x01  # corrupt the nonce
    with pytest.raises(InvalidTag):
        crypto.decrypt(bytes(blob))


def test_wrong_key_cannot_decrypt():
    enc = ChunkCrypto()
    dec = ChunkCrypto()  # different random key
    blob = enc.encrypt(b"sensitive payload")
    with pytest.raises(InvalidTag):
        dec.decrypt(blob)


def test_round_trip_recovers_plaintext():
    crypto = ChunkCrypto()
    pt = b"the quick brown fox" * 100
    assert crypto.decrypt(crypto.encrypt(pt)) == pt


def test_key_must_be_32_bytes():
    with pytest.raises(ValueError):
        ChunkCrypto(key=b"too-short")


def test_audit_captures_100_percent_of_accesses(store):
    """Count before/after every path: granted, denied, and absent-key."""
    paths = [
        (ADMIN, CHUNK_KEY),     # granted, present
        (ADMIN, OTHER_KEY),     # granted, present
        (INTRUDER, CHUNK_KEY),  # denied
        (INTRUDER, OTHER_KEY),  # denied
        (ADMIN, ("z", "z", "z")),  # granted by policy? no -> denied (not in set)
    ]
    before = len(store.audit)
    for role, key in paths:
        store.fetch(role, key)
    assert len(store.audit) == before + len(paths)


def test_absent_key_for_authorized_role_returns_empty_and_audits():
    crypto = ChunkCrypto()
    access = AccessControl({ADMIN: "*"})  # wildcard: allowed everywhere
    s = SecureChunkStore(crypto=crypto, access=access, audit=AuditLogger())
    out = s.fetch(ADMIN, CHUNK_KEY)  # nothing stored
    assert out == []
    assert len(s.audit) == 1
    assert s.audit.entries[-1].granted is True


def test_wildcard_role_allowed_everywhere():
    access = AccessControl({ADMIN: "*"})
    assert access.is_allowed(ADMIN, CHUNK_KEY)
    assert access.is_allowed(ADMIN, OTHER_KEY)


def test_unknown_role_denied_by_default():
    access = AccessControl({ADMIN: [CHUNK_KEY]})
    assert access.is_allowed("nobody", CHUNK_KEY) is False


def test_grant_and_revoke():
    access = AccessControl()
    assert access.is_allowed(ADMIN, CHUNK_KEY) is False
    access.grant(ADMIN, CHUNK_KEY)
    assert access.is_allowed(ADMIN, CHUNK_KEY) is True
    access.revoke(ADMIN)
    assert access.is_allowed(ADMIN, CHUNK_KEY) is False
