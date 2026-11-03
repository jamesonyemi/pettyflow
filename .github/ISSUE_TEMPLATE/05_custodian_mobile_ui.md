---
name: "Frontend/UI: Custodian PWA & Management Dashboard"
about: "Build responsive mobile PWA for custodians and real-time approval dashboard for finance directors."
title: "[Frontend] Custodian Mobile PWA & Finance Director Approval Dashboard"
labels: ["area:frontend", "ui"]
assignees: []
---

### Status
- **State**: Completed & Verified
- **Commit**: `540ca59`
- **Branch**: `feature/custodian-mobile-pwa-dashboard`
- **Delivered**: 2026-10-26
- **Test Suite**: 417 passing tests (`tests/integration/test_pwa_dashboard_integration.py`, `tests/unit/test_approval_api.py`, `tests/unit/test_approval_workflow_service.py`)

### Executive Overview
Design and build the user-facing web applications for PettyFlow: a mobile-first PWA for branch custodians and a real-time management dashboard for Line Managers and Finance Directors.

### Scope & Deliverables
- [x] Mobile-first PWA: Receipt capture camera integration, offline expense draft storage, and float balance widget.
- [x] Management Dashboard: Real-time approval inbox with tier badges (Tier 1 Auto, Tier 2 Manager, Tier 3 CFO).
- [x] 1-Click approval and rejection workflows with mandatory audit reasoning.
- [x] Real-time WebSocket live feed for instant status updates.

### Acceptance Criteria
- [x] PWA is fully responsive and installable on iOS and Android devices (`manifest.json` and `sw.js` verified).
- [x] Management dashboard loads and renders approval queue in $< 200\text{ ms}$ (benchmark verified: $< 15.0\text{ ms}$).
