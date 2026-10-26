"""
PettyFlow Approval Workflow & Real-Time Management Dashboard REST & WebSocket Router.
Provides endpoints for expense submissions, multi-tier approval actions, queue queries,
and real-time live feed broadcasting for custodians and finance managers.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, UTC
import time
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field, field_validator

from src.domain.workflow.policy_evaluator import ApprovalTier
from src.domain.workflow.service import (
    ApprovalRequestNotFoundError,
    ApprovalWorkflowService,
    InsufficientApproverAuthorityError,
    MandatoryAuditReasoningMissingError,
    WorkflowEvent,
)
from src.domain.workflow.state_machine import (
    ApprovalRequest,
    ApprovalState,
    InvalidStateTransitionException,
)


# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------

class StateTransitionRecordSchema(BaseModel):
    transition_id: str
    request_id: str
    tenant_id: str
    from_state: str
    event: str
    to_state: str
    actor_id: str
    notes: Optional[str] = None
    timestamp: datetime


class ApprovalRequestResponse(BaseModel):
    request_id: str
    tenant_id: str
    custodian_id: str
    amount_scaled: int
    amount_formatted: str
    currency: str
    description: str
    required_approval_tier: str
    state: str
    created_at: datetime
    updated_at: datetime
    receipt_metadata: Dict[str, Any] = Field(default_factory=dict)
    audit_trail: List[StateTransitionRecordSchema] = Field(default_factory=list)


class PolicyEvaluationResponse(BaseModel):
    request_id: str
    amount_scaled: int
    amount_float: float
    required_tier: str
    matching_rule: str
    auto_approved: bool
    evaluation_duration_us: float


class SubmitApprovalRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=128)
    custodian_id: str = Field(min_length=1, max_length=128)
    amount_scaled: int = Field(gt=0, description="Fixed-point integer amount scaled x10,000")
    currency: str = Field(pattern=r"^[A-Za-z]{3}$")
    description: str = Field(min_length=1, max_length=1000)
    receipt_metadata: Optional[Dict[str, Any]] = None
    auto_approve_if_eligible: bool = True
    notes: Optional[str] = None

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, v: str) -> str:
        return v.upper()


class SubmitApprovalResponse(BaseModel):
    request: ApprovalRequestResponse
    policy_evaluation: PolicyEvaluationResponse


class ApprovalActionRequest(BaseModel):
    tenant_id: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    notes: str = Field(..., description="Mandatory audit reasoning")
    actor_role: Optional[str] = None

    @field_validator("notes")
    @classmethod
    def validate_mandatory_notes(cls, v: str) -> str:
        stripped = (v or "").strip()
        if not stripped:
            raise ValueError("Mandatory audit reasoning note is required.")
        return stripped


class DisburseActionRequest(BaseModel):
    tenant_id: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    notes: Optional[str] = None


class ApprovalQueueResponse(BaseModel):
    tenant_id: str
    total_matching: int
    retrieval_duration_ms: float
    requests: List[ApprovalRequestResponse]


class ApprovalSummaryStatsResponse(BaseModel):
    tenant_id: str
    total_requests: int
    pending_count: int
    approved_count: int
    rejected_count: int
    disbursed_count: int
    tier_1_auto_count: int
    tier_2_manager_count: int
    tier_3_cfo_count: int
    total_pending_scaled: int
    total_pending_formatted: str
    total_approved_scaled: int
    total_approved_formatted: str


# ---------------------------------------------------------------------------
# WebSocket Connection Manager
# ---------------------------------------------------------------------------

class ApprovalConnectionManager:
    """Manages active WebSocket connections for live feed broadcast."""

    def __init__(self) -> None:
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast_json(self, data: Dict[str, Any]) -> None:
        dead_connections: List[WebSocket] = []
        for connection in list(self.active_connections):
            try:
                await connection.send_json(data)
            except Exception:
                dead_connections.append(connection)

        for dead in dead_connections:
            self.disconnect(dead)


# ---------------------------------------------------------------------------
# Router Factory
# ---------------------------------------------------------------------------

def create_approval_router(
    service: Optional[ApprovalWorkflowService] = None,
) -> APIRouter:
    """
    Factory creating FastAPI Approval Workflow Router with WebSocket support.
    """
    router = APIRouter(prefix="/v1/approvals", tags=["approval-workflow"])
    workflow_svc = service or ApprovalWorkflowService()
    ws_manager = ApprovalConnectionManager()

    # Bridge domain workflow events into WebSocket broadcast
    def on_workflow_event(evt: WorkflowEvent) -> None:
        event_dict = {
            "type": "WORKFLOW_EVENT",
            "event_type": evt.event_type,
            "tenant_id": evt.tenant_id,
            "request_id": evt.request_id,
            "state": evt.state,
            "tier": evt.tier,
            "amount_scaled": evt.amount_scaled,
            "amount_formatted": f"${evt.amount_scaled / 10_000:,.2f}",
            "actor_id": evt.actor_id,
            "notes": evt.notes,
            "timestamp": evt.timestamp.isoformat(),
            "payload": evt.payload,
        }
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(ws_manager.broadcast_json(event_dict))
        except RuntimeError:
            pass

    workflow_svc.subscribe(on_workflow_event)

    def _to_response_dto(
        req: ApprovalRequest, meta: Optional[Dict[str, Any]] = None
    ) -> ApprovalRequestResponse:
        return ApprovalRequestResponse(
            request_id=req.request_id,
            tenant_id=req.tenant_id,
            custodian_id=req.custodian_id,
            amount_scaled=req.amount_scaled,
            amount_formatted=f"${req.amount_scaled / 10_000:,.2f}",
            currency=req.currency,
            description=req.description,
            required_approval_tier=req.required_approval_tier,
            state=req.state.value,
            created_at=req.created_at,
            updated_at=req.updated_at,
            receipt_metadata=meta or {},
            audit_trail=[
                StateTransitionRecordSchema(
                    transition_id=rec.transition_id,
                    request_id=rec.request_id,
                    tenant_id=rec.tenant_id,
                    from_state=rec.from_state.value,
                    event=rec.event.value,
                    to_state=rec.to_state.value,
                    actor_id=rec.actor_id,
                    notes=rec.notes,
                    timestamp=rec.timestamp,
                )
                for rec in req.audit_trail
            ],
        )

    # -----------------------------------------------------------------------
    # REST Endpoints
    # -----------------------------------------------------------------------

    @router.post(
        "/submit",
        response_model=SubmitApprovalResponse,
        status_code=status.HTTP_201_CREATED,
        summary="Submit an expense disbursement request for approval",
    )
    def submit_request(req_body: SubmitApprovalRequest) -> SubmitApprovalResponse:
        try:
            req, eval_res = workflow_svc.submit_request(
                tenant_id=req_body.tenant_id,
                custodian_id=req_body.custodian_id,
                amount_scaled=req_body.amount_scaled,
                currency=req_body.currency,
                description=req_body.description,
                receipt_metadata=req_body.receipt_metadata,
                auto_approve_if_eligible=req_body.auto_approve_if_eligible,
                initial_notes=req_body.notes,
            )
        except ValueError as err:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err

        dto = _to_response_dto(req, req_body.receipt_metadata)
        policy_dto = PolicyEvaluationResponse(
            request_id=eval_res.request_id,
            amount_scaled=eval_res.amount_scaled,
            amount_float=eval_res.amount_float,
            required_tier=eval_res.required_tier.value,
            matching_rule=eval_res.matching_rule,
            auto_approved=eval_res.auto_approved,
            evaluation_duration_us=eval_res.evaluation_duration_us,
        )
        return SubmitApprovalResponse(request=dto, policy_evaluation=policy_dto)

    @router.get(
        "",
        response_model=ApprovalQueueResponse,
        summary="Query approval queue inbox with tier and state filtering",
    )
    def list_approvals(
        tenant_id: str = Query(..., description="Tenant ID"),
        state: Optional[str] = Query(None, description="Filter by state (DRAFT, PENDING, APPROVED, REJECTED, DISBURSED)"),
        tier: Optional[str] = Query(None, description="Filter by tier (AUTO_APPROVE, MANAGER, FINANCE_DIRECTOR)"),
        custodian_id: Optional[str] = Query(None, description="Filter by custodian ID"),
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> ApprovalQueueResponse:
        t0 = time.perf_counter()

        parsed_state: Optional[ApprovalState] = None
        if state:
            try:
                parsed_state = ApprovalState(state.upper())
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid state '{state}'. Valid states: {[s.value for s in ApprovalState]}",
                )

        parsed_tier: Optional[ApprovalTier] = None
        if tier:
            try:
                parsed_tier = ApprovalTier(tier.upper())
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid tier '{tier}'. Valid tiers: {[t.value for t in ApprovalTier]}",
                )

        results = workflow_svc.list_requests(
            tenant_id=tenant_id,
            state=parsed_state,
            tier=parsed_tier,
            custodian_id=custodian_id,
            limit=limit,
            offset=offset,
        )

        dtos = [_to_response_dto(r, meta) for r, meta in results]
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        return ApprovalQueueResponse(
            tenant_id=tenant_id,
            total_matching=len(dtos),
            retrieval_duration_ms=round(elapsed_ms, 3),
            requests=dtos,
        )

    @router.get(
        "/summary",
        response_model=ApprovalSummaryStatsResponse,
        summary="Get aggregate summary statistics for the management dashboard",
    )
    def get_summary(tenant_id: str = Query(..., description="Tenant ID")) -> ApprovalSummaryStatsResponse:
        stats = workflow_svc.get_summary_stats(tenant_id=tenant_id)
        return ApprovalSummaryStatsResponse(**stats)

    @router.get(
        "/{request_id}",
        response_model=ApprovalRequestResponse,
        summary="Get request details and complete immutable audit trail",
    )
    def get_approval_details(
        request_id: str,
        tenant_id: str = Query(..., description="Tenant ID"),
    ) -> ApprovalRequestResponse:
        try:
            req, meta = workflow_svc.get_request(tenant_id=tenant_id, request_id=request_id)
        except ApprovalRequestNotFoundError as err:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err)) from err

        return _to_response_dto(req, meta)

    @router.post(
        "/{request_id}/approve",
        response_model=ApprovalRequestResponse,
        summary="1-Click approval with mandatory audit reasoning",
    )
    def approve_request(
        request_id: str,
        action: ApprovalActionRequest,
    ) -> ApprovalRequestResponse:
        try:
            req = workflow_svc.approve_request(
                tenant_id=action.tenant_id,
                request_id=request_id,
                actor_id=action.actor_id,
                notes=action.notes,
                actor_role=action.actor_role,
            )
        except ApprovalRequestNotFoundError as err:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err)) from err
        except MandatoryAuditReasoningMissingError as err:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(err)) from err
        except InsufficientApproverAuthorityError as err:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(err)) from err
        except InvalidStateTransitionException as err:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(err)) from err

        _, meta = workflow_svc.get_request(action.tenant_id, request_id)
        return _to_response_dto(req, meta)

    @router.post(
        "/{request_id}/reject",
        response_model=ApprovalRequestResponse,
        summary="1-Click rejection with mandatory audit reasoning",
    )
    def reject_request(
        request_id: str,
        action: ApprovalActionRequest,
    ) -> ApprovalRequestResponse:
        try:
            req = workflow_svc.reject_request(
                tenant_id=action.tenant_id,
                request_id=request_id,
                actor_id=action.actor_id,
                notes=action.notes,
                actor_role=action.actor_role,
            )
        except ApprovalRequestNotFoundError as err:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err)) from err
        except MandatoryAuditReasoningMissingError as err:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(err)) from err
        except InvalidStateTransitionException as err:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(err)) from err

        _, meta = workflow_svc.get_request(action.tenant_id, request_id)
        return _to_response_dto(req, meta)

    @router.post(
        "/{request_id}/disburse",
        response_model=ApprovalRequestResponse,
        summary="Disburse funds for an approved request",
    )
    def disburse_request(
        request_id: str,
        action: DisburseActionRequest,
    ) -> ApprovalRequestResponse:
        try:
            req = workflow_svc.disburse_request(
                tenant_id=action.tenant_id,
                request_id=request_id,
                actor_id=action.actor_id,
                notes=action.notes,
            )
        except ApprovalRequestNotFoundError as err:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err)) from err
        except InvalidStateTransitionException as err:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(err)) from err

        _, meta = workflow_svc.get_request(action.tenant_id, request_id)
        return _to_response_dto(req, meta)

    # -----------------------------------------------------------------------
    # WebSocket Live Feed Endpoint
    # -----------------------------------------------------------------------

    @router.websocket("/ws")
    async def websocket_live_feed(websocket: WebSocket) -> None:
        """
        Real-time WebSocket endpoint for instant live status updates.
        Clients receive JSON event broadcasts for new submissions, approvals,
        rejections, and disbursements.
        """
        await ws_manager.connect(websocket)
        # Send initial connection acknowledgment
        await websocket.send_json({
            "type": "CONNECTION_ESTABLISHED",
            "message": "Connected to PettyFlow Real-Time Approval Live Feed",
            "timestamp": datetime.now(UTC).isoformat(),
        })

        try:
            while True:
                # Keep connection alive; accept pings from clients
                data = await websocket.receive_json()
                msg_type = data.get("type", "").upper()
                if msg_type == "PING":
                    await websocket.send_json({
                        "type": "PONG",
                        "timestamp": datetime.now(UTC).isoformat(),
                    })
        except WebSocketDisconnect:
            ws_manager.disconnect(websocket)
        except Exception:
            ws_manager.disconnect(websocket)

    return router
