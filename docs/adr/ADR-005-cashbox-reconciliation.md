# ADR-005: 3-Way Automated Cash Box & Bank Statement Reconciliation Architecture

## Context & Problem Statement
Petty cash funds require daily or shift-level physical cash counts that must be matched against the immutable ledger's system float and clearing bank statements. Discrepancies (cash shortages and overages) must be automatically detected down to the exact cent, evaluated against corporate policy risk thresholds, and adjusted using strictly balanced double-entry journal postings.

## Decision Drivers
- **Financial Precision**: 64-bit integer fixed-point arithmetic (`scaled x10^4`) to eliminate all floating-point rounding errors.
- **Double-Entry Invariant**: Every variance adjustment batch MUST satisfy \(\sum \text{Debits} \equiv \sum \text{Credits}\).
- **Multi-Tier Policy Control**: Minor variances (\(\le \$5.00\)) auto-adjust, moderate variances (\(\$5.00 - \$50.00\)) require Manager sign-off, and major variances (\(> \$50.00\)) escalate to the Finance Director and trigger fraud audits.
- **Zero-Trust Multi-Tenancy**: Explicit `tenant_id` verification preventing cross-tenant ledger tampering.

## Considered Options
1. **Manual Spreadsheet Reconciliation**: Error-prone, lacks audit trails and cryptographic double-entry integrity.
2. **2-Way Match (Cash vs System Only)**: Misses bank transfer clearing latency and unrecorded ACH deposits.
3. **3-Way Reconciliation Engine (Cash Count vs System Float vs Bank Feeds) with Automated Double-Entry Adjustments**: Chosen.

## Decision Outcome
Chosen Option: **3-Way Reconciliation Engine** (`ReconciliationMatcher` + `VarianceAnalyzer` + `reconciliation_router`).

### Structural Invariants:
1. Denomination breakdown calculates physical cash deterministically.
2. Cash shortages debit `ACC_CASH_OVER_SHORT` (Expense) and credit Fund Asset account.
3. Cash overages debit Fund Asset account and credit `ACC_CASH_OVER_SHORT` (Revenue).
4. Dual-authorization sign-off endpoints record timestamps and roles for SOC2 compliance.

## Consequences
- **Positive**: Complete visibility into daily cash variances, zero unrecorded leakage, automated journal posting.
- **Negative**: Requires custodians to input denomination counts during closing.
