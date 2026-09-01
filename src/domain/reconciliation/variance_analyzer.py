"""Variance Analyzer & Auto-Adjustment Ledger Generator.

Analyzes physical cash count discrepancies against corporate policy thresholds
and automatically constructs balanced double-entry ledger batches for approved
variances (Cash Over / Short accounting).

Invariants:
- Strict double-entry invariant: sum(Debits) == sum(Credits) for all generated adjustments.
- All monetary arithmetic uses 64-bit integer fixed-point (scaled x10^4).
- Policy tiers:
    - Minor variance (<= $5.00): AUTO_ADJUSTED
    - Moderate variance ($5.00 - $50.00): PENDING_APPROVAL (Manager required)
    - Major variance (> $50.00): ESCALATED_AUDIT (Finance Director + Fraud Review)
"""

from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from src.domain.ledger.entry import EntryType, PostingLeg, TransactionBatch
from src.domain.reconciliation.matcher import ReconciliationResult, ReconciliationStatus


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class VarianceDisposition(str, Enum):
    AUTO_ADJUSTED = "AUTO_ADJUSTED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    ESCALATED_AUDIT = "ESCALATED_AUDIT"
    REJECTED = "REJECTED"


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

# Default Policy Thresholds (scaled x10^4)
DEFAULT_AUTO_ADJUST_THRESHOLD_SCALED = 50_000     # $5.00
DEFAULT_MANAGER_THRESHOLD_SCALED = 500_000        # $50.00


@dataclass
class VarianceEntry:
    """Detailed record of a single analyzed variance."""
    variance_id: str
    tenant_id: str
    fund_id: str
    reconciliation_id: str
    variance_scaled: int                # Positive = Over, Negative = Short
    disposition: VarianceDisposition
    required_role: Optional[str] = None
    adjustment_batch: Optional[TransactionBatch] = None
    notes: Optional[str] = None


@dataclass
class VarianceAnalysisResult:
    """Outcome of analyzing a reconciliation variance."""
    analysis_id: str
    reconciliation_id: str
    tenant_id: str
    fund_id: str
    variance_scaled: int
    variance_formatted: str
    disposition: VarianceDisposition
    is_auto_adjusted: bool
    requires_escalation: bool
    required_role: Optional[str]
    adjustment_batch: Optional[TransactionBatch]
    policy_summary: str
    analyzed_at: str = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return {
            "analysis_id": self.analysis_id,
            "reconciliation_id": self.reconciliation_id,
            "tenant_id": self.tenant_id,
            "fund_id": self.fund_id,
            "variance_scaled": self.variance_scaled,
            "variance_formatted": self.variance_formatted,
            "disposition": self.disposition.value,
            "is_auto_adjusted": self.is_auto_adjusted,
            "requires_escalation": self.requires_escalation,
            "required_role": self.required_role,
            "adjustment_batch_id": self.adjustment_batch.transaction_id if self.adjustment_batch else None,
            "policy_summary": self.policy_summary,
            "analyzed_at": self.analyzed_at,
        }


# ---------------------------------------------------------------------------
# Variance Analyzer
# ---------------------------------------------------------------------------

class VarianceAnalyzer:
    """Evaluates reconciliation variances and creates balanced adjustment journal entries."""

    def __init__(
        self,
        auto_adjust_threshold_scaled: int = DEFAULT_AUTO_ADJUST_THRESHOLD_SCALED,
        manager_threshold_scaled: int = DEFAULT_MANAGER_THRESHOLD_SCALED,
        cash_over_short_account_id: str = "ACC_CASH_OVER_SHORT",
    ):
        self.auto_adjust_threshold_scaled = auto_adjust_threshold_scaled
        self.manager_threshold_scaled = manager_threshold_scaled
        self.cash_over_short_account_id = cash_over_short_account_id

    def generate_adjustment_batch(
        self,
        tenant_id: str,
        fund_account_id: str,
        variance_scaled: int,
        reason: str = "Daily Cash Reconciliation Adjustment",
    ) -> TransactionBatch:
        """Construct a balanced double-entry TransactionBatch for variance adjustment.

        Accounting logic:
        - Cash Short (variance < 0):
            Debit: Cash Over/Short Expense Account
            Credit: Petty Cash Asset Account
        - Cash Over (variance > 0):
            Debit: Petty Cash Asset Account
            Credit: Cash Over/Short Revenue Account

        Guarantees: sum(Debits) == sum(Credits).
        """
        if variance_scaled == 0:
            raise ValueError("Cannot create adjustment batch for zero variance")

        tx_id = f"ADJ-{uuid.uuid4().hex[:12].upper()}"
        abs_variance = abs(variance_scaled)

        if variance_scaled < 0:
            # Cash Shortage: Expense increased, Petty Cash decreased
            legs = [
                PostingLeg(
                    account_id=self.cash_over_short_account_id,
                    entry_type=EntryType.DEBIT,
                    amount_scaled=abs_variance,
                ),
                PostingLeg(
                    account_id=fund_account_id,
                    entry_type=EntryType.CREDIT,
                    amount_scaled=abs_variance,
                ),
            ]
            desc = f"{reason}: Cash Shortage of ${abs_variance / 10_000:.2f}"
        else:
            # Cash Overage: Petty Cash increased, Revenue increased
            legs = [
                PostingLeg(
                    account_id=fund_account_id,
                    entry_type=EntryType.DEBIT,
                    amount_scaled=abs_variance,
                ),
                PostingLeg(
                    account_id=self.cash_over_short_account_id,
                    entry_type=EntryType.CREDIT,
                    amount_scaled=abs_variance,
                ),
            ]
            desc = f"{reason}: Cash Overage of ${abs_variance / 10_000:.2f}"

        # TransactionBatch validates sum(Debits) == sum(Credits) in __post_init__
        return TransactionBatch(
            transaction_id=tx_id,
            tenant_id=tenant_id,
            description=desc,
            legs=legs,
        )

    def analyze(
        self,
        reconciliation_result: ReconciliationResult,
        fund_account_id: str,
    ) -> VarianceAnalysisResult:
        """Analyze reconciliation result and produce disposition and adjustment if eligible."""
        analysis_id = f"VANAL-{uuid.uuid4().hex[:12].upper()}"
        variance = reconciliation_result.cash_variance_scaled
        abs_variance = abs(variance)
        tenant_id = reconciliation_result.tenant_id
        fund_id = reconciliation_result.fund_id

        if variance == 0:
            return VarianceAnalysisResult(
                analysis_id=analysis_id,
                reconciliation_id=reconciliation_result.reconciliation_id,
                tenant_id=tenant_id,
                fund_id=fund_id,
                variance_scaled=0,
                variance_formatted="$0.00",
                disposition=VarianceDisposition.AUTO_ADJUSTED,
                is_auto_adjusted=True,
                requires_escalation=False,
                required_role=None,
                adjustment_batch=None,
                policy_summary="No variance detected. Fund is perfectly balanced.",
            )

        variance_formatted = f"${variance / 10_000:.2f}"

        if abs_variance <= self.auto_adjust_threshold_scaled:
            # Minor variance: Auto-adjust
            batch = self.generate_adjustment_batch(
                tenant_id=tenant_id,
                fund_account_id=fund_account_id,
                variance_scaled=variance,
                reason="Auto-Adjustment (Under Minor Variance Threshold)",
            )
            return VarianceAnalysisResult(
                analysis_id=analysis_id,
                reconciliation_id=reconciliation_result.reconciliation_id,
                tenant_id=tenant_id,
                fund_id=fund_id,
                variance_scaled=variance,
                variance_formatted=variance_formatted,
                disposition=VarianceDisposition.AUTO_ADJUSTED,
                is_auto_adjusted=True,
                requires_escalation=False,
                required_role=None,
                adjustment_batch=batch,
                policy_summary=(
                    f"Variance {variance_formatted} is within auto-adjustment limit "
                    f"(${self.auto_adjust_threshold_scaled / 10_000:.2f}). Balanced journal generated."
                ),
            )

        elif abs_variance <= self.manager_threshold_scaled:
            # Moderate variance: Manager approval required
            batch = self.generate_adjustment_batch(
                tenant_id=tenant_id,
                fund_account_id=fund_account_id,
                variance_scaled=variance,
                reason="Reconciliation Adjustment (Pending Manager Sign-Off)",
            )
            return VarianceAnalysisResult(
                analysis_id=analysis_id,
                reconciliation_id=reconciliation_result.reconciliation_id,
                tenant_id=tenant_id,
                fund_id=fund_id,
                variance_scaled=variance,
                variance_formatted=variance_formatted,
                disposition=VarianceDisposition.PENDING_APPROVAL,
                is_auto_adjusted=False,
                requires_escalation=False,
                required_role="FINANCE_MANAGER",
                adjustment_batch=batch,
                policy_summary=(
                    f"Variance {variance_formatted} exceeds auto-adjustment limit but is within "
                    f"manager threshold (${self.manager_threshold_scaled / 10_000:.2f}). Requires Manager sign-off."
                ),
            )

        else:
            # Major variance: Escalate to Finance Director + Fraud Audit
            return VarianceAnalysisResult(
                analysis_id=analysis_id,
                reconciliation_id=reconciliation_result.reconciliation_id,
                tenant_id=tenant_id,
                fund_id=fund_id,
                variance_scaled=variance,
                variance_formatted=variance_formatted,
                disposition=VarianceDisposition.ESCALATED_AUDIT,
                is_auto_adjusted=False,
                requires_escalation=True,
                required_role="FINANCE_DIRECTOR",
                adjustment_batch=None,
                policy_summary=(
                    f"CRITICAL: Variance {variance_formatted} exceeds manager threshold "
                    f"(${self.manager_threshold_scaled / 10_000:.2f}). Escalated to Finance Director & Audit."
                ),
            )
