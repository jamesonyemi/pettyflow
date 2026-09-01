# ADR-004: Fraud Screening Architecture — Perceptual Hashing & Sliding-Window Detection

## Context & Problem Statement

PettyFlow requires an automated fraud screening layer to prevent:
1. **Receipt duplication fraud**: Custodians submitting the same receipt multiple times.
2. **Split-transaction bypass**: Deliberately splitting a single large purchase into multiple
   smaller transactions to circumvent approval thresholds (< $50 auto-approve, $50–$500 manager,
   > $500 Finance Director).
3. **Amount anomaly detection**: Statistically unusual transaction amounts vs. historical baseline.
4. **Velocity abuse**: Unusual burst of transactions within short time windows.

## Decision Drivers

- Latency threshold: Fraud evaluation must complete in < 5ms per transaction (inline with API path).
- Accuracy requirement: 100% detection of duplicate images; < 2% false positive rate on control set.
- No heavy ML runtime: Inference path must not require GPU or large model loading per request.
- Multi-tenancy isolation: Fraud signals must be scoped per `tenant_id` — no cross-tenant leakage.
- Financial accuracy: All monetary comparisons use 64-bit integer fixed-point (x10^4).

## Considered Options

1. **Deep Learning CNN image similarity** (e.g., VGG-16 embeddings)
   - Pro: High accuracy on complex natural image variations.
   - Con: Requires GPU inference server, 200ms+ latency, large memory footprint.

2. **Perceptual Hash (dHash)** ← Chosen
   - Pro: O(1) computation from PIL resample, ~64-bit integer comparison, < 1ms latency.
   - Con: Cannot detect intentional high-frequency noise injection (easily mitigated by preprocessing).

3. **Exact MD5/SHA256 hash match only**
   - Pro: Zero false positives on exact matches.
   - Con: Any single-pixel change defeats it; fails JPEG recompression variants.

## Decision Outcome

**Chosen Option: Perceptual Hash (dHash) + Sliding-Window Temporal Detector**

- `perceptual_hash.py`: Computes 64-bit dHash fingerprint using 9×8 greyscale thumbnail pixel differences.
  Hamming distance ≤ 5 bits triggers duplicate flag.
- `split_tx_detector.py`: Deque-based sliding window (24h TTL) per `(tenant_id, custodian_id)`.
  Flags when ≥ 2 transactions sum > approval threshold.
- `risk_scorer.py`: Composite 0–100 score aggregating 4 signals with static weights.
  Score ≥ 50 → automatic hold; score ≥ 80 → automatic block + escalation.

## Consequences

- **Positive**:
  - Sub-millisecond duplicate detection without external service dependencies.
  - 100% detection rate on identical/near-identical receipt images in synthetic tests.
  - Stateless hasher; stateful detector uses in-process deque (upgradeable to Redis sorted sets).
  - Multi-tenant isolation enforced at the `DuplicateReceiptDetector._store` dictionary level.

- **Negative / Mitigations**:
  - dHash is vulnerable to adversarial high-frequency noise injection.
    → Mitigation: `ReceiptImagePreprocessor` (Week 5) applies autocontrast + sharpen before hashing.
  - In-process deque is lost on restart.
    → Mitigation: Week 10 Redis integration will persist sliding windows.
  - z-score anomaly requires ≥ 2 historical samples.
    → Mitigation: Low-history custodians default to no anomaly flag (conservative false-positive policy).
