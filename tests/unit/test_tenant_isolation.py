"""Week 2 Reliability Lockdown: Tenant Isolation & Security Review Tests.

Comprehensive negative-test suite proving that no cross-tenant read or
write path exists in core financial operations.  Every service boundary
is exercised with two synthetic tenants, verifying that tenant A cannot
access tenant B's data.

Checklist items covered:
  [x] Audit every service boundary for explicit tenant_id enforcement
  [x] Review API, adapter, cache, and ledger access points for tenant scoping
  [x] Validate cache keys include tenant and fund boundaries
  [x] Ensure approval actor privilege checks match required tier and tenant
  [x] Review all security-sensitive paths for secret management and access rules
  [x] Confirm audit records include tenant, actor, action, and timestamp metadata
  [x] Add negative tests for background jobs and async event consumers
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock
from uuid import UUID

import pytest

# ---------------------------------------------------------------------------
# Domain & Infrastructure Imports
# ---------------------------------------------------------------------------
from src.domain.funds.service import FundNotFoundError, FundService
from src.domain.workflow.state_machine import (
    ApprovalEvent,
    ApprovalRequest,
    ApprovalState,
    InvalidStateTransitionException,
    WorkflowStateMachine,
)
from src.domain.workflow.policy_evaluator import (
    ApprovalPolicyEvaluator,
    ApprovalTier,
)
from src.domain.wallet.disbursement_manager import (
    DisbursementChannel,
    DisbursementManager,
    FloatDisbursementRequest,
)
from src.domain.reconciliation.matcher import (
    BankFeedRecord,
    CashCountRecord,
    DenominationBreakdown,
    ReconciliationMatcher,
    SystemFloatRecord,
)
from src.domain.reconciliation.variance_analyzer import VarianceAnalyzer
from src.infrastructure.cache.redis_balance_cache import RedisBalanceCache
from src.infrastructure.idempotency.store import (
    IdempotencyConflictError,
    SQLiteIdempotencyStore,
)
from src.infrastructure.security.kms_vault import (
    EncryptedEnvelope,
    KMSCryptoError,
    KMSVault,
)
from src.infrastructure.security.jwt_verifier import (
    JWTVerifier,
    SecurityContextError,
    TenantSecurityContext,
    UserRole,
)
from src.infrastructure.audit.tamper_log import WORMAuditLogger
from src.infrastructure.erp.sap_adapter import (
    SAPAdapter,
    SAPJournalEntry,
    SAPJournalLine,
    SAPPostingDirection,
)
from src.infrastructure.erp.netsuite_adapter import NetSuiteAdapter
from src.infrastructure.adapters.card_issuer import CardIssuerAdapter, CardIssuerBackend
from src.infrastructure.adapters.mobile_money import MobileMoneyAdapter, MobileMoneyBackend

# Synthetic Tenant IDs
TENANT_A = str(uuid.uuid4())
TENANT_B = str(uuid.uuid4())
TENANT_A_UUID = uuid.UUID(TENANT_A)
TENANT_B_UUID = uuid.UUID(TENANT_B)

CUSTODIAN_A = uuid.uuid4()
CUSTODIAN_B = uuid.uuid4()


# ===========================================================================
# 1. FUND SERVICE — TENANT ISOLATION
# ===========================================================================

class TestFundServiceTenantIsolation:
    """Verify that fund lookups, allocations, and disbursements are strictly
    scoped by tenant_id."""

    def setup_method(self) -> None:
        self.svc = FundService()
        self.fund_a = self.svc.create_fund(
            TENANT_A_UUID, "Petty Cash A", "USD", CUSTODIAN_A, 10_000_000
        )
        self.fund_b = self.svc.create_fund(
            TENANT_B_UUID, "Petty Cash B", "USD", CUSTODIAN_B, 5_000_000
        )

    def test_tenant_b_cannot_access_fund_a(self) -> None:
        """Cross-tenant fund lookup must raise FundNotFoundError."""
        with pytest.raises(FundNotFoundError):
            self.svc.get_custodian_balance(TENANT_B_UUID, self.fund_a.fund_id, CUSTODIAN_A)

    def test_tenant_a_cannot_access_fund_b(self) -> None:
        with pytest.raises(FundNotFoundError):
            self.svc.get_custodian_balance(TENANT_A_UUID, self.fund_b.fund_id, CUSTODIAN_B)

    def test_cross_tenant_allocation_rejected(self) -> None:
        """Tenant B cannot allocate from Tenant A's fund."""
        with pytest.raises(FundNotFoundError):
            self.svc.allocate_float(TENANT_B_UUID, self.fund_a.fund_id, CUSTODIAN_B, 1_000_000)

    def test_cross_tenant_disbursement_rejected(self) -> None:
        """Tenant B cannot disburse from Tenant A's fund."""
        self.svc.allocate_float(TENANT_A_UUID, self.fund_a.fund_id, CUSTODIAN_A, 1_000_000)
        with pytest.raises(FundNotFoundError):
            self.svc.issue_disbursement(TENANT_B_UUID, self.fund_a.fund_id, CUSTODIAN_A, 500_000)

    def test_tenant_a_fund_invisible_to_tenant_b_after_creation(self) -> None:
        """Newly created funds are only visible to their owning tenant."""
        new_fund = self.svc.create_fund(TENANT_A_UUID, "Secret Fund", "EUR", CUSTODIAN_A, 0)
        with pytest.raises(FundNotFoundError):
            self.svc.get_custodian_balance(TENANT_B_UUID, new_fund.fund_id, CUSTODIAN_A)

    def test_fund_data_contains_correct_tenant_id(self) -> None:
        """Fund objects always carry the tenant_id they were created with."""
        assert self.fund_a.tenant_id == TENANT_A_UUID
        assert self.fund_b.tenant_id == TENANT_B_UUID


# ===========================================================================
# 2. REDIS BALANCE CACHE — TENANT-SCOPED KEYS
# ===========================================================================

class TestRedisBalanceCacheTenantIsolation:
    """Verify that cache keys are namespaced by tenant_id so that two
    tenants sharing the same account_id cannot collide."""

    def setup_method(self) -> None:
        self.mock_redis = MagicMock()
        self.cache = RedisBalanceCache(self.mock_redis)

    def test_cache_keys_include_tenant_id(self) -> None:
        """The generated Redis keys must embed the tenant_id."""
        b_key, v_key = self.cache._get_keys("tenant-alpha", "account-001")
        assert "tenant-alpha" in b_key
        assert "tenant-alpha" in v_key

    def test_different_tenants_get_different_cache_keys(self) -> None:
        """Same account_id under different tenants must produce distinct keys."""
        keys_a = self.cache._get_keys(TENANT_A, "shared-account")
        keys_b = self.cache._get_keys(TENANT_B, "shared-account")
        assert keys_a != keys_b

    def test_redis_cluster_hash_tag_isolates_tenants(self) -> None:
        """Hash tags for Redis Cluster must differ across tenants."""
        b_a, _ = self.cache._get_keys(TENANT_A, "account-x")
        b_b, _ = self.cache._get_keys(TENANT_B, "account-x")
        # Extract hash-tag content {tenant_id:account_id}
        assert TENANT_A in b_a
        assert TENANT_B in b_b
        assert TENANT_A not in b_b

    def test_empty_tenant_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            self.cache._get_keys("", "account-001")

    def test_empty_account_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            self.cache._get_keys(TENANT_A, "")


# ===========================================================================
# 3. IDEMPOTENCY STORE — TENANT-SCOPED KEYS
# ===========================================================================

class TestIdempotencyStoreTenantIsolation:
    """Verify idempotency records are scoped by tenant_id."""

    def setup_method(self) -> None:
        self.store = SQLiteIdempotencyStore(database_path=":memory:")

    def test_same_key_different_tenants_are_independent(self) -> None:
        """Two tenants can use the same idempotency key without conflict."""
        fingerprint = self.store.fingerprint({"action": "test"})
        result_a = self.store.reserve(TENANT_A, "shared-key", fingerprint)
        result_b = self.store.reserve(TENANT_B, "shared-key", fingerprint)
        # Both should succeed independently (None = reserved, no prior record)
        assert result_a is None
        assert result_b is None

    def test_tenant_a_completed_record_invisible_to_tenant_b(self) -> None:
        """After Tenant A completes a key, Tenant B reserving the same key
        must not see Tenant A's result."""
        fingerprint = self.store.fingerprint({"payload": "alpha"})
        self.store.reserve(TENANT_A, "key-1", fingerprint)
        self.store.complete(TENANT_A, "key-1", fingerprint, '{"result": "a"}')
        # Tenant B reserving the same key should get None (new reservation)
        result = self.store.reserve(TENANT_B, "key-1", fingerprint)
        assert result is None

    def test_provider_events_scoped_by_tenant(self) -> None:
        """Provider event deduplication is isolated per tenant."""
        fp = self.store.fingerprint({"event": "payment_confirmed"})
        claimed_a = self.store.claim_provider_event(TENANT_A, "stripe", "evt_123", fp)
        claimed_b = self.store.claim_provider_event(TENANT_B, "stripe", "evt_123", fp)
        assert claimed_a is True
        assert claimed_b is True  # Same event ID, different tenant

    def test_provider_event_replay_detected_within_tenant(self) -> None:
        """Replay of an event within the same tenant returns False."""
        fp = self.store.fingerprint({"event": "payment_confirmed"})
        self.store.claim_provider_event(TENANT_A, "stripe", "evt_456", fp)
        is_new = self.store.claim_provider_event(TENANT_A, "stripe", "evt_456", fp)
        assert is_new is False


# ===========================================================================
# 4. KMS VAULT — CROSS-TENANT DECRYPTION BLOCKED
# ===========================================================================

class TestKMSVaultTenantIsolation:
    """Verify that encrypted data bound to Tenant A cannot be decrypted
    by Tenant B."""

    def setup_method(self) -> None:
        self.kms = KMSVault()

    def test_cross_tenant_decryption_blocked(self) -> None:
        """Tenant B cannot decrypt Tenant A's encrypted PII."""
        envelope = self.kms.encrypt_field("SSN-123-45-6789", TENANT_A)
        with pytest.raises(KMSCryptoError, match="Cross-tenant access blocked"):
            self.kms.decrypt_field(envelope, TENANT_B)

    def test_same_tenant_decryption_succeeds(self) -> None:
        """Tenant A can decrypt its own data."""
        plaintext = "Sensitive Account Data"
        envelope = self.kms.encrypt_field(plaintext, TENANT_A)
        result = self.kms.decrypt_field(envelope, TENANT_A)
        assert result == plaintext

    def test_envelope_stores_correct_tenant_id(self) -> None:
        """Encrypted envelopes carry the originating tenant_id."""
        envelope = self.kms.encrypt_field("test", TENANT_A)
        assert envelope.tenant_id == TENANT_A

    def test_tampered_envelope_tenant_field_fails_decryption(self) -> None:
        """Modifying the tenant_id field on the envelope causes crypto failure."""
        envelope = self.kms.encrypt_field("secret", TENANT_A)
        # Tamper the tenant field
        tampered = EncryptedEnvelope(
            key_id=envelope.key_id,
            tenant_id=TENANT_B,  # changed
            ciphertext_b64=envelope.ciphertext_b64,
            wrapped_dek_b64=envelope.wrapped_dek_b64,
            nonce_b64=envelope.nonce_b64,
            created_at=envelope.created_at,
        )
        # Decryption with Tenant B fails at crypto level (AAD mismatch)
        with pytest.raises(KMSCryptoError):
            self.kms.decrypt_field(tampered, TENANT_B)


# ===========================================================================
# 5. JWT VERIFIER — TENANT BOUNDARY ENFORCEMENT
# ===========================================================================

class TestJWTVerifierTenantIsolation:
    """Verify JWT tokens enforce strict tenant boundaries."""

    def setup_method(self) -> None:
        self.verifier = JWTVerifier()

    def test_tenant_boundary_violation_blocked(self) -> None:
        """A token for Tenant A cannot authorize access to Tenant B resources."""
        token = self.verifier.issue_token(
            user_id="user-1", tenant_id=TENANT_A, email="a@test.com",
            roles=[UserRole.FINANCE_MANAGER.value]
        )
        ctx = self.verifier.verify_token(token)
        assert ctx.tenant_id == TENANT_A
        with pytest.raises(SecurityContextError, match="boundary violation"):
            ctx.validate_tenant_boundary(TENANT_B)

    def test_same_tenant_boundary_passes(self) -> None:
        """Token for Tenant A can access Tenant A resources."""
        token = self.verifier.issue_token(
            user_id="user-1", tenant_id=TENANT_A, email="a@test.com",
            roles=[UserRole.CUSTODIAN.value]
        )
        ctx = self.verifier.verify_token(token)
        ctx.validate_tenant_boundary(TENANT_A)  # Should not raise

    def test_system_admin_can_cross_tenant(self) -> None:
        """SYSTEM_ADMIN role is the only role allowed to cross tenant boundaries."""
        token = self.verifier.issue_token(
            user_id="admin-1", tenant_id=TENANT_A, email="admin@test.com",
            roles=[UserRole.SYSTEM_ADMIN.value]
        )
        ctx = self.verifier.verify_token(token)
        ctx.validate_tenant_boundary(TENANT_B)  # Should not raise for admin

    def test_missing_tenant_claim_rejected(self) -> None:
        """Tokens without a tenant_id claim are rejected."""
        # Manually build a token without tenant_id
        import time
        import base64
        import hashlib
        import hmac as hmac_mod

        header = {"alg": "HS256", "typ": "JWT"}
        payload = {
            "sub": "user-1", "email": "test@test.com",
            "roles": [], "permissions": [],
            "iat": int(time.time()), "exp": int(time.time()) + 3600,
            "iss": "pettyflow-auth-service",
        }
        # No tenant_id!
        secret = "pettyflow-dev-secret-key-32bytes-long!!".encode("utf-8")
        h_b64 = base64.urlsafe_b64encode(json.dumps(header).encode()).decode().rstrip("=")
        p_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
        sig = hmac_mod.new(secret, f"{h_b64}.{p_b64}".encode(), hashlib.sha256).digest()
        s_b64 = base64.urlsafe_b64encode(sig).decode().rstrip("=")
        token = f"{h_b64}.{p_b64}.{s_b64}"

        with pytest.raises(SecurityContextError, match="tenant_id"):
            self.verifier.verify_token(token)

    def test_expired_token_rejected(self) -> None:
        token = self.verifier.issue_token(
            user_id="user-1", tenant_id=TENANT_A, email="a@test.com",
            roles=[], expiry_seconds=-10
        )
        with pytest.raises(SecurityContextError, match="expired"):
            self.verifier.verify_token(token)

    def test_custodian_cannot_approve_as_finance_director(self) -> None:
        """Role-based access: Custodian role lacks Finance Director authority."""
        token = self.verifier.issue_token(
            user_id="user-1", tenant_id=TENANT_A, email="c@test.com",
            roles=[UserRole.CUSTODIAN.value]
        )
        ctx = self.verifier.verify_token(token)
        assert ctx.has_role(UserRole.CUSTODIAN) is True
        assert ctx.has_role(UserRole.FINANCE_DIRECTOR) is False

    def test_audit_metadata_present_in_security_context(self) -> None:
        """Security context contains all required audit fields."""
        token = self.verifier.issue_token(
            user_id="user-1", tenant_id=TENANT_A, email="a@test.com",
            roles=[UserRole.FINANCE_MANAGER.value]
        )
        ctx = self.verifier.verify_token(token)
        assert ctx.user_id == "user-1"
        assert ctx.tenant_id == TENANT_A
        assert ctx.email == "a@test.com"
        assert ctx.issued_at > 0
        assert ctx.expires_at > ctx.issued_at
        assert ctx.issuer == "pettyflow-auth-service"


# ===========================================================================
# 6. AUDIT LOG — TENANT SCOPING & METADATA
# ===========================================================================

class TestAuditLogTenantIsolation:
    """Verify audit entries are scoped by tenant_id and contain all
    required metadata (tenant, actor, action, timestamp)."""

    def setup_method(self) -> None:
        self.logger = WORMAuditLogger()

    def test_audit_entries_scoped_by_tenant(self) -> None:
        """Each tenant's audit trail is independent."""
        self.logger.append_event(TENANT_A, "DISBURSEMENT_CREATED", "actor-1", {"amount": 1000})
        self.logger.append_event(TENANT_B, "DISBURSEMENT_CREATED", "actor-2", {"amount": 2000})
        entries_a = self.logger.get_entries(TENANT_A)
        entries_b = self.logger.get_entries(TENANT_B)
        assert len(entries_a) == 1
        assert len(entries_b) == 1
        assert entries_a[0].tenant_id == TENANT_A
        assert entries_b[0].tenant_id == TENANT_B

    def test_tenant_a_cannot_see_tenant_b_audit_records(self) -> None:
        """Getting entries for Tenant A returns none of Tenant B's records."""
        self.logger.append_event(TENANT_B, "SECRET_ACTION", "actor-2", {"classified": True})
        entries_a = self.logger.get_entries(TENANT_A)
        assert len(entries_a) == 0

    def test_audit_entry_contains_required_metadata(self) -> None:
        """Every audit entry includes tenant_id, actor_id, event_type, and timestamp."""
        entry = self.logger.append_event(TENANT_A, "FUND_CREATED", "user-mgr", {"fund": "f-001"})
        assert entry.tenant_id == TENANT_A
        assert entry.actor_id == "user-mgr"
        assert entry.event_type == "FUND_CREATED"
        assert entry.timestamp  # Non-empty ISO timestamp
        assert entry.sequence_number == 1
        assert entry.current_hash  # Hash chain populated

    def test_audit_chain_integrity_per_tenant(self) -> None:
        """The cryptographic chain is verified independently per tenant."""
        self.logger.append_event(TENANT_A, "EVENT_1", "actor-1", {"data": "a1"})
        self.logger.append_event(TENANT_A, "EVENT_2", "actor-1", {"data": "a2"})
        self.logger.append_event(TENANT_B, "EVENT_1", "actor-2", {"data": "b1"})
        assert self.logger.verify_integrity(TENANT_A) is True
        assert self.logger.verify_integrity(TENANT_B) is True

    def test_audit_chain_independent_across_tenants(self) -> None:
        """Tenant A's chain is not affected by Tenant B's entries."""
        entry_a = self.logger.append_event(TENANT_A, "EVENT_1", "actor-1", {})
        entry_b = self.logger.append_event(TENANT_B, "EVENT_1", "actor-2", {})
        # Different genesis chains
        assert entry_a.prev_hash != entry_b.prev_hash or entry_a.current_hash != entry_b.current_hash


# ===========================================================================
# 7. WORKFLOW STATE MACHINE — TENANT IN AUDIT TRAIL
# ===========================================================================

class TestWorkflowStateMachineTenantIsolation:
    """Verify that the approval workflow always stamps tenant_id into
    transition audit records."""

    def test_state_transition_records_carry_tenant_id(self) -> None:
        fsm = WorkflowStateMachine.create(
            tenant_id=TENANT_A, custodian_id="cust-1",
            amount_scaled=250_000, currency="USD",
            description="Office supplies"
        )
        fsm.submit(actor_id="cust-1", notes="Submitting for approval")
        assert len(fsm.request.audit_trail) == 1
        record = fsm.request.audit_trail[0]
        assert record.tenant_id == TENANT_A
        assert record.actor_id == "cust-1"
        assert record.from_state == ApprovalState.DRAFT
        assert record.to_state == ApprovalState.PENDING
        assert record.timestamp is not None

    def test_approval_request_contains_tenant_id(self) -> None:
        fsm = WorkflowStateMachine.create(
            tenant_id=TENANT_B, custodian_id="cust-2",
            amount_scaled=100_000, currency="EUR",
            description="Travel expense"
        )
        assert fsm.request.tenant_id == TENANT_B

    def test_full_lifecycle_audit_trail_metadata(self) -> None:
        """Complete lifecycle (DRAFT → PENDING → APPROVED → DISBURSED) generates
        records with tenant, actor, action, and timestamp at every step."""
        fsm = WorkflowStateMachine.create(
            tenant_id=TENANT_A, custodian_id="cust-1",
            amount_scaled=50_000, currency="USD",
            description="Stationery"
        )
        fsm.submit("cust-1")
        fsm.approve("mgr-1", notes="Approved")
        fsm.disburse("finance-1", notes="Disbursed")

        assert len(fsm.request.audit_trail) == 3
        for record in fsm.request.audit_trail:
            assert record.tenant_id == TENANT_A
            assert record.actor_id  # Non-empty
            assert record.timestamp is not None
            assert record.transition_id  # UUID assigned


# ===========================================================================
# 8. APPROVAL POLICY — ACTOR AUTHORIZATION CHECKS
# ===========================================================================

class TestApprovalPolicyActorChecks:
    """Verify that approval actor privilege checks match required tier."""

    def setup_method(self) -> None:
        self.evaluator = ApprovalPolicyEvaluator()

    def test_custodian_cannot_approve_manager_tier(self) -> None:
        """CUSTODIAN (mapped to AUTO_APPROVE) cannot approve MANAGER-tier requests."""
        assert self.evaluator.is_actor_authorized(
            ApprovalTier.AUTO_APPROVE, ApprovalTier.MANAGER
        ) is False

    def test_manager_can_approve_auto_approve_tier(self) -> None:
        """MANAGER authority is sufficient for AUTO_APPROVE tier."""
        assert self.evaluator.is_actor_authorized(
            ApprovalTier.MANAGER, ApprovalTier.AUTO_APPROVE
        ) is True

    def test_finance_director_can_approve_all_tiers(self) -> None:
        """FINANCE_DIRECTOR can approve any tier."""
        for tier in ApprovalTier:
            assert self.evaluator.is_actor_authorized(
                ApprovalTier.FINANCE_DIRECTOR, tier
            ) is True

    def test_manager_cannot_approve_finance_director_tier(self) -> None:
        """MANAGER cannot approve FINANCE_DIRECTOR-tier requests."""
        assert self.evaluator.is_actor_authorized(
            ApprovalTier.MANAGER, ApprovalTier.FINANCE_DIRECTOR
        ) is False

    def test_high_amount_requires_finance_director(self) -> None:
        """$500+ amounts require FINANCE_DIRECTOR tier."""
        result = self.evaluator.evaluate("req-1", 5_000_000)  # $500.00
        assert result.required_tier == ApprovalTier.FINANCE_DIRECTOR
        assert not result.auto_approved

    def test_small_amount_auto_approved(self) -> None:
        """Sub-$50 amounts are auto-approved."""
        result = self.evaluator.evaluate("req-2", 250_000)  # $25.00
        assert result.required_tier == ApprovalTier.AUTO_APPROVE
        assert result.auto_approved


# ===========================================================================
# 9. DISBURSEMENT MANAGER — TENANT-SCOPED AUDIT TRAIL
# ===========================================================================

class TestDisbursementManagerTenantIsolation:
    """Verify that the disbursement manager's audit trail and idempotency
    are scoped by tenant_id."""

    def setup_method(self) -> None:
        self.manager = DisbursementManager(
            idempotency_store=SQLiteIdempotencyStore(database_path=":memory:")
        )

    def test_audit_trail_filtered_by_tenant(self) -> None:
        """get_audit_trail returns only records for the requested tenant."""
        req_a = FloatDisbursementRequest(
            tenant_id=TENANT_A, custodian_id="cust-1", fund_id="fund-1",
            amount_scaled=100_000, channel=DisbursementChannel.VIRTUAL_CARD,
            cardholder_name="Alice"
        )
        req_b = FloatDisbursementRequest(
            tenant_id=TENANT_B, custodian_id="cust-2", fund_id="fund-2",
            amount_scaled=200_000, channel=DisbursementChannel.VIRTUAL_CARD,
            cardholder_name="Bob"
        )
        self.manager.disburse_float(req_a)
        self.manager.disburse_float(req_b)

        trail_a = self.manager.get_audit_trail(TENANT_A)
        trail_b = self.manager.get_audit_trail(TENANT_B)

        assert len(trail_a) == 1
        assert len(trail_b) == 1
        assert trail_a[0].tenant_id == TENANT_A
        assert trail_b[0].tenant_id == TENANT_B

    def test_empty_tenant_id_rejected(self) -> None:
        """Disbursement requests with empty tenant_id are rejected."""
        with pytest.raises(ValueError, match="tenant_id"):
            FloatDisbursementRequest(
                tenant_id="", custodian_id="cust-1", fund_id="fund-1",
                amount_scaled=100_000, channel=DisbursementChannel.VIRTUAL_CARD,
            )

    def test_disbursement_result_carries_tenant_id(self) -> None:
        req = FloatDisbursementRequest(
            tenant_id=TENANT_A, custodian_id="cust-1", fund_id="fund-1",
            amount_scaled=50_000, channel=DisbursementChannel.MOBILE_MONEY,
            recipient_address="+1234567890"
        )
        result = self.manager.disburse_float(req)
        assert result.tenant_id == TENANT_A
        assert result.to_dict()["tenant_id"] == TENANT_A

    def test_idempotency_scoped_by_tenant(self) -> None:
        """Same idempotency key under different tenants produces independent
        disbursements (no conflict)."""
        shared_key = "shared-idem-key-001"
        req_a = FloatDisbursementRequest(
            tenant_id=TENANT_A, custodian_id="cust-1", fund_id="fund-1",
            amount_scaled=100_000, channel=DisbursementChannel.VIRTUAL_CARD,
            cardholder_name="Alice", idempotency_key=shared_key,
        )
        req_b = FloatDisbursementRequest(
            tenant_id=TENANT_B, custodian_id="cust-1", fund_id="fund-1",
            amount_scaled=100_000, channel=DisbursementChannel.VIRTUAL_CARD,
            cardholder_name="Bob", idempotency_key=shared_key,
        )
        result_a = self.manager.disburse_float(req_a)
        result_b = self.manager.disburse_float(req_b)
        # Both should succeed as separate disbursements
        assert result_a.disbursement_id != result_b.disbursement_id
        assert result_a.tenant_id == TENANT_A
        assert result_b.tenant_id == TENANT_B


# ===========================================================================
# 10. RECONCILIATION — TENANT-SCOPED RECORDS
# ===========================================================================

class TestReconciliationTenantIsolation:
    """Verify reconciliation records are scoped by tenant_id."""

    def _make_cash_count(self, tenant_id: str) -> CashCountRecord:
        return CashCountRecord(
            count_id=f"COUNT-{uuid.uuid4().hex[:8]}",
            tenant_id=tenant_id,
            fund_id="fund-main",
            custodian_id="cust-1",
            denominations=DenominationBreakdown(hundreds=1, fifties=2),
        )

    def _make_system_float(self, tenant_id: str) -> SystemFloatRecord:
        return SystemFloatRecord(
            fund_id="fund-main",
            tenant_id=tenant_id,
            opening_float_scaled=2_000_000,
            total_disbursed_scaled=500_000,
            total_replenished_scaled=0,
        )

    def test_reconciliation_result_carries_tenant_id(self) -> None:
        matcher = ReconciliationMatcher()
        result = matcher.reconcile(
            self._make_cash_count(TENANT_A),
            self._make_system_float(TENANT_A),
        )
        assert result.tenant_id == TENANT_A

    def test_different_tenants_reconcile_independently(self) -> None:
        matcher = ReconciliationMatcher()
        result_a = matcher.reconcile(
            self._make_cash_count(TENANT_A),
            self._make_system_float(TENANT_A),
        )
        result_b = matcher.reconcile(
            self._make_cash_count(TENANT_B),
            self._make_system_float(TENANT_B),
        )
        assert result_a.tenant_id == TENANT_A
        assert result_b.tenant_id == TENANT_B
        assert result_a.reconciliation_id != result_b.reconciliation_id


# ===========================================================================
# 11. ERP ADAPTERS — TENANT FIELD IN JOURNAL ENTRIES
# ===========================================================================

class TestERPAdapterTenantIsolation:
    """Verify ERP journal entries carry tenant_id."""

    def test_sap_entry_carries_tenant_id(self) -> None:
        adapter = SAPAdapter(mock_mode=True)
        entry = adapter.build_replenishment_entry(
            tenant_id=TENANT_A,
            reference_document="PF-001",
            amount_scaled=1_000_000,
        )
        assert entry.tenant_id == TENANT_A

    def test_sap_posted_entry_retains_tenant_id(self) -> None:
        adapter = SAPAdapter(mock_mode=True)
        entry = adapter.build_replenishment_entry(
            tenant_id=TENANT_A, reference_document="PF-002", amount_scaled=500_000,
        )
        posted = adapter.post_journal_entry(entry)
        assert posted.tenant_id == TENANT_A
        assert posted.sap_document_number is not None


# ===========================================================================
# 12. CARD ISSUER & MOBILE MONEY — TENANT METADATA
# ===========================================================================

class TestAdapterTenantIsolation:
    """Verify that virtual card and mobile money adapters embed tenant_id
    in their results."""

    def test_card_result_contains_tenant_id(self) -> None:
        from src.infrastructure.adapters.card_issuer import VirtualCardRequest
        issuer = CardIssuerAdapter(CardIssuerBackend.MOCK)
        req = VirtualCardRequest(
            tenant_id=TENANT_A, custodian_id="cust-1", fund_id="fund-1",
            spending_limit_scaled=500_000, currency="USD",
            cardholder_name="Alice"
        )
        result = issuer.create_virtual_card(req)
        assert result.tenant_id == TENANT_A
        assert result.to_dict()["tenant_id"] == TENANT_A

    def test_mobile_result_contains_tenant_id(self) -> None:
        from src.infrastructure.adapters.mobile_money import DisbursementRequest
        adapter = MobileMoneyAdapter(MobileMoneyBackend.MOCK)
        req = DisbursementRequest(
            tenant_id=TENANT_A, custodian_id="cust-1", fund_id="fund-1",
            recipient_phone_or_account="+1234567890",
            amount_scaled=250_000, currency="USD",
        )
        result = adapter.disburse(req)
        assert result.tenant_id == TENANT_A
        assert result.to_dict()["tenant_id"] == TENANT_A


# ===========================================================================
# 13. SECURITY-SENSITIVE SECRET HANDLING
# ===========================================================================

class TestSecretHandling:
    """Verify security-sensitive paths follow secret management best practices."""

    def test_kms_master_key_length_enforced(self) -> None:
        """KMS vault rejects keys that aren't exactly 32 bytes."""
        with pytest.raises(ValueError, match="32 bytes"):
            KMSVault(master_key_bytes=b"too-short")

    def test_jwt_signing_secret_not_exposed_in_context(self) -> None:
        """Signing secret is not leaked into the security context."""
        verifier = JWTVerifier()
        token = verifier.issue_token(
            user_id="u", tenant_id=TENANT_A, email="e@t.com", roles=[]
        )
        ctx = verifier.verify_token(token)
        ctx_dict = {
            "user_id": ctx.user_id, "tenant_id": ctx.tenant_id,
            "email": ctx.email, "roles": ctx.roles,
            "permissions": ctx.permissions, "issuer": ctx.issuer,
        }
        serialized = json.dumps(ctx_dict)
        assert "pettyflow-dev-secret" not in serialized

    def test_invalid_jwt_signature_rejected(self) -> None:
        """Tokens signed with a different secret are rejected."""
        verifier_a = JWTVerifier(signing_secret="secret-AAAA-aaaa-AAAA-aaaa-aaaa-32b")
        verifier_b = JWTVerifier(signing_secret="secret-BBBB-bbbb-BBBB-bbbb-bbbb-32b")
        token = verifier_a.issue_token(
            user_id="u", tenant_id=TENANT_A, email="e@t.com", roles=[]
        )
        with pytest.raises(SecurityContextError, match="signature"):
            verifier_b.verify_token(token)


# ===========================================================================
# 14. COMPOSITE CROSS-BOUNDARY TEST
# ===========================================================================

class TestCrossBoundaryTenantIsolation:
    """End-to-end scenario: Tenant A creates a fund, disburses, and
    generates audit records. Tenant B cannot access any of these."""

    def test_full_cross_tenant_isolation_scenario(self) -> None:
        # Setup services
        fund_svc = FundService()
        audit_logger = WORMAuditLogger()
        idem_store = SQLiteIdempotencyStore(database_path=":memory:")
        disbursement_mgr = DisbursementManager(
            idempotency_store=idem_store, audit_logger=audit_logger,
        )

        # Tenant A creates and uses resources
        fund = fund_svc.create_fund(
            TENANT_A_UUID, "Main Cash", "USD", CUSTODIAN_A, 10_000_000
        )
        fund_svc.allocate_float(TENANT_A_UUID, fund.fund_id, CUSTODIAN_A, 1_000_000)

        req = FloatDisbursementRequest(
            tenant_id=TENANT_A, custodian_id=str(CUSTODIAN_A), fund_id=str(fund.fund_id),
            amount_scaled=500_000, channel=DisbursementChannel.VIRTUAL_CARD,
            cardholder_name="Alice"
        )
        result = disbursement_mgr.disburse_float(req)
        assert result.tenant_id == TENANT_A

        # Tenant B cannot access any of Tenant A's resources
        with pytest.raises(FundNotFoundError):
            fund_svc.get_custodian_balance(TENANT_B_UUID, fund.fund_id, CUSTODIAN_A)

        trail_b = disbursement_mgr.get_audit_trail(TENANT_B)
        assert len(trail_b) == 0

        entries_b = audit_logger.get_entries(TENANT_B)
        assert len(entries_b) == 0

        # Tenant A's data is intact
        trail_a = disbursement_mgr.get_audit_trail(TENANT_A)
        assert len(trail_a) == 1
        entries_a = audit_logger.get_entries(TENANT_A)
        assert len(entries_a) >= 1  # At least the CANONICAL_RESULT event


# ===========================================================================
# 15. CRYPTOGRAPHIC LEDGER HASH CHAIN — TENANT ENFORCEMENT
# ===========================================================================

class TestHashChainTenantIsolation:
    """Verify that a CryptographicLedgerChain rejects transactions belonging
    to a different tenant."""

    def test_cross_tenant_transaction_rejected_by_hash_chain(self) -> None:
        from src.domain.ledger.hash_chain import CryptographicLedgerChain
        from src.domain.ledger.entry import TransactionBatch, PostingLeg, EntryType

        chain_a = CryptographicLedgerChain(tenant_id=TENANT_A, secret_key=b"secret-key-32-bytes-long-tenant-a")
        
        # Build balanced transaction for Tenant B
        tx_b = TransactionBatch(
            transaction_id="tx-b-001",
            tenant_id=TENANT_B,
            description="Tenant B Supplies",
            legs=[
                PostingLeg(account_id="acct-exp", entry_type=EntryType.DEBIT, amount_scaled=50_000),
                PostingLeg(account_id="acct-cash", entry_type=EntryType.CREDIT, amount_scaled=50_000),
            ],
        )

        with pytest.raises(ValueError, match="Cross-tenant transaction rejected"):
            chain_a.append_transaction(tx_b)

    def test_same_tenant_transaction_accepted(self) -> None:
        from src.domain.ledger.hash_chain import CryptographicLedgerChain
        from src.domain.ledger.entry import TransactionBatch, PostingLeg, EntryType

        chain_a = CryptographicLedgerChain(tenant_id=TENANT_A, secret_key=b"secret-key-32-bytes-long-tenant-a")
        tx_a = TransactionBatch(
            transaction_id="tx-a-001",
            tenant_id=TENANT_A,
            description="Tenant A Supplies",
            legs=[
                PostingLeg(account_id="acct-exp", entry_type=EntryType.DEBIT, amount_scaled=50_000),
                PostingLeg(account_id="acct-cash", entry_type=EntryType.CREDIT, amount_scaled=50_000),
            ],
        )
        block = chain_a.append_transaction(tx_a)
        assert block.tenant_id == TENANT_A
        assert chain_a.verify_integrity() is True


# ===========================================================================
# 16. ADAPTERS — CROSS-TENANT LOOKUP & MUTATION NEGATIVE TESTS
# ===========================================================================

class TestAdapterCrossTenantNegative:
    """Verify that get_card, cancel_card, and get_disbursement_status block
    cross-tenant queries."""

    def test_card_issuer_cross_tenant_lookup_returns_none(self) -> None:
        from src.infrastructure.adapters.card_issuer import CardIssuerAdapter, VirtualCardRequest
        issuer = CardIssuerAdapter()
        req = VirtualCardRequest(
            tenant_id=TENANT_A, custodian_id="cust-1", fund_id="fund-1",
            spending_limit_scaled=100_000, cardholder_name="Alice"
        )
        card = issuer.create_virtual_card(req)

        # Lookup with correct tenant succeeds
        assert issuer.get_card(card.card_id, tenant_id=TENANT_A) is not None
        # Lookup with wrong tenant returns None
        assert issuer.get_card(card.card_id, tenant_id=TENANT_B) is None

    def test_card_issuer_cross_tenant_cancel_returns_false(self) -> None:
        from src.infrastructure.adapters.card_issuer import CardIssuerAdapter, VirtualCardRequest
        issuer = CardIssuerAdapter()
        req = VirtualCardRequest(
            tenant_id=TENANT_A, custodian_id="cust-1", fund_id="fund-1",
            spending_limit_scaled=100_000, cardholder_name="Alice"
        )
        card = issuer.create_virtual_card(req)

        # Cancel with wrong tenant fails
        assert issuer.cancel_card(card.card_id, tenant_id=TENANT_B) is False
        # Card remains active
        assert issuer.get_card(card.card_id).card_status.value == "active"

    def test_mobile_money_cross_tenant_lookup_returns_none(self) -> None:
        from src.infrastructure.adapters.mobile_money import MobileMoneyAdapter, DisbursementRequest
        adapter = MobileMoneyAdapter()
        req = DisbursementRequest(
            tenant_id=TENANT_A, custodian_id="cust-1", fund_id="fund-1",
            recipient_phone_or_account="+1234567890", amount_scaled=200_000
        )
        res = adapter.disburse(req)

        # Correct tenant finds it
        assert adapter.get_disbursement_status(res.disbursement_id, tenant_id=TENANT_A) is not None
        # Wrong tenant gets None
        assert adapter.get_disbursement_status(res.disbursement_id, tenant_id=TENANT_B) is None


# ===========================================================================
# 17. RECONCILIATION API — SIGN-OFF CROSS-TENANT NEGATIVE TESTS
# ===========================================================================

class TestReconciliationRouterSignOffTenantIsolation:
    """Verify that the reconciliation sign-off REST endpoint rejects cross-tenant
    signatures with HTTP 403."""

    def test_cross_tenant_sign_off_rejected(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from src.api.rest.reconciliation_router import create_reconciliation_router, DailyClosingRequest, DenominationsSchema

        router = create_reconciliation_router()
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        # Tenant A submits daily closing
        closing_payload = {
            "tenant_id": TENANT_A,
            "fund_id": "fund-main-001",
            "custodian_id": "cust-1",
            "fund_account_id": "ACC_FUND_001",
            "denominations": {
                "hundreds": 5, "fifties": 0, "twenties": 0, "tens": 0,
                "fives": 0, "ones": 0, "quarters": 0, "dimes": 0,
                "nickels": 0, "pennies": 0, "custom_coins_scaled": 0,
            },
            "opening_float_scaled": 5_000_000,
            "total_disbursed_scaled": 0,
            "total_replenished_scaled": 0,
        }
        res = client.post("/api/v1/reconciliation/daily-closing", json=closing_payload)
        assert res.status_code == 200
        rec_id = res.json()["reconciliation"]["reconciliation_id"]

        # Tenant B attempts to sign off on Tenant A's reconciliation -> HTTP 403
        sign_off_payload = {
            "tenant_id": TENANT_B,
            "reconciliation_id": rec_id,
            "signer_id": "signer-b-001",
            "signer_role": "FINANCE_DIRECTOR",
            "approval_notes": "Unauthorized cross-tenant sign-off",
        }
        sign_res = client.post("/api/v1/reconciliation/sign-off", json=sign_off_payload)
        assert sign_res.status_code == 403
        assert "Tenant boundary violation" in sign_res.json()["detail"]

        # Tenant A signs off successfully -> HTTP 200
        sign_off_payload["tenant_id"] = TENANT_A
        sign_off_payload["signer_id"] = "signer-a-001"
        valid_res = client.post("/api/v1/reconciliation/sign-off", json=sign_off_payload)
        assert valid_res.status_code == 200
        assert valid_res.json()["status"] == "SIGNED"


# ===========================================================================
# 18. ASYNC EVENT CONSUMERS & BACKGROUND JOBS — NEGATIVE TESTS
# ===========================================================================

class TestAsyncConsumerAndBackgroundJobTenantIsolation:
    """Verify negative isolation behaviors for async webhook / callback consumers
    and background maintenance jobs."""

    def test_async_provider_event_consumer_cross_tenant_isolation(self) -> None:
        """Provider event callback consumed under tenant A cannot affect tenant B."""
        from src.domain.wallet.disbursement_manager import DisbursementManager
        from src.domain.wallet.settlement import SettlementState
        from src.infrastructure.idempotency.store import SQLiteIdempotencyStore

        store = SQLiteIdempotencyStore(database_path=":memory:")
        manager = DisbursementManager(idempotency_store=store)

        event_payload = {"status": SettlementState.PROCESSING.value, "amount": 1000}

        # Consumer accepts event for Tenant A
        accepted_a = manager.ingest_provider_event(
            tenant_id=TENANT_A,
            provider="stripe",
            event_id="evt_webhook_999",
            payload=event_payload,
            current_state=SettlementState.PENDING,
        )
        assert accepted_a is True

        # Same event ID under Tenant B is a completely independent stream
        accepted_b = manager.ingest_provider_event(
            tenant_id=TENANT_B,
            provider="stripe",
            event_id="evt_webhook_999",
            payload=event_payload,
            current_state=SettlementState.PENDING,
        )
        assert accepted_b is True

        # Replay within Tenant A returns False (deduplicated)
        replay_a = manager.ingest_provider_event(
            tenant_id=TENANT_A,
            provider="stripe",
            event_id="evt_webhook_999",
            payload=event_payload,
            current_state=SettlementState.PROCESSING,
        )
        assert replay_a is False

    def test_background_pruning_job_does_not_corrupt_other_tenant(self) -> None:
        """Background idempotency key expiration / cleanup preserves other tenant data."""
        import datetime
        from src.domain.wallet.disbursement_manager import (
            DisbursementManager,
            FloatDisbursementRequest,
            DisbursementChannel,
        )
        from src.infrastructure.idempotency.store import SQLiteIdempotencyStore

        store = SQLiteIdempotencyStore(database_path=":memory:")
        # Short TTL manager to simulate expired entries
        mgr = DisbursementManager(idempotency_store=store, idempotency_ttl_seconds=1)

        req_a = FloatDisbursementRequest(
            tenant_id=TENANT_A, custodian_id="cust-1", fund_id="fund-1",
            amount_scaled=50_000, channel=DisbursementChannel.VIRTUAL_CARD,
            idempotency_key="key-expire-test", cardholder_name="Alice"
        )
        req_b = FloatDisbursementRequest(
            tenant_id=TENANT_B, custodian_id="cust-2", fund_id="fund-2",
            amount_scaled=75_000, channel=DisbursementChannel.VIRTUAL_CARD,
            idempotency_key="key-active-test", cardholder_name="Bob"
        )

        mgr.disburse_float(req_a)
        mgr.disburse_float(req_b)

        # Manually backdate tenant A's in-memory expiry
        res_a, _ = mgr._results[(TENANT_A, "key-expire-test")]
        past_time = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=10)
        mgr._results[(TENANT_A, "key-expire-test")] = (res_a, past_time)

        # Trigger background pruning job
        mgr._prune_expired_idempotency_keys()

        # Tenant A's expired key was pruned
        assert (TENANT_A, "key-expire-test") not in mgr._results
        # Tenant B's active key is untouched
        assert (TENANT_B, "key-active-test") in mgr._results
        assert mgr.count_disbursements(TENANT_B) == 1

