"""NetSuite SuiteTalk REST Integration Connector.

Provides bidirectional journal entry synchronization between PettyFlow and
NetSuite via the SuiteTalk REST API.

Mock backend for testing — in production replace _call_netsuite() with
actual TBA (Token-Based Authentication) signed REST calls to:
  POST /services/rest/record/v1/journalentry

All monetary values use 64-bit integer fixed-point (scaled x10^4).
Acceptance Criterion: Bidirectional sync verified with mock NetSuite environment.
"""

from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

class NetSuiteLineType(str, Enum):
    DEBIT = "debit"
    CREDIT = "credit"


@dataclass
class NetSuiteJournalLine:
    """A single line in a NetSuite journal entry."""
    account_id: str                     # NetSuite internal account ID or number
    line_type: NetSuiteLineType
    amount_scaled: int                  # 64-bit int fixed-point (x10^4)
    currency: str = "USD"
    department: str = ""
    class_value: str = ""               # 'class' is reserved keyword in Python
    memo: str = ""

    @property
    def amount_float(self) -> float:
        return self.amount_scaled / 10_000.0


@dataclass
class NetSuiteJournalEntry:
    """NetSuite journal entry for petty cash replenishment."""
    tenant_id: str
    subsidiary: str                     # NetSuite subsidiary ID
    reference_document: str             # PettyFlow transaction ID
    posting_period: str                 # e.g., "Aug 2026"
    memo: str
    lines: List[NetSuiteJournalLine] = field(default_factory=list)
    idempotency_key: Optional[str] = None
    netsuite_internal_id: Optional[str] = None  # Set after successful post
    netsuite_tran_id: Optional[str] = None       # e.g., "JE-00042"
    synced_at: Optional[str] = None
    is_approved: bool = False

    def validate_balance(self) -> bool:
        """Enforce double-entry: total debits == total credits."""
        debits = sum(l.amount_scaled for l in self.lines if l.line_type == NetSuiteLineType.DEBIT)
        credits = sum(l.amount_scaled for l in self.lines if l.line_type == NetSuiteLineType.CREDIT)
        if debits != credits:
            raise ValueError(
                f"NetSuite journal entry imbalanced: debits={debits}, credits={credits}"
            )
        return True


class NetSuiteAdapterError(Exception):
    """Raised when NetSuite SuiteTalk API call fails."""
    pass


# ---------------------------------------------------------------------------
# NetSuite Adapter
# ---------------------------------------------------------------------------

class NetSuiteAdapter:
    """NetSuite SuiteTalk REST journal entry connector.

    Mock backend returns synthetic NetSuite internal IDs.
    Production: inject account_id, consumer_key, token_key for TBA signing.
    """

    def __init__(
        self,
        subsidiary: str = "1",
        default_currency: str = "USD",
        mock_mode: bool = True,
    ):
        self.subsidiary = subsidiary
        self.default_currency = default_currency
        self.mock_mode = mock_mode
        # idempotency_key -> NetSuiteJournalEntry
        self._posted: Dict[str, NetSuiteJournalEntry] = {}
        self._sync_log: List[NetSuiteJournalEntry] = []
        self._je_counter: int = 0

    def post_journal_entry(self, entry: NetSuiteJournalEntry) -> NetSuiteJournalEntry:
        """Post a balanced journal entry to NetSuite.

        Args:
            entry: NetSuiteJournalEntry with balanced lines.

        Returns:
            Updated entry with netsuite_internal_id and synced_at set.

        Raises:
            NetSuiteAdapterError: If posting fails.
        """
        entry.validate_balance()

        # Idempotency check
        if entry.idempotency_key:
            existing = self._posted.get(entry.idempotency_key)
            if existing is not None:
                return existing

        # Generate mock NetSuite internal ID and journal entry number
        self._je_counter += 1
        internal_id = str(uuid.uuid4().int % 1_000_000)
        tran_id = f"JE-{self._je_counter:05d}"
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()

        entry.netsuite_internal_id = internal_id
        entry.netsuite_tran_id = tran_id
        entry.synced_at = now_str
        entry.is_approved = True

        if entry.idempotency_key:
            self._posted[entry.idempotency_key] = entry
        self._sync_log.append(entry)
        return entry

    def get_journal_entry(self, netsuite_internal_id: str) -> Optional[NetSuiteJournalEntry]:
        """Retrieve a posted journal entry by NetSuite internal ID."""
        for entry in self._sync_log:
            if entry.netsuite_internal_id == netsuite_internal_id:
                return entry
        return None

    def sync_log_count(self) -> int:
        """Return number of posted journal entries."""
        return len(self._sync_log)

    def build_replenishment_entry(
        self,
        tenant_id: str,
        reference_document: str,
        amount_scaled: int,
        petty_cash_account: str = "10100",
        bank_account: str = "10000",
        currency: str = "USD",
        memo: str = "Petty Cash Replenishment",
    ) -> NetSuiteJournalEntry:
        """Factory: build a standard petty cash replenishment journal entry.

        Debit:  Petty Cash account (increase float on hand)
        Credit: Bank/Checking account (decrease bank balance)
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        posting_period = now.strftime("%b %Y")  # e.g., "Aug 2026"

        entry = NetSuiteJournalEntry(
            tenant_id=tenant_id,
            subsidiary=self.subsidiary,
            reference_document=reference_document,
            posting_period=posting_period,
            memo=memo,
            idempotency_key=reference_document,
            lines=[
                NetSuiteJournalLine(
                    account_id=petty_cash_account,
                    line_type=NetSuiteLineType.DEBIT,
                    amount_scaled=amount_scaled,
                    currency=currency,
                    memo=f"Float replenishment — {memo}",
                ),
                NetSuiteJournalLine(
                    account_id=bank_account,
                    line_type=NetSuiteLineType.CREDIT,
                    amount_scaled=amount_scaled,
                    currency=currency,
                    memo=f"Bank drawdown — {memo}",
                ),
            ],
        )
        return entry
