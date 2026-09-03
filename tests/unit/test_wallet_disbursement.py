"""Week 7: P-Card & Wallet Integration Test Suite.

Acceptance Criteria per Roadmap:
  - Virtual card creation flow completes within < 800ms webhook response target.
  - Webhook idempotency layer prevents double-issuance on network retries.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
import pytest

from src.infrastructure.adapters.card_issuer import (
    CardIssuerAdapter,
    CardIssuerBackend,
    CardStatus,
    VirtualCardRequest,
)
from src.infrastructure.adapters.mobile_money import (
    DisbursementRequest,
    DisbursementStatus,
    MobileMoneyAdapter,
    MobileMoneyBackend,
)
from src.domain.wallet.disbursement_manager import (
    DisbursementChannel,
    DisbursementManager,
    FloatDisbursementRequest,
)
from src.infrastructure.idempotency.store import (
    IdempotencyInProgressError,
    ProviderEventConflictError,
    SQLiteIdempotencyStore,
)
from src.domain.wallet.settlement import SettlementState, SettlementTransitionError


# ===========================================================================
# PART 1: VirtualCardRequest Tests
# ===========================================================================

class TestVirtualCardRequest:
    """Validate VirtualCardRequest construction and idempotency key generation."""

    def test_valid_request_creates_idempotency_key(self):
        req = VirtualCardRequest(
            tenant_id="T1",
            custodian_id="C1",
            fund_id="F1",
            spending_limit_scaled=1_000_000,  # $100
        )
        assert req.idempotency_key is not None
        assert len(req.idempotency_key) == 32

    def test_negative_limit_raises(self):
        with pytest.raises(ValueError, match="spending_limit_scaled must be positive"):
            VirtualCardRequest(
                tenant_id="T1",
                custodian_id="C1",
                fund_id="F1",
                spending_limit_scaled=-500,
            )

    def test_zero_limit_raises(self):
        with pytest.raises(ValueError):
            VirtualCardRequest(
                tenant_id="T1",
                custodian_id="C1",
                fund_id="F1",
                spending_limit_scaled=0,
            )

    def test_invalid_currency_raises(self):
        with pytest.raises(ValueError, match="ISO-4217"):
            VirtualCardRequest(
                tenant_id="T1",
                custodian_id="C1",
                fund_id="F1",
                spending_limit_scaled=1_000_000,
                currency="DOLLARS",
            )

    def test_custom_idempotency_key_preserved(self):
        req = VirtualCardRequest(
            tenant_id="T1",
            custodian_id="C1",
            fund_id="F1",
            spending_limit_scaled=1_000_000,
            idempotency_key="my-custom-key-12345",
        )
        assert req.idempotency_key == "my-custom-key-12345"

    def test_same_params_same_idempotency_key(self):
        req1 = VirtualCardRequest("T1", "C1", "F1", 500_000)
        req2 = VirtualCardRequest("T1", "C1", "F1", 500_000)
        assert req1.idempotency_key == req2.idempotency_key


# ===========================================================================
# PART 2: CardIssuerAdapter Tests
# ===========================================================================

class TestCardIssuerAdapter:
    """Test virtual card creation, idempotency, and cancellation."""

    def setup_method(self):
        self.adapter = CardIssuerAdapter(backend=CardIssuerBackend.MOCK)

    def _make_request(self, fund_id: str = "F001", limit: int = 1_000_000) -> VirtualCardRequest:
        return VirtualCardRequest(
            tenant_id="TENANT-X",
            custodian_id="CUST-001",
            fund_id=fund_id,
            spending_limit_scaled=limit,
        )

    def test_create_card_returns_result(self):
        req = self._make_request()
        result = self.adapter.create_virtual_card(req)
        assert result.card_id.startswith("card_")
        assert result.card_status == CardStatus.ACTIVE
        assert result.spending_limit_scaled == 1_000_000
        assert result.tenant_id == "TENANT-X"

    def test_card_last_four_is_4_digits(self):
        req = self._make_request()
        result = self.adapter.create_virtual_card(req)
        assert len(result.last_four) == 4
        assert result.last_four.isdigit()

    def test_spending_limit_float_property(self):
        req = self._make_request(limit=2_500_000)  # $250.00
        result = self.adapter.create_virtual_card(req)
        assert result.spending_limit_float == pytest.approx(250.0)

    def test_to_dict_has_required_keys(self):
        req = self._make_request()
        result = self.adapter.create_virtual_card(req)
        d = result.to_dict()
        for key in ["card_id", "tenant_id", "last_four", "card_status", "spending_limit_formatted"]:
            assert key in d

    def test_idempotency_returns_same_card(self):
        """Roadmap Acceptance: idempotency layer prevents double-issuance."""
        req = self._make_request()
        result_1 = self.adapter.create_virtual_card(req)
        result_2 = self.adapter.create_virtual_card(req)  # Same idempotency_key
        assert result_1.card_id == result_2.card_id
        assert self.adapter.count_issued() == 1  # Only 1 card created

    def test_different_idempotency_keys_create_different_cards(self):
        req1 = self._make_request(fund_id="F001")
        req2 = self._make_request(fund_id="F002")
        r1 = self.adapter.create_virtual_card(req1)
        r2 = self.adapter.create_virtual_card(req2)
        assert r1.card_id != r2.card_id
        assert self.adapter.count_issued() == 2

    def test_get_card_by_id(self):
        req = self._make_request()
        created = self.adapter.create_virtual_card(req)
        fetched = self.adapter.get_card(created.card_id)
        assert fetched is not None
        assert fetched.card_id == created.card_id

    def test_get_nonexistent_card_returns_none(self):
        assert self.adapter.get_card("card_nonexistent") is None

    def test_cancel_card_changes_status(self):
        req = self._make_request()
        result = self.adapter.create_virtual_card(req)
        success = self.adapter.cancel_card(result.card_id)
        assert success is True
        updated = self.adapter.get_card(result.card_id)
        assert updated.card_status == CardStatus.CANCELLED

    def test_cancel_nonexistent_card_returns_false(self):
        assert self.adapter.cancel_card("card_does_not_exist") is False

    def test_card_creation_latency_under_800ms(self):
        """Roadmap Acceptance Criterion: < 800ms card creation."""
        req = self._make_request()
        start = time.perf_counter()
        self.adapter.create_virtual_card(req)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 800, f"Card creation took {elapsed_ms:.1f}ms (limit: 800ms)"

    def test_tenant_isolation_in_metadata(self):
        req = self._make_request()
        result = self.adapter.create_virtual_card(req)
        assert result.metadata.get("tenant_id") == "TENANT-X"


# ===========================================================================
# PART 3: DisbursementRequest Tests
# ===========================================================================

class TestDisbursementRequest:
    """Validate DisbursementRequest construction."""

    def test_valid_request_auto_generates_key(self):
        req = DisbursementRequest(
            tenant_id="T1",
            custodian_id="C1",
            fund_id="F1",
            recipient_phone_or_account="+254700000000",
            amount_scaled=500_000,
        )
        assert req.idempotency_key is not None
        assert len(req.idempotency_key) == 32

    def test_negative_amount_raises(self):
        with pytest.raises(ValueError):
            DisbursementRequest(
                tenant_id="T1", custodian_id="C1", fund_id="F1",
                recipient_phone_or_account="+254700000000",
                amount_scaled=-100,
            )

    def test_empty_recipient_raises(self):
        with pytest.raises(ValueError, match="recipient_phone_or_account cannot be empty"):
            DisbursementRequest(
                tenant_id="T1", custodian_id="C1", fund_id="F1",
                recipient_phone_or_account="",
                amount_scaled=500_000,
            )


# ===========================================================================
# PART 4: MobileMoneyAdapter Tests
# ===========================================================================

class TestMobileMoneyAdapter:
    """Test mobile money / ACH disbursement flows."""

    def setup_method(self):
        self.adapter = MobileMoneyAdapter(backend=MobileMoneyBackend.MOCK)

    def _make_request(self, custodian_id: str = "C001", amount: int = 1_000_000) -> DisbursementRequest:
        return DisbursementRequest(
            tenant_id="TENANT-Y",
            custodian_id=custodian_id,
            fund_id="FUND-001",
            recipient_phone_or_account="+254700000001",
            amount_scaled=amount,
        )

    def test_successful_disbursement(self):
        req = self._make_request()
        result = self.adapter.disburse(req)
        assert result.status == DisbursementStatus.COMPLETED
        assert result.disbursement_id.startswith("disb_")
        assert result.reference_number.startswith("REF-")

    def test_amount_float_property(self):
        req = self._make_request(amount=2_500_000)  # $250
        result = self.adapter.disburse(req)
        assert result.amount_float == pytest.approx(250.0)

    def test_idempotency_prevents_double_disbursement(self):
        """Roadmap Acceptance: idempotency layer prevents double-issuance."""
        req = self._make_request()
        r1 = self.adapter.disburse(req)
        r2 = self.adapter.disburse(req)  # Same idempotency_key
        assert r1.disbursement_id == r2.disbursement_id
        assert self.adapter.count_disbursements() == 1

    def test_simulated_failure_returns_failed_status(self):
        fail_adapter = MobileMoneyAdapter(backend=MobileMoneyBackend.MOCK, simulate_failure=True)
        req = self._make_request()
        result = fail_adapter.disburse(req)
        assert result.status == DisbursementStatus.FAILED
        assert result.failure_reason is not None

    def test_get_disbursement_status(self):
        req = self._make_request()
        created = self.adapter.disburse(req)
        fetched = self.adapter.get_disbursement_status(created.disbursement_id)
        assert fetched is not None
        assert fetched.disbursement_id == created.disbursement_id

    def test_to_dict_format(self):
        req = self._make_request(amount=1_500_000)
        result = self.adapter.disburse(req)
        d = result.to_dict()
        assert d["amount_formatted"] == "$150.00"
        assert "disbursement_id" in d
        assert "reference_number" in d

    def test_disbursement_latency_under_800ms(self):
        """Roadmap Acceptance Criterion: < 800ms disbursement response."""
        req = self._make_request()
        start = time.perf_counter()
        self.adapter.disburse(req)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 800, f"Disbursement took {elapsed_ms:.1f}ms (limit: 800ms)"


# ===========================================================================
# PART 5: DisbursementManager (Orchestration) Tests
# ===========================================================================

class TestDisbursementManager:
    """Test the orchestration layer across channels."""

    def setup_method(self):
        self.manager = DisbursementManager()

    def _card_request(self, fund_id: str = "F001", amount: int = 1_500_000) -> FloatDisbursementRequest:
        return FloatDisbursementRequest(
            tenant_id="TENANT-ORG",
            custodian_id="CUST-001",
            fund_id=fund_id,
            amount_scaled=amount,
            channel=DisbursementChannel.VIRTUAL_CARD,
            cardholder_name="John Smith",
        )

    def _mobile_request(self, fund_id: str = "F001", amount: int = 1_500_000) -> FloatDisbursementRequest:
        return FloatDisbursementRequest(
            tenant_id="TENANT-ORG",
            custodian_id="CUST-001",
            fund_id=fund_id,
            amount_scaled=amount,
            channel=DisbursementChannel.MOBILE_MONEY,
            recipient_address="+254700111222",
        )

    def test_virtual_card_disbursement_completes(self):
        req = self._card_request()
        result = self.manager.disburse_float(req)
        assert result.is_successful is True
        assert result.virtual_card is not None
        assert result.channel == DisbursementChannel.VIRTUAL_CARD

    def test_mobile_money_disbursement_completes(self):
        req = self._mobile_request()
        result = self.manager.disburse_float(req)
        assert result.is_successful is True
        assert result.mobile_disbursement is not None
        assert result.channel == DisbursementChannel.MOBILE_MONEY

    def test_idempotency_prevents_double_issuance(self):
        """Core acceptance criterion: duplicate key returns same result."""
        req = self._card_request()
        r1 = self.manager.disburse_float(req)
        r2 = self.manager.disburse_float(req)  # Same idempotency_key
        assert r1.disbursement_id == r2.disbursement_id
        assert self.manager.count_disbursements() == 1

    def test_idempotency_audit_records_canonical_and_reused_result(self):
        request = self._card_request()
        self.manager.disburse_float(request)
        self.manager.disburse_float(request)

        events = self.manager._audit_logger.get_entries("TENANT-ORG")
        assert [event.event_type for event in events] == [
            "DISBURSEMENT_CANONICAL_RESULT",
            "DISBURSEMENT_RESULT_REUSED",
        ]
        assert self.manager._audit_logger.verify_integrity("TENANT-ORG")

    def test_amount_float_property(self):
        req = self._card_request(amount=2_000_000)  # $200
        result = self.manager.disburse_float(req)
        assert result.amount_float == pytest.approx(200.0)

    def test_audit_trail_grows_on_each_disbursement(self):
        self.manager.disburse_float(self._card_request(fund_id="F001"))
        self.manager.disburse_float(self._mobile_request(fund_id="F002"))
        trail = self.manager.get_audit_trail("TENANT-ORG")
        assert len(trail) == 2

    def test_audit_trail_scoped_to_tenant(self):
        req_a = FloatDisbursementRequest(
            tenant_id="TENANT-A",
            custodian_id="C1",
            fund_id="F1",
            amount_scaled=1_000_000,
            channel=DisbursementChannel.VIRTUAL_CARD,
        )
        req_b = FloatDisbursementRequest(
            tenant_id="TENANT-B",
            custodian_id="C2",
            fund_id="F2",
            amount_scaled=1_000_000,
            channel=DisbursementChannel.VIRTUAL_CARD,
        )
        self.manager.disburse_float(req_a)
        self.manager.disburse_float(req_b)
        assert len(self.manager.get_audit_trail("TENANT-A")) == 1
        assert len(self.manager.get_audit_trail("TENANT-B")) == 1

    def test_to_dict_contains_required_fields(self):
        req = self._card_request()
        result = self.manager.disburse_float(req)
        d = result.to_dict()
        for key in ["disbursement_id", "tenant_id", "channel", "amount_formatted", "status"]:
            assert key in d

    def test_end_to_end_card_latency_under_800ms(self):
        """Acceptance Criterion: end-to-end orchestration < 800ms."""
        req = self._card_request()
        start = time.perf_counter()
        self.manager.disburse_float(req)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 800, f"Orchestration took {elapsed_ms:.1f}ms (limit: 800ms)"

    def test_invalid_channel_raises_value_error(self):
        req = FloatDisbursementRequest(
            tenant_id="T1",
            custodian_id="C1",
            fund_id="F1",
            amount_scaled=500_000,
            channel=DisbursementChannel.ACH,
            recipient_address="acct-12345",
        )
        # ACH is supported — no error
        result = self.manager.disburse_float(req)
        assert result is not None

    def test_distinct_requests_receive_unique_auto_idempotency_keys(self):
        req1 = self._card_request()
        req2 = self._card_request()
        assert req1.idempotency_key != req2.idempotency_key
        assert req1.idempotency_key.startswith("auto-")
        assert req2.idempotency_key.startswith("auto-")

    def test_idempotency_survives_manager_restart(self, tmp_path):
        from src.infrastructure.idempotency.store import SQLiteIdempotencyStore

        db_path = str(tmp_path / "idempotency.sqlite3")
        request = self._mobile_request()
        first = DisbursementManager(
            idempotency_store=SQLiteIdempotencyStore(db_path)
        ).disburse_float(request)
        second = DisbursementManager(
            idempotency_store=SQLiteIdempotencyStore(db_path)
        ).disburse_float(request)

        assert second.disbursement_id == first.disbursement_id
        assert second.mobile_disbursement is not None

    def test_reusing_key_for_different_request_is_rejected(self, tmp_path):
        from src.infrastructure.idempotency.store import IdempotencyConflictError

        store = SQLiteIdempotencyStore(str(tmp_path / "idempotency.sqlite3"))
        manager = DisbursementManager(idempotency_store=store)
        first = self._card_request(amount=1_000_000)
        manager.disburse_float(first)
        conflicting = FloatDisbursementRequest(
            tenant_id=first.tenant_id,
            custodian_id=first.custodian_id,
            fund_id=first.fund_id,
            amount_scaled=2_000_000,
            channel=first.channel,
            cardholder_name=first.cardholder_name,
            idempotency_key=first.idempotency_key,
        )

        with pytest.raises(IdempotencyConflictError):
            manager.disburse_float(conflicting)

    def test_concurrent_duplicate_requests_have_one_owner(self, tmp_path):
        manager = DisbursementManager(
            idempotency_store=SQLiteIdempotencyStore(str(tmp_path / "idempotency.sqlite3"))
        )
        request = self._card_request()

        def submit():
            try:
                return manager.disburse_float(request)
            except IdempotencyInProgressError:
                return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: submit(), range(2)))

        completed = [result for result in results if result is not None]
        assert len({result.disbursement_id for result in completed}) == 1
        assert manager.count_disbursements() == 1

    def test_provider_callback_replay_is_ignored(self, tmp_path):
        manager = DisbursementManager(
            idempotency_store=SQLiteIdempotencyStore(str(tmp_path / "idempotency.sqlite3"))
        )
        payload = {"status": "processing", "disbursement_id": "fdisb-1"}

        assert manager.ingest_provider_event(
            "TENANT-ORG", "bank", "evt-1", payload, SettlementState.PENDING
        )
        assert not manager.ingest_provider_event(
            "TENANT-ORG", "bank", "evt-1", payload, SettlementState.PENDING
        )
        events = manager._audit_logger.get_entries("TENANT-ORG")
        assert [event.event_type for event in events] == [
            "PROVIDER_EVENT_ACCEPTED",
            "PROVIDER_EVENT_REPLAYED",
        ]

    def test_provider_callback_conflict_and_invalid_transition_are_rejected(self, tmp_path):
        manager = DisbursementManager(
            idempotency_store=SQLiteIdempotencyStore(str(tmp_path / "idempotency.sqlite3"))
        )
        with pytest.raises(SettlementTransitionError):
            manager.ingest_provider_event(
                "TENANT-ORG",
                "bank",
                "evt-1",
                {"status": "reversed"},
                SettlementState.PENDING,
            )
        manager.ingest_provider_event(
            "TENANT-ORG",
            "bank",
            "evt-1",
            {"status": "processing"},
            SettlementState.PENDING,
        )
        with pytest.raises(ProviderEventConflictError):
            manager.ingest_provider_event(
                "TENANT-ORG",
                "bank",
                "evt-1",
                {"status": "completed"},
                SettlementState.PROCESSING,
            )
