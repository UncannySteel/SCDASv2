# Threat Model and Informal Security Argument

**Adversary.** We assume an honest-but-curious storage adversary with full read access to encrypted chunks at rest (the per-chunk AES-GCM blobs in `SecureChunkStore._blobs`) and to the unencrypted SCADS index metadata (chunk_key → chunk_id mapping). The adversary may also observe access patterns, i.e., which chunk_keys are fetched and in what order. The adversary cannot inject or modify ciphertext without detection (GCM authenticated decryption is invoked on every read; tampered or wrong-key blobs raise `InvalidTag`).

We do not consider an active adversary capable of compromising the SCADS coordinator process, the AES key material, or the role-based access-control policy — those are trust-boundary assumptions inherited from the deployment environment, not properties this framework establishes.

**Confidentiality goal.** Per-chunk plaintext records (the `list[dict]` payload of each chunk) must remain indistinguishable to the adversary defined above. We claim **IND-CPA security** of the per-chunk payload by direct reduction to the IND-CPA security of AES-256-GCM: each chunk is encrypted under the same 256-bit key with a freshly sampled 12-byte nonce from `os.urandom` (see `core/security.py:ChunkCrypto.encrypt`), the standard construction for which AES-GCM achieves IND-CPA security up to the GCM birthday bound (~2³² messages per key before nonce-reuse risk dominates). Formal proof is omitted by reference to the standard NIST SP 800-38D analysis.

**Integrity.** Per-chunk authenticity is guaranteed by the GCM authentication tag. Any modification of a ciphertext blob causes `decrypt` to raise — the system has no silent-corruption code path.

**Access control.** RBAC is enforced before decryption in a load-bearing ordering: `audit → policy check → (deny: return [], NO decrypt call) → decrypt → return`. The audit log is append-only and un-bypassable on any data-touching path (`core/security.py:SecureChunkStore.fetch`). Denied accesses never invoke the cipher and are still recorded.

**Acknowledged limitations.**

1. **Access-pattern leakage at chunk granularity.** The adversary learns which `chunk_key` triples are fetched. Since chunk_keys carry semantic information (`time_window`, `region`, `data_type`), this leaks coarse query intent even though chunk contents remain confidential. Mitigation via ORAM or batched fetches is out of scope for this PoC.
2. **Index metadata is plaintext.** The chunk_key → chunk_id index is not encrypted, so the schema of segmentation is visible. This is a deliberate trade-off for O(1) lookup; an encrypted-index variant (PRF-keyed) is straightforward future work.
3. **No key management or rotation.** A single AES-256 key is used per `ChunkCrypto` instance for the PoC lifetime. Production deployment requires KMS integration and re-encryption-on-rotation; both are outside this paper's scope.
4. **No side-channel resistance.** Timing, cache, and power side-channels against the cipher implementation are inherited from the underlying `cryptography` library; this work makes no additional claims.

**Venue fit.** This threat model and informal argument are sized for a systems / data-management venue (IEEE Big Data, ICDE, IoT-J, TPDS, Cloud). A security-track submission (S&P, TIFS, CCS) would require a formal IND-CPA proof, explicit ORAM/PIR comparison, and side-channel analysis; we reserve those for follow-on work.
