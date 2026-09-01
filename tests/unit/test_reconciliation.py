"""Unit and Integration Tests for Week 9: Automated Cash Box & Bank Reconciliation Module."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI

from src.domain.ledger.entry import EntryType
from src.domain.reconciliation.matcher import (
    BankFeedRecord,
    CashCountRecord,
    DenominationBreakdown,
    ReconciliationMatcher,
    ReconciliationStatus,
    SystemFloatRecord,
)
from src.domain.reconciliation.variance_analyzer import (
    VarianceAnalyzer,
    VarianceDisposition,
)
from src.api.rest.reconciliation_router import create_reconciliation_router


# ---------------------------------------------------------------------------
# Test Denomination Breakdown & Cash Count
# ---------------------------------------------------------------------------

class TestDenominationBreakdown:
    def test_denomination_calculation_exact(self):
        # 2 x $100 + 1 x $50 + 3 x $20 + 2 x $10 + 4 x $5 + 5 x $1
        # = 200 + 50 + 60 + 20 + 20 + 5 = $355.00
        # Coins: 4 x 0.25 ($1) + 5 x 0.10 ($0.50) + 2 x 0.05 ($0.10) + 5 x 0.01 ($0.05)
        # = $1.65
        # Total = $356.65 = 3_566_500 scaled
        denom = DenominationBreakdown(
            hundreds=2,
            fifties=1,
            twenties=3,
            tens=2,
            fives=4,
            ones=5,
            quarters=4,
            dimes=5,
            nickels=2,
            pennies=5,
        )
        assert denom.total_scaled == 3_566_500

    def test_cash_count_record_properties(self):
        denom = DenominationBreakdown(hundreds=1)  # $100.00
        count = CashCountRecord(
            count_id="C1",
            tenant_id="T1",
            fund_id="F1",
            custodian_id="CUST1",
            denominations=denom,
        )
        assert count.total_physical_scaled == 1_000_000
        assert count.total_physical_float == 100.0


# ---------------------------------------------------------------------------
# Test Reconciliation Matcher
# ---------------------------------------------------------------------------

class TestReconciliationMatcher:
    def setup_method(self):
        self.matcher = ReconciliationMatcher()

    def _make_count(self, hundreds: int = 5, singles: int = 0) -> CashCountRecord:
        # 5 hundreds = $500.00
        denom = DenominationBreakdown(hundreds=hundreds, ones=singles)
        return CashCountRecord(
            count_id="COUNT-01",
            tenant_id="TENANT-1",
            fund_id="FUND-01",
            custodian_id="CUST-01",
            denominations=denom,
        )

    def _make_float(
        self,
        opening: int = 5_000_000,      # $500.00
        disbursed: int = 1_000_000,    # $100.00
        replenished: int = 1_000_000,  # $100.00
    ) -> SystemFloatRecord:
        return SystemFloatRecord(
            fund_id="FUND-01",
            tenant_id="TENANT-1",
            opening_float_scaled=opening,
            total_disbursed_scaled=disbursed,
            total_replenished_scaled=replenished,
        )

    def test_exact_match_balanced(self):
        # Opening $500, Disbursed $100, Replenished $100 -> Expected $500
        count = self._make_count(hundreds=5)  # $500
        system_float = self._make_float()
        bank_feed = BankFeedRecord(
            feed_id="BF1",
            tenant_id="TENANT-1",
            bank_account_id="B1",
            cleared_replenishments_scaled=1_000_000,
        )

        result = self.matcher.reconcile(count, system_float, bank_feed)
        assert result.status == ReconciliationStatus.BALANCED
        assert result.is_balanced is True
        assert result.is_exact_match is True
        assert result.cash_variance_scaled == 0
        assert result.bank_variance_scaled == 0

    def test_cash_shortage_detected(self):
        # Physical $490 (4 hundreds + 90 singles), Expected $500 -> Short $10
        count = self._make_count(hundreds=4, singles=90)  # $490
        system_float = self._make_float()

        result = self.matcher.reconcile(count, system_float)
        assert result.status == ReconciliationStatus.VARIANCE_SHORT
        assert result.is_balanced is False
        assert result.cash_variance_scaled == -100_000  # -$10.00
        assert result.cash_variance_float == -10.0

    def test_cash_overage_detected(self):
        # Physical $505 (5 hundreds + 5 singles), Expected $500 -> Over $5
        count = self._make_count(hundreds=5, singles=5)  # $505
        system_float = self._make_float()

        result = self.matcher.reconcile(count, system_float)
        assert result.status == ReconciliationStatus.VARIANCE_OVER
        assert result.is_balanced is False
        assert result.cash_variance_scaled == 50_000  # +$5.00
        assert result.cash_variance_float == 5.0

    def test_bank_discrepancy_flags_audit(self):
        # Physical matches system, but bank cleared $800 instead of $1000
        count = self._make_count(hundreds=5)
        system_float = self._make_float()
        bank_feed = BankFeedRecord(
            feed_id="BF1",
            tenant_id="TENANT-1",
            bank_account_id="B1",
            cleared_replenishments_scaled=800_000,  # $80 instead of $100
        )

        result = self.matcher.reconcile(count, system_float, bank_feed)
        assert result.status == ReconciliationStatus.DISCREPANCY_PENDING_AUDIT
        assert result.is_balanced is False
        assert result.bank_variance_scaled == -200_000

    def test_dual_variance_reports_both_sources(self):
        # Both cash shortage (-$10.00) and bank discrepancy (-$20.00)
        count = self._make_count(hundreds=4, singles=90)  # $490 (short $10)
        system_float = self._make_float()
        bank_feed = BankFeedRecord(
            feed_id="BF1",
            tenant_id="TENANT-1",
            bank_account_id="B1",
            cleared_replenishments_scaled=800_000,  # $80 instead of $100 (short $20)
        )

        result = self.matcher.reconcile(count, system_float, bank_feed)
        assert result.status == ReconciliationStatus.DISCREPANCY_PENDING_AUDIT
        assert "Bank statement replenishment variance" in result.details["message"]
        assert "Physical cash is short of system float" in result.details["message"]

    def test_tenant_mismatch_raises_error(self):
        count = self._make_count()
        system_float = SystemFloatRecord(
            fund_id="FUND-01",
            tenant_id="TENANT-DIFFERENT",
            opening_float_scaled=5_000_000,
            total_disbursed_scaled=0,
            total_replenished_scaled=0,
        )
        with pytest.raises(ValueError, match="Tenant isolation violation"):
            self.matcher.reconcile(count, system_float)


# ---------------------------------------------------------------------------
# Test Variance Analyzer & Double-Entry Adjustments
# ---------------------------------------------------------------------------

class TestVarianceAnalyzer:
    def setup_method(self):
        self.analyzer = VarianceAnalyzer(
            auto_adjust_threshold_scaled=50_000,   # $5.00
            manager_threshold_scaled=500_000,      # $50.00
        )
        self.matcher = ReconciliationMatcher()

    def test_zero_variance_auto_adjusted(self):
        count = CashCountRecord(
            count_id="C1",
            tenant_id="T1",
            fund_id="F1",
            custodian_id="CUST1",
            denominations=DenominationBreakdown(hundreds=5),
        )
        sfloat = SystemFloatRecord(
            fund_id="F1",
            tenant_id="T1",
            opening_float_scaled=5_000_000,
            total_disbursed_scaled=0,
            total_replenished_scaled=0,
        )
        rec = self.matcher.reconcile(count, sfloat)
        analysis = self.analyzer.analyze(rec, fund_account_id="ACC_FUND_01")

        assert analysis.disposition == VarianceDisposition.AUTO_ADJUSTED
        assert analysis.is_auto_adjusted is True
        assert analysis.adjustment_batch is None

    def test_minor_shortage_generates_balanced_journal(self):
        # Shortage of $3.00 (30,000 scaled) <= $5.00
        count = CashCountRecord(
            count_id="C1",
            tenant_id="T1",
            fund_id="F1",
            custodian_id="CUST1",
            denominations=DenominationBreakdown(hundreds=4, ones=97),  # $497
        )
        sfloat = SystemFloatRecord(
            fund_id="F1",
            tenant_id="T1",
            opening_float_scaled=5_000_000,
            total_disbursed_scaled=0,
            total_replenished_scaled=0,
        )
        rec = self.matcher.reconcile(count, sfloat)
        analysis = self.analyzer.analyze(rec, fund_account_id="ACC_FUND_01")

        assert analysis.disposition == VarianceDisposition.AUTO_ADJUSTED
        assert analysis.is_auto_adjusted is True
        assert analysis.adjustment_batch is not None

        # Verify strict double-entry invariant on generated batch
        batch = analysis.adjustment_batch
        assert batch.validate_balance() is True
        debits = sum(leg.amount_scaled for leg in batch.legs if leg.entry_type == EntryType.DEBIT)
        credits = sum(leg.amount_scaled for leg in batch.legs if leg.entry_type == EntryType.CREDIT)
        assert debits == credits == 30_000

    def test_minor_overage_generates_balanced_journal(self):
        # Overage of $2.50 (25,000 scaled) <= $5.00
        count = CashCountRecord(
            count_id="C1",
            tenant_id="T1",
            fund_id="F1",
            custodian_id="CUST1",
            denominations=DenominationBreakdown(hundreds=5, quarters=10),  # $502.50
        )
        sfloat = SystemFloatRecord(
            fund_id="F1",
            tenant_id="T1",
            opening_float_scaled=5_000_000,
            total_disbursed_scaled=0,
            total_replenished_scaled=0,
        )
        rec = self.matcher.reconcile(count, sfloat)
        analysis = self.analyzer.analyze(rec, fund_account_id="ACC_FUND_01")

        assert analysis.disposition == VarianceDisposition.AUTO_ADJUSTED
        assert analysis.is_auto_adjusted is True
        assert analysis.adjustment_batch is not None

        batch = analysis.adjustment_batch
        assert batch.validate_balance() is True
        debits = sum(leg.amount_scaled for leg in batch.legs if leg.entry_type == EntryType.DEBIT)
        credits = sum(leg.amount_scaled for leg in batch.legs if leg.entry_type == EntryType.CREDIT)
        assert debits == credits == 25_000

    def test_moderate_variance_requires_manager_approval(self):
        # Shortage of $20.00 (200,000 scaled) -> > $5 and <= $50
        count = CashCountRecord(
            count_id="C1",
            tenant_id="T1",
            fund_id="F1",
            custodian_id="CUST1",
            denominations=DenominationBreakdown(hundreds=4, twenties=4),  # $480
        )
        sfloat = SystemFloatRecord(
            fund_id="F1",
            tenant_id="T1",
            opening_float_scaled=5_000_000,
            total_disbursed_scaled=0,
            total_replenished_scaled=0,
        )
        rec = self.matcher.reconcile(count, sfloat)
        analysis = self.analyzer.analyze(rec, fund_account_id="ACC_FUND_01")

        assert analysis.disposition == VarianceDisposition.PENDING_APPROVAL
        assert analysis.is_auto_adjusted is False
        assert analysis.required_role == "FINANCE_MANAGER"
        assert analysis.adjustment_batch is not None
        assert analysis.adjustment_batch.validate_balance() is True

    def test_major_variance_escalates_to_director(self):
        # Shortage of $150.00 (1,500,000 scaled) -> > $50
        count = CashCountRecord(
            count_id="C1",
            tenant_id="T1",
            fund_id="F1",
            custodian_id="CUST1",
            denominations=DenominationBreakdown(hundreds=3, fifties=1),  # $350
        )
        sfloat = SystemFloatRecord(
            fund_id="F1",
            tenant_id="T1",
            opening_float_scaled=5_000_000,
            total_disbursed_scaled=0,
            total_replenished_scaled=0,
        )
        rec = self.matcher.reconcile(count, sfloat)
        analysis = self.analyzer.analyze(rec, fund_account_id="ACC_FUND_01")

        assert analysis.disposition == VarianceDisposition.ESCALATED_AUDIT
        assert analysis.is_auto_adjusted is False
        assert analysis.requires_escalation is True
        assert analysis.required_role == "FINANCE_DIRECTOR"
        assert analysis.adjustment_batch is None  # Held pending formal investigation


# ---------------------------------------------------------------------------
# Test Reconciliation REST API
# ---------------------------------------------------------------------------

class TestReconciliationAPI:
    def setup_method(self):
        self.app = FastAPI()
        self.app.include_router(create_reconciliation_router())
        self.client = TestClient(self.app)

    def test_daily_closing_endpoint_balanced(self):
        payload = {
            "tenant_id": "T123",
            "fund_id": "F123",
            "custodian_id": "CUST123",
            "fund_account_id": "ACC_PETTY_123",
            "denominations": {
                "hundreds": 5,
                "fifties": 0,
                "twenties": 0,
                "tens": 0,
                "fives": 0,
                "ones": 0,
                "quarters": 0,
                "dimes": 0,
                "nickels": 0,
                "pennies": 0,
                "custom_coins_scaled": 0,
            },
            "opening_float_scaled": 5_000_000,
            "total_disbursed_scaled": 0,
            "total_replenished_scaled": 0,
        }
        res = self.client.post("/api/v1/reconciliation/daily-closing", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["reconciliation"]["is_balanced"] is True
        assert data["analysis"]["disposition"] == "AUTO_ADJUSTED"

    def test_sign_off_flow(self):
        # 1. Close day
        payload = {
            "tenant_id": "T123",
            "fund_id": "F123",
            "custodian_id": "CUST123",
            "fund_account_id": "ACC_PETTY_123",
            "denominations": {"hundreds": 5},
            "opening_float_scaled": 5_000_000,
            "total_disbursed_scaled": 0,
            "total_replenished_scaled": 0,
        }
        close_res = self.client.post("/api/v1/reconciliation/daily-closing", json=payload)
        rec_id = close_res.json()["reconciliation"]["reconciliation_id"]

        # 2. Custodian sign-off
        sign_payload = {
            "tenant_id": "T123",
            "reconciliation_id": rec_id,
            "signer_id": "CUST123",
            "signer_role": "CUSTODIAN",
            "approval_notes": "Physical cash count verified.",
        }
        sign_res = self.client.post("/api/v1/reconciliation/sign-off", json=sign_payload)
        assert sign_res.status_code == 200
        assert sign_res.json()["total_signatures"] == 1

        # 3. Manager sign-off
        mgr_sign = {
            "tenant_id": "T123",
            "reconciliation_id": rec_id,
            "signer_id": "MGR456",
            "signer_role": "FINANCE_MANAGER",
            "approval_notes": "Manager closing approved.",
        }
        mgr_res = self.client.post("/api/v1/reconciliation/sign-off", json=mgr_sign)
        assert mgr_res.status_code == 200
        assert mgr_res.json()["total_signatures"] == 2

    def test_get_history_endpoint(self):
        payload = {
            "tenant_id": "T_HIST",
            "fund_id": "F_HIST",
            "custodian_id": "CUST_HIST",
            "fund_account_id": "ACC_PETTY",
            "denominations": {"hundreds": 2},
            "opening_float_scaled": 2_000_000,
            "total_disbursed_scaled": 0,
            "total_replenished_scaled": 0,
        }
        self.client.post("/api/v1/reconciliation/daily-closing", json=payload)

        hist_res = self.client.get("/api/v1/reconciliation/history?tenant_id=T_HIST")
        assert hist_res.status_code == 200
        assert hist_res.json()["total_records"] >= 1
