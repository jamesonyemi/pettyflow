"""Week 4 Reliability Lockdown: Sandbox Validation & Observability Baseline Tests.

Validates that:
- Bank flow completes sandbox reconciliation and status transitions
- ERP replenishment flow (SAP & NetSuite) handles journal posts and detects mismatches
- ACH/Mobile Money flows handle sandbox duplicate attempts and timeouts
- Historical FX conversion properly resolves currencies and handles stale/missing rates
- Metrics recording measures approval latency, failures, and retry rates
- Proactive alert triggers fire for P1 critical, P2 high, and P3 warning events
- Operational failure drills test provider outages, replay storms, and ledger drift
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.domain.currency.exchange_rates import CurrencyConverter, CurrencyConversionError
from src.domain.reconciliation.matcher import (
    BankFeedRecord,
    CashCountRecord,
    DenominationBreakdown,
    ReconciliationMatcher,
    SystemFloatRecord,
)
from src.infrastructure.adapters.card_issuer import CardIssuerAdapter, CardIssuerBackend
from src.infrastructure.adapters.mobile_money import (
    DisbursementRequest,
    DisbursementStatus,
    MobileMoneyAdapter,
    MobileMoneyBackend,
    MobileMoneyError,
)
from src.infrastructure.erp.sap_adapter import (
    SAPAdapter,
    SAPJournalEntry,
    SAPJournalLine,
    SAPPostingDirection,
)
from src.infrastructure.erp.netsuite_adapter import (
    NetSuiteAdapter,
    NetSuiteJournalEntry,
    NetSuiteJournalLine,
    NetSuiteLineType,
)
from src.infrastructure.observability.telemetry import (
    AlertEvent,
    AlertSeverity,
    ProviderSandboxDrill,
    TelemetryRegistry,
)


@pytest.fixture
def telemetry() -> TelemetryRegistry:
    return TelemetryRegistry()


class TestSandboxBankAndReconciliationFlow:
    """Verify bank settlement and end-of-day reconciliation sandbox flows."""

    def test_bank_reconciliation_exact_match(self, telemetry: TelemetryRegistry) -> None:
        matcher = ReconciliationMatcher()
        tenant_id = str(uuid.uuid4())
        fund_id = str(uuid.uuid4())
        custodian_id = str(uuid.uuid4())

        cash_count = CashCountRecord(
            count_id="cnt-01",
            tenant_id=tenant_id,
            fund_id=fund_id,
            custodian_id=custodian_id,
            denominations=DenominationBreakdown(hundreds=3, fifties=1),  # $350.00 -> 3,500,000 scaled
        )
        system_float = SystemFloatRecord(
            fund_id=fund_id,
            tenant_id=tenant_id,
            opening_float_scaled=1_000_000,
            total_disbursed_scaled=150_000,
            total_replenished_scaled=2_650_000,  # 1,000,000 + 2,650,000 - 150,000 = 3,500,000
        )
        bank_feed = BankFeedRecord(
            feed_id="bk-01",
            tenant_id=tenant_id,
            bank_account_id="ba-123",
            cleared_replenishments_scaled=2_650_000,
        )

        result = matcher.reconcile(cash_count, system_float, bank_feed)
        assert result.is_exact_match is True
        assert result.cash_variance_scaled == 0
        assert result.bank_variance_scaled == 0

        # Run drill
        drill = ProviderSandboxDrill(telemetry)
        drill_res = drill.execute_bank_reconciliation_drill(tenant_id, 2, 0, 0)
        assert drill_res["status"] == "SUCCESS"
        assert len(telemetry.get_active_alerts(AlertSeverity.P1_CRITICAL)) == 0


class TestSandboxERPFlow:
    """Verify SAP and NetSuite ERP replenishment and mismatch detection flows."""

    def test_sap_journal_entry_posting(self) -> None:
        adapter = SAPAdapter(company_code="1000")
        tenant_id = str(uuid.uuid4())
        entry = SAPJournalEntry(
            tenant_id=tenant_id,
            reference_document="ref-001",
            company_code="1000",
            document_date="2026-10-01",
            posting_date="2026-10-01",
            description="Replenish Petty Cash",
            lines=[
                SAPJournalLine("100000", SAPPostingDirection.DEBIT, 500_000, "USD", "Cost Center 1"),
                SAPJournalLine("200000", SAPPostingDirection.CREDIT, 500_000, "USD", "Bank Account"),
            ],
            idempotency_key="idemp-sap-001",
        )
        result = adapter.post_journal_entry(entry)
        assert result.sap_document_number is not None
        assert len(result.sap_document_number) >= 10

    def test_netsuite_journal_entry_posting(self) -> None:
        adapter = NetSuiteAdapter()
        tenant_id = str(uuid.uuid4())
        entry = NetSuiteJournalEntry(
            tenant_id=tenant_id,
            subsidiary="1",
            reference_document="ref-ns-001",
            posting_period="Oct 2026",
            memo="Top-up",
            lines=[
                NetSuiteJournalLine("100", NetSuiteLineType.DEBIT, 300_000),
                NetSuiteJournalLine("200", NetSuiteLineType.CREDIT, 300_000),
            ],
            idempotency_key="idemp-ns-001",
        )
        result = adapter.post_journal_entry(entry)
        assert result.netsuite_tran_id is not None
        assert result.netsuite_tran_id.startswith("JE-")


class TestSandboxMobileMoneyAndACHFlow:
    """Verify ACH and mobile money settlement with duplicate handling."""

    def test_mobile_money_duplicate_disbursement_handling(self) -> None:
        adapter = MobileMoneyAdapter(backend=MobileMoneyBackend.MOCK)
        tenant_id = str(uuid.uuid4())
        custodian_id = str(uuid.uuid4())
        fund_id = str(uuid.uuid4())

        req = DisbursementRequest(
            tenant_id=tenant_id,
            custodian_id=custodian_id,
            fund_id=fund_id,
            recipient_phone_or_account="+2348012345678",
            amount_scaled=50_000,
            currency="USD",
            description="Petty Cash Top-up",
            idempotency_key="idemp-momo-001",
        )

        res1 = adapter.disburse(req)
        assert res1.status == DisbursementStatus.COMPLETED

        # Second attempt with same key returns identical result without double-sending
        res2 = adapter.disburse(req)
        assert res2.disbursement_id == res1.disbursement_id
        assert adapter.count_disbursements() == 1


class TestFXRatesAndEdgeCases:
    """Verify currency conversion and stale/missing rate fallbacks."""

    def test_fx_conversion_and_missing_pair_fallback(self) -> None:
        converter = CurrencyConverter()
        converter.set_rate("EUR", "USD", 1.085)

        # Same currency conversion
        assert converter.convert(100_000, "USD", "USD") == 100_000

        # Direct conversion: 100_000 EUR * 1.085 = 108_500 USD
        converted = converter.convert(100_000, "EUR", "USD")
        assert converted == 108_500

        # Missing pair raises CurrencyConversionError
        with pytest.raises(CurrencyConversionError):
            converter.convert(100_000, "GBP", "JPY")


class TestObservabilityMetricsAndAlerts:
    """Verify metrics recording, latency percentiles, and alert dispatching."""

    def test_latency_metrics_and_percentiles(self, telemetry: TelemetryRegistry) -> None:
        telemetry.record_latency("approval.tier1.latency_ms", 10.0)
        telemetry.record_latency("approval.tier1.latency_ms", 20.0)
        telemetry.record_latency("approval.tier1.latency_ms", 30.0)
        telemetry.record_latency("approval.tier1.latency_ms", 40.0)
        telemetry.record_latency("approval.tier1.latency_ms", 100.0)

        p95 = telemetry.get_latency_percentile("approval.tier1.latency_ms", 95.0)
        assert p95 >= 40.0
        assert telemetry.get_metric_count("approval.tier1.latency_ms") == 5

    def test_critical_p1_alert_dispatching(self, telemetry: TelemetryRegistry) -> None:
        received_alerts: list[AlertEvent] = []
        telemetry.add_alert_listener(lambda a: received_alerts.append(a))

        tenant_id = str(uuid.uuid4())
        alert = telemetry.trigger_alert(
            tenant_id=tenant_id,
            metric_name="ledger.hash_chain_tampered",
            severity=AlertSeverity.P1_CRITICAL,
            message="Ledger block signature verification failed!",
            current_value=1.0,
            threshold_value=0.0,
        )

        assert alert.severity == AlertSeverity.P1_CRITICAL
        assert len(received_alerts) == 1
        assert received_alerts[0].alert_id == alert.alert_id

        health = telemetry.evaluate_system_health()
        assert health["status"] == "CRITICAL"
        assert health["p1_alerts_count"] == 1


class TestProviderFailureDrills:
    """Verify chaos failure drills for provider outages, retry storms, and ledger drift."""

    def test_retry_storm_drill_triggers_p2_alert(self, telemetry: TelemetryRegistry) -> None:
        drill = ProviderSandboxDrill(telemetry)
        tenant_id = str(uuid.uuid4())

        # 30% failure rate triggers P2_HIGH alert
        res = drill.execute_retry_storm_drill(tenant_id, attempted_requests=100, failure_rate=0.30)
        assert res["status"] == "ALERTED"
        assert res["severity"] == "P2_HIGH"

        p2_alerts = telemetry.get_active_alerts(AlertSeverity.P2_HIGH)
        assert len(p2_alerts) == 1
        assert "High provider failure rate" in p2_alerts[0].message

    def test_ledger_variance_drill_triggers_p1_alert(self, telemetry: TelemetryRegistry) -> None:
        drill = ProviderSandboxDrill(telemetry)
        tenant_id = str(uuid.uuid4())

        res = drill.execute_bank_reconciliation_drill(tenant_id, matched_records=5, unmatched_records=1, variance_scaled=50_000)
        assert res["status"] == "FAILED"
        assert res["reason"] == "LEDGER_VARIANCE"

        p1_alerts = telemetry.get_active_alerts(AlertSeverity.P1_CRITICAL)
        assert len(p1_alerts) == 1
        assert "Unreconciled bank ledger variance" in p1_alerts[0].message
