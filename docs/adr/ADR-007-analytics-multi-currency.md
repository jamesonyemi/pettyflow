# ADR-007: Real-Time Financial Analytics, Fixed-Point Multi-Currency & Reporting Architecture

## Context & Problem Statement
Petty cash floats across global enterprise subsidiaries operate in diverse local currencies (USD, EUR, GBP, JPY, CAD, KES, NGN). Reporting and executive spend analytics require:
1. Exact historical spot rate conversions and revaluations without floating-point precision loss.
2. Ultra-fast real-time spend aggregations (< 150 ms) across multi-million transaction ledgers.
3. Automated streaming PDF and CSV financial statement generation.

## Decision Drivers
- **Micro-Precision Fixed Point**: Rate scale factor \(10^6\) and amount scale factor \(10^4\).
- **Sub-150ms Vector Aggregation**: Single-pass dictionary accumulator across departmental, location, and categorical dimensions.
- **Reporting Determinism**: Exported CSV and PDF summaries directly match ledger balances.

## Considered Options
1. **Float-Based Conversions & Database-Side Dynamic Group-Bys**: Susceptible to float drift and high query latency on large datasets.
2. **Fixed-Point Arithmetic (\(10^6\) rate scale) + Vectorized In-Memory Aggregations + Streaming ReportLab Exporter**: Chosen.

## Decision Outcome
Chosen Option: **Fixed-Point Currency Converter (`CurrencyConverter`) + `SpendAggregator` + `reports_router`**.

### Mathematical Invariants:
- `converted_scaled = (amount_scaled * rate_scaled) // 1_000_000`.
- Arbitrage-free triangular conversion via base currency.
- Aggregation latency < 150 ms across 1,000,000 rows.

## Consequences
- **Positive**: Exact multi-currency consolidation, instant executive dashboards, reliable automated statement exports.
- **Negative**: Requires historical daily spot rate caching.
