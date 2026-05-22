"""Layer 4 — Lightweight Security: per-chunk AES-256-GCM + RBAC + audit logging.

Scope (honest positioning, spec §2/§11): this module implements per-chunk
authenticated encryption (AES-256-GCM), role-based access control, and an
un-bypassable audit log. It does NOT implement key management, key rotation,
or any side-channel resistance, and makes no cryptographic guarantees beyond
those provided by the underlying AES-GCM primitive.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Iterable

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from core.types import Chunk, ChunkKey

KEY_BYTES = 32   # AES-256
NONCE_BYTES = 12  # GCM standard nonce length


class ChunkCrypto:
    """AES-256-GCM authenticated encryption for a single payload.

    The serialized blob is `nonce (12 bytes) || ciphertext+tag`. Decryption of a
    tampered blob or a blob produced under a different key raises
    `cryptography.exceptions.InvalidTag`.
    """

    def __init__(self, key: bytes | None = None) -> None:
        if key is None:
            key = AESGCM.generate_key(bit_length=256)
        if len(key) != KEY_BYTES:
            raise ValueError(f"AES-256 requires a {KEY_BYTES}-byte key, got {len(key)}")
        self._key = key
        self._aes = AESGCM(key)

    @property
    def key(self) -> bytes:
        return self._key

    def encrypt(self, pt: bytes) -> bytes:
        nonce = os.urandom(NONCE_BYTES)
        ct = self._aes.encrypt(nonce, pt, None)
        return nonce + ct

    def decrypt(self, blob: bytes) -> bytes:
        nonce, ct = blob[:NONCE_BYTES], blob[NONCE_BYTES:]
        # Raises InvalidTag on tamper or wrong key.
        return self._aes.decrypt(nonce, ct, None)


class AccessControl:
    """Role-based access control at chunk granularity.

    Policy maps a role to either the wildcard ``"*"`` (all chunks) or a set of
    allowed chunk keys. Unknown roles and unlisted chunk keys are denied by
    default (fail-closed).
    """

    WILDCARD = "*"

    def __init__(self, policy: dict[str, Any] | None = None) -> None:
        self._policy: dict[str, Any] = {}
        for role, allowed in (policy or {}).items():
            if allowed == self.WILDCARD:
                self._policy[role] = self.WILDCARD
            else:
                self._policy[role] = set(allowed)

    def grant(self, role: str, chunk_key: ChunkKey | None = None) -> None:
        if chunk_key is None:
            self._policy[role] = self.WILDCARD
            return
        existing = self._policy.get(role)
        if existing == self.WILDCARD:
            return
        if existing is None:
            existing = set()
            self._policy[role] = existing
        existing.add(chunk_key)

    def revoke(self, role: str) -> None:
        self._policy.pop(role, None)

    def is_allowed(self, role: str, chunk_key: ChunkKey) -> bool:
        allowed = self._policy.get(role)
        if allowed is None:
            return False
        if allowed == self.WILDCARD:
            return True
        return chunk_key in allowed


@dataclass
class AuditEntry:
    role: str
    chunk_key: ChunkKey
    action: str
    granted: bool


class AuditLogger:
    """Append-only record of every access attempt, granted or denied."""

    def __init__(self) -> None:
        self.entries: list[AuditEntry] = []

    def log(self, role: str, chunk_key: ChunkKey, action: str, granted: bool) -> None:
        self.entries.append(AuditEntry(role, chunk_key, action, granted))

    def __len__(self) -> int:
        return len(self.entries)


def _serialize(records: list[dict]) -> bytes:
    return json.dumps(records, separators=(",", ":")).encode("utf-8")


def _deserialize(pt: bytes) -> list[dict]:
    return json.loads(pt.decode("utf-8"))


class SecureChunkStore:
    """Encrypted-at-rest chunk store, gated by RBAC and audited on every access.

    ``fetch`` enforces a LOAD-BEARING ordering:

        audit (record attempt) -> RBAC check -> (deny: return [], NO decrypt)
        -> decrypt -> return records

    The audit log cannot be bypassed by any data-touching path, and ``decrypt``
    is never invoked when access is denied.
    """

    def __init__(
        self,
        crypto: ChunkCrypto | None = None,
        access: AccessControl | None = None,
        audit: AuditLogger | None = None,
    ) -> None:
        self.crypto = crypto or ChunkCrypto()
        self.access = access or AccessControl()
        self.audit = audit or AuditLogger()
        self._blobs: dict[ChunkKey, bytes] = {}

    def store_chunk(self, chunk: Chunk) -> None:
        self.store_records(chunk.chunk_key, chunk.records)

    def store_records(self, chunk_key: ChunkKey, records: list[dict]) -> None:
        self._blobs[chunk_key] = self.crypto.encrypt(_serialize(records))

    def fetch(self, role: str, chunk_key: ChunkKey) -> list[dict]:
        granted = self.access.is_allowed(role, chunk_key)
        # Audit first and unconditionally — un-bypassable for any data path.
        self.audit.log(role, chunk_key, "fetch", granted)
        if not granted:
            # Denied: return empty, NO decrypt call, no exception.
            return []
        blob = self._blobs.get(chunk_key)
        if blob is None:
            return []
        pt = self.crypto.decrypt(blob)
        return _deserialize(pt)
