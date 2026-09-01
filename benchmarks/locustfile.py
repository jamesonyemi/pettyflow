"""Distributed Locust Load Testing Suite for PettyFlow 100k TPS Benchmarks.

Simulates high-concurrency traffic targeting:
- Float Balance Inquiries (Target: < 50 µs cache / < 10 ms API)
- Receipt OCR Processing
- 3-Way Reconciliation Closing
- Disbursement Requests

Usage:
    locust -f benchmarks/locustfile.py --headless -u 1000 -r 100 --run-time 1m --host http://localhost:8000
"""

from __future__ import annotations

import json
import random
import uuid
from locust import HttpUser, between, task, events


TENANTS = [f"tenant_bench_{i:03d}" for i in range(10)]
FUNDS = [f"fund_bench_{i:03d}" for i in range(50)]


class PettyFlowLoadUser(HttpUser):
    """High-throughput synthetic user simulating multi-tenant enterprise workload."""
    wait_time = between(0.001, 0.005)  # High frequency for 100k TPS simulation

    def on_start(self):
        self.tenant_id = random.choice(TENANTS)
        self.fund_id = random.choice(FUNDS)
        self.auth_headers = {
            "Content-Type": "application/json",
            "X-Tenant-ID": self.tenant_id,
        }

    @task(60)
    def query_custodian_balance(self):
        """Read-heavy float balance query (60% of total load)."""
        self.client.get(
            f"/api/v1/float/balance?tenant_id={self.tenant_id}&fund_id={self.fund_id}",
            headers=self.auth_headers,
            name="/api/v1/float/balance",
        )

    @task(20)
    def get_spend_summary(self):
        """Executive financial analytics summary query (20% of load)."""
        self.client.get(
            f"/api/v1/reports/spend-summary?tenant_id={self.tenant_id}",
            headers=self.auth_headers,
            name="/api/v1/reports/spend-summary",
        )

    @task(10)
    def submit_disbursement(self):
        """Disbursement creation with idempotency (10% of load)."""
        tx_id = str(uuid.uuid4())
        payload = {
            "tenant_id": self.tenant_id,
            "fund_id": self.fund_id,
            "custodian_id": "cust_bench_01",
            "amount_scaled": random.randint(10_000, 500_000),  # $1 to $50
            "expense_category": random.choice(["Supplies", "Travel", "Meals", "Courier"]),
            "description": "Benchmark synthetic disbursement",
            "idempotency_key": tx_id,
        }
        self.client.post(
            "/api/v1/float/disburse",
            json=payload,
            headers=self.auth_headers,
            name="/api/v1/float/disburse",
        )

    @task(10)
    def query_reconciliation_history(self):
        """Reconciliation audit history lookup (10% of load)."""
        self.client.get(
            f"/api/v1/reconciliation/history?tenant_id={self.tenant_id}",
            headers=self.auth_headers,
            name="/api/v1/reconciliation/history",
        )
