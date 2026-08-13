"""
PettyFlow Core Double-Entry Ledger Engine
Implements strict double-entry balance invariants, fixed-point integer math,
account classifications, and atomic posting validation.
"""

from enum import Enum
from typing import List
from dataclasses import dataclass, field
import datetime

class AccountCategory(Enum):
    ASSET = "ASSET"          # Normal Balance: Debit
    LIABILITY = "LIABILITY"  # Normal Balance: Credit
    EQUITY = "EQUITY"        # Normal Balance: Credit
    EXPENSE = "EXPENSE"      # Normal Balance: Debit
    REVENUE = "REVENUE"      # Normal Balance: Credit

class EntryType(Enum):
    DEBIT = "DEBIT"
    CREDIT = "CREDIT"

class UnbalancedLedgerEntryException(Exception):
    """Raised when sum(Debits) != sum(Credits) for a given transaction batch."""
    pass

class InvalidAccountTypeException(Exception):
    """Raised when an account operation violates account category constraints."""
    pass

SCALE_FACTOR = 10_000  # 1.0000 currency unit precision (e.g., $100.25 -> 1,002,500)

def float_to_scaled_int(amount: float) -> int:
    """Convert float currency to 64-bit integer fixed-point (scaled by 10,000)."""
    return int(round(amount * SCALE_FACTOR))

def scaled_int_to_float(scaled_amount: int) -> float:
    """Convert scaled integer fixed-point back to float currency."""
    return scaled_amount / SCALE_FACTOR

@dataclass(frozen=True)
class Account:
    account_id: str
    tenant_id: str
    name: str
    category: AccountCategory
    currency: str = "USD"
    created_at: datetime.datetime = field(default_factory=datetime.datetime.now)

@dataclass(frozen=True)
class PostingLeg:
    account_id: str
    entry_type: EntryType
    amount_scaled: int  # Fixed-point 64-bit integer scaled by 10,000

    def __post_init__(self):
        if self.amount_scaled <= 0:
            raise ValueError(f"Posting leg amount must be positive scaled integer, got {self.amount_scaled}")

@dataclass
class TransactionBatch:
    transaction_id: str
    tenant_id: str
    description: str
    legs: List[PostingLeg]
    timestamp: datetime.datetime = field(default_factory=datetime.datetime.now)

    def validate_balance(self) -> bool:
        """
        Enforce Zero-Sum Double-Entry Invariant:
        sum(Debits) == sum(Credits)
        """
        total_debits = 0
        total_credits = 0
        for leg in self.legs:
            if leg.entry_type == EntryType.DEBIT:
                total_debits += leg.amount_scaled
            else:
                total_credits += leg.amount_scaled

        if total_debits != total_credits:
            raise UnbalancedLedgerEntryException(
                f"Transaction {self.transaction_id} is unbalanced! "
                f"Total Debits: {total_debits} ({total_debits/SCALE_FACTOR:.4f}), "
                f"Total Credits: {total_credits} ({total_credits/SCALE_FACTOR:.4f})"
            )
        return True
