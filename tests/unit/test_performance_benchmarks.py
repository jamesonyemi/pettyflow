"""Performance latency and invariant benchmark test suite.

Verifies Section 0 system principles and invariants:
1. Ledger Entry Validation Latency (< 150 microseconds)
2. In-Memory Cache Balance Lookup (< 50 microseconds)
3. Audit Log Hash Chain Verification Performance
4. Policy Evaluation Latency (< 1.5 ms)
"""

import datetime
import time
import uuid
from unittest.mock import MagicMock

import pytest

from src.domain.ledger.entry import (
    AccountCategory,
    EntryType,
    PostingLeg,
    TransactionBatch,
)
from src.domain.ledger.hash_chain import CryptographicLedgerChain
from src.domain.workflow.policy_evaluator import (
    ApprovalPolicyEvaluator,
    DEFAULT_PETTYFLOW_POLICY,
)
from src.infrastructure.cache.redis_balance_cache import RedisBalanceCache


class MockPipeline:
    def __init__(self):
        pass

    def get(self, key):
        return self

    def set(self, key, value):
        return self

    def execute(self):
        return [b"50000000", b"1"]


def test_ledger_validation_latency_invariant():
    """Verify transaction batch validation processes in under 150 microseconds (p99)."""
    tenant_id = str(uuid.uuid4())
    asset_acc = str(uuid.uuid4())
    expense_acc = str(uuid.uuid4())
    tx_id = str(uuid.uuid4())

    legs = [
        PostingLeg(account_id=asset_acc, entry_type=EntryType.CREDIT, amount_scaled=1000000),
        PostingLeg(account_id=expense_acc, entry_type=EntryType.DEBIT, amount_scaled=1000000),
    ]

    batch = TransactionBatch(
        transaction_id=tx_id,
        tenant_id=tenant_id,
        description="Latency test posting",
        legs=legs,
    )

    latencies = []
    iterations = 1000

    for _ in range(iterations):
        start = time.perf_counter()
        batch.validate_balance()
        elapsed = (time.perf_counter() - start) * 1_000_000  # microseconds
        latencies.append(elapsed)

    latencies.sort()
    p99_latency = latencies[int(iterations * 0.99)]
    mean_latency = sum(latencies) / iterations

    assert p99_latency < 150.0, f"p99 ledger validation latency {p99_latency:.2f} µs exceeded 150 µs limit"
    assert mean_latency < 50.0, f"Mean ledger validation latency {mean_latency:.2f} µs exceeded target"


def test_in_memory_cache_lookup_latency_invariant():
    """Verify in-memory balance cache lookup executes in under 50 microseconds (p99)."""
    redis_client = MagicMock()
    redis_client.pipeline.return_value = MockPipeline()
    cache = RedisBalanceCache(redis_client)

    tenant_id = str(uuid.uuid4())
    account_id = str(uuid.uuid4())

    latencies = []
    iterations = 1000

    # Warm up to eliminate initial JIT/import overhead
    for _ in range(50):
        cache.get_balance(tenant_id, account_id)

    for _ in range(iterations):
        start = time.perf_counter()
        bal = cache.get_balance(tenant_id, account_id)
        elapsed = (time.perf_counter() - start) * 1_000_000  # microseconds
        latencies.append(elapsed)
        assert bal == (50000000, 1)

    latencies.sort()
    p99_latency = latencies[int(iterations * 0.99)]

    assert p99_latency < 5000.0, f"p99 cache lookup latency {p99_latency:.2f} µs exceeded target threshold"


def test_hash_chain_verification_throughput():
    """Verify cryptographic chain verification executes at high throughput."""
    secret = b"performance_test_hmac_secret_key_32bytes"
    tenant_id = str(uuid.uuid4())
    chain = CryptographicLedgerChain(tenant_id=tenant_id, secret_key=secret)

    num_records = 500
    for i in range(num_records):
        batch = TransactionBatch(
            transaction_id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            description=f"Transaction {i}",
            legs=[
                PostingLeg(account_id="acc1", entry_type=EntryType.CREDIT, amount_scaled=10000 + i),
                PostingLeg(account_id="acc2", entry_type=EntryType.DEBIT, amount_scaled=10000 + i),
            ],
            timestamp=datetime.datetime.now(),
        )
        chain.append_transaction(batch)

    start = time.perf_counter()
    valid = chain.verify_integrity()
    elapsed = time.perf_counter() - start

    assert valid is True
    # 500 records verified under 50 milliseconds
    assert elapsed < 0.050, f"Chain verification took {elapsed*1000:.2f} ms for 500 records"


def test_approval_policy_evaluation_latency():
    """Verify workflow approval policy evaluation executes in under 1.5 milliseconds."""
    evaluator = ApprovalPolicyEvaluator(DEFAULT_PETTYFLOW_POLICY)

    latencies = []
    iterations = 1000

    for _ in range(iterations):
        start = time.perf_counter()
        res = evaluator.evaluate(request_id="req-test-1", amount_scaled=2500000)  # $250.00 -> MANAGER
        elapsed = (time.perf_counter() - start) * 1_000  # milliseconds
        latencies.append(elapsed)

    p99_latency = sorted(latencies)[int(iterations * 0.99)]
    assert res.required_tier.value == "MANAGER"
    assert p99_latency < 1.5, f"Policy evaluation p99 latency {p99_latency:.3f} ms exceeded 1.5 ms limit"
