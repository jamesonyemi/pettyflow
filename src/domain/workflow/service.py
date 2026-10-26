"""
PettyFlow Approval Workflow Service
Thread-safe, tenant-isolated domain service managing disbursement approval requests,
policy-based tier evaluation, state machine transitions, and real-time event broadcasting.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, UTC
from threading import RLock
from typing import Any, Callable, Dict, List, Optional, Tuple
import uuid

from src.domain.workflow.policy_evaluator import (
    ApprovalPolicyEvaluator,
    ApprovalTier,
    DEFAULT_PETTYFLOW_POLICY,
    PolicyEvaluationResult,
)
from src.domain.workflow.state_machine import (
    ApprovalRequest,
    ApprovalState,
    ApprovalWorkflowException,
    WorkflowStateMachine,
)


class ApprovalRequestNotFoundError(ApprovalWorkflowException):
    """Raised when an approval request cannot be found within tenant scope."""


class MandatoryAuditReasoningMissingError(ApprovalWorkflowException):
    """Raised when an approver or rejector does not provide mandatory audit reasoning."""


class InsufficientApproverAuthorityError(ApprovalWorkflowException):
    """Raised when an actor lacks the required authorization tier to approve a request."""


@dataclass
class WorkflowEvent:
    """Event payload broadcast when a request changes state or is created."""
    event_type: str
    tenant_id: str
    request_id: str
    state: str
    tier: str
    amount_scaled: int
    actor_id: str
    notes: Optional[str]
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    payload: Dict[str, Any] = field(default_factory=dict)


class ApprovalWorkflowService:
    """
    Thread-safe approval workflow orchestrator.
    Manages in-memory tenant-isolated requests, executes policy checks,
    advances the finite state machine, and notifies subscribers.
    """

    def __init__(
        self,
        policy_evaluator: Optional[ApprovalPolicyEvaluator] = None,
    ) -> None:
        self._policy_evaluator = policy_evaluator or ApprovalPolicyEvaluator(
            rules=DEFAULT_PETTYFLOW_POLICY
        )
        # Store requests keyed by (tenant_id, request_id)
        self._requests: Dict[Tuple[str, str], ApprovalRequest] = {}
        # Additional metadata store for receipt details, merchant info, etc.
        self._metadata: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self._subscribers: List[Callable[[WorkflowEvent], Any]] = []
        self._lock = RLock()

    def subscribe(self, callback: Callable[[WorkflowEvent], Any]) -> None:
        """Register an event listener for real-time live feed updates."""
        with self._lock:
            if callback not in self._subscribers:
                self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[WorkflowEvent], Any]) -> None:
        """Unregister an event listener."""
        with self._lock:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

    def _broadcast(self, event: WorkflowEvent) -> None:
        """Deliver event to all registered listeners without holding internal lock."""
        listeners: List[Callable[[WorkflowEvent], Any]]
        with self._lock:
            listeners = list(self._subscribers)

        for listener in listeners:
            try:
                res = listener(event)
                if asyncio.iscoroutine(res):
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(res)
                    except RuntimeError:
                        asyncio.run(res)
            except Exception:
                # Never allow listener failures to abort core workflow transitions
                pass

    def submit_request(
        self,
        tenant_id: str,
        custodian_id: str,
        amount_scaled: int,
        currency: str,
        description: str,
        receipt_metadata: Optional[Dict[str, Any]] = None,
        auto_approve_if_eligible: bool = True,
        initial_notes: Optional[str] = None,
    ) -> Tuple[ApprovalRequest, PolicyEvaluationResult]:
        """
        Create and submit a disbursement request for policy evaluation and approval.

        Args:
            tenant_id: Unique identifier of the tenant.
            custodian_id: ID of the submitting branch custodian.
            amount_scaled: Fixed-point 64-bit integer amount (scaled x10,000).
            currency: ISO-4217 currency code (e.g. 'USD').
            description: Justification for expenditure.
            receipt_metadata: Optional structured OCR extraction or receipt details.
            auto_approve_if_eligible: If True, requests matching AUTO_APPROVE tier
                                     are transitioned to APPROVED immediately.
            initial_notes: Optional notes on submission.

        Returns:
            Tuple of (ApprovalRequest, PolicyEvaluationResult).
        """
        if not tenant_id or not str(tenant_id).strip():
            raise ValueError("tenant_id must not be blank")
        if not custodian_id or not str(custodian_id).strip():
            raise ValueError("custodian_id must not be blank")
        if not isinstance(amount_scaled, int) or isinstance(amount_scaled, bool):
            raise TypeError("amount_scaled must be an integer fixed-point value")
        if amount_scaled <= 0:
            raise ValueError("amount_scaled must be strictly positive")
        if not currency or len(currency.strip()) != 3:
            raise ValueError("currency must be a valid 3-letter ISO code")
        if not description or not description.strip():
            raise ValueError("description must not be blank")

        clean_currency = currency.strip().upper()
        clean_tenant = str(tenant_id).strip()
        clean_custodian = str(custodian_id).strip()
        clean_description = description.strip()
        request_id = str(uuid.uuid4())

        # Evaluate policy tier
        eval_result = self._policy_evaluator.evaluate(
            request_id=request_id,
            amount_scaled=amount_scaled,
        )

        with self._lock:
            # Create DRAFT request
            request = ApprovalRequest(
                request_id=request_id,
                tenant_id=clean_tenant,
                custodian_id=clean_custodian,
                amount_scaled=amount_scaled,
                currency=clean_currency,
                description=clean_description,
                required_approval_tier=eval_result.required_tier.value,
                state=ApprovalState.DRAFT,
            )
            sm = WorkflowStateMachine(request)

            # Custodian submits request -> advances to PENDING
            submit_notes = initial_notes or f"Submitted by custodian. Required tier: {eval_result.required_tier.value}"
            sm.submit(actor_id=clean_custodian, notes=submit_notes)

            # Auto-approve if eligible by policy
            is_auto_approved = False
            if eval_result.auto_approved and auto_approve_if_eligible:
                sm.approve(
                    actor_id="SYSTEM_AUTO_APPROVER",
                    notes=f"Auto-approved by policy rule: {eval_result.matching_rule}",
                )
                is_auto_approved = True

            self._requests[(clean_tenant, request_id)] = request
            if receipt_metadata:
                self._metadata[(clean_tenant, request_id)] = dict(receipt_metadata)

        # Broadcast event
        event_type = "REQUEST_AUTO_APPROVED" if is_auto_approved else "REQUEST_SUBMITTED"
        event = WorkflowEvent(
            event_type=event_type,
            tenant_id=clean_tenant,
            request_id=request_id,
            state=request.state.value,
            tier=request.required_approval_tier,
            amount_scaled=amount_scaled,
            actor_id=clean_custodian if not is_auto_approved else "SYSTEM_AUTO_APPROVER",
            notes=submit_notes,
            payload={
                "currency": clean_currency,
                "description": clean_description,
                "auto_approved": is_auto_approved,
                "policy_rule": eval_result.matching_rule,
                "receipt": receipt_metadata or {},
            },
        )
        self._broadcast(event)

        return request, eval_result

    def approve_request(
        self,
        tenant_id: str,
        request_id: str,
        actor_id: str,
        notes: str,
        actor_role: Optional[str] = None,
    ) -> ApprovalRequest:
        """
        Approve a pending disbursement request.
        Mandatory audit reasoning: 'notes' MUST be provided.
        """
        clean_tenant = str(tenant_id).strip()
        clean_request_id = str(request_id).strip()
        clean_actor = str(actor_id).strip()
        clean_notes = (notes or "").strip()

        if not clean_actor:
            raise ValueError("actor_id must not be blank")
        if not clean_notes:
            raise MandatoryAuditReasoningMissingError(
                "Mandatory audit reasoning is required to approve a disbursement request."
            )

        with self._lock:
            key = (clean_tenant, clean_request_id)
            if key not in self._requests:
                raise ApprovalRequestNotFoundError(
                    f"Approval request '{clean_request_id}' not found for tenant '{clean_tenant}'"
                )
            request = self._requests[key]

            # Verify actor authority tier if role provided
            if actor_role:
                self._verify_role_authority(request.required_approval_tier, actor_role, clean_actor)

            sm = WorkflowStateMachine(request)
            sm.approve(actor_id=clean_actor, notes=clean_notes)
            request.updated_at = datetime.now(UTC)

        event = WorkflowEvent(
            event_type="REQUEST_APPROVED",
            tenant_id=clean_tenant,
            request_id=clean_request_id,
            state=request.state.value,
            tier=request.required_approval_tier,
            amount_scaled=request.amount_scaled,
            actor_id=clean_actor,
            notes=clean_notes,
            payload={"actor_role": actor_role or ""},
        )
        self._broadcast(event)
        return request

    def reject_request(
        self,
        tenant_id: str,
        request_id: str,
        actor_id: str,
        notes: str,
        actor_role: Optional[str] = None,
    ) -> ApprovalRequest:
        """
        Reject a pending disbursement request.
        Mandatory audit reasoning: 'notes' MUST be provided.
        """
        clean_tenant = str(tenant_id).strip()
        clean_request_id = str(request_id).strip()
        clean_actor = str(actor_id).strip()
        clean_notes = (notes or "").strip()

        if not clean_actor:
            raise ValueError("actor_id must not be blank")
        if not clean_notes:
            raise MandatoryAuditReasoningMissingError(
                "Mandatory audit reasoning is required to reject a disbursement request."
            )

        with self._lock:
            key = (clean_tenant, clean_request_id)
            if key not in self._requests:
                raise ApprovalRequestNotFoundError(
                    f"Approval request '{clean_request_id}' not found for tenant '{clean_tenant}'"
                )
            request = self._requests[key]

            sm = WorkflowStateMachine(request)
            sm.reject(actor_id=clean_actor, notes=clean_notes)
            request.updated_at = datetime.now(UTC)

        event = WorkflowEvent(
            event_type="REQUEST_REJECTED",
            tenant_id=clean_tenant,
            request_id=clean_request_id,
            state=request.state.value,
            tier=request.required_approval_tier,
            amount_scaled=request.amount_scaled,
            actor_id=clean_actor,
            notes=clean_notes,
            payload={"actor_role": actor_role or ""},
        )
        self._broadcast(event)
        return request

    def disburse_request(
        self,
        tenant_id: str,
        request_id: str,
        actor_id: str,
        notes: Optional[str] = None,
    ) -> ApprovalRequest:
        """Disburse funds for an already approved request."""
        clean_tenant = str(tenant_id).strip()
        clean_request_id = str(request_id).strip()
        clean_actor = str(actor_id).strip()
        clean_notes = (notes or "Funds disbursed by finance custodian").strip()

        if not clean_actor:
            raise ValueError("actor_id must not be blank")

        with self._lock:
            key = (clean_tenant, clean_request_id)
            if key not in self._requests:
                raise ApprovalRequestNotFoundError(
                    f"Approval request '{clean_request_id}' not found for tenant '{clean_tenant}'"
                )
            request = self._requests[key]

            sm = WorkflowStateMachine(request)
            sm.disburse(actor_id=clean_actor, notes=clean_notes)
            request.updated_at = datetime.now(UTC)

        event = WorkflowEvent(
            event_type="REQUEST_DISBURSED",
            tenant_id=clean_tenant,
            request_id=clean_request_id,
            state=request.state.value,
            tier=request.required_approval_tier,
            amount_scaled=request.amount_scaled,
            actor_id=clean_actor,
            notes=clean_notes,
        )
        self._broadcast(event)
        return request

    def get_request(self, tenant_id: str, request_id: str) -> Tuple[ApprovalRequest, Dict[str, Any]]:
        """Retrieve a specific request along with its receipt metadata."""
        clean_tenant = str(tenant_id).strip()
        clean_request_id = str(request_id).strip()

        with self._lock:
            key = (clean_tenant, clean_request_id)
            if key not in self._requests:
                raise ApprovalRequestNotFoundError(
                    f"Approval request '{clean_request_id}' not found for tenant '{clean_tenant}'"
                )
            request = self._requests[key]
            meta = self._metadata.get(key, {})
            return request, dict(meta)

    def list_requests(
        self,
        tenant_id: str,
        state: Optional[ApprovalState] = None,
        tier: Optional[ApprovalTier] = None,
        custodian_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Tuple[ApprovalRequest, Dict[str, Any]]]:
        """
        List requests matching criteria within tenant scope.
        Returns list of (ApprovalRequest, metadata) ordered by creation time DESC.
        Target retrieval latency < 50 microseconds in memory.
        """
        clean_tenant = str(tenant_id).strip()

        with self._lock:
            matched: List[Tuple[ApprovalRequest, Dict[str, Any]]] = []
            for (t_id, r_id), req in self._requests.items():
                if t_id != clean_tenant:
                    continue
                if state is not None and req.state != state:
                    continue
                if tier is not None and req.required_approval_tier != tier.value:
                    continue
                if custodian_id is not None and req.custodian_id != custodian_id:
                    continue
                meta = self._metadata.get((t_id, r_id), {})
                matched.append((req, meta))

        # Order descending by creation time
        matched.sort(key=lambda item: item[0].created_at, reverse=True)
        return matched[offset:offset + limit]

    def get_summary_stats(self, tenant_id: str) -> Dict[str, Any]:
        """Aggregate real-time metrics for management dashboard KPI cards."""
        clean_tenant = str(tenant_id).strip()

        with self._lock:
            total_count = 0
            pending_count = 0
            approved_count = 0
            rejected_count = 0
            disbursed_count = 0
            tier_1_auto_count = 0
            tier_2_manager_count = 0
            tier_3_cfo_count = 0
            total_pending_scaled = 0
            total_approved_scaled = 0

            for (t_id, _), req in self._requests.items():
                if t_id != clean_tenant:
                    continue
                total_count += 1
                if req.state == ApprovalState.PENDING:
                    pending_count += 1
                    total_pending_scaled += req.amount_scaled
                    if req.required_approval_tier == ApprovalTier.MANAGER.value:
                        tier_2_manager_count += 1
                    elif req.required_approval_tier == ApprovalTier.FINANCE_DIRECTOR.value:
                        tier_3_cfo_count += 1
                elif req.state == ApprovalState.APPROVED:
                    approved_count += 1
                    total_approved_scaled += req.amount_scaled
                elif req.state == ApprovalState.REJECTED:
                    rejected_count += 1
                elif req.state == ApprovalState.DISBURSED:
                    disbursed_count += 1

                if req.required_approval_tier == ApprovalTier.AUTO_APPROVE.value:
                    tier_1_auto_count += 1

        return {
            "tenant_id": clean_tenant,
            "total_requests": total_count,
            "pending_count": pending_count,
            "approved_count": approved_count,
            "rejected_count": rejected_count,
            "disbursed_count": disbursed_count,
            "tier_1_auto_count": tier_1_auto_count,
            "tier_2_manager_count": tier_2_manager_count,
            "tier_3_cfo_count": tier_3_cfo_count,
            "total_pending_scaled": total_pending_scaled,
            "total_pending_formatted": f"${total_pending_scaled / 10_000:,.2f}",
            "total_approved_scaled": total_approved_scaled,
            "total_approved_formatted": f"${total_approved_scaled / 10_000:,.2f}",
        }

    @staticmethod
    def _verify_role_authority(required_tier: str, actor_role: str, actor_id: str) -> None:
        """Verify actor role is sufficient for the required tier."""
        role_normalized = actor_role.strip().upper()
        # Higher role covers lower tiers
        tier_hierarchy = {
            ApprovalTier.AUTO_APPROVE.value: 0,
            ApprovalTier.MANAGER.value: 1,
            ApprovalTier.FINANCE_DIRECTOR.value: 2,
        }
        role_tier_map = {
            "AUTO": 0,
            "MANAGER": 1,
            "LINE_MANAGER": 1,
            "FINANCE_MANAGER": 1,
            "FINANCE_DIRECTOR": 2,
            "CFO": 2,
            "DIRECTOR": 2,
            "ADMIN": 2,
        }
        required_level = tier_hierarchy.get(required_tier, 1)
        actor_level = role_tier_map.get(role_normalized, 0)
        if actor_level < required_level:
            raise InsufficientApproverAuthorityError(
                f"Actor '{actor_id}' with role '{actor_role}' lacks authority for required tier '{required_tier}'"
            )
