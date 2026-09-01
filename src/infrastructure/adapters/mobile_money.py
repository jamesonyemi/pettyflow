"""Mobile Money Disbursement Adapter — M-Pesa / ACH / Venmo Enterprise Interface.

Handles outbound mobile money and ACH disbursements for petty cash float
delivery to custodians' mobile wallets or bank accounts.

Supported backends (mock/stub):
  - M_PESA: Safaricom M-Pesa B2C (Kenya/East Africa)
  - ACH: US Automated Clearing House (NACHA-format)
  - VENMO: Venmo for Business API

Design Rules:
  - All disbursement requests carry an idempotency_key to prevent double-sending.
  - All monetary values use 64-bit integer fixed-point (scaled x10^4).
  - Tenant isolation enforced: every disbursement is tagged with tenant_id.
  - Status polling via `get_disbursement_status(disbursement_id)`.
"""

from __future__ import annotations

import datetime
import hashlib
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class MobileMoneyBackend(str, Enum):
    M_PESA = "mpesa"
    ACH = "ach"
    VENMO = "venmo"
    MOCK = "mock"


class DisbursementStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    REVERSED = "reversed"


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class DisbursementRequest:
    """Request model for a mobile money / ACH disbursement."""
    tenant_id: str
    custodian_id: str
    fund_id: str
    recipient_phone_or_account: str     # Phone (M-Pesa) or bank account (ACH) or email (Venmo)
    amount_scaled: int                  # 64-bit int fixed-point (x10^4)
    currency: str = "USD"
    description: str = "Petty Cash Float Disbursement"
    idempotency_key: Optional[str] = None
    metadata: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.amount_scaled <= 0:
            raise ValueError(
                f"amount_scaled must be positive, got {self.amount_scaled}"
            )
        if len(self.currency) != 3:
            raise ValueError(f"currency must be ISO-4217 3-char code, got '{self.currency}'")
        if not self.recipient_phone_or_account:
            raise ValueError("recipient_phone_or_account cannot be empty")
        if not self.idempotency_key:
            key_material = (
                f"{self.tenant_id}:{self.custodian_id}:{self.fund_id}:"
                f"{self.recipient_phone_or_account}:{self.amount_scaled}"
            )
            self.idempotency_key = hashlib.sha256(key_material.encode()).hexdigest()[:32]


@dataclass
class DisbursementResult:
    """Result of a mobile money / ACH disbursement attempt."""
    disbursement_id: str
    tenant_id: str
    custodian_id: str
    fund_id: str
    recipient_phone_or_account: str
    amount_scaled: int
    currency: str
    status: DisbursementStatus
    backend: MobileMoneyBackend
    idempotency_key: str
    reference_number: str               # External provider reference
    initiated_at: str
    completed_at: Optional[str] = None
    failure_reason: Optional[str] = None
    metadata: Dict[str, str] = field(default_factory=dict)

    @property
    def amount_float(self) -> float:
        return self.amount_scaled / 10_000.0

    def to_dict(self) -> dict:
        return {
            "disbursement_id": self.disbursement_id,
            "tenant_id": self.tenant_id,
            "custodian_id": self.custodian_id,
            "fund_id": self.fund_id,
            "recipient": self.recipient_phone_or_account,
            "amount_scaled": self.amount_scaled,
            "amount_formatted": f"${self.amount_float:.2f}",
            "currency": self.currency,
            "status": self.status.value,
            "backend": self.backend.value,
            "idempotency_key": self.idempotency_key,
            "reference_number": self.reference_number,
            "initiated_at": self.initiated_at,
            "completed_at": self.completed_at,
            "failure_reason": self.failure_reason,
            "metadata": self.metadata,
        }


class MobileMoneyError(Exception):
    """Raised when mobile money disbursement fails."""
    pass


class DuplicateDisbursementError(MobileMoneyError):
    """Raised when idempotency key matches an existing disbursement."""
    def __init__(self, idempotency_key: str, existing: DisbursementResult):
        self.idempotency_key = idempotency_key
        self.existing = existing
        super().__init__(
            f"Disbursement already initiated for idempotency_key={idempotency_key[:8]}..."
        )


# ---------------------------------------------------------------------------
# Mobile Money Adapter — Mock Backend
# ---------------------------------------------------------------------------

class MobileMoneyAdapter:
    """Adapter for mobile money and ACH disbursements.

    Mock backend for local testing — returns completed disbursements instantly.
    Production: inject backend config and API credentials via constructor.
    """

    def __init__(
        self,
        backend: MobileMoneyBackend = MobileMoneyBackend.MOCK,
        simulate_failure: bool = False,
    ):
        self.backend = backend
        self.simulate_failure = simulate_failure
        # idempotency_key -> DisbursementResult
        self._disbursements: Dict[str, DisbursementResult] = {}
        self._by_id: Dict[str, DisbursementResult] = {}

    def disburse(self, request: DisbursementRequest) -> DisbursementResult:
        """Initiate a mobile money / ACH disbursement.

        Idempotent: returns existing result for the same idempotency_key.

        Args:
            request: DisbursementRequest with recipient, amount, and tenant context.

        Returns:
            DisbursementResult with status and provider reference.

        Raises:
            MobileMoneyError: If disbursement fails at the provider level.
        """
        # Idempotency check — return existing without double-sending
        existing = self._disbursements.get(request.idempotency_key)
        if existing is not None:
            return existing

        disbursement_id = f"disb_{uuid.uuid4().hex[:16]}"
        now = datetime.datetime.now(datetime.timezone.utc)
        ref_num = f"REF-{hashlib.md5(disbursement_id.encode()).hexdigest()[:8].upper()}"

        if self.simulate_failure:
            result = DisbursementResult(
                disbursement_id=disbursement_id,
                tenant_id=request.tenant_id,
                custodian_id=request.custodian_id,
                fund_id=request.fund_id,
                recipient_phone_or_account=request.recipient_phone_or_account,
                amount_scaled=request.amount_scaled,
                currency=request.currency,
                status=DisbursementStatus.FAILED,
                backend=self.backend,
                idempotency_key=request.idempotency_key,
                reference_number=ref_num,
                initiated_at=now.isoformat(),
                failure_reason="Simulated provider failure",
                metadata=request.metadata,
            )
        else:
            result = DisbursementResult(
                disbursement_id=disbursement_id,
                tenant_id=request.tenant_id,
                custodian_id=request.custodian_id,
                fund_id=request.fund_id,
                recipient_phone_or_account=request.recipient_phone_or_account,
                amount_scaled=request.amount_scaled,
                currency=request.currency,
                status=DisbursementStatus.COMPLETED,
                backend=self.backend,
                idempotency_key=request.idempotency_key,
                reference_number=ref_num,
                initiated_at=now.isoformat(),
                completed_at=now.isoformat(),
                metadata={
                    **request.metadata,
                    "tenant_id": request.tenant_id,
                    "fund_id": request.fund_id,
                },
            )

        self._disbursements[request.idempotency_key] = result
        self._by_id[disbursement_id] = result
        return result

    def get_disbursement_status(self, disbursement_id: str) -> Optional[DisbursementResult]:
        """Look up status of a disbursement by ID."""
        return self._by_id.get(disbursement_id)

    def count_disbursements(self) -> int:
        """Return total number of unique disbursements (for testing)."""
        return len(self._by_id)
