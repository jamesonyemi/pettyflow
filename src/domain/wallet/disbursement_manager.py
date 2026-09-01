"""Secure Tokenized Float Disbursement Manager.

Orchestrates petty cash float disbursements across multiple channels
(virtual P-Card, mobile money/ACH) with:
  - Channel routing logic based on custodian preference and amount.
  - Idempotency enforcement across all downstream adapters.
  - Webhook idempotency layer to prevent double-issuance on network retries.
  - Full disbursement audit trail.

All monetary values use 64-bit integer fixed-point (scaled x10^4) per Section 0.
Acceptance Criterion: card creation / disbursement flow completes < 800ms target.
"""

from __future__ import annotations

import datetime
import hashlib
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from src.infrastructure.adapters.card_issuer import (
    CardIssuerAdapter,
    CardIssuerBackend,
    VirtualCardRequest,
    VirtualCardResult,
)
from src.infrastructure.adapters.mobile_money import (
    DisbursementRequest,
    DisbursementResult,
    DisbursementStatus,
    MobileMoneyAdapter,
    MobileMoneyBackend,
)


# ---------------------------------------------------------------------------
# Enumerations & Models
# ---------------------------------------------------------------------------

class DisbursementChannel(str, Enum):
    VIRTUAL_CARD = "virtual_card"
    MOBILE_MONEY = "mobile_money"
    ACH = "ach"


@dataclass
class FloatDisbursementRequest:
    """Domain-level request for a float disbursement."""
    tenant_id: str
    custodian_id: str
    fund_id: str
    amount_scaled: int                  # 64-bit int fixed-point (x10^4)
    channel: DisbursementChannel
    currency: str = "USD"
    recipient_address: str = ""        # Phone/email/account number for mobile/ACH
    cardholder_name: str = ""          # For virtual card issuance
    description: str = "Petty Cash Float"
    idempotency_key: Optional[str] = None
    metadata: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.tenant_id or not str(self.tenant_id).strip():
            raise ValueError("tenant_id cannot be empty")
        if not self.custodian_id or not str(self.custodian_id).strip():
            raise ValueError("custodian_id cannot be empty")
        if not self.fund_id or not str(self.fund_id).strip():
            raise ValueError("fund_id cannot be empty")
        if self.amount_scaled <= 0:
            raise ValueError(f"amount_scaled must be positive, got {self.amount_scaled}")
        if not self.idempotency_key:
            self.idempotency_key = f"auto-{uuid.uuid4().hex}"


@dataclass
class FloatDisbursementResult:
    """Result of a float disbursement through any channel."""
    disbursement_id: str
    tenant_id: str
    custodian_id: str
    fund_id: str
    channel: DisbursementChannel
    amount_scaled: int
    currency: str
    status: str                         # 'completed', 'pending', 'failed'
    idempotency_key: str
    virtual_card: Optional[VirtualCardResult] = None
    mobile_disbursement: Optional[DisbursementResult] = None
    initiated_at: str = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )
    completed_at: Optional[str] = None
    failure_reason: Optional[str] = None

    @property
    def is_successful(self) -> bool:
        return self.status == "completed"

    @property
    def amount_float(self) -> float:
        return self.amount_scaled / 10_000.0

    def to_dict(self) -> dict:
        return {
            "disbursement_id": self.disbursement_id,
            "tenant_id": self.tenant_id,
            "custodian_id": self.custodian_id,
            "fund_id": self.fund_id,
            "channel": self.channel.value,
            "amount_scaled": self.amount_scaled,
            "amount_formatted": f"${self.amount_float:.2f}",
            "currency": self.currency,
            "status": self.status,
            "idempotency_key": self.idempotency_key,
            "virtual_card": self.virtual_card.to_dict() if self.virtual_card else None,
            "mobile_disbursement": self.mobile_disbursement.to_dict() if self.mobile_disbursement else None,
            "initiated_at": self.initiated_at,
            "completed_at": self.completed_at,
            "failure_reason": self.failure_reason,
        }


# ---------------------------------------------------------------------------
# Disbursement Manager
# ---------------------------------------------------------------------------

class DisbursementManager:
    """Orchestrates secure float disbursements across virtual card and mobile channels.

    Idempotency Layer:
        All disbursement requests are deduplicated by idempotency_key.
        A second call with the same key returns the original result without
        re-initiating any downstream payment — preventing double-issuance
        on network retries or webhook replay attacks.
    """

    def __init__(
        self,
        card_issuer: Optional[CardIssuerAdapter] = None,
        mobile_adapter: Optional[MobileMoneyAdapter] = None,
        idempotency_ttl_seconds: int = 86_400,
    ):
        self._card_issuer = card_issuer or CardIssuerAdapter(CardIssuerBackend.MOCK)
        self._mobile_adapter = mobile_adapter or MobileMoneyAdapter(MobileMoneyBackend.MOCK)
        if idempotency_ttl_seconds <= 0:
            raise ValueError("idempotency_ttl_seconds must be positive")
        self._idempotency_ttl_seconds = idempotency_ttl_seconds
        # Idempotency store: idempotency_key -> (FloatDisbursementResult, expires_at)
        self._results: Dict[str, tuple[FloatDisbursementResult, datetime.datetime]] = {}
        self._audit_trail: List[FloatDisbursementResult] = []

    def _prune_expired_idempotency_keys(self) -> None:
        now = datetime.datetime.now(datetime.timezone.utc)
        expired_keys = [
            key for key, (_, expiry) in self._results.items() if expiry <= now
        ]
        for key in expired_keys:
            self._results.pop(key, None)

    def _record_idempotent_result(self, request: FloatDisbursementRequest, result: FloatDisbursementResult) -> None:
        expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
            seconds=self._idempotency_ttl_seconds
        )
        self._results[request.idempotency_key] = (result, expires_at)
        self._audit_trail.append(result)

    def disburse_float(
        self, request: FloatDisbursementRequest
    ) -> FloatDisbursementResult:
        """Route and execute a float disbursement via the appropriate channel.

        Idempotent: returns cached result for duplicate idempotency_key.

        Args:
            request: FloatDisbursementRequest specifying channel, amount, and tenant context.

        Returns:
            FloatDisbursementResult with channel-specific payload and status.
        """
        if not isinstance(request, FloatDisbursementRequest):
            raise TypeError("request must be a FloatDisbursementRequest")

        self._prune_expired_idempotency_keys()
        cached = self._results.get(request.idempotency_key)
        if cached is not None:
            return cached[0]

        disbursement_id = f"fdisb_{uuid.uuid4().hex[:16]}"
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()

        if request.channel == DisbursementChannel.VIRTUAL_CARD:
            result = self._disburse_via_card(request, disbursement_id, now_str)
        elif request.channel in (DisbursementChannel.MOBILE_MONEY, DisbursementChannel.ACH):
            result = self._disburse_via_mobile(request, disbursement_id, now_str)
        else:
            raise ValueError(f"Unknown disbursement channel: {request.channel}")

        self._record_idempotent_result(request, result)
        return result

    def _disburse_via_card(
        self,
        request: FloatDisbursementRequest,
        disbursement_id: str,
        now_str: str,
    ) -> FloatDisbursementResult:
        """Issue a virtual P-Card for float disbursement."""
        card_request = VirtualCardRequest(
            tenant_id=request.tenant_id,
            custodian_id=request.custodian_id,
            fund_id=request.fund_id,
            spending_limit_scaled=request.amount_scaled,
            currency=request.currency,
            cardholder_name=request.cardholder_name,
            idempotency_key=request.idempotency_key,
            metadata=request.metadata,
        )
        card_result = self._card_issuer.create_virtual_card(card_request)

        return FloatDisbursementResult(
            disbursement_id=disbursement_id,
            tenant_id=request.tenant_id,
            custodian_id=request.custodian_id,
            fund_id=request.fund_id,
            channel=DisbursementChannel.VIRTUAL_CARD,
            amount_scaled=request.amount_scaled,
            currency=request.currency,
            status="completed",
            idempotency_key=request.idempotency_key,
            virtual_card=card_result,
            initiated_at=now_str,
            completed_at=now_str,
        )

    def _disburse_via_mobile(
        self,
        request: FloatDisbursementRequest,
        disbursement_id: str,
        now_str: str,
    ) -> FloatDisbursementResult:
        """Initiate mobile money / ACH disbursement."""
        mobile_request = DisbursementRequest(
            tenant_id=request.tenant_id,
            custodian_id=request.custodian_id,
            fund_id=request.fund_id,
            recipient_phone_or_account=request.recipient_address,
            amount_scaled=request.amount_scaled,
            currency=request.currency,
            description=request.description,
            idempotency_key=request.idempotency_key,
            metadata=request.metadata,
        )
        mobile_result = self._mobile_adapter.disburse(mobile_request)

        return FloatDisbursementResult(
            disbursement_id=disbursement_id,
            tenant_id=request.tenant_id,
            custodian_id=request.custodian_id,
            fund_id=request.fund_id,
            channel=request.channel,
            amount_scaled=request.amount_scaled,
            currency=request.currency,
            status=mobile_result.status.value,
            idempotency_key=request.idempotency_key,
            mobile_disbursement=mobile_result,
            initiated_at=now_str,
            completed_at=mobile_result.completed_at,
            failure_reason=mobile_result.failure_reason,
        )

    def get_audit_trail(self, tenant_id: str) -> List[FloatDisbursementResult]:
        """Return all disbursement audit records for a tenant."""
        return [r for r in self._audit_trail if r.tenant_id == tenant_id]

    def count_disbursements(self) -> int:
        """Total unique disbursements (for testing)."""
        return len(self._results)
