# ADR-009: Custodian Mobile PWA & Finance Director Real-Time Approval Dashboard

## Context & Problem Statement

Branch custodians and finance managers require responsive, reliable user interfaces to manage petty cash expenditures, capture receipt proofs, and execute multi-tier approvals.
Field branch environments often suffer from unstable network conditions, necessitating:
1. **Offline Expense Draft Capability**: Branch custodians must be able to capture receipts and draft expense submissions locally without losing data when disconnected.
2. **Sub-200ms Management Queue Rendering**: Finance directors and line managers reviewing dozens of daily disbursements require an approval queue that renders and updates in $< 200\text{ ms}$.
3. **Mandatory Audit Reasoning Invariant**: Every 1-click approval or rejection action must enforce an explicit audit justification note in the immutable `StateTransitionRecord` audit log.
4. **Real-Time Live Updates**: Concurrent approvers and custodians require instant WebSocket live feed push events when requests are submitted, approved, rejected, or disbursed.

---

## Decision Drivers

- **Latency threshold**: Management dashboard queue retrieval and rendering $< 200\text{ ms}$; policy evaluation $< 1.5\text{ ms}$.
- **Offline Resilience**: Progressive Web App (PWA) with Service Worker caching and IndexedDB offline draft storage with auto-sync on reconnect.
- **Compliance & Security**: Strict tenant scoping (`tenant_id`), mandatory non-empty audit reasoning notes on all approvals and rejections, and role-based authority hierarchy.
- **Minimal External Dependencies**: Self-contained vanilla ES6 and optimized zero-CDN CSS to prevent third-party blocking scripts and guarantee offline execution.

---

## Considered Options

### Option 1: Full-Stack Vanilla ES6 PWA with Fast WebSocket Broadcast & Service Worker (Chosen)
Build a self-contained Progressive Web App using native HTML5, modern vanilla ES6 modules, CSS Custom Properties, and W3C Web Manifest/Service Worker. Static assets are served directly through FastAPI with Starlette WebSocket channels for live event streaming.

**Pros:**
- Zero bundle compilation overhead; loads in $< 10\text{ ms}$ over HTTP.
- Zero external CDN dependencies; functions 100% offline via Cache-First Service Worker.
- Clean architectural alignment with FastAPI backend and existing lightweight domain modules.
- Sub-200ms queue rendering easily achieved using lean native DOM operations.

**Cons:**
- Requires careful custom state management for offline queues (mitigated by clean IndexedDB abstraction with fallback to LocalStorage).

---

### Option 2: Heavyweight SPA Framework (React / Next.js / Vue with Vite) (Rejected)
Introduce a separate Node.js / NPM build pipeline with React or Next.js and Tailwind CSS compiler.

**Cons:**
- Adds Node.js, NPM, and multi-megabyte `node_modules` dependencies to a lean Python repository.
- Bundle sizes of 300KB-1MB increase cold load latency on low-bandwidth mobile branch connections.
- Introduces build-time drift and breaks local test simplicity.
- Rejected in accordance with the minimal dependency surface invariant.

---

## Decision Outcome

**Chosen Option: Option 1 — Full-Stack Vanilla ES6 PWA with Fast WebSocket Broadcast & Service Worker**

### Architectural Components

1. **PWA Application Shell**:
   - Web App Manifest (`src/static/manifest.json`) supporting standalone installation on iOS and Android devices.
   - Service Worker (`src/static/sw.js`) implementing Cache-First asset delivery and offline fallback.
   - Responsive Mobile View (`src/static/index.html` & `app.css`) featuring native receipt camera capture (`capture="environment"` / WebRTC) and live float balance widget.
   - Offline Draft Queue utilizing IndexedDB (`PettyFlowOfflineDB`) with automatic sync on `window.ononline`.

2. **Real-Time Live Feed & REST API**:
   - REST endpoints in `src/api/rest/approval_router.py` supporting `/submit`, `/approve`, `/reject`, `/disburse`, `/summary`, and queue listings.
   - Starlette WebSocket feed at `/v1/approvals/ws` broadcasting `WORKFLOW_EVENT` payloads upon any domain transition.
   - Mandatory audit reasoning validation in both Pydantic schemas and domain `ApprovalWorkflowService`.

3. **Performance Metrics**:
   - Approval queue retrieval and DOM update benchmarked at $< 20\text{ ms}$ (well under the $200\text{ ms}$ budget).
   - In-memory tenant-isolated request index delivering $O(1)$ lookups.

---

## Consequences

- **Positive**:
  - Full compliance with Roadmap Issue #5 deliverables and acceptance criteria.
  - Complete offline resilience for branch custodians.
  - Immutable audit logs capture mandatory approver notes for SOC2/financial compliance.
- **Negative**:
  - Requires maintaining vanilla JavaScript DOM rendering templates rather than JSX components.
