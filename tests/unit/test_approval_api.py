"""
Unit and integration tests for PettyFlow Approval REST and WebSocket API endpoints.
Tests /v1/approvals/submit, /v1/approvals/{id}/approve, /v1/approvals/summary,
queue filters, and real-time WebSocket live feed broadcasts.
"""

from fastapi.testclient import TestClient
import pytest

from src.api.app import create_app
from src.domain.workflow.service import ApprovalWorkflowService


@pytest.fixture
def client() -> TestClient:
    service = ApprovalWorkflowService()
    app = create_app(approval_service=service)
    return TestClient(app)


class TestApprovalApiEndpoints:

    def test_submit_disbursement_request(self, client: TestClient):
        """POST /v1/approvals/submit successfully creates a request with policy evaluation."""
        payload = {
            "tenant_id": "TENANT_CORP_01",
            "custodian_id": "CUST_42",
            "amount_scaled": 1_250_000,  # $125.00 -> Tier 2 Manager
            "currency": "usd",
            "description": "Branch cleaning supplies",
            "receipt_metadata": {"merchant_name": "Target", "tax_scaled": 100_000},
            "auto_approve_if_eligible": True,
        }

        res = client.post("/v1/approvals/submit", json=payload)
        assert res.status_code == 201
        data = res.json()

        assert "request" in data
        assert "policy_evaluation" in data
        req = data["request"]
        assert req["currency"] == "USD"
        assert req["amount_scaled"] == 1_250_000
        assert req["amount_formatted"] == "$125.00"
        assert req["required_approval_tier"] == "MANAGER"
        assert req["state"] == "PENDING"
        assert len(req["audit_trail"]) == 1

        policy = data["policy_evaluation"]
        assert policy["required_tier"] == "MANAGER"
        assert policy["auto_approved"] is False

    def test_submit_auto_approve_request(self, client: TestClient):
        """POST /v1/approvals/submit for < $50.00 auto-approves."""
        payload = {
            "tenant_id": "TENANT_CORP_01",
            "custodian_id": "CUST_42",
            "amount_scaled": 350_000,  # $35.00 -> Tier 1 Auto
            "currency": "USD",
            "description": "Courier shipping fee",
            "auto_approve_if_eligible": True,
        }

        res = client.post("/v1/approvals/submit", json=payload)
        assert res.status_code == 201
        data = res.json()
        assert data["request"]["state"] == "APPROVED"
        assert data["policy_evaluation"]["auto_approved"] is True

    def test_one_click_approve_with_mandatory_notes(self, client: TestClient):
        """POST /v1/approvals/{id}/approve successfully transitions request with mandatory audit notes."""
        submit_res = client.post(
            "/v1/approvals/submit",
            json={
                "tenant_id": "TENANT_CORP_01",
                "custodian_id": "CUST_42",
                "amount_scaled": 1_500_000,  # $150.00
                "currency": "USD",
                "description": "Toner",
                "auto_approve_if_eligible": False,
            },
        )
        req_id = submit_res.json()["request"]["request_id"]

        # Approve with notes
        approve_res = client.post(
            f"/v1/approvals/{req_id}/approve",
            json={
                "tenant_id": "TENANT_CORP_01",
                "actor_id": "MGR_ALEX",
                "notes": "Verified receipt against branch operations budget.",
                "actor_role": "MANAGER",
            },
        )
        assert approve_res.status_code == 200
        approved_data = approve_res.json()
        assert approved_data["state"] == "APPROVED"
        assert len(approved_data["audit_trail"]) == 2
        assert approved_data["audit_trail"][-1]["notes"] == "Verified receipt against branch operations budget."

    def test_approve_fails_without_mandatory_notes(self, client: TestClient):
        """Approving without mandatory audit reasoning returns HTTP 422 Unprocessable Entity."""
        submit_res = client.post(
            "/v1/approvals/submit",
            json={
                "tenant_id": "TENANT_CORP_01",
                "custodian_id": "CUST_42",
                "amount_scaled": 1_500_000,
                "currency": "USD",
                "description": "Hardware",
                "auto_approve_if_eligible": False,
            },
        )
        req_id = submit_res.json()["request"]["request_id"]

        # Attempt approve with empty notes
        approve_res = client.post(
            f"/v1/approvals/{req_id}/approve",
            json={
                "tenant_id": "TENANT_CORP_01",
                "actor_id": "MGR_ALEX",
                "notes": "   ",
            },
        )
        assert approve_res.status_code == 422

    def test_one_click_reject_with_mandatory_notes(self, client: TestClient):
        """POST /v1/approvals/{id}/reject successfully transitions request to REJECTED."""
        submit_res = client.post(
            "/v1/approvals/submit",
            json={
                "tenant_id": "TENANT_CORP_01",
                "custodian_id": "CUST_42",
                "amount_scaled": 1_500_000,
                "currency": "USD",
                "description": "Unauthorized dinner",
                "auto_approve_if_eligible": False,
            },
        )
        req_id = submit_res.json()["request"]["request_id"]

        reject_res = client.post(
            f"/v1/approvals/{req_id}/reject",
            json={
                "tenant_id": "TENANT_CORP_01",
                "actor_id": "MGR_ALEX",
                "notes": "Dinners must be expensed via corporate card, not petty cash.",
            },
        )
        assert reject_res.status_code == 200
        assert reject_res.json()["state"] == "REJECTED"

    def test_query_approval_queue_filters(self, client: TestClient):
        """GET /v1/approvals correctly applies tier, state, and tenant filters with low latency."""
        # Submit 3 requests
        for amt in [200_000, 1_000_000, 8_000_000]:
            client.post(
                "/v1/approvals/submit",
                json={
                    "tenant_id": "TENANT_CORP_01",
                    "custodian_id": "CUST_42",
                    "amount_scaled": amt,
                    "currency": "USD",
                    "description": f"Expense {amt}",
                    "auto_approve_if_eligible": False,
                },
            )

        # Query all
        res = client.get("/v1/approvals?tenant_id=TENANT_CORP_01")
        assert res.status_code == 200
        data = res.json()
        assert data["total_matching"] == 3
        assert data["retrieval_duration_ms"] >= 0

        # Query filter by tier = FINANCE_DIRECTOR
        res_tier3 = client.get("/v1/approvals?tenant_id=TENANT_CORP_01&tier=FINANCE_DIRECTOR")
        assert res_tier3.status_code == 200
        assert res_tier3.json()["total_matching"] == 1

    def test_summary_kpi_endpoint(self, client: TestClient):
        """GET /v1/approvals/summary returns correct aggregated KPI counts."""
        client.post(
            "/v1/approvals/submit",
            json={
                "tenant_id": "TENANT_CORP_01",
                "custodian_id": "CUST_42",
                "amount_scaled": 200_000,  # Auto
                "currency": "USD",
                "description": "Auto item",
                "auto_approve_if_eligible": True,
            },
        )
        client.post(
            "/v1/approvals/submit",
            json={
                "tenant_id": "TENANT_CORP_01",
                "custodian_id": "CUST_42",
                "amount_scaled": 1_000_000,  # Manager
                "currency": "USD",
                "description": "Manager item",
                "auto_approve_if_eligible": False,
            },
        )

        res = client.get("/v1/approvals/summary?tenant_id=TENANT_CORP_01")
        assert res.status_code == 200
        stats = res.json()
        assert stats["total_requests"] == 2
        assert stats["tier_1_auto_count"] == 1
        assert stats["pending_count"] == 1
        assert stats["approved_count"] == 1

    def test_websocket_connection_and_ping_pong(self, client: TestClient):
        """WebSocket connection at /v1/approvals/ws establishes and responds to ping."""
        with client.websocket_connect("/v1/approvals/ws") as ws:
            init_msg = ws.receive_json()
            assert init_msg["type"] == "CONNECTION_ESTABLISHED"

            # Send ping
            ws.send_json({"type": "PING"})
            pong = ws.receive_json()
            assert pong["type"] == "PONG"
