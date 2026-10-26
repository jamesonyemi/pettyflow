"""
Unit tests for PettyFlow Approval Workflow Service.
Tests policy evaluation, state machine integration, mandatory audit reasoning,
role authority checking, and event broadcasting.
"""

import pytest

from src.domain.workflow.policy_evaluator import ApprovalTier
from src.domain.workflow.service import (
    ApprovalRequestNotFoundError,
    ApprovalWorkflowService,
    InsufficientApproverAuthorityError,
    MandatoryAuditReasoningMissingError,
    WorkflowEvent,
)
from src.domain.workflow.state_machine import (
    ApprovalState,
    InvalidStateTransitionException,
)


@pytest.fixture
def workflow_service() -> ApprovalWorkflowService:
    return ApprovalWorkflowService()


class TestApprovalWorkflowService:

    def test_submit_tier_1_auto_approves(self, workflow_service: ApprovalWorkflowService):
        """Tier 1 (< $50.00) should automatically transition to APPROVED."""
        req, eval_res = workflow_service.submit_request(
            tenant_id="TENANT_01",
            custodian_id="CUSTODIAN_01",
            amount_scaled=250_000,  # $25.00
            currency="USD",
            description="Branch coffee supplies",
            auto_approve_if_eligible=True,
        )

        assert req.state == ApprovalState.APPROVED
        assert req.required_approval_tier == ApprovalTier.AUTO_APPROVE.value
        assert eval_res.auto_approved is True
        assert len(req.audit_trail) == 2  # SUBMIT then APPROVE
        assert req.audit_trail[-1].actor_id == "SYSTEM_AUTO_APPROVER"

    def test_submit_tier_2_stays_pending(self, workflow_service: ApprovalWorkflowService):
        """Tier 2 ($50.00 - $499.99) requires Manager approval and remains PENDING."""
        req, eval_res = workflow_service.submit_request(
            tenant_id="TENANT_01",
            custodian_id="CUSTODIAN_01",
            amount_scaled=1_500_000,  # $150.00
            currency="USD",
            description="Printer paper and toner cartridges",
            auto_approve_if_eligible=True,
        )

        assert req.state == ApprovalState.PENDING
        assert req.required_approval_tier == ApprovalTier.MANAGER.value
        assert eval_res.auto_approved is False
        assert len(req.audit_trail) == 1
        assert req.audit_trail[0].actor_id == "CUSTODIAN_01"

    def test_submit_tier_3_cfo_stays_pending(self, workflow_service: ApprovalWorkflowService):
        """Tier 3 (>= $500.00) requires Finance Director / CFO approval."""
        req, eval_res = workflow_service.submit_request(
            tenant_id="TENANT_01",
            custodian_id="CUSTODIAN_01",
            amount_scaled=7_500_000,  # $750.00
            currency="USD",
            description="Emergency HVAC repair technician payment",
            auto_approve_if_eligible=True,
        )

        assert req.state == ApprovalState.PENDING
        assert req.required_approval_tier == ApprovalTier.FINANCE_DIRECTOR.value
        assert eval_res.auto_approved is False

    def test_approve_request_with_mandatory_notes(self, workflow_service: ApprovalWorkflowService):
        """Approving a pending request with valid notes succeeds and records audit trail."""
        req, _ = workflow_service.submit_request(
            tenant_id="TENANT_01",
            custodian_id="CUSTODIAN_01",
            amount_scaled=1_200_000,  # $120.00
            currency="USD",
            description="Office stationery",
            auto_approve_if_eligible=False,
        )

        approved_req = workflow_service.approve_request(
            tenant_id="TENANT_01",
            request_id=req.request_id,
            actor_id="MGR_ALICE",
            notes="Verified itemized receipt against policy.",
            actor_role="MANAGER",
        )

        assert approved_req.state == ApprovalState.APPROVED
        last_rec = approved_req.audit_trail[-1]
        assert last_rec.actor_id == "MGR_ALICE"
        assert last_rec.notes == "Verified itemized receipt against policy."

    def test_mandatory_audit_reasoning_enforced_on_approve(self, workflow_service: ApprovalWorkflowService):
        """Approving without notes raises MandatoryAuditReasoningMissingError."""
        req, _ = workflow_service.submit_request(
            tenant_id="TENANT_01",
            custodian_id="CUSTODIAN_01",
            amount_scaled=1_000_000,
            currency="USD",
            description="Tools",
            auto_approve_if_eligible=False,
        )

        with pytest.raises(MandatoryAuditReasoningMissingError):
            workflow_service.approve_request(
                tenant_id="TENANT_01",
                request_id=req.request_id,
                actor_id="MGR_ALICE",
                notes="   ",  # Blank/whitespace
            )

    def test_mandatory_audit_reasoning_enforced_on_reject(self, workflow_service: ApprovalWorkflowService):
        """Rejecting without notes raises MandatoryAuditReasoningMissingError."""
        req, _ = workflow_service.submit_request(
            tenant_id="TENANT_01",
            custodian_id="CUSTODIAN_01",
            amount_scaled=1_000_000,
            currency="USD",
            description="Tools",
            auto_approve_if_eligible=False,
        )

        with pytest.raises(MandatoryAuditReasoningMissingError):
            workflow_service.reject_request(
                tenant_id="TENANT_01",
                request_id=req.request_id,
                actor_id="MGR_ALICE",
                notes="",
            )

    def test_reject_request_transitions_to_rejected(self, workflow_service: ApprovalWorkflowService):
        """Rejecting request transitions state to REJECTED."""
        req, _ = workflow_service.submit_request(
            tenant_id="TENANT_01",
            custodian_id="CUSTODIAN_01",
            amount_scaled=1_000_000,
            currency="USD",
            description="Lunch with vendor",
            auto_approve_if_eligible=False,
        )

        rejected = workflow_service.reject_request(
            tenant_id="TENANT_01",
            request_id=req.request_id,
            actor_id="MGR_BOB",
            notes="Entertainment expenses require prior written PO.",
        )

        assert rejected.state == ApprovalState.REJECTED

    def test_insufficient_authority_prevents_manager_approving_tier_3(
        self, workflow_service: ApprovalWorkflowService
    ):
        """A line manager role cannot approve a FINANCE_DIRECTOR required tier request."""
        req, _ = workflow_service.submit_request(
            tenant_id="TENANT_01",
            custodian_id="CUSTODIAN_01",
            amount_scaled=8_000_000,  # $800.00
            currency="USD",
            description="Emergency server hardware component",
            auto_approve_if_eligible=False,
        )

        with pytest.raises(InsufficientApproverAuthorityError):
            workflow_service.approve_request(
                tenant_id="TENANT_01",
                request_id=req.request_id,
                actor_id="MGR_CAROL",
                notes="Looks fine to me.",
                actor_role="MANAGER",
            )

        # But a CFO can approve it
        cfo_approved = workflow_service.approve_request(
            tenant_id="TENANT_01",
            request_id=req.request_id,
            actor_id="CFO_DAVE",
            notes="Executive approval granted for server emergency.",
            actor_role="CFO",
        )
        assert cfo_approved.state == ApprovalState.APPROVED

    def test_invalid_state_transition_prevented(self, workflow_service: ApprovalWorkflowService):
        """Terminal states like REJECTED cannot be approved."""
        req, _ = workflow_service.submit_request(
            tenant_id="TENANT_01",
            custodian_id="CUSTODIAN_01",
            amount_scaled=1_000_000,
            currency="USD",
            description="Testing terminal",
            auto_approve_if_eligible=False,
        )

        workflow_service.reject_request(
            tenant_id="TENANT_01",
            request_id=req.request_id,
            actor_id="MGR_ALICE",
            notes="Rejected.",
        )

        with pytest.raises(InvalidStateTransitionException):
            workflow_service.approve_request(
                tenant_id="TENANT_01",
                request_id=req.request_id,
                actor_id="MGR_ALICE",
                notes="Trying to approve after rejection.",
            )

    def test_subscriber_receives_broadcast_events(self, workflow_service: ApprovalWorkflowService):
        """Registered subscriber receives events upon submission and approval."""
        received_events: list[WorkflowEvent] = []

        def on_event(evt: WorkflowEvent):
            received_events.append(evt)

        workflow_service.subscribe(on_event)

        req, _ = workflow_service.submit_request(
            tenant_id="TENANT_01",
            custodian_id="CUSTODIAN_01",
            amount_scaled=600_000,  # $60.00 -> Tier 2
            currency="USD",
            description="Team postage",
            auto_approve_if_eligible=False,
        )

        assert len(received_events) == 1
        assert received_events[0].event_type == "REQUEST_SUBMITTED"

        workflow_service.approve_request(
            tenant_id="TENANT_01",
            request_id=req.request_id,
            actor_id="MGR_ALICE",
            notes="Approved postage expense.",
        )

        assert len(received_events) == 2
        assert received_events[1].event_type == "REQUEST_APPROVED"

    def test_tenant_isolation_scoping(self, workflow_service: ApprovalWorkflowService):
        """Requests from Tenant A cannot be queried or updated under Tenant B."""
        req, _ = workflow_service.submit_request(
            tenant_id="TENANT_ALPHA",
            custodian_id="CUST_01",
            amount_scaled=1_000_000,
            currency="USD",
            description="Alpha expense",
        )

        with pytest.raises(ApprovalRequestNotFoundError):
            workflow_service.get_request(tenant_id="TENANT_BETA", request_id=req.request_id)

        alpha_list = workflow_service.list_requests(tenant_id="TENANT_ALPHA")
        beta_list = workflow_service.list_requests(tenant_id="TENANT_BETA")
        assert len(alpha_list) == 1
        assert len(beta_list) == 0
