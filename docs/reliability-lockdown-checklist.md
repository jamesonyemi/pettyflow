# PettyFlow: 4-Week Reliability Lockdown Checklist

## Objective
Stabilize the critical production path before any further product expansion or UI work. This checklist focuses only on core reliability, security, migration safety, sandbox validation, and observability.

## Principle
No feature expansion, no UI work, and no roadmap acceleration until all stop/go gates below are green.

---

## Execution controls

Every checklist item must have:

- **Owner**: the engineering or operations role accountable for completion
- **Dependency**: the earlier gate, contract, or environment prerequisite it relies on
- **Evidence**: a test report, migration log, dashboard, runbook, or review record
- **Exit metric**: an objective pass/fail threshold

The work is sequential: Week 1 establishes idempotency, event identity, and audit contracts; Week 2 verifies tenant enforcement; Week 3 depends on those contracts for safe migration comparison; Week 4 depends on the validated flows and telemetry baseline. No week may be signed off by the implementer alone.

## Required prerequisites

- A staging environment with production-like schema, representative ledger snapshots, backup/restore access, and isolated test tenants
- Provider sandbox credentials for bank, ERP, ACH/mobile-money flows, with secrets managed through the approved KMS lifecycle
- A durable persistence mechanism and uniqueness constraints for idempotency keys and provider event IDs
- A test harness capable of concurrent duplicate submissions, callback replay, provider timeout, outage, and partial-settlement simulations
- A telemetry destination with dashboards, alert routing, on-call ownership, and retention defined before Week 4 sign-off

## Control matrix

Before marking an item complete, record the owner, dependency, evidence link, execution date, and result in the execution report. Minimum accountable roles are:

| Area | Accountable owner | Required evidence |
| --- | --- | --- |
| Disbursement, ledger, and replay safety | Payments engineering | Duplicate/replay test report and canonical-result audit sample |
| Tenant isolation and authorization | Security engineering | Negative isolation test report and security review sign-off |
| Schema migration and rollback | Platform engineering | Staging migration log, restore proof, and rollback runbook |
| Provider sandbox flows | Integrations engineering | Provider traces, reconciliation results, and failure-drill report |
| Metrics, alerts, and incident response | SRE/operations | Dashboard links, alert test output, and incident drill record |

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
- [ ] Enforce bounded retention, uniqueness, and concurrency-safe reads for idempotency keys and provider event IDs

### Acceptance criteria
- Retries of the same external request do not create multiple disbursements
- Duplicate callback payloads do not trigger duplicate ledger mutations
- A repeated business action returns the original result or a safe retriable status, never a second financial effect
- Audit trail proves which action was canonical for a given idempotency key
- Unknown, partial, and manually-recovered settlement states have explicit retry and operator-handling outcomes

### Test expectations
- Unit tests for duplicate request protection
- Retry simulation tests for network failures
- Replay tests for provider callback payloads
- Ledger invariants remain unchanged after duplicate events
- Concurrent duplicate submissions and callbacks produce one canonical outcome

### Stop/Go gate
Proceed only if:
- duplicate request tests pass
- callback replay tests pass
- audit trail confirms canonical outcomes
- concurrency tests show one committed financial effect per business action
- the retry/state transition contract is reviewed by payments engineering and operations

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
- [ ] Add negative tests for background jobs and asynchronous event consumers, not only synchronous APIs

### Acceptance criteria
- No cross-tenant read or write path exists in core financial operations
- Funds and approvals cannot be accessed outside their tenant context
- Security-sensitive actions are signed and auditable
- Secret handling follows the defined KMS lifecycle policy
- Tenant context cannot be widened, omitted, or replaced by caller-controlled values

### Test expectations
- Multi-tenant isolation tests at API and service boundaries
- Cache key isolation tests
- Approval authorization tests across tenant boundaries
- Audit metadata validation tests
- Negative tests cover API, adapter, cache, ledger, and background-job boundaries

### Stop/Go gate
Proceed only if:
- all tenant isolation tests pass
- no security review finding remains open for critical paths
- audit metadata is complete for sensitive actions
- test coverage includes every identified service boundary and has a security-owner sign-off

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
- [ ] Document migration mechanism, preconditions, expand/contract compatibility, backup point, and maximum rollback window
- [ ] Verify restore from the backup used by the rollback exercise before treating it as valid

### Acceptance criteria
- Schema changes can be rolled back without losing financial integrity
- Migration failure leaves the ledger in a recoverable state
- Critical data transformations are verified and auditable
- Rollback meets the documented recovery-time and zero-financial-data-loss thresholds

### Test expectations
- Migration testing against realistic ledger snapshots
- Rollback simulation under partial deployment failure
- Ledger invariant checks before and after migration
- Comparisons of pre/post state to confirm no silent drift
- Restore and rollback tests use a representative snapshot and record checksums/counts for critical tables

### Stop/Go gate
Proceed only if:
- rollback test passes
- ledger integrity remains valid after simulated failure
- migration runbook is complete and reviewed
- the rollback exercise meets its declared recovery-time objective with zero unreconciled ledger drift
- platform and payments owners sign off on the migration evidence

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
- [ ] Assign an owner and severity threshold to every alert and verify the notification path

### Acceptance criteria
- Bank, ERP, and mobile settlement flows succeed and fail predictably in sandbox
- External provider failures are visible and actionable
- FX conversion edge cases are handled without silent financial drift
- Critical operations are measurable and alertable
- Every critical metric has a dashboard location, retention policy, alert threshold, and on-call owner

### Test expectations
- Sandbox end-to-end flows for settlement and reconciliation
- Retry and outage simulation tests
- Alerts and incident response drill
- Observability validation across critical workflow steps
- Alert delivery is verified for provider outage, retry storm, ledger drift, and stale FX scenarios

### Stop/Go gate
Proceed only if:
- all sandbox E2E flows pass
- alerts and dashboards are operational
- incident runbook is validated
- no critical reliability issue remains unmitigated
- each critical alert fires in a controlled drill and reaches the assigned on-call route

---

## Hard stop rules
Do not proceed to the next phase if any of the following remain unresolved:
- duplicate financial action risk
- cross-tenant isolation gap
- migration rollback uncertainty
- unobserved critical payment path
- open issue in sandbox settlement or rate logic
- missing evidence, owner sign-off, or objective exit metric for any completed gate

---

## Definition of done for the reliability lockdown
The reliability lockdown is complete only when all of the following are true:
- idempotency is proven across retries and duplicate events
- tenant isolation is verified across service boundaries
- rollback safety is proven for deployment failure modes
- sandbox validation passes for critical external flows
- operational alerts and dashboards are live for critical workflows
- each gate has an execution report, evidence links, owner sign-off, and recorded residual risks

## Output expected at the end of the 4 weeks
- execution report covering each week’s validation results
- list of remaining risks with priority and owner
- list of signed-off stop/go gates
- evidence index mapping every checklist item to its test, runbook, dashboard, or review record
- residual risk register with severity, owner, mitigation, and target date
- recommendation for whether roadmap expansion may continue

This checkpoint is the gate before any workflow UX, reporting maturity, or UI work.
