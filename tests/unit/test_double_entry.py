"""
Unit Test Suite for PettyFlow Core Ledger Engine & Hash Chain
Validates double-entry invariants, fixed-point precision, latency benchmarks,
and cryptographic tamper detection.
"""

import unittest
import time
import uuid
import datetime
import random

from src.domain.ledger.entry import (
    Account, AccountCategory, EntryType, PostingLeg, TransactionBatch,
    UnbalancedLedgerEntryException, float_to_scaled_int, scaled_int_to_float
)
from src.domain.ledger.hash_chain import CryptographicLedgerChain, ChainTamperedException

class TestDoubleEntryEngine(unittest.TestCase):

    def setUp(self):
        self.tenant_id = str(uuid.uuid4())
        self.secret_key = b"super-secret-hmac-key-pettyflow-2026"
        
        self.cash_account = Account(
            account_id=str(uuid.uuid4()),
            tenant_id=self.tenant_id,
            name="Physical Cash Float",
            category=AccountCategory.ASSET
        )
        
        self.supplies_account = Account(
            account_id=str(uuid.uuid4()),
            tenant_id=self.tenant_id,
            name="Office Supplies Expense",
            category=AccountCategory.EXPENSE
        )

    def test_balanced_transaction_success(self):
        """Test a valid double-entry transaction posting."""
        amount_scaled = float_to_scaled_int(45.50)  # $45.50 -> 455,000
        
        leg1 = PostingLeg(account_id=self.supplies_account.account_id, entry_type=EntryType.DEBIT, amount_scaled=amount_scaled)
        leg2 = PostingLeg(account_id=self.cash_account.account_id, entry_type=EntryType.CREDIT, amount_scaled=amount_scaled)
        
        tx = TransactionBatch(
            transaction_id=str(uuid.uuid4()),
            tenant_id=self.tenant_id,
            description="Paper & Pens",
            legs=[leg1, leg2]
        )
        
        self.assertTrue(tx.validate_balance())

    def test_unbalanced_transaction_failure(self):
        """Verify UnbalancedLedgerEntryException is raised when debits != credits."""
        leg1 = PostingLeg(account_id=self.supplies_account.account_id, entry_type=EntryType.DEBIT, amount_scaled=float_to_scaled_int(50.00))
        leg2 = PostingLeg(account_id=self.cash_account.account_id, entry_type=EntryType.CREDIT, amount_scaled=float_to_scaled_int(49.99))
        
        tx = TransactionBatch(
            transaction_id=str(uuid.uuid4()),
            tenant_id=self.tenant_id,
            description="Unbalanced Coffee Expense",
            legs=[leg1, leg2]
        )
        
        with self.assertRaises(UnbalancedLedgerEntryException):
            tx.validate_balance()

    def test_fixed_point_precision(self):
        """Verify exact precision with floating point conversion."""
        amount = 1234567.8912
        scaled = float_to_scaled_int(amount)
        self.assertEqual(scaled, 12345678912)
        converted_back = scaled_int_to_float(scaled)
        self.assertEqual(converted_back, 1234567.8912)

    def test_cryptographic_chain_append_and_verify(self):
        """Test append-only cryptographic ledger signing and integrity verification."""
        chain = CryptographicLedgerChain(tenant_id=self.tenant_id, secret_key=self.secret_key)
        
        for i in range(10):
            amount = float_to_scaled_int(10.0 + i)
            tx = TransactionBatch(
                transaction_id=str(uuid.uuid4()),
                tenant_id=self.tenant_id,
                description=f"Transaction {i+1}",
                legs=[
                    PostingLeg(account_id=self.supplies_account.account_id, entry_type=EntryType.DEBIT, amount_scaled=amount),
                    PostingLeg(account_id=self.cash_account.account_id, entry_type=EntryType.CREDIT, amount_scaled=amount)
                ]
            )
            chain.append_transaction(tx)

        self.assertEqual(len(chain.blocks), 10)
        self.assertTrue(chain.verify_integrity())

    def test_tamper_detection(self):
        """Verify chain integrity fails when a historical block is tampered with."""
        chain = CryptographicLedgerChain(tenant_id=self.tenant_id, secret_key=self.secret_key)
        
        for i in range(5):
            amount = float_to_scaled_int(20.0)
            tx = TransactionBatch(
                transaction_id=str(uuid.uuid4()),
                tenant_id=self.tenant_id,
                description=f"Tx {i+1}",
                legs=[
                    PostingLeg(account_id=self.supplies_account.account_id, entry_type=EntryType.DEBIT, amount_scaled=amount),
                    PostingLeg(account_id=self.cash_account.account_id, entry_type=EntryType.CREDIT, amount_scaled=amount)
                ]
            )
            chain.append_transaction(tx)

        # Tamper with block 2 payload amount
        chain.blocks[2].legs_payload[0]["amt"] += 1000

        with self.assertRaises(ChainTamperedException):
            chain.verify_integrity()

    def test_10k_transactions_benchmark(self):
        """
        Acceptance Benchmark: Process 10,000 double-entry transactions in memory in < 500 ms.
        """
        chain = CryptographicLedgerChain(tenant_id=self.tenant_id, secret_key=self.secret_key)
        
        # Pre-create transaction templates to measure core ledger & HMAC append speed
        tx_list = [
            TransactionBatch(
                transaction_id=f"tx-bench-{i}",
                tenant_id=self.tenant_id,
                description=f"Bulk Tx {i}",
                legs=[
                    PostingLeg(account_id=self.supplies_account.account_id, entry_type=EntryType.DEBIT, amount_scaled=100_000),
                    PostingLeg(account_id=self.cash_account.account_id, entry_type=EntryType.CREDIT, amount_scaled=100_000)
                ]
            )
            for i in range(10_000)
        ]
        
        start_time = time.perf_counter()
        
        for tx in tx_list:
            chain.append_transaction(tx)

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        
        self.assertEqual(len(chain.blocks), 10_000)
        self.assertTrue(chain.verify_integrity())
        print(f"\n[BENCHMARK RESULT] 10,000 Transactions Processed & HMAC-Signed in {elapsed_ms:.2f} ms")
        self.assertLess(elapsed_ms, 500.0, f"Benchmark failed: Took {elapsed_ms:.2f} ms (limit 500 ms)")

if __name__ == "__main__":
    unittest.main()
