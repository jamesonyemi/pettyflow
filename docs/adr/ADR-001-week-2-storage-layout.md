# ADR-001: Month-and-Tenant Partitioned Ledger Storage

## Context & Problem Statement

Postings are append-only and always tenant scoped. Week 2 requires time-based
partition management without concentrating every tenant's writes in one table.

## Decision Drivers

- Latency threshold: keep tenant account queries on a bounded set of indexes.
- Security constraint: prevent a posting from referring to an account owned by a different tenant.
- Financial accuracy: retain immutable, positive fixed-point posting amounts.

## Considered Options

1. Range partition postings by month only.
2. Create one list partition per tenant per month.
3. Range partition by month, then hash partition each month by tenant identifier.

## Decision Outcome

Chosen Option: 3, because it provides both temporal retention management and
tenant write distribution with a fixed, operationally manageable number of
partitions. Sixteen hash partitions are provisioned for the active month. A
default partition keeps writes available if the next-month provisioning task is late.

## Consequences

- Positive: tenant-scoped workloads distribute predictably and the composite
  account foreign key enforces tenant isolation in the database.
- Negative: operations must provision future monthly partitions and monitor the
  default partition; migrations include an explicit destructive undo script for
  disposable-environment rollback verification.
