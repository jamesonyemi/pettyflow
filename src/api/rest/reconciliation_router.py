"""Daily Closing & 3-Way Reconciliation REST API Router."""

from __future__ import annotations

import datetime
import uuid
from typing import Dict, List, Optional
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from src.domain.reconciliation.matcher import (
    BankFeedRecord,
    CashCountRecord,
    DenominationBreakdown,
    ReconciliationMatcher,
    ReconciliationResult,
    SystemFloatRecord,
)
from src.domain.reconciliation.variance_analyzer import (
    VarianceAnalyzer,
    VarianceAnalysisResult,
)


# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------

class DenominationsSchema(BaseModel):
    hundreds: int = Field(default=0, ge=0)
    fifties: int = Field(default=0, ge=0)
    twenties: int = Field(default=0, ge=0)
    tens: int = Field(default=0, ge=0)
    fives: int = Field(default=0, ge=0)
    ones: int = Field(default=0, ge=0)
    quarters: int = Field(default=0, ge=0)
    dimes: int = Field(default=0, ge=0)
    nickels: int = Field(default=0, ge=0)
    pennies: int = Field(default=0, ge=0)
    custom_coins_scaled: int = Field(default=0, ge=0)


class DailyClosingRequest(BaseModel):
    tenant_id: str
    fund_id: str
    custodian_id: str
    fund_account_id: str = "ACC_FUND_001"
    denominations: DenominationsSchema
    opening_float_scaled: int = Field(..., ge=0)
    total_disbursed_scaled: int = Field(..., ge=0)
    total_replenished_scaled: int = Field(..., ge=0)
    bank_cleared_replenishments_scaled: Optional[int] = None
    notes: Optional[str] = None


class SignOffRequest(BaseModel):
    tenant_id: str
    reconciliation_id: str
    signer_id: str
    signer_role: str                   # e.g. "CUSTODIAN", "FINANCE_MANAGER", "FINANCE_DIRECTOR"
    approval_notes: Optional[str] = None


class VarianceAdjustmentRequest(BaseModel):
    tenant_id: str
    reconciliation_id: str
    fund_account_id: str
    authorized_by: str
    authorization_role: str


# ---------------------------------------------------------------------------
# Router Factory
# ---------------------------------------------------------------------------

def create_reconciliation_router(
    matcher: Optional[ReconciliationMatcher] = None,
    analyzer: Optional[VarianceAnalyzer] = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/reconciliation", tags=["Reconciliation"])
    
    _matcher = matcher or ReconciliationMatcher()
    _analyzer = analyzer or VarianceAnalyzer()

    # In-memory stores for audit records
    _history: Dict[str, dict] = {}
    _signoffs: Dict[str, List[dict]] = {}

    @router.post("/daily-closing", status_code=status.HTTP_200_OK)
    def submit_daily_closing(payload: DailyClosingRequest):
        denom = DenominationBreakdown(
            hundreds=payload.denominations.hundreds,
            fifties=payload.denominations.fifties,
            twenties=payload.denominations.twenties,
            tens=payload.denominations.tens,
            fives=payload.denominations.fives,
            ones=payload.denominations.ones,
            quarters=payload.denominations.quarters,
            dimes=payload.denominations.dimes,
            nickels=payload.denominations.nickels,
            pennies=payload.denominations.pennies,
            custom_coins_scaled=payload.denominations.custom_coins_scaled,
        )

        cash_count = CashCountRecord(
            count_id=f"COUNT-{uuid.uuid4().hex[:8].upper()}",
            tenant_id=payload.tenant_id,
            fund_id=payload.fund_id,
            custodian_id=payload.custodian_id,
            denominations=denom,
            notes=payload.notes,
        )

        system_float = SystemFloatRecord(
            fund_id=payload.fund_id,
            tenant_id=payload.tenant_id,
            opening_float_scaled=payload.opening_float_scaled,
            total_disbursed_scaled=payload.total_disbursed_scaled,
            total_replenished_scaled=payload.total_replenished_scaled,
        )

        bank_feed = None
        if payload.bank_cleared_replenishments_scaled is not None:
            bank_feed = BankFeedRecord(
                feed_id=f"FEED-{uuid.uuid4().hex[:8].upper()}",
                tenant_id=payload.tenant_id,
                bank_account_id="BANK_MAIN",
                cleared_replenishments_scaled=payload.bank_cleared_replenishments_scaled,
            )

        try:
            rec_result = _matcher.reconcile(cash_count, system_float, bank_feed)
            analysis = _analyzer.analyze(rec_result, payload.fund_account_id)
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

        response_data = {
            "reconciliation": rec_result.to_dict(),
            "analysis": analysis.to_dict(),
            "has_adjustment": analysis.adjustment_batch is not None,
        }

        _history[rec_result.reconciliation_id] = response_data
        _signoffs[rec_result.reconciliation_id] = []
        return response_data

    @router.post("/sign-off", status_code=status.HTTP_200_OK)
    def sign_off_reconciliation(payload: SignOffRequest):
        if payload.reconciliation_id not in _history:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Reconciliation ID '{payload.reconciliation_id}' not found.",
            )

        record = {
            "signer_id": payload.signer_id,
            "signer_role": payload.signer_role,
            "signed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "approval_notes": payload.approval_notes,
        }
        _signoffs[payload.reconciliation_id].append(record)

        return {
            "reconciliation_id": payload.reconciliation_id,
            "status": "SIGNED",
            "total_signatures": len(_signoffs[payload.reconciliation_id]),
            "signatures": _signoffs[payload.reconciliation_id],
        }

    @router.get("/history", status_code=status.HTTP_200_OK)
    def get_history(tenant_id: str):
        records = [
            r for r in _history.values()
            if r["reconciliation"]["tenant_id"] == tenant_id
        ]
        return {"tenant_id": tenant_id, "total_records": len(records), "records": records}

    return router
