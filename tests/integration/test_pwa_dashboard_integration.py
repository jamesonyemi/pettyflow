"""
Integration and performance tests for Custodian Mobile PWA & Finance Management Dashboard.
Validates PWA web manifest, Service Worker asset caching, end-to-end approval lifecycle,
and verifies the < 200ms queue render / retrieval performance benchmark.
"""

import time
from fastapi.testclient import TestClient
import pytest

from src.api.app import create_app
from src.domain.workflow.service import ApprovalWorkflowService


@pytest.fixture
def client() -> TestClient:
    service = ApprovalWorkflowService()
    app = create_app(approval_service=service)
    return TestClient(app)


class TestPWADashboardIntegration:

    def test_pwa_application_shell_serves_html(self, client: TestClient):
        """GET / serves the mobile PWA HTML application shell with 200 OK."""
        res = client.get("/")
        assert res.status_code == 200
        assert "text/html" in res.headers["content-type"]
        assert "PettyFlow | Custodian PWA & Approval Dashboard" in res.text
        assert 'href="/manifest.json"' in res.text
        assert 'src="/static/js/app.js"' in res.text

    def test_pwa_web_manifest(self, client: TestClient):
        """GET /manifest.json returns valid W3C PWA manifest with standalone display."""
        res = client.get("/manifest.json")
        assert res.status_code == 200
        manifest = res.json()
        assert manifest["name"] == "PettyFlow Custodian PWA & Management Dashboard"
        assert manifest["short_name"] == "PettyFlow"
        assert manifest["display"] == "standalone"
        assert manifest["start_url"] == "/"
        assert len(manifest["icons"]) >= 2

    def test_service_worker_serves_javascript(self, client: TestClient):
        """GET /sw.js serves the service worker script with 200 OK."""
        res = client.get("/sw.js")
        assert res.status_code == 200
        assert "javascript" in res.headers["content-type"]
        assert "CACHE_NAME" in res.text
        assert "STATIC_ASSETS" in res.text

    def test_static_assets_serve(self, client: TestClient):
        """Static CSS and JS files serve successfully."""
        css_res = client.get("/static/css/app.css")
        assert css_res.status_code == 200
        assert "var(--brand-indigo)" in css_res.text

        js_res = client.get("/static/js/app.js")
        assert js_res.status_code == 200
        assert "openDB" in js_res.text
        assert "renderApprovalQueueDOM" in js_res.text

    def test_end_to_end_disbursement_lifecycle(self, client: TestClient):
        """
        Complete E2E test:
        1. Custodian submits request (Tier 2 Manager approval needed).
        2. Management inbox queries pending list.
        3. Manager inspects request details.
        4. Manager approves with mandatory audit reasoning note.
        5. Finance disburses request.
        6. Full immutable audit trail confirms all state transitions.
        """
        tenant_id = "TENANT_E2E_01"
        custodian_id = "CUSTODIAN_BRANCH_07"

        # 1. Submit
        submit_res = client.post(
            "/v1/approvals/submit",
            json={
                "tenant_id": tenant_id,
                "custodian_id": custodian_id,
                "amount_scaled": 2_500_000,  # $250.00
                "currency": "USD",
                "description": "Branch internet router emergency replacement",
                "receipt_metadata": {"merchant_name": "Best Buy Commercial", "receipt_id": "REC-9912"},
                "auto_approve_if_eligible": False,
            },
        )
        assert submit_res.status_code == 201
        req_id = submit_res.json()["request"]["request_id"]

        # 2. Query pending queue
        queue_res = client.get(f"/v1/approvals?tenant_id={tenant_id}&state=PENDING")
        assert queue_res.status_code == 200
        assert queue_res.json()["total_matching"] == 1

        # 3. Inspect details
        detail_res = client.get(f"/v1/approvals/{req_id}?tenant_id={tenant_id}")
        assert detail_res.status_code == 200
        assert detail_res.json()["receipt_metadata"]["merchant_name"] == "Best Buy Commercial"

        # 4. Approve with mandatory note
        approve_res = client.post(
            f"/v1/approvals/{req_id}/approve",
            json={
                "tenant_id": tenant_id,
                "actor_id": "MGR_SARAH_CONNOR",
                "notes": "Verified router receipt against emergency branch hardware contingency fund.",
                "actor_role": "MANAGER",
            },
        )
        assert approve_res.status_code == 200
        assert approve_res.json()["state"] == "APPROVED"

        # 5. Disburse
        disburse_res = client.post(
            f"/v1/approvals/{req_id}/disburse",
            json={
                "tenant_id": tenant_id,
                "actor_id": "FINANCE_TREASURY_01",
                "notes": "Electronic petty cash wallet transfer completed.",
            },
        )
        assert disburse_res.status_code == 200
        assert disburse_res.json()["state"] == "DISBURSED"

        # 6. Verify full audit trail
        final_res = client.get(f"/v1/approvals/{req_id}?tenant_id={tenant_id}")
        audit = final_res.json()["audit_trail"]
        assert len(audit) == 3
        assert audit[0]["event"] == "SUBMIT"
        assert audit[1]["event"] == "APPROVE"
        assert audit[1]["actor_id"] == "MGR_SARAH_CONNOR"
        assert audit[2]["event"] == "DISBURSE"
        assert audit[2]["actor_id"] == "FINANCE_TREASURY_01"

    def test_queue_retrieval_performance_under_200ms(self, client: TestClient):
        """
        Acceptance Criteria:
        Management dashboard loads and renders approval queue in < 200 ms.
        Benchmarks querying a queue containing 100 requests.
        """
        tenant_id = "TENANT_PERF_BENCHMARK"

        # Populate 100 requests
        for i in range(100):
            client.post(
                "/v1/approvals/submit",
                json={
                    "tenant_id": tenant_id,
                    "custodian_id": f"CUST_{i%5}",
                    "amount_scaled": (i + 1) * 100_000,
                    "currency": "USD",
                    "description": f"Benchmark transaction #{i}",
                    "auto_approve_if_eligible": False,
                },
            )

        # Benchmark 20 iterations of queue queries
        latencies_ms = []
        for _ in range(20):
            t0 = time.perf_counter()
            res = client.get(f"/v1/approvals?tenant_id={tenant_id}&limit=100")
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            assert res.status_code == 200
            assert res.json()["total_matching"] == 100
            latencies_ms.append(elapsed_ms)

        avg_latency = sum(latencies_ms) / len(latencies_ms)
        p99_latency = sorted(latencies_ms)[int(len(latencies_ms) * 0.95)]

        # Target is < 200 ms; in-memory typically executes in < 15 ms
        assert p99_latency < 200.0, f"p99 queue query latency ({p99_latency:.2f}ms) exceeded 200ms budget!"
        assert avg_latency < 50.0, f"Average queue query latency ({avg_latency:.2f}ms) exceeded 50ms!"
