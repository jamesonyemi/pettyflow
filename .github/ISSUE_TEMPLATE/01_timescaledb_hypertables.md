---
name: "Data Platform: Production TimescaleDB Hypertables"
about: "Configure production TimescaleDB partitioned hypertables, continuous aggregates, and compression policies."
title: "[Data Platform] Production TimescaleDB Hypertables & Continuous Aggregates"
labels: ["area:database", "enhancement"]
assignees: []
---

### Executive Overview
Configure production TimescaleDB partitioned hypertables for `audit_trail` and `ledger_blocks` to support microsecond time-series queries and historical audit retention.

### Scope & Deliverables
- [ ] Configure `audit_trail` as a TimescaleDB hypertable partitioned by `created_at` (7-day chunk intervals).
- [ ] Establish continuous aggregate views for daily spend per fund and monthly category rollups.
- [ ] Configure automatic chunk compression policies for audit logs and ledger blocks older than 90 days.
- [ ] Implement data retention and archival policies to cold S3/GCS storage.

### Acceptance Criteria
- Time-series spend aggregation queries execute in $< 10\text{ ms}$.
- Audit logs maintain immutable cryptographic hash chain integrity after compression.
- Automated tests verify hypertable partition creation and rollup query accuracy.
