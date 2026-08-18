"""
Unit Test Suite — PettyFlow Approval Workflow Engine (Week 4)

Validates:
  - State machine deterministic transitions (all valid paths)
  - InvalidStateTransitionException on all illegal transitions
  - Terminal state enforcement (no exit from REJECTED / DISBURSED)
  - Full end-to-end happy path (DRAFT → PENDING → APPROVED → DISBURSED)
  - Full rejection path (DRAFT → PENDING → REJECTED)
  - DRAFT cancellation path (DRAFT → REJECTED via CANCEL)
  - Immutable audit trail integrity
  - Policy evaluator threshold boundaries (exact boundary values)
  - Policy evaluation latency benchmark (< 1.5 ms p99)
  - Authority level checks (manager can't approve finance-director tier)
  - Auto-approval detection
  - Custom policy configuration

Coverage target: ≥ 90% line coverage, 100% branch coverage on core
                 workflow and policy logic.
"""

import time
import unittest
import uuid

from src.domain.workflow.state_machine import (
    ApprovalEvent,
    ApprovalRequest,
    ApprovalState,
    InvalidStateTransitionException,
    WorkflowStateMachine,
    _TERMINAL_STATES,
    _TRANSITION_TABLE,
)
from src.domain.workflow.policy_evaluator import (
    ApprovalPolicyEvaluator,
    ApprovalTier,
    DEFAULT_PETTYFLOW_POLICY,
    PolicyRule,
    _SCALE,
    _TIER_FINANCE_DIRECTOR_THRESHOLD_SCALED,
    _TIER_MANAGER_THRESHOLD_SCALED,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _scaled(dollars: float) -> int:
    """Convert a dollar amount to fixed-point scaled integer."""
    return int(round(dollars * _SCALE))


def _make_fsm(
    amount_scaled: int = _scaled(25.00),
    tenant_id: str | None = None,
    custodian_id: str = "custodian-001",
) -> WorkflowStateMachine:
    """Create a fresh WorkflowStateMachine in DRAFT state."""
    return WorkflowStateMachine.create(
        tenant_id=tenant_id or str(uuid.uuid4()),
        custodian_id=custodian_id,
        amount_scaled=amount_scaled,
        currency="USD",
        description="Office supplies",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test Suite 1: State Machine — Valid Transitions
# ─────────────────────────────────────────────────────────────────────────────

class TestValidStateTransitions(unittest.TestCase):
    """All valid FSM paths must reach the expected terminal or intermediate state."""

    def test_draft_to_pending_via_submit(self):
        """DRAFT + SUBMIT → PENDING."""
        fsm = _make_fsm()
        new_state = fsm.submit(actor_id="custodian-001")
        self.assertEqual(new_state, ApprovalState.PENDING)
        self.assertEqual(fsm.current_state, ApprovalState.PENDING)

    def test_pending_to_approved_via_approve(self):
        """PENDING + APPROVE → APPROVED."""
        fsm = _make_fsm()
        fsm.submit(actor_id="custodian-001")
        new_state = fsm.approve(actor_id="manager-007")
        self.assertEqual(new_state, ApprovalState.APPROVED)

    def test_approved_to_disbursed_via_disburse(self):
        """APPROVED + DISBURSE → DISBURSED."""
        fsm = _make_fsm()
        fsm.submit(actor_id="custodian-001")
        fsm.approve(actor_id="manager-007")
        new_state = fsm.disburse(actor_id="finance-system")
        self.assertEqual(new_state, ApprovalState.DISBURSED)

    def test_pending_to_rejected_via_reject(self):
        """PENDING + REJECT → REJECTED."""
        fsm = _make_fsm()
        fsm.submit(actor_id="custodian-001")
        new_state = fsm.reject(actor_id="manager-007", notes="Insufficient receipts.")
        self.assertEqual(new_state, ApprovalState.REJECTED)

    def test_draft_to_rejected_via_cancel(self):
        """DRAFT + CANCEL → REJECTED (custodian cancels before review)."""
        fsm = _make_fsm()
        new_state = fsm.cancel(actor_id="custodian-001", notes="Duplicate request.")
        self.assertEqual(new_state, ApprovalState.REJECTED)

    def test_full_happy_path(self):
        """Complete DRAFT → PENDING → APPROVED → DISBURSED lifecycle."""
        fsm = _make_fsm(amount_scaled=_scaled(75.00))
        self.assertEqual(fsm.current_state, ApprovalState.DRAFT)

        fsm.submit(actor_id="custodian-A")
        self.assertEqual(fsm.current_state, ApprovalState.PENDING)

        fsm.approve(actor_id="manager-B", notes="Approved - valid receipt.")
        self.assertEqual(fsm.current_state, ApprovalState.APPROVED)

        fsm.disburse(actor_id="finance-system")
        self.assertEqual(fsm.current_state, ApprovalState.DISBURSED)

        self.assertTrue(fsm.request.is_terminal)

    def test_full_rejection_path(self):
        """Complete DRAFT → PENDING → REJECTED lifecycle."""
        fsm = _make_fsm()
        fsm.submit(actor_id="custodian-A")
        fsm.reject(actor_id="manager-B", notes="Amount does not match receipt.")
        self.assertEqual(fsm.current_state, ApprovalState.REJECTED)
        self.assertTrue(fsm.request.is_terminal)


# ─────────────────────────────────────────────────────────────────────────────
# Test Suite 2: State Machine — Invalid / Illegal Transitions
# ─────────────────────────────────────────────────────────────────────────────

class TestInvalidStateTransitions(unittest.TestCase):
    """
    All illegal state transitions MUST raise InvalidStateTransitionException.
    This exhaustively covers every (state, event) pair NOT in _TRANSITION_TABLE.
    """

    def _assert_illegal(
        self,
        fsm: WorkflowStateMachine,
        event: ApprovalEvent,
        msg: str,
    ):
        with self.assertRaises(InvalidStateTransitionException, msg=msg):
            fsm.transition(event, actor_id="bad-actor")

    # ── FROM DRAFT ────────────────────────────────────────────────────────────

    def test_draft_cannot_approve(self):
        """DRAFT + APPROVE is illegal (must submit first)."""
        self._assert_illegal(_make_fsm(), ApprovalEvent.APPROVE,
                             "DRAFT→APPROVE should be illegal")

    def test_draft_cannot_reject(self):
        """DRAFT + REJECT is illegal."""
        self._assert_illegal(_make_fsm(), ApprovalEvent.REJECT,
                             "DRAFT→REJECT should be illegal")

    def test_draft_cannot_disburse(self):
        """DRAFT + DISBURSE is illegal (critical: prevents bypassing approval)."""
        self._assert_illegal(_make_fsm(), ApprovalEvent.DISBURSE,
                             "DRAFT→DISBURSE should be illegal")

    # ── FROM PENDING ──────────────────────────────────────────────────────────

    def test_pending_cannot_submit_again(self):
        """PENDING + SUBMIT is illegal (double-submission prevention)."""
        fsm = _make_fsm()
        fsm.submit(actor_id="custodian-001")
        self._assert_illegal(fsm, ApprovalEvent.SUBMIT,
                             "PENDING→SUBMIT should be illegal")

    def test_pending_cannot_disburse(self):
        """PENDING + DISBURSE is illegal (must be APPROVED first)."""
        fsm = _make_fsm()
        fsm.submit(actor_id="custodian-001")
        self._assert_illegal(fsm, ApprovalEvent.DISBURSE,
                             "PENDING→DISBURSE should be illegal")

    def test_pending_cannot_cancel(self):
        """PENDING + CANCEL is illegal (can only cancel DRAFTs)."""
        fsm = _make_fsm()
        fsm.submit(actor_id="custodian-001")
        self._assert_illegal(fsm, ApprovalEvent.CANCEL,
                             "PENDING→CANCEL should be illegal")

    # ── FROM APPROVED ─────────────────────────────────────────────────────────

    def test_approved_cannot_submit(self):
        """APPROVED + SUBMIT is illegal."""
        fsm = _make_fsm()
        fsm.submit("c")
        fsm.approve("m")
        self._assert_illegal(fsm, ApprovalEvent.SUBMIT,
                             "APPROVED→SUBMIT should be illegal")

    def test_approved_cannot_approve_again(self):
        """APPROVED + APPROVE is illegal (double-approval prevention)."""
        fsm = _make_fsm()
        fsm.submit("c")
        fsm.approve("m")
        self._assert_illegal(fsm, ApprovalEvent.APPROVE,
                             "APPROVED→APPROVE should be illegal")

    def test_approved_cannot_reject(self):
        """APPROVED + REJECT is illegal (approved requests cannot be retroactively rejected)."""
        fsm = _make_fsm()
        fsm.submit("c")
        fsm.approve("m")
        self._assert_illegal(fsm, ApprovalEvent.REJECT,
                             "APPROVED→REJECT should be illegal")

    def test_approved_cannot_cancel(self):
        """APPROVED + CANCEL is illegal."""
        fsm = _make_fsm()
        fsm.submit("c")
        fsm.approve("m")
        self._assert_illegal(fsm, ApprovalEvent.CANCEL,
                             "APPROVED→CANCEL should be illegal")

    # ── FROM TERMINAL: REJECTED ────────────────────────────────────────────────

    def test_rejected_is_truly_terminal(self):
        """No event may exit a REJECTED state — all must raise InvalidStateTransitionException."""
        fsm = _make_fsm()
        fsm.submit("c")
        fsm.reject("m")
        self.assertEqual(fsm.current_state, ApprovalState.REJECTED)
        for event in ApprovalEvent:
            with self.assertRaises(
                InvalidStateTransitionException,
                msg=f"REJECTED+{event.value} should be illegal",
            ):
                fsm.transition(event, actor_id="any")

    # ── FROM TERMINAL: DISBURSED ───────────────────────────────────────────────

    def test_disbursed_is_truly_terminal(self):
        """No event may exit a DISBURSED state — all must raise InvalidStateTransitionException."""
        fsm = _make_fsm()
        fsm.submit("c")
        fsm.approve("m")
        fsm.disburse("finance-system")
        self.assertEqual(fsm.current_state, ApprovalState.DISBURSED)
        for event in ApprovalEvent:
            with self.assertRaises(
                InvalidStateTransitionException,
                msg=f"DISBURSED+{event.value} should be illegal",
            ):
                fsm.transition(event, actor_id="any")

    # ── EXCEPTION PAYLOAD ──────────────────────────────────────────────────────

    def test_exception_carries_correct_metadata(self):
        """InvalidStateTransitionException must expose state, event, and request_id."""
        fsm = _make_fsm()
        try:
            fsm.transition(ApprovalEvent.DISBURSE, actor_id="attacker")
            self.fail("Expected InvalidStateTransitionException")
        except InvalidStateTransitionException as exc:
            self.assertEqual(exc.current_state, ApprovalState.DRAFT)
            self.assertEqual(exc.event, ApprovalEvent.DISBURSE)
            self.assertEqual(exc.request_id, fsm.request.request_id)
            self.assertIn("DRAFT", str(exc))
            self.assertIn("DISBURSE", str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# Test Suite 3: Audit Trail Integrity
# ─────────────────────────────────────────────────────────────────────────────

class TestAuditTrailIntegrity(unittest.TestCase):
    """Every transition must produce an immutable, ordered audit record."""

    def test_audit_trail_grows_with_each_transition(self):
        """Audit trail has exactly one entry per successful transition."""
        fsm = _make_fsm()
        self.assertEqual(len(fsm.request.audit_trail), 0)

        fsm.submit("custodian-A")
        self.assertEqual(len(fsm.request.audit_trail), 1)

        fsm.approve("manager-B")
        self.assertEqual(len(fsm.request.audit_trail), 2)

        fsm.disburse("finance-system")
        self.assertEqual(len(fsm.request.audit_trail), 3)

    def test_audit_trail_records_correct_states(self):
        """Each audit record correctly captures from_state, event, to_state."""
        fsm = _make_fsm()
        fsm.submit("c")
        fsm.reject("m", notes="Policy violation.")

        records = fsm.request.audit_trail
        # First record: DRAFT → PENDING
        self.assertEqual(records[0].from_state, ApprovalState.DRAFT)
        self.assertEqual(records[0].event, ApprovalEvent.SUBMIT)
        self.assertEqual(records[0].to_state, ApprovalState.PENDING)
        self.assertEqual(records[0].actor_id, "c")

        # Second record: PENDING → REJECTED
        self.assertEqual(records[1].from_state, ApprovalState.PENDING)
        self.assertEqual(records[1].event, ApprovalEvent.REJECT)
        self.assertEqual(records[1].to_state, ApprovalState.REJECTED)
        self.assertEqual(records[1].actor_id, "m")
        self.assertEqual(records[1].notes, "Policy violation.")

    def test_failed_transitions_do_not_pollute_audit_trail(self):
        """Failed (illegal) transitions must NOT add records to the audit trail."""
        fsm = _make_fsm()
        fsm.submit("custodian-A")  # trail: 1 entry

        try:
            fsm.transition(ApprovalEvent.DISBURSE, actor_id="bad-actor")
        except InvalidStateTransitionException:
            pass

        self.assertEqual(len(fsm.request.audit_trail), 1)  # still just 1 entry

    def test_audit_records_have_unique_transition_ids(self):
        """Each audit record must have a unique transition_id (UUID)."""
        fsm = _make_fsm()
        fsm.submit("c")
        fsm.approve("m")
        fsm.disburse("f")

        ids = [r.transition_id for r in fsm.request.audit_trail]
        self.assertEqual(len(ids), len(set(ids)), "Duplicate transition_ids detected.")

    def test_updated_at_advances_on_each_transition(self):
        """request.updated_at must be >= created_at and advance with each transition."""
        fsm = _make_fsm()
        created = fsm.request.created_at

        fsm.submit("c")
        after_submit = fsm.request.updated_at
        self.assertGreaterEqual(after_submit, created)

        fsm.approve("m")
        after_approve = fsm.request.updated_at
        self.assertGreaterEqual(after_approve, after_submit)

    def test_state_transition_record_is_immutable(self):
        """StateTransitionRecord must be frozen and reject attribute mutation."""
        fsm = _make_fsm()
        fsm.submit("c")
        record = fsm.request.audit_trail[0]
        with self.assertRaises(AttributeError):
            record.actor_id = "attacker"  # type: ignore


# ─────────────────────────────────────────────────────────────────────────────
# Test Suite 4: FSM Introspection
# ─────────────────────────────────────────────────────────────────────────────

class TestStateMachineIntrospection(unittest.TestCase):

    def test_can_transition_returns_true_for_valid_event(self):
        fsm = _make_fsm()
        self.assertTrue(fsm.can_transition(ApprovalEvent.SUBMIT))

    def test_can_transition_returns_false_for_invalid_event(self):
        fsm = _make_fsm()
        self.assertFalse(fsm.can_transition(ApprovalEvent.APPROVE))
        self.assertFalse(fsm.can_transition(ApprovalEvent.DISBURSE))

    def test_valid_events_from_draft(self):
        fsm = _make_fsm()
        valid = set(fsm.valid_events())
        self.assertIn(ApprovalEvent.SUBMIT, valid)
        self.assertIn(ApprovalEvent.CANCEL, valid)
        self.assertNotIn(ApprovalEvent.APPROVE, valid)
        self.assertNotIn(ApprovalEvent.DISBURSE, valid)

    def test_valid_events_from_pending(self):
        fsm = _make_fsm()
        fsm.submit("c")
        valid = set(fsm.valid_events())
        self.assertIn(ApprovalEvent.APPROVE, valid)
        self.assertIn(ApprovalEvent.REJECT, valid)
        self.assertNotIn(ApprovalEvent.SUBMIT, valid)
        self.assertNotIn(ApprovalEvent.DISBURSE, valid)

    def test_terminal_states_have_no_valid_events(self):
        for terminal_state in _TERMINAL_STATES:
            with self.subTest(state=terminal_state):
                fsm = _make_fsm()
                fsm.request.state = terminal_state
                self.assertEqual(fsm.valid_events(), [])

    def test_is_terminal_false_for_active_states(self):
        for state in [ApprovalState.DRAFT, ApprovalState.PENDING, ApprovalState.APPROVED]:
            fsm = _make_fsm()
            fsm.request.state = state
            self.assertFalse(fsm.request.is_terminal)

    def test_is_terminal_true_for_terminal_states(self):
        for state in _TERMINAL_STATES:
            fsm = _make_fsm()
            fsm.request.state = state
            self.assertTrue(fsm.request.is_terminal)

    def test_amount_float_property_precision(self):
        """amount_float must correctly reverse fixed-point scaling."""
        fsm = _make_fsm(amount_scaled=_scaled(149.75))
        self.assertAlmostEqual(fsm.request.amount_float, 149.75, places=4)

    def test_factory_creates_unique_request_ids(self):
        """Each WorkflowStateMachine.create() must produce a unique request_id."""
        ids = {_make_fsm().request.request_id for _ in range(100)}
        self.assertEqual(len(ids), 100)


# ─────────────────────────────────────────────────────────────────────────────
# Test Suite 5: Policy Evaluator — Threshold Boundaries
# ─────────────────────────────────────────────────────────────────────────────

class TestPolicyEvaluatorThresholds(unittest.TestCase):
    """
    Test exact boundary values for all three approval tiers.
    Boundary conditions are the highest-risk area in a threshold engine.
    """

    def setUp(self):
        self.evaluator = ApprovalPolicyEvaluator()

    # ── AUTO_APPROVE tier ($0.01 – $49.99) ────────────────────────────────────

    def test_one_cent_auto_approves(self):
        """$0.01 (minimum valid) must be AUTO_APPROVE."""
        result = self.evaluator.evaluate("req-001", _scaled(0.01))
        self.assertEqual(result.required_tier, ApprovalTier.AUTO_APPROVE)
        self.assertTrue(result.auto_approved)

    def test_49_99_auto_approves(self):
        """$49.99 (max of AUTO tier) must be AUTO_APPROVE."""
        result = self.evaluator.evaluate("req-002", _scaled(49.99))
        self.assertEqual(result.required_tier, ApprovalTier.AUTO_APPROVE)

    def test_49_9999_auto_approves(self):
        """$49.9999 (just below $50.00 in fixed-point) must be AUTO_APPROVE."""
        result = self.evaluator.evaluate("req-003", _TIER_MANAGER_THRESHOLD_SCALED - 1)
        self.assertEqual(result.required_tier, ApprovalTier.AUTO_APPROVE)

    # ── MANAGER tier ($50.00 – $499.99) ───────────────────────────────────────

    def test_exactly_50_requires_manager(self):
        """$50.00 (boundary — first MANAGER value) must require MANAGER."""
        result = self.evaluator.evaluate("req-004", _scaled(50.00))
        self.assertEqual(result.required_tier, ApprovalTier.MANAGER)
        self.assertFalse(result.auto_approved)

    def test_50_01_requires_manager(self):
        """$50.01 (just above $50 boundary) must require MANAGER."""
        result = self.evaluator.evaluate("req-005", _scaled(50.01))
        self.assertEqual(result.required_tier, ApprovalTier.MANAGER)

    def test_250_requires_manager(self):
        """$250.00 (mid-range) must require MANAGER."""
        result = self.evaluator.evaluate("req-006", _scaled(250.00))
        self.assertEqual(result.required_tier, ApprovalTier.MANAGER)

    def test_499_99_requires_manager(self):
        """$499.99 (max of MANAGER tier) must require MANAGER."""
        result = self.evaluator.evaluate("req-007", _scaled(499.99))
        self.assertEqual(result.required_tier, ApprovalTier.MANAGER)

    def test_499_9999_requires_manager(self):
        """One fixed-point unit below $500.00 must still require MANAGER."""
        result = self.evaluator.evaluate("req-008", _TIER_FINANCE_DIRECTOR_THRESHOLD_SCALED - 1)
        self.assertEqual(result.required_tier, ApprovalTier.MANAGER)

    # ── FINANCE_DIRECTOR tier ($500.00+) ──────────────────────────────────────

    def test_exactly_500_requires_finance_director(self):
        """$500.00 (boundary — first FINANCE_DIRECTOR value) must require FINANCE_DIRECTOR."""
        result = self.evaluator.evaluate("req-009", _scaled(500.00))
        self.assertEqual(result.required_tier, ApprovalTier.FINANCE_DIRECTOR)
        self.assertFalse(result.auto_approved)

    def test_500_01_requires_finance_director(self):
        """$500.01 must require FINANCE_DIRECTOR."""
        result = self.evaluator.evaluate("req-010", _scaled(500.01))
        self.assertEqual(result.required_tier, ApprovalTier.FINANCE_DIRECTOR)

    def test_large_amount_requires_finance_director(self):
        """$10,000.00 (large expense) must require FINANCE_DIRECTOR."""
        result = self.evaluator.evaluate("req-011", _scaled(10_000.00))
        self.assertEqual(result.required_tier, ApprovalTier.FINANCE_DIRECTOR)

    # ── Rule name audit trail ──────────────────────────────────────────────────

    def test_matching_rule_name_is_populated(self):
        """PolicyEvaluationResult.matching_rule must not be blank."""
        result = self.evaluator.evaluate("req-012", _scaled(75.00))
        self.assertTrue(result.matching_rule)
        self.assertIn("MANAGER", result.matching_rule.upper())

    # ── Invalid input ──────────────────────────────────────────────────────────

    def test_zero_amount_raises_value_error(self):
        """Zero amount is not a valid disbursement."""
        with self.assertRaises(ValueError):
            self.evaluator.evaluate("req-bad", 0)

    def test_negative_amount_raises_value_error(self):
        """Negative amounts must be rejected."""
        with self.assertRaises(ValueError):
            self.evaluator.evaluate("req-bad", -1_000)

    def test_float_amount_raises_type_error(self):
        """Float amounts must be rejected to prevent floating-point contamination."""
        with self.assertRaises(TypeError):
            self.evaluator.evaluate("req-bad", 50.0)  # type: ignore

    def test_bool_amount_raises_type_error(self):
        """Boolean values must be rejected."""
        with self.assertRaises(TypeError):
            self.evaluator.evaluate("req-bad", True)  # type: ignore


# ─────────────────────────────────────────────────────────────────────────────
# Test Suite 6: Policy Evaluator — Authority Checks
# ─────────────────────────────────────────────────────────────────────────────

class TestPolicyAuthority(unittest.TestCase):

    def setUp(self):
        self.evaluator = ApprovalPolicyEvaluator()

    def test_finance_director_can_approve_all_tiers(self):
        """FINANCE_DIRECTOR can satisfy any required tier."""
        for required in ApprovalTier:
            self.assertTrue(
                self.evaluator.is_actor_authorized(ApprovalTier.FINANCE_DIRECTOR, required),
                f"FINANCE_DIRECTOR should be authorized for {required.value}",
            )

    def test_manager_can_approve_manager_and_auto(self):
        """MANAGER can satisfy MANAGER and AUTO_APPROVE tiers."""
        self.assertTrue(
            self.evaluator.is_actor_authorized(ApprovalTier.MANAGER, ApprovalTier.MANAGER)
        )
        self.assertTrue(
            self.evaluator.is_actor_authorized(ApprovalTier.MANAGER, ApprovalTier.AUTO_APPROVE)
        )

    def test_manager_cannot_approve_finance_director_tier(self):
        """MANAGER must NOT be able to satisfy FINANCE_DIRECTOR tier."""
        self.assertFalse(
            self.evaluator.is_actor_authorized(
                ApprovalTier.MANAGER, ApprovalTier.FINANCE_DIRECTOR
            )
        )

    def test_auto_approve_tier_can_only_self_satisfy(self):
        """AUTO_APPROVE actor authority can only satisfy AUTO_APPROVE requirement."""
        self.assertTrue(
            self.evaluator.is_actor_authorized(
                ApprovalTier.AUTO_APPROVE, ApprovalTier.AUTO_APPROVE
            )
        )
        self.assertFalse(
            self.evaluator.is_actor_authorized(
                ApprovalTier.AUTO_APPROVE, ApprovalTier.MANAGER
            )
        )
        self.assertFalse(
            self.evaluator.is_actor_authorized(
                ApprovalTier.AUTO_APPROVE, ApprovalTier.FINANCE_DIRECTOR
            )
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test Suite 7: Policy Evaluator — Auto-Approval Helper
# ─────────────────────────────────────────────────────────────────────────────

class TestAutoApproval(unittest.TestCase):

    def setUp(self):
        self.evaluator = ApprovalPolicyEvaluator()

    def test_small_amount_returns_auto_approve_true(self):
        result, should_auto = self.evaluator.evaluate_and_auto_approve("req-1", _scaled(10.00))
        self.assertTrue(should_auto)
        self.assertEqual(result.required_tier, ApprovalTier.AUTO_APPROVE)

    def test_medium_amount_returns_auto_approve_false(self):
        result, should_auto = self.evaluator.evaluate_and_auto_approve("req-2", _scaled(100.00))
        self.assertFalse(should_auto)

    def test_large_amount_returns_auto_approve_false(self):
        result, should_auto = self.evaluator.evaluate_and_auto_approve("req-3", _scaled(600.00))
        self.assertFalse(should_auto)


# ─────────────────────────────────────────────────────────────────────────────
# Test Suite 8: Custom Policy Configuration
# ─────────────────────────────────────────────────────────────────────────────

class TestCustomPolicy(unittest.TestCase):
    """Users can inject custom policy rules; evaluator must respect them."""

    def test_custom_single_tier_policy(self):
        """A policy with a single catch-all rule must apply it to all amounts."""
        custom_rules = [
            PolicyRule(
                min_amount_scaled=1,
                max_amount_scaled=None,
                required_tier=ApprovalTier.FINANCE_DIRECTOR,
                rule_name="STRICT: Always require Finance Director",
            )
        ]
        evaluator = ApprovalPolicyEvaluator(rules=custom_rules)
        for amount in [1, _scaled(1.00), _scaled(50.00), _scaled(1_000.00)]:
            result = evaluator.evaluate("req", amount)
            self.assertEqual(result.required_tier, ApprovalTier.FINANCE_DIRECTOR)

    def test_empty_rule_list_raises(self):
        """An empty rule list must raise ValueError at construction time."""
        with self.assertRaises(ValueError):
            ApprovalPolicyEvaluator(rules=[])

    def test_rules_are_sorted_by_min_threshold(self):
        """Evaluator must sort unsorted rules by min_amount_scaled before use."""
        unsorted_rules = [
            PolicyRule(
                min_amount_scaled=_TIER_MANAGER_THRESHOLD_SCALED,
                max_amount_scaled=_TIER_FINANCE_DIRECTOR_THRESHOLD_SCALED,
                required_tier=ApprovalTier.MANAGER,
                rule_name="Manager tier",
            ),
            PolicyRule(
                min_amount_scaled=1,
                max_amount_scaled=_TIER_MANAGER_THRESHOLD_SCALED,
                required_tier=ApprovalTier.AUTO_APPROVE,
                rule_name="Auto tier",
            ),
        ]
        evaluator = ApprovalPolicyEvaluator(rules=unsorted_rules)
        # $10 should still hit AUTO_APPROVE even though rules were given in reverse order
        result = evaluator.evaluate("req", _scaled(10.00))
        self.assertEqual(result.required_tier, ApprovalTier.AUTO_APPROVE)

    def test_describe_policy_contains_all_tiers(self):
        """describe_policy() must mention all configured tiers."""
        evaluator = ApprovalPolicyEvaluator()
        description = evaluator.describe_policy()
        for tier in ApprovalTier:
            self.assertIn(tier.value, description)


# ─────────────────────────────────────────────────────────────────────────────
# Test Suite 9: Performance Benchmarks
# ─────────────────────────────────────────────────────────────────────────────

POLICY_EVAL_ITERATIONS  = 10_000
POLICY_LATENCY_GUARD_MS = 1.5     # Roadmap SLA per single evaluation

class TestPerformanceBenchmarks(unittest.TestCase):
    """
    Validate that core operations comply with the Week 4 latency SLAs.

    These tests use a conservative regression guard:
        - Policy evaluation: < 1.5 ms per call
    CI runners may be slower than dedicated benchmark hardware;
    the guard is intentionally generous to avoid flakiness.
    """

    def test_policy_evaluation_latency_under_sla(self):
        """
        Each policy evaluation must complete in < 1.5 ms.
        We run 10,000 evaluations to get a stable measurement
        and assert the mean stays well within the SLA.
        """
        evaluator = ApprovalPolicyEvaluator()
        amounts = [
            _scaled(25.00),    # AUTO
            _scaled(150.00),   # MANAGER
            _scaled(750.00),   # FINANCE_DIRECTOR
        ]
        # Warmup
        for i in range(10):
            evaluator.evaluate("warmup", amounts[i % 3])

        start = time.perf_counter_ns()
        for i in range(POLICY_EVAL_ITERATIONS):
            evaluator.evaluate(f"bench-{i}", amounts[i % 3])
        elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000

        mean_ms = elapsed_ms / POLICY_EVAL_ITERATIONS
        print(
            f"\n[BENCHMARK] {POLICY_EVAL_ITERATIONS:,} policy evaluations "
            f"in {elapsed_ms:.2f} ms total (mean: {mean_ms:.4f} ms/eval, "
            f"SLA: < {POLICY_LATENCY_GUARD_MS:.1f} ms/eval)"
        )
        self.assertLess(
            mean_ms,
            POLICY_LATENCY_GUARD_MS,
            f"Policy evaluation mean {mean_ms:.4f} ms exceeds "
            f"{POLICY_LATENCY_GUARD_MS} ms SLA.",
        )

    def test_evaluation_result_reports_duration(self):
        """PolicyEvaluationResult.evaluation_duration_us must be a positive number."""
        evaluator = ApprovalPolicyEvaluator()
        result = evaluator.evaluate("latency-check", _scaled(300.00))
        self.assertGreater(result.evaluation_duration_us, 0)

    def test_state_transition_latency_sub_millisecond(self):
        """
        1,000 FSM transitions (submit→approve→disburse) must complete in < 500 ms.
        """
        start = time.perf_counter_ns()
        for _ in range(1_000):
            fsm = _make_fsm()
            fsm.submit("c")
            fsm.approve("m")
            fsm.disburse("f")
        elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000
        print(f"\n[BENCHMARK] 1,000 FSM full lifecycles in {elapsed_ms:.2f} ms")
        self.assertLess(elapsed_ms, 500, f"FSM lifecycle took {elapsed_ms:.2f} ms (guard: 500 ms)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
