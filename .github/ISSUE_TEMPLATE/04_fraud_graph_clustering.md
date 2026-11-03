---
name: "Fraud/Risk: Advanced Split-Transaction Graph Clustering"
about: "Multi-signal anomaly detection, temporal graph clustering, and smurfing prevention."
title: "[Security/Fraud] Advanced Split-Transaction Graph Clustering & Velocity Rules"
labels: ["area:fraud", "security"]
assignees: []
---

### Status
- **State**: Completed & Merged (PR #13)
- **Commit**: `94ba9b9` / `a45142e`
- **Branch**: `main`
- **Delivered**: 2026-10-17

### Executive Overview
Expand petty cash fraud screening with temporal graph clustering and velocity rules to detect policy evasion and smurfing across custodians.

### Scope & Deliverables
- [x] Enhance `SplitTxDetector` (`src/services/fraud/split_tx_detector.py`) with temporal graph clustering for linked receipts within 48h windows.
- [x] Implement merchant category anomaly detection against historical custodian spending baselines.
- [x] Add velocity anomaly checks (e.g. $> 3$ disbursements within 1 hour).
- [x] Seal tamper-evident fraud evidence packages directly into the WORM audit trail.

### Acceptance Criteria
- [x] Policy threshold evasion attempts (e.g. 2 x $48 receipts to bypass $50 approval limit) flagged with `SPLIT_TRANSACTION_DETECTED`.
- [x] Automated tests verify detection rates across synthetic fraudulent receipt datasets.
