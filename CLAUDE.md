# PettyFlow Project Guidelines

PettyFlow is a mission-critical, double-entry petty cash management platform featuring a mobile-first Progressive Web App (PWA) for branch custodians and a real-time management dashboard for Line Managers and Finance Directors.

---

## Technical & Architectural Invariants

1. **Zero External CDN & Zero Bundler Dependencies**:
   - The user-facing frontend is a pure Vanilla ES6 Progressive Web App with native CSS custom properties.
   - **NO** React, Next.js, Vue, Tailwind CLI, Webpack, Vite, or external NPM build pipelines.
   - Zero external fonts or third-party CDN scripts to ensure deterministic 100% offline capability.

2. **Performance Budgets**:
   - Management approval queue retrieval and DOM update must render in $< 200\text{ ms}$ (benchmark target $< 20\text{ ms}$).
   - Policy evaluations must execute in $< 1.5\text{ ms}$.
   - All visual transitions must animate strictly on GPU-accelerated properties (`transform` and `opacity`) to eliminate layout recalculation and jank.

3. **Offline Resilience & Data Integrity**:
   - Branch custodians must be able to draft disbursements and capture receipt proofs completely offline.
   - Offline drafts are stored locally in IndexedDB (`PettyFlowOfflineDB`) and automatically sync on network restoration (`window.ononline`).
   - Every 1-click approval or rejection requires a mandatory non-empty audit reasoning note in the immutable `StateTransitionRecord` audit log.

---

## UI/UX Design Context

### Users & Scenarios
- **Branch Custodians**: Quick receipt capture on mobile devices in low-connectivity retail branches. Primary requirements: fast camera shutter, instant offline draft feedback, and large touch targets.
- **Finance Directors & Line Managers**: High-density approval inbox on desktop/tablets. Primary requirements: instant queue filtering, multi-tier badges, and 1-click decision audit trails.

### Brand Personality & Tone
- **Voice**: Utilitarian, precise, high-trust financial ledger.
- **Personality**: Fast, disciplined, durable.

### Aesthetic Direction & Tokens
- **Theme**: Utilitarian dark mode using perceptually uniform OKLCH.
- **Palette**:
  - Background Neutral: Cool-tinted slate `oklch(14% 0.012 250)` (`#0b0f19`)
  - Surface Elevation: `oklch(18% 0.015 250)` (`#111827`)
  - Border Subtle: `oklch(28% 0.015 250)` (`#1e293b`)
  - Brand Accent: Indigo `oklch(58% 0.22 275)` (`#6366f1`)
  - Tier 1 (Auto): Emerald `oklch(70% 0.16 150)` (`#10b981`)
  - Tier 2 (Manager): Amber `oklch(75% 0.15 70)` (`#f59e0b`)
  - Tier 3 (CFO): Crimson `oklch(65% 0.20 25)` (`#ef4444`)
- **Touch & Ergonomics**:
  - Minimum 44px touch targets on mobile touch devices (`@media (pointer: coarse)`).
  - Explicit safe-area padding for mobile notches and home bars (`env(safe-area-inset-*)`).
  - High-contrast `:focus-visible` keyboard rings (2px offset) across all interactive elements.
- **Motion Guidelines**:
  - Micro-interactions (toggle, button press): 100–150ms.
  - State changes (queue approval update): 200–300ms.
  - Modal drawers: 300–400ms with exponential deceleration (`cubic-bezier(0.25, 1, 0.5, 1)`).
  - No bounce or elastic overshoot effects.
