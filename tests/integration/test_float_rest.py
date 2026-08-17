"""REST contract tests for float management and generated OpenAPI."""

from uuid import uuid4

from fastapi.testclient import TestClient

from src.api.app import create_app
from src.domain.funds.service import FundService


class TestFloatRestAPI:
    def setup_method(self) -> None:
        self.client = TestClient(create_app(FundService()))
        self.tenant_id = str(uuid4())
        self.custodian_id = str(uuid4())

    def create_fund(self) -> str:
        response = self.client.post(
            "/v1/funds",
            json={
                "tenant_id": self.tenant_id,
                "name": " Accra Office Float ",
                "currency": "usd",
                "custodian_id": self.custodian_id,
                "initial_amount_scaled": 1_000_000,
            },
        )
        assert response.status_code == 201
        assert response.json()["currency"] == "USD"
        assert response.json()["name"] == "Accra Office Float"
        return response.json()["fund_id"]

    def test_allocate_disburse_and_get_balance(self) -> None:
        fund_id = self.create_fund()
        allocation = self.client.post(
            f"/v1/funds/{fund_id}/allocations",
            json={"tenant_id": self.tenant_id, "custodian_id": self.custodian_id, "amount_scaled": 400_000},
        )
        assert allocation.status_code == 200
        assert allocation.json()["amount_scaled"] == 400_000

        disbursement = self.client.post(
            f"/v1/funds/{fund_id}/disbursements",
            json={"tenant_id": self.tenant_id, "custodian_id": self.custodian_id, "amount_scaled": 125_000},
        )
        assert disbursement.status_code == 200
        assert disbursement.json()["amount_scaled"] == 275_000

        balance = self.client.get(
            f"/v1/funds/{fund_id}/custodians/{self.custodian_id}/balance",
            params={"tenant_id": self.tenant_id},
        )
        assert balance.status_code == 200
        assert balance.json()["amount_scaled"] == 275_000

    def test_rejects_invalid_and_cross_tenant_operations(self) -> None:
        fund_id = self.create_fund()
        invalid = self.client.post(
            "/v1/funds",
            json={
                "tenant_id": self.tenant_id,
                "name": "Fund",
                "currency": "US",
                "custodian_id": self.custodian_id,
                "initial_amount_scaled": 0,
            },
        )
        assert invalid.status_code == 422

        cross_tenant = self.client.post(
            f"/v1/funds/{fund_id}/allocations",
            json={"tenant_id": str(uuid4()), "custodian_id": self.custodian_id, "amount_scaled": 1},
        )
        assert cross_tenant.status_code == 404

        insufficient = self.client.post(
            f"/v1/funds/{fund_id}/allocations",
            json={"tenant_id": self.tenant_id, "custodian_id": self.custodian_id, "amount_scaled": 2_000_000},
        )
        assert insufficient.status_code == 409

    def test_openapi_exposes_float_management_operations(self) -> None:
        schema = self.client.get("/openapi.json")
        assert schema.status_code == 200
        assert "/v1/funds" in schema.json()["paths"]
        assert "/v1/funds/{fund_id}/allocations" in schema.json()["paths"]
