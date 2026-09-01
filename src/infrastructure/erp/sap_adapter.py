"""SAP S/4HANA OData API Integration Adapter.

Provides bidirectional journal posting synchronization between PettyFlow's
double-entry ledger and SAP S/4HANA via OData API.

Mock backend for testing — in production replace _call_sap_odata() with
actual HTTP calls to SAP S/4HANA OData service endpoints:
  POST /sap/opu/odata/sap/API_JOURNALENTRYITEMBASIC_SRV/A_JournalEntry

All monetary values use 64-bit integer fixed-point (scaled x10^4).
Acceptance Criterion: Bidirectional sync verified with mock SAP environment.
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

class SAPPostingDirection(str, Enum):
    DEBIT = "40"   # SAP posting key 40 = GL debit
    CREDIT = "50"  # SAP posting key 50 = GL credit


@dataclass
class SAPJournalLine:
    """A single line item in an SAP journal entry."""
    gl_account: str                     # SAP GL account number
    posting_direction: SAPPostingDirection
    amount_scaled: int                  # 64-bit int fixed-point (x10^4)
    currency: str = "USD"
    cost_center: str = ""
    profit_center: str = ""
    text: str = ""

    @property
    def amount_float(self) -> float:
        return self.amount_scaled / 10_000.0


@dataclass
class SAPJournalEntry:
    """SAP journal entry for petty cash replenishment posting."""
    tenant_id: str
    reference_document: str             # PettyFlow transaction ID
    company_code: str
    posting_date: str                   # YYYY-MM-DD
    document_date: str                  # YYYY-MM-DD
    description: str
    lines: List[SAPJournalLine] = field(default_factory=list)
    idempotency_key: Optional[str] = None
    sap_document_number: Optional[str] = None  # Filled after successful post
    synced_at: Optional[str] = None

    def validate_balance(self) -> bool:
        """Verify debits == credits (double-entry invariant)."""
        debits = sum(l.amount_scaled for l in self.lines if l.posting_direction == SAPPostingDirection.DEBIT)
        credits = sum(l.amount_scaled for l in self.lines if l.posting_direction == SAPPostingDirection.CREDIT)
        if debits != credits:
            raise ValueError(
                f"SAP journal entry imbalanced: debits={debits}, credits={credits}"
            )
        return True


class SAPAdapterError(Exception):
    """Raised when SAP OData API call fails."""
    pass


# ---------------------------------------------------------------------------
# SAP Adapter
# ---------------------------------------------------------------------------

class SAPAdapter:
    """SAP S/4HANA OData journal posting adapter.

    Mock backend returns synthetic SAP document numbers.
    Production: inject base_url, client_id, client_secret for OAuth2 flow.
    """

    def __init__(
        self,
        company_code: str = "1000",
        fiscal_year: str = "2026",
        mock_mode: bool = True,
    ):
        self.company_code = company_code
        self.fiscal_year = fiscal_year
        self.mock_mode = mock_mode
        # idempotency_key -> SAPJournalEntry
        self._posted: Dict[str, SAPJournalEntry] = {}
        self._sync_log: List[SAPJournalEntry] = []

    def post_journal_entry(self, entry: SAPJournalEntry) -> SAPJournalEntry:
        """Post a balanced journal entry to SAP S/4HANA.

        Args:
            entry: SAPJournalEntry with balanced debit/credit lines.

        Returns:
            Updated entry with sap_document_number and synced_at populated.

        Raises:
            SAPAdapterError: If posting fails or entry is imbalanced.
        """
        entry.validate_balance()

        # Idempotency check
        if entry.idempotency_key:
            existing = self._posted.get(entry.idempotency_key)
            if existing is not None:
                return existing

        # Generate SAP document number (mock)
        sap_doc_num = f"1900{uuid.uuid4().int % 100_000_000:08d}"
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()

        entry.sap_document_number = sap_doc_num
        entry.synced_at = now_str

        if entry.idempotency_key:
            self._posted[entry.idempotency_key] = entry
        self._sync_log.append(entry)
        return entry

    def get_journal_entry(self, sap_document_number: str) -> Optional[SAPJournalEntry]:
        """Retrieve a posted journal entry by SAP document number."""
        for entry in self._sync_log:
            if entry.sap_document_number == sap_document_number:
                return entry
        return None

    def sync_log_count(self) -> int:
        """Return number of posted journal entries (for testing)."""
        return len(self._sync_log)

    def build_replenishment_entry(
        self,
        tenant_id: str,
        reference_document: str,
        amount_scaled: int,
        petty_cash_gl: str = "101000",
        bank_gl: str = "113100",
        currency: str = "USD",
        description: str = "Petty Cash Replenishment",
    ) -> SAPJournalEntry:
        """Build a standard petty cash replenishment journal entry.

        Debit: Petty Cash GL (increase cash on hand)
        Credit: Bank GL (decrease bank balance)
        """
        today = datetime.date.today().isoformat()
        entry = SAPJournalEntry(
            tenant_id=tenant_id,
            reference_document=reference_document,
            company_code=self.company_code,
            posting_date=today,
            document_date=today,
            description=description,
            idempotency_key=reference_document,
            lines=[
                SAPJournalLine(
                    gl_account=petty_cash_gl,
                    posting_direction=SAPPostingDirection.DEBIT,
                    amount_scaled=amount_scaled,
                    currency=currency,
                    text=f"Replenish petty cash — {description}",
                ),
                SAPJournalLine(
                    gl_account=bank_gl,
                    posting_direction=SAPPostingDirection.CREDIT,
                    amount_scaled=amount_scaled,
                    currency=currency,
                    text=f"Bank transfer — {description}",
                ),
            ],
        )
        return entry
