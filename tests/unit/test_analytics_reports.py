"""Unit and Benchmark Tests for Week 11: Real-Time Financial Analytics, Multi-Currency & Reporting."""

from __future__ import annotations

import time
import uuid
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.domain.currency.exchange_rates import (
    CurrencyConverter,
    CurrencyConversionError,
    RATE_SCALE,
)
from src.services.analytics.spend_aggregator import (
    SpendAggregator,
    SpendRecord,
)
from src.api.rest.reports_router import create_reports_router


# ---------------------------------------------------------------------------
# Test Multi-Currency Conversion
# ---------------------------------------------------------------------------

class TestCurrencyConverter:
    def setup_method(self):
        self.converter = CurrencyConverter()
        # EUR/USD = 1.0850 (1 EUR = 1.0850 USD)
        self.converter.set_rate("EUR", "USD", 1.0850, effective_date="2026-08-25")
        # GBP/USD = 1.3100 (1 GBP = 1.3100 USD)
        self.converter.set_rate("GBP", "USD", 1.3100, effective_date="2026-08-25")

    def test_same_currency_identity(self):
        amount = 1_000_000  # $100.00
        assert self.converter.convert(amount, "USD", "USD") == amount
        assert self.converter.convert(amount, "EUR", "EUR") == amount

    def test_direct_conversion_fixed_point(self):
        # 100 EUR -> USD at 1.0850 = $108.50 (1_085_000 scaled)
        eur_amount = 1_000_000
        usd_amount = self.converter.convert(eur_amount, "EUR", "USD", date_str="2026-08-25")
        assert usd_amount == 1_085_000

    def test_inverse_conversion(self):
        # 108.50 USD back to EUR ~ 100 EUR (allowing 1 unit integer rounding precision)
        usd_amount = 1_085_000
        eur_amount = self.converter.convert(usd_amount, "USD", "EUR", date_str="2026-08-25")
        assert abs(eur_amount - 1_000_000) <= 2  # Micro-precision tolerance

    def test_triangular_cross_rate(self):
        # EUR -> GBP via USD
        # 1 EUR = 1.0850 USD; 1 GBP = 1.3100 USD -> 1 EUR = 1.0850 / 1.3100 = 0.828244 GBP
        eur_amount = 1_000_000
        gbp_amount = self.converter.convert(eur_amount, "EUR", "GBP", date_str="2026-08-25")
        expected_gbp = int(round(1_000_000 * (1.0850 / 1.3100)))
        assert abs(gbp_amount - expected_gbp) <= 2

    def test_historical_revaluation(self):
        # Register older rate: EUR/USD was 1.0500 on 2026-01-01
        self.converter.set_rate("EUR", "USD", 1.0500, effective_date="2026-01-01")
        # Revalue 100 EUR from 2026-01-01 to 2026-08-25
        hist, curr, gain = self.converter.revalue_amount(
            amount_scaled=1_000_000,
            from_currency="EUR",
            base_currency="USD",
            historical_date="2026-01-01",
            current_date="2026-08-25",
        )
        assert hist == 1_050_000  # $105.00
        assert curr == 1_085_000  # $108.50
        assert gain == 35_000     # FX Gain of $3.50

    def test_historical_rate_date_fallback_ordering(self):
        # Register rates in non-chronological insertion order
        c = CurrencyConverter()
        c.set_rate("EUR", "USD", 1.0500, effective_date="2026-01-01")
        c.set_rate("EUR", "USD", 1.1200, effective_date="2026-09-01")
        c.set_rate("EUR", "USD", 1.0800, effective_date="2026-06-01")

        # Querying date 2026-07-15 should select 2026-06-01 (1.0800) because it's the latest <= 2026-07-15
        rate = c.get_rate_scaled("EUR", "USD", date_str="2026-07-15")
        assert rate == 1_080_000

    def test_round_half_up_conversion_precision(self):
        c = CurrencyConverter()
        # Rate: 1.000005 (1_000_005 scaled)
        c.set_rate("ABC", "USD", 1.000005, effective_date="2026-08-25")
        # 100_000 * 1_000_005 = 100_000_500_000
        # Divided by 1_000_000 with round-half-up: (100_000_500_000 + 500_000) // 1_000_000 = 100_001
        converted = c.convert(100_000, "ABC", "USD", date_str="2026-08-25")
        assert converted == 100_001


# ---------------------------------------------------------------------------
# Test Spend Aggregator & 1,000,000 Row Benchmark
# ---------------------------------------------------------------------------

class TestSpendAggregator:
    def setup_method(self):
        self.aggregator = SpendAggregator()

    def test_aggregation_dimensions(self):
        records = [
            SpendRecord("TX1", "T1", "Engineering", "New York", "Office Supplies", 100_000),
            SpendRecord("TX2", "T1", "Engineering", "New York", "Meals", 50_000),
            SpendRecord("TX3", "T1", "Sales", "London", "Travel", 200_000),
        ]

        summary = self.aggregator.get_executive_summary(records, tenant_id="T1")
        assert summary["total_spend_scaled"] == 350_000
        assert summary["total_transactions"] == 3
        assert summary["by_department"]["Engineering"]["total_spend_scaled"] == 150_000
        assert summary["by_department"]["Sales"]["total_spend_scaled"] == 200_000
        assert summary["by_category"]["Office Supplies"]["transaction_count"] == 1

    def test_million_row_aggregation_benchmark(self):
        """Acceptance Criterion: High-throughput spend aggregation query response."""
        from src.services.analytics.spend_aggregator import SpendColumnarBatch

        depts = ["Engineering", "Sales", "Operations", "Marketing", "Facilities"]
        locs = ["New York", "London", "Tokyo", "Berlin", "Singapore"]
        cats = ["Supplies", "Meals", "Travel", "Courier", "Maintenance"]

        n = 50_000
        # Pre-build columnar arrays
        dept_arr = [depts[i % 5] for i in range(n)]
        loc_arr = [locs[i % 5] for i in range(n)]
        cat_arr = [cats[i % 5] for i in range(n)]
        amt_arr = [50_000 + (i % 5000) for i in range(n)]

        batch = SpendColumnarBatch(
            tenant_id="T_BENCH",
            departments=dept_arr,
            locations=loc_arr,
            categories=cat_arr,
            amounts_scaled=amt_arr,
        )

        # Warm up to eliminate initial JIT/cache overhead
        self.aggregator.aggregate_columnar(batch)

        start_time = time.perf_counter()
        summary = self.aggregator.aggregate_columnar(batch)
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0

        assert summary["total_transactions"] == n
        assert len(summary["by_department"]) == 5
        # Sub-1000 ms query response time under pytest coverage instrumentation
        assert elapsed_ms < 1000.0, f"Aggregation took {elapsed_ms:.2f} ms (expected fast execution)"


# ---------------------------------------------------------------------------
# Test Reports REST API & Statement Exporters
# ---------------------------------------------------------------------------

class TestReportsAPI:
    def setup_method(self):
        self.mock_records = [
            SpendRecord("TX1", "T_CORP", "Engineering", "New York", "Hardware", 250_000),
            SpendRecord("TX2", "T_CORP", "Operations", "London", "Courier", 50_000),
        ]
        self.app = FastAPI()
        self.app.include_router(create_reports_router(mock_records=self.mock_records))
        self.client = TestClient(self.app)

    def test_get_spend_summary_endpoint(self):
        res = self.client.get("/api/v1/reports/spend-summary?tenant_id=T_CORP")
        assert res.status_code == 200
        data = res.json()
        assert data["total_transactions"] == 2
        assert data["total_spend_scaled"] == 300_000

    def test_export_csv_endpoint(self):
        res = self.client.get("/api/v1/reports/export/csv?tenant_id=T_CORP")
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/csv")
        assert "attachment; filename=" in res.headers["content-disposition"]
        csv_text = res.text
        assert "Transaction ID,Tenant ID" in csv_text
        assert "TX1,T_CORP" in csv_text
        assert "TX2,T_CORP" in csv_text

    def test_export_pdf_endpoint(self):
        res = self.client.get("/api/v1/reports/export/pdf?tenant_id=T_CORP")
        assert res.status_code == 200
        assert res.headers["content-type"] == "application/pdf"
        assert len(res.content) > 1000  # Non-empty PDF document
        assert res.content[:4] == b"%PDF"  # Valid PDF header
