# PettyFlow: 4-Week Reliability Lockdown Checklist

## Objective
Stabilize the critical production path before any further product expansion or UI work. This checklist focuses only on core reliability, security, migration safety, sandbox validation, and observability.

## Principle
No feature expansion, no UI work, and no roadmap acceleration until all stop/go gates below are green.

---

## Week 1: Idempotency and replay safety

### Primary goal
Guarantee that retries, callback replays, and duplicate submissions cannot cause duplicate financial actions.

### Tasks
- [ ] Implement durable idempotency storage for disbursement requests
- [ ] Attach idempotency keys to critical ledger writes and external settlement operations
- [ ] Validate same request submitted twice produces the same result without duplicate side effects
- [ ] Add webhook callback replay protection and deduplication by provider event ID
- [ ] Define request/retry semantics for payment failures, network retries, and partial settlement states
- [ ] Add audit records documenting idempotency key usage and result reuse

### Acceptance criteria
- Retries of the same external request do not create multiple disbursements
- Duplicate callback payloads do not trigger duplicate ledger mutations
- A repeated business action returns the original result or a safe retriable status, never a second financial effect
- Audit trail proves which action was canonical for a given idempotency key

### Test expectations
- Unit tests for duplicate request protection
- Retry simulation tests for network failures
- Replay tests for provider callback payloads
- Ledger invariants remain unchanged after duplicate events

### Stop/Go gate
Proceed only if:
- duplicate request tests pass
- callback replay tests pass
- audit trail confirms canonical outcomes

---

## Week 2: Tenant isolation and security review

### Primary goal
Prove strict tenant boundaries and eliminate accidental cross-tenant access.

### Tasks
- [ ] Audit every service boundary for explicit tenant_id enforcement
- [ ] Review API, adapter, cache, and ledger access points for tenant scoping
- [ ] Validate cache keys include tenant and fund boundaries
- [ ] Ensure approval actor privilege checks match required tier and tenant
- [ ] Review all security-sensitive paths for secret management and access rules
- [ ] Confirm audit records include tenant, actor, action, and timestamp metadata

### Acceptance criteria
- No cross-tenant read or write path exists in core financial operations
- Funds and approvals cannot be accessed outside their tenant context
- Security-sensitive actions are signed and auditable
- Secret handling follows the defined KMS lifecycle policy

### Test expectations
- Multi-tenant isolation tests at API and service boundaries
- Cache key isolation tests
- Approval authorization tests across tenant boundaries
- Audit metadata validation tests

### Stop/Go gate
Proceed only if:
- all tenant isolation tests pass
- no security review finding remains open for critical paths
- audit metadata is complete for sensitive actions

---

## Week 3: Migration safety and rollback validation

### Primary goal
Prove that schema, data, and financial state changes are safe to deploy and revert.

### Tasks
- [ ] Run full migration dry-runs in staging
- [ ] Validate ledger compatibility with the new schema or state model
- [ ] Write and execute rollback plan for partial or failed deployment
- [ ] Validate financial state continuity before and after migration
- [ ] Confirm any data transform is reversible and audit-friendly
- [ ] Test schema deployment failure modes without data corruption

### Acceptance criteria
- Schema changes can be rolled back without losing financial integrity
- Migration failure leaves the ledger in a recoverable state
- Critical data transformations are verified and auditable

### Test expectations
- Migration testing against realistic ledger snapshots
- Rollback simulation under partial deployment failure
- Ledger invariant checks before and after migration
- Comparisons of pre/post state to confirm no silent drift

### Stop/Go gate
Proceed only if:
- rollback test passes
- ledger integrity remains valid after simulated failure
- migration runbook is complete and reviewed

---

## Week 4: Sandbox validation and observability baseline

### Primary goal
Prove that critical external flows work under real-world conditions and can be observed when they fail.

### Tasks
- [ ] Validate bank flow in sandbox for settlement status transitions and reconciliation
- [ ] Validate ERP replenishment flow in sandbox and mismatch detection
- [ ] Validate ACH/mobile money settlement flow in sandbox and duplicate handling
- [ ] Validate historical FX conversion and missing-rate edge cases
- [ ] Add metrics for approval latency, disbursement failures, ledger drift, provider retries, and stale FX
- [ ] Add alerts for critical operational failures and high retry rates
- [ ] Run a failure drill for timeout, provider outage, and replay scenarios

### Acceptance criteria
- Bank, ERP, and mobile settlement flows succeed and fail predictably in sandbox
- External provider failures are visible and actionable
- FX conversion edge cases are handled without silent financial drift
- Critical operations are measurable and alertable

### Test expectations
- Sandbox end-to-end flows for settlement and reconciliation
- Retry and outage simulation tests
- Alerts and incident response drill
- Observability validation across critical workflow steps

### Stop/Go gate
Proceed only if:
- all sandbox E2E flows pass
- alerts and dashboards are operational
- incident runbook is validated
- no critical reliability issue remains unmitigated

---

## Hard stop rules
Do not proceed to the next phase if any of the following remain unresolved:
- duplicate financial action risk
- cross-tenant isolation gap
- migration rollback uncertainty
- unobserved critical payment path
- open issue in sandbox settlement or rate logic

---

## Definition of done for the reliability lockdown
The reliability lockdown is complete only when all of the following are true:
- idempotency is proven across retries and duplicate events
- tenant isolation is verified across service boundaries
- rollback safety is proven for deployment failure modes
- sandbox validation passes for critical external flows
- operational alerts and dashboards are live for critical workflows

## Output expected at the end of the 4 weeks
- execution report covering each week’s validation results
- list of remaining risks with priority and owner
- list of signed-off stop/go gates
- recommendation for whether roadmap expansion may continue

This checkpoint is the gate before any workflow UX, reporting maturity, or UI work.
