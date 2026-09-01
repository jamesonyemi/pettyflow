"""3-Way Automated Cash Box & Bank Reconciliation Engine.

Implements rigorous 3-way matching:
1. Physical Cash Count (denominations & coin totals)
2. System Float Balance (opening float + replenishments - disbursements)
3. Bank Feed Statement (cleared ACH/wire replenishments)

All monetary calculations use 64-bit integer fixed-point arithmetic (scaled x10^4).
Invariants:
- Zero floating-point rounding.
- Explicit tenant scoping.
- Down-to-the-cent variance identification.
"""

from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ReconciliationStatus(str, Enum):
    BALANCED = "BALANCED"
    VARIANCE_OVER = "VARIANCE_OVER"
    VARIANCE_SHORT = "VARIANCE_SHORT"
    DISCREPANCY_PENDING_AUDIT = "DISCREPANCY_PENDING_AUDIT"


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class DenominationBreakdown:
    """Cash denomination counts for physical cash box audits."""
    hundreds: int = 0      # $100 bills
    fifties: int = 0       # $50 bills
    twenties: int = 0      # $20 bills
    tens: int = 0          # $10 bills
    fives: int = 0         # $5 bills
    ones: int = 0          # $1 bills
    quarters: int = 0      # $0.25 coins
    dimes: int = 0         # $0.10 coins
    nickels: int = 0       # $0.05 coins
    pennies: int = 0       # $0.01 coins
    custom_coins_scaled: int = 0  # Miscellaneous coins scaled x10^4

    @property
    def total_scaled(self) -> int:
        """Compute exact physical cash sum in scaled integer units (x10^4)."""
        return (
            self.hundreds * 100 * 10_000
            + self.fifties * 50 * 10_000
            + self.twenties * 20 * 10_000
            + self.tens * 10 * 10_000
            + self.fives * 5 * 10_000
            + self.ones * 1 * 10_000
            + self.quarters * 2_500
            + self.dimes * 1_000
            + self.nickels * 500
            + self.pennies * 100
            + self.custom_coins_scaled
        )


@dataclass
class CashCountRecord:
    """Physical cash box count performed by custodian."""
    count_id: str
    tenant_id: str
    fund_id: str
    custodian_id: str
    denominations: DenominationBreakdown
    counted_at: str = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )
    verified_by: Optional[str] = None
    notes: Optional[str] = None

    @property
    def total_physical_scaled(self) -> int:
        return self.denominations.total_scaled

    @property
    def total_physical_float(self) -> float:
        return self.total_physical_scaled / 10_000.0


@dataclass
class SystemFloatRecord:
    """Internal ledger state of the petty cash fund."""
    fund_id: str
    tenant_id: str
    opening_float_scaled: int
    total_disbursed_scaled: int
    total_replenished_scaled: int
    pending_disbursements_scaled: int = 0

    @property
    def expected_balance_scaled(self) -> int:
        """Expected physical cash in drawer = Opening + Replenished - Disbursed."""
        return (
            self.opening_float_scaled
            + self.total_replenished_scaled
            - self.total_disbursed_scaled
        )

    @property
    def effective_balance_scaled(self) -> int:
        """Available balance factoring in pending disbursements."""
        return self.expected_balance_scaled - self.pending_disbursements_scaled


@dataclass
class BankFeedRecord:
    """Bank statement feed record for fund replenishment account."""
    feed_id: str
    tenant_id: str
    bank_account_id: str
    cleared_replenishments_scaled: int
    pending_transfers_scaled: int = 0
    statement_date: str = field(
        default_factory=lambda: datetime.date.today().isoformat()
    )


@dataclass
class ReconciliationResult:
    """Consolidated outcome of 3-way cash reconciliation."""
    reconciliation_id: str
    tenant_id: str
    fund_id: str
    status: ReconciliationStatus
    physical_cash_scaled: int
    system_expected_scaled: int
    bank_cleared_scaled: int
    cash_variance_scaled: int            # Physical - System (positive = Over, negative = Short)
    bank_variance_scaled: int            # Bank Cleared - System Replenished
    is_balanced: bool
    reconciled_at: str = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )
    details: Dict[str, str] = field(default_factory=dict)

    @property
    def cash_variance_float(self) -> float:
        return self.cash_variance_scaled / 10_000.0

    @property
    def is_exact_match(self) -> bool:
        return self.cash_variance_scaled == 0 and self.bank_variance_scaled == 0

    def to_dict(self) -> dict:
        return {
            "reconciliation_id": self.reconciliation_id,
            "tenant_id": self.tenant_id,
            "fund_id": self.fund_id,
            "status": self.status.value,
            "is_balanced": self.is_balanced,
            "is_exact_match": self.is_exact_match,
            "physical_cash_formatted": f"${self.physical_cash_scaled / 10_000:.2f}",
            "system_expected_formatted": f"${self.system_expected_scaled / 10_000:.2f}",
            "bank_cleared_formatted": f"${self.bank_cleared_scaled / 10_000:.2f}",
            "cash_variance_scaled": self.cash_variance_scaled,
            "cash_variance_formatted": f"${self.cash_variance_float:.2f}",
            "bank_variance_scaled": self.bank_variance_scaled,
            "bank_variance_formatted": f"${self.bank_variance_scaled / 10_000:.2f}",
            "reconciled_at": self.reconciled_at,
            "details": self.details,
        }


# ---------------------------------------------------------------------------
# Reconciliation Matcher
# ---------------------------------------------------------------------------

class ReconciliationMatcher:
    """Performs 3-way matching between physical cash, system ledger, and bank feed."""

    def __init__(self, tolerance_scaled: int = 0):
        """Tolerance for trivial rounding (default 0 for zero-trust strict matching)."""
        self.tolerance_scaled = tolerance_scaled

    def reconcile(
        self,
        cash_count: CashCountRecord,
        system_float: SystemFloatRecord,
        bank_feed: Optional[BankFeedRecord] = None,
    ) -> ReconciliationResult:
        """Execute 3-way reconciliation match.

        Args:
            cash_count: Physical cash count record.
            system_float: Internal ledger system float record.
            bank_feed: Optional bank feed statement.

        Returns:
            ReconciliationResult with calculated variances and status.
        """
        if cash_count.tenant_id != system_float.tenant_id:
            raise ValueError("Tenant isolation violation: cash count and system float tenant IDs mismatch")
        if bank_feed and bank_feed.tenant_id != cash_count.tenant_id:
            raise ValueError("Tenant isolation violation: bank feed tenant ID mismatch")

        rec_id = f"REC-{uuid.uuid4().hex[:12].upper()}"
        physical = cash_count.total_physical_scaled
        expected = system_float.expected_balance_scaled

        # Cash Box Variance: Physical - Expected
        cash_variance = physical - expected

        # Bank Feed Variance: Bank Cleared - System Replenished
        bank_cleared = bank_feed.cleared_replenishments_scaled if bank_feed else system_float.total_replenished_scaled
        bank_variance = bank_cleared - system_float.total_replenished_scaled

        # Determine status
        details = {}
        has_cash_variance = abs(cash_variance) > self.tolerance_scaled
        has_bank_variance = abs(bank_variance) > self.tolerance_scaled

        if not has_cash_variance and not has_bank_variance:
            status = ReconciliationStatus.BALANCED
            is_balanced = True
            details["message"] = "Physical cash and bank feeds match system float perfectly."
        elif has_bank_variance:
            status = ReconciliationStatus.DISCREPANCY_PENDING_AUDIT
            is_balanced = False
            msg_parts = [f"Bank statement replenishment variance detected: ${bank_variance / 10_000:.2f}"]
            if has_cash_variance:
                if cash_variance > 0:
                    msg_parts.append(f"Physical cash exceeds system float by ${cash_variance / 10_000:.2f} (Cash Over).")
                else:
                    msg_parts.append(f"Physical cash is short of system float by ${abs(cash_variance) / 10_000:.2f} (Cash Short).")
            details["message"] = " | ".join(msg_parts)
        elif cash_variance > 0:
            status = ReconciliationStatus.VARIANCE_OVER
            is_balanced = False
            details["message"] = f"Physical cash exceeds system float by ${cash_variance / 10_000:.2f} (Cash Over)."
        else:
            status = ReconciliationStatus.VARIANCE_SHORT
            is_balanced = False
            details["message"] = f"Physical cash is short of system float by ${abs(cash_variance) / 10_000:.2f} (Cash Short)."

        return ReconciliationResult(
            reconciliation_id=rec_id,
            tenant_id=cash_count.tenant_id,
            fund_id=cash_count.fund_id,
            status=status,
            physical_cash_scaled=physical,
            system_expected_scaled=expected,
            bank_cleared_scaled=bank_cleared,
            cash_variance_scaled=cash_variance,
            bank_variance_scaled=bank_variance,
            is_balanced=is_balanced,
            details=details,
        )
