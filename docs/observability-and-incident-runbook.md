# PettyFlow Observability Baseline & Incident Response Runbook

## 1. Executive Summary

This runbook establishes production observability standards, telemetry metrics, alert severity matrices, notification dispatching, and failure drill playbooks across PettyFlow core financial and payment subsystems.

---

## 2. Telemetry Metrics Matrix

| Metric Name | Type | Description | Target SLA / Baseline |
|---|---|---|---|
| `approval.tier1.latency_ms` | Histogram / Latency | Tier 1 workflow policy evaluation latency | $p95 < 1.5\text{ ms}$ |
| `disbursement.requests.total` | Counter | Total outgoing float disbursement requests | Monitored |
| `disbursement.requests.failed` | Counter | Failed outgoing disbursement requests | Failure rate $< 1.0\%$ |
| `ledger.unreconciled_variance` | Gauge | Total unreconciled variance in bank 3-way match | Strict $0.00$ |
| `provider.failure_rate` | Gauge | External payment/ERP provider failure ratio | Threshold $< 20.0\%$ |
| `fx.stale_rate_hours` | Gauge | Time elapsed since last ECB/Fixer FX spot rate refresh | $< 24\text{ hours}$ |

---

## 3. Alert Severity Matrix & Escalation Paths

| Severity | Definition & Trigger Criteria | On-Call Route | SLA Response Time |
|---|---|---|---|
| **P1 Critical** | - Ledger cryptographic signature violation<br>- Unreconciled bank/cash variance $> 0$<br>- Duplicate debit detected | Payments Lead & SRE Lead (PagerDuty high-urgency) | $< 5\text{ minutes}$ |
| **P2 High** | - Provider failure rate $> 20\%$ in 5-minute window<br>- Provider timeout/outage detected<br>- Retry storm threshold reached | Integrations Engineer on-call (Slack `#alerts-payments` + SMS) | $< 15\text{ minutes}$ |
| **P3 Warning** | - Stale FX rates $> 24\text{h}$<br>- Low petty cash fund balance ($< 15\%$ capacity)<br>- Minor queue backlog | Operations Queue (Ticket + Slack `#alerts-ops`) | Next business day |

---

## 4. Incident Response & Failure Drills

### 4.1 Upstream Provider Outage Drill
- **Trigger**: Payment provider returns HTTP 502/503/504 or network timeout.
- **Automated Mitigation**:
  1. Circuit breaker engages after 5 consecutive failures.
  2. In-flight requests are placed in `PENDING_RETRY` with exponential backoff.
  3. P2 alert dispatched to Integrations team.
  4. Idempotency reservation ensures retried requests never double-charge.

### 4.2 Cryptographic Ledger Drift Drill
- **Trigger**: `CryptographicLedgerChain.verify_integrity()` returns `False` or bank feed 3-way match indicates variance.
- **Immediate Playbook**:
  1. Automated P1 page dispatched immediately.
  2. Automatic freeze placed on outgoing disbursements for affected tenant.
  3. Rollback / snapshot inspection initiated per `docs/migration-and-rollback-runbook.md`.
