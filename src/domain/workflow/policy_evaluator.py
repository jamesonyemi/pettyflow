"""
PettyFlow Approval Policy Evaluator
Implements a configurable, enterprise-grade rule engine that determines the
required approval tier and authorised actors for petty cash disbursement
requests based on amount thresholds and organisational policy.

Default PettyFlow Policy:
    ┌────────────────────────────┬─────────────────────────────┐
    │ Amount Range               │ Required Tier               │
    ├────────────────────────────┼─────────────────────────────┤
    │ $0.01 – $49.99             │ AUTO_APPROVE                │
    │ $50.00 – $499.99           │ MANAGER                     │
    │ $500.00 and above          │ FINANCE_DIRECTOR            │
    └────────────────────────────┴─────────────────────────────┘

Performance target: Policy evaluation completes in < 1.5 ms (p99).
All monetary comparisons use fixed-point integer arithmetic (× 10,000)
to eliminate floating-point rounding errors.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


# ─────────────────────────────────────────────────────────────────────────────
# Fixed-point constants (scaled by 10,000; $1.00 → 10,000)
# ─────────────────────────────────────────────────────────────────────────────

_SCALE = 10_000

# Threshold boundaries (inclusive lower bound, stored in base units)
_TIER_MANAGER_THRESHOLD_SCALED:         int = 50 * _SCALE    # $50.00  → 500,000 base units
_TIER_FINANCE_DIRECTOR_THRESHOLD_SCALED: int = 500 * _SCALE   # $500.00 → 5,000,000 base units


# ─────────────────────────────────────────────────────────────────────────────
# Approval Tier Enumeration
# ─────────────────────────────────────────────────────────────────────────────

class ApprovalTier(Enum):
    """
    Hierarchical approval authority tiers.
    Tiers are ordered by authority level (higher value = higher authority).
    """
    AUTO_APPROVE     = "AUTO_APPROVE"      # System auto-approves; no human needed
    MANAGER          = "MANAGER"           # Line manager or team lead
    FINANCE_DIRECTOR = "FINANCE_DIRECTOR"  # Finance director or CFO delegation


# ─────────────────────────────────────────────────────────────────────────────
# Policy Data Model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PolicyRule:
    """
    A single threshold-based policy rule.

    Fields:
        min_amount_scaled: Inclusive lower bound (fixed-point × 10,000).
                           Use 1 for the first tier ($0.01+).
        max_amount_scaled: Exclusive upper bound (fixed-point × 10,000).
                           Use None for the highest tier (unbounded).
        required_tier:     The ApprovalTier that must authorise requests
                           falling within this range.
        rule_name:         Human-readable rule identifier for audit logs.
    """
    min_amount_scaled: int
    max_amount_scaled: Optional[int]  # None → unbounded upper limit
    required_tier: ApprovalTier
    rule_name: str

    def __post_init__(self) -> None:
        if not isinstance(self.min_amount_scaled, int) or isinstance(self.min_amount_scaled, bool):
            raise TypeError("min_amount_scaled must be an integer fixed-point value.")
        if self.min_amount_scaled <= 0:
            raise ValueError("min_amount_scaled must be strictly positive.")
        if self.max_amount_scaled is not None:
            if not isinstance(self.max_amount_scaled, int) or isinstance(self.max_amount_scaled, bool):
                raise TypeError("max_amount_scaled must be an integer fixed-point value or None.")
            if self.max_amount_scaled <= self.min_amount_scaled:
                raise ValueError("max_amount_scaled must be greater than min_amount_scaled.")
        if not self.rule_name or not str(self.rule_name).strip():
            raise ValueError("rule_name must be a non-empty string.")

    def matches(self, amount_scaled: int) -> bool:
        """Return True if amount_scaled falls within this rule's range."""
        if amount_scaled < self.min_amount_scaled:
            return False
        if self.max_amount_scaled is not None and amount_scaled >= self.max_amount_scaled:
            return False
        return True


class PolicyConfigurationError(ValueError):
    """Raised when an approval policy is invalid or ambiguous."""


@dataclass(frozen=True)
class PolicyEvaluationResult:
    """
    Immutable result of a policy evaluation for a single disbursement request.
    """
    request_id: str
    amount_scaled: int
    required_tier: ApprovalTier
    matching_rule: str
    auto_approved: bool
    evaluation_duration_us: float   # Microseconds; target < 1,500 µs (1.5 ms)
    is_fallback: bool = False

    @property
    def amount_float(self) -> float:
        return self.amount_scaled / _SCALE

    def __str__(self) -> str:
        tier_label = self.required_tier.value
        auto = " [AUTO-APPROVED]" if self.auto_approved else ""
        fallback = " [FALLBACK]" if self.is_fallback else ""
        return (
            f"PolicyEvaluationResult("
            f"request={self.request_id!r}, "
            f"amount=${self.amount_float:.2f}, "
            f"tier={tier_label}{auto}{fallback}, "
            f"rule={self.matching_rule!r}, "
            f"eval_time={self.evaluation_duration_us:.1f}µs)"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Default Policy
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_PETTYFLOW_POLICY: List[PolicyRule] = [
    PolicyRule(
        min_amount_scaled=1,
        max_amount_scaled=_TIER_MANAGER_THRESHOLD_SCALED,
        required_tier=ApprovalTier.AUTO_APPROVE,
        rule_name="PETTYFLOW-POLICY-001: Auto-approve < $50.00",
    ),
    PolicyRule(
        min_amount_scaled=_TIER_MANAGER_THRESHOLD_SCALED,
        max_amount_scaled=_TIER_FINANCE_DIRECTOR_THRESHOLD_SCALED,
        required_tier=ApprovalTier.MANAGER,
        rule_name="PETTYFLOW-POLICY-002: Manager approval $50.00–$499.99",
    ),
    PolicyRule(
        min_amount_scaled=_TIER_FINANCE_DIRECTOR_THRESHOLD_SCALED,
        max_amount_scaled=None,
        required_tier=ApprovalTier.FINANCE_DIRECTOR,
        rule_name="PETTYFLOW-POLICY-003: Finance Director approval >= $500.00",
    ),
]


# ─────────────────────────────────────────────────────────────────────────────
# Policy Evaluator
# ─────────────────────────────────────────────────────────────────────────────

class ApprovalPolicyEvaluator:
    """
    Threshold-based approval policy rule engine.

    Rules are evaluated in ascending order of min_amount_scaled. The first
    rule whose range includes the request amount wins. This is an O(n) scan
    where n is the number of rules (3 in the default policy), which is
    effectively O(1) for practical configurations.

    Thread-safety: Stateless after construction; safe for concurrent calls.

    Usage:
        evaluator = ApprovalPolicyEvaluator(rules=DEFAULT_PETTYFLOW_POLICY)
        result = evaluator.evaluate(request_id="abc-123", amount_scaled=450_000)
        # → requires MANAGER tier for $45.00
    """

    def __init__(self, rules: Optional[List[PolicyRule]] = None) -> None:
        """
        Args:
            rules: Ordered list of PolicyRule objects (ascending by threshold).
                   Defaults to DEFAULT_PETTYFLOW_POLICY if not provided.
        """
        candidate_rules = list(rules if rules is not None else DEFAULT_PETTYFLOW_POLICY)
        if not candidate_rules:
            raise ValueError("ApprovalPolicyEvaluator requires at least one PolicyRule.")

        self._rules: List[PolicyRule] = sorted(candidate_rules, key=lambda r: r.min_amount_scaled)

        for idx, rule in enumerate(self._rules):
            if rule.min_amount_scaled <= 0:
                raise PolicyConfigurationError(
                    f"Rule '{rule.rule_name}' has a non-positive minimum threshold."
                )
            if rule.max_amount_scaled is not None and rule.max_amount_scaled <= rule.min_amount_scaled:
                raise PolicyConfigurationError(
                    f"Rule '{rule.rule_name}' defines an invalid range: max must be greater than min."
                )
            if idx == 0:
                continue
            prev = self._rules[idx - 1]
            if prev.max_amount_scaled is not None and rule.min_amount_scaled < prev.max_amount_scaled:
                raise ValueError(
                    "ApprovalPolicyEvaluator requires non-overlapping policy ranges; "
                    f"rule '{prev.rule_name}' overlaps '{rule.rule_name}'."
                )

    # ------------------------------------------------------------------
    # Core Evaluation
    # ------------------------------------------------------------------

    def evaluate(
        self,
        request_id: str,
        amount_scaled: int,
    ) -> PolicyEvaluationResult:
        """
        Determine the required approval tier for the given amount.

        Args:
            request_id:    The UUID of the ApprovalRequest being evaluated.
            amount_scaled: Disbursement amount in fixed-point units (× 10,000).

        Returns:
            PolicyEvaluationResult with the matched tier and performance metrics.

        Raises:
            ValueError: If amount_scaled is non-positive (zero or negative amounts
                        are not valid disbursements).
        """
        if isinstance(amount_scaled, bool) or not isinstance(amount_scaled, int):
            raise TypeError(
                f"amount_scaled must be an integer fixed-point value; received {type(amount_scaled).__name__}."
            )
        if amount_scaled <= 0:
            raise ValueError(
                f"amount_scaled must be a positive integer; received {amount_scaled}."
            )

        t_start = time.perf_counter_ns()

        matched_rule: Optional[PolicyRule] = None
        for rule in self._rules:
            if rule.matches(amount_scaled):
                matched_rule = rule
                break

        # Fallback: if no rule matches (e.g., custom policy with gaps),
        # apply the highest-authority tier as a safe default.
        fallback_used = False
        if matched_rule is None:
            matched_rule = PolicyRule(
                min_amount_scaled=amount_scaled,
                max_amount_scaled=None,
                required_tier=ApprovalTier.FINANCE_DIRECTOR,
                rule_name="PETTYFLOW-POLICY-FALLBACK: Finance Director (no rule matched)",
            )
            fallback_used = True

        t_end = time.perf_counter_ns()
        duration_us = (t_end - t_start) / 1_000.0

        return PolicyEvaluationResult(
            request_id=request_id,
            amount_scaled=amount_scaled,
            required_tier=matched_rule.required_tier,
            matching_rule=matched_rule.rule_name,
            auto_approved=(matched_rule.required_tier == ApprovalTier.AUTO_APPROVE),
            evaluation_duration_us=duration_us,
            is_fallback=fallback_used,
        )

    def is_actor_authorized(
        self,
        actor_tier: ApprovalTier,
        required_tier: ApprovalTier,
    ) -> bool:
        """
        Return True if actor_tier has sufficient authority to satisfy required_tier.

        Authority ordering (ascending): AUTO_APPROVE < MANAGER < FINANCE_DIRECTOR.
        A higher-tier actor can always approve a lower-tier requirement.
        """
        _TIER_AUTHORITY: dict[ApprovalTier, int] = {
            ApprovalTier.AUTO_APPROVE:     0,
            ApprovalTier.MANAGER:          1,
            ApprovalTier.FINANCE_DIRECTOR: 2,
        }
        return _TIER_AUTHORITY[actor_tier] >= _TIER_AUTHORITY[required_tier]

    def evaluate_and_auto_approve(
        self,
        request_id: str,
        amount_scaled: int,
    ) -> tuple[PolicyEvaluationResult, bool]:
        """
        Evaluate policy and return whether the request qualifies for auto-approval.

        Returns:
            Tuple of (PolicyEvaluationResult, should_auto_approve: bool)
        """
        result = self.evaluate(request_id, amount_scaled)
        return result, result.auto_approved

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def rules(self) -> List[PolicyRule]:
        """Read-only view of the configured policy rules."""
        return list(self._rules)

    def describe_policy(self) -> str:
        """Human-readable policy description for logging and audit."""
        lines = ["PettyFlow Approval Policy:"]
        for rule in self._rules:
            lo = rule.min_amount_scaled / _SCALE
            hi = f"${rule.max_amount_scaled / _SCALE:.2f}" if rule.max_amount_scaled else "∞"
            lines.append(
                f"  ${lo:.2f} – {hi}  →  {rule.required_tier.value}  ({rule.rule_name})"
            )
        return "\n".join(lines)

    def __repr__(self) -> str:
        return f"ApprovalPolicyEvaluator(rules={len(self._rules)})"
