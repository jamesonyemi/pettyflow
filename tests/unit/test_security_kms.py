"""Unit and Security Tests for Week 10: Multi-Tenant Zero-Trust Security, KMS & Audit Framework."""

from __future__ import annotations

import json
import time
import pytest

from src.infrastructure.security.kms_vault import (
    EncryptedEnvelope,
    KMSCryptoError,
    KMSVault,
)
from src.infrastructure.security.jwt_verifier import (
    JWTVerifier,
    SecurityContextError,
    UserRole,
)
from src.infrastructure.audit.tamper_log import (
    AuditEntry,
    AuditIntegrityError,
    WORMAuditLogger,
)


# ---------------------------------------------------------------------------
# Test KMS Vault & Envelope Encryption
# ---------------------------------------------------------------------------

class TestKMSVault:
    def setup_method(self):
        self.vault = KMSVault()

    def test_encrypt_decrypt_roundtrip(self):
        sensitive_data = "SSN: 000-12-3456 | Routing: 021000021"
        tenant_id = "tenant-enterprise-alpha"

        envelope = self.vault.encrypt_field(sensitive_data, tenant_id)
        assert envelope.tenant_id == tenant_id
        assert envelope.ciphertext_b64 != sensitive_data
        assert len(envelope.wrapped_dek_b64) > 0

        # Decrypt with correct tenant context
        decrypted = self.vault.decrypt_field(envelope, accessing_tenant_id=tenant_id)
        assert decrypted == sensitive_data

    def test_cross_tenant_decryption_blocked_at_tenant_check(self):
        sensitive_data = "Corporate Tax ID: 99-8877665"
        tenant_a = "tenant-acme-corp"
        tenant_b = "tenant-evil-corp"

        envelope = self.vault.encrypt_field(sensitive_data, tenant_a)

        # Attempting decryption as Tenant B must fail
        with pytest.raises(KMSCryptoError, match="Cross-tenant access blocked"):
            self.vault.decrypt_field(envelope, accessing_tenant_id=tenant_b)

    def test_tampered_ciphertext_fails_decryption(self):
        sensitive_data = "Secret Bank Balance"
        tenant_id = "tenant-001"

        envelope = self.vault.encrypt_field(sensitive_data, tenant_id)

        # Tamper with ciphertext
        tampered_envelope = EncryptedEnvelope(
            key_id=envelope.key_id,
            tenant_id=envelope.tenant_id,
            ciphertext_b64="dGFtcGVyZWQtY2lwaGVydGV4dC1kYXRh",
            wrapped_dek_b64=envelope.wrapped_dek_b64,
            nonce_b64=envelope.nonce_b64,
            created_at=envelope.created_at,
        )

        with pytest.raises(KMSCryptoError, match="Decryption failed"):
            self.vault.decrypt_field(tampered_envelope, accessing_tenant_id=tenant_id)

    def test_envelope_json_serialization(self):
        envelope = self.vault.encrypt_field("Test Data", "tenant-1")
        json_str = envelope.to_json()
        restored = EncryptedEnvelope.from_json(json_str)

        assert restored.key_id == envelope.key_id
        assert restored.tenant_id == envelope.tenant_id
        assert restored.ciphertext_b64 == envelope.ciphertext_b64
        assert self.vault.decrypt_field(restored, "tenant-1") == "Test Data"


# ---------------------------------------------------------------------------
# Test Zero-Trust JWT Verifier
# ---------------------------------------------------------------------------

class TestJWTVerifier:
    def setup_method(self):
        self.verifier = JWTVerifier(signing_secret="my-super-secure-test-secret-key-32b!")

    def test_issue_and_verify_token(self):
        token = self.verifier.issue_token(
            user_id="user_123",
            tenant_id="tenant_abc",
            email="custodian@example.com",
            roles=[UserRole.CUSTODIAN.value],
            permissions=["float:disburse", "receipt:upload"],
        )

        ctx = self.verifier.verify_token(token)
        assert ctx.user_id == "user_123"
        assert ctx.tenant_id == "tenant_abc"
        assert ctx.has_role(UserRole.CUSTODIAN) is True
        assert ctx.has_role(UserRole.FINANCE_DIRECTOR) is False
        assert ctx.has_permission("float:disburse") is True
        assert ctx.has_permission("system:admin") is False

    def test_tenant_boundary_validation(self):
        token = self.verifier.issue_token(
            user_id="user_123",
            tenant_id="tenant_abc",
            email="user@abc.com",
            roles=[UserRole.CUSTODIAN.value],
        )
        ctx = self.verifier.verify_token(token)

        # Same tenant should pass
        ctx.validate_tenant_boundary("tenant_abc")

        # Different tenant must raise SecurityContextError
        with pytest.raises(SecurityContextError, match="Multi-tenant boundary violation"):
            ctx.validate_tenant_boundary("tenant_xyz")

    def test_expired_token_rejected(self):
        token = self.verifier.issue_token(
            user_id="user_123",
            tenant_id="tenant_abc",
            email="user@abc.com",
            roles=[UserRole.CUSTODIAN.value],
            expiry_seconds=-10,  # Expired 10 seconds ago
        )
        # Without leeway or insufficient leeway, it fails
        with pytest.raises(SecurityContextError, match="JWT token has expired"):
            self.verifier.verify_token(token, leeway_seconds=0)

        with pytest.raises(SecurityContextError, match="JWT token has expired"):
            self.verifier.verify_token(token, leeway_seconds=5)

        # With adequate clock skew leeway, it succeeds
        ctx = self.verifier.verify_token(token, leeway_seconds=30)
        assert ctx.user_id == "user_123"
        assert ctx.tenant_id == "tenant_abc"

    def test_invalid_signature_rejected(self):
        token = self.verifier.issue_token(
            user_id="user_123",
            tenant_id="tenant_abc",
            email="user@abc.com",
            roles=[UserRole.CUSTODIAN.value],
        )
        # Modify token payload
        parts = token.split(".")
        tampered_token = f"{parts[0]}.eyJuYW1lIjoidGFtcGVyZWQifQ.{parts[2]}"

        with pytest.raises(SecurityContextError, match="Invalid JWT cryptographic signature"):
            self.verifier.verify_token(tampered_token)


# ---------------------------------------------------------------------------
# Test WORM Tamper-Evident Audit Log
# ---------------------------------------------------------------------------

class TestWORMAuditLogger:
    def setup_method(self):
        self.logger = WORMAuditLogger()

    def test_audit_append_and_verify_chain(self):
        tenant_id = "tenant-audit-1"

        # Append several events
        self.logger.append_event(
            tenant_id=tenant_id,
            event_type="FLOAT_ALLOCATED",
            actor_id="user_admin",
            payload={"fund_id": "F1", "amount": 5000000},
        )
        self.logger.append_event(
            tenant_id=tenant_id,
            event_type="DISBURSEMENT_ISSUED",
            actor_id="custodian_01",
            payload={"disbursement_id": "D1", "amount": 1000000},
        )
        self.logger.append_event(
            tenant_id=tenant_id,
            event_type="DAILY_CLOSING_SIGNED",
            actor_id="manager_01",
            payload={"reconciliation_id": "R1", "status": "BALANCED"},
        )

        entries = self.logger.get_entries(tenant_id)
        assert len(entries) == 3
        assert entries[0].sequence_number == 1
        assert entries[1].sequence_number == 2
        assert entries[2].sequence_number == 3

        # Verify integrity
        assert self.logger.verify_integrity(tenant_id) is True

    def test_tampered_payload_detected(self):
        tenant_id = "tenant-tamper-test"

        self.logger.append_event(tenant_id, "EVENT_1", "actor_1", {"data": "orig_1"})
        self.logger.append_event(tenant_id, "EVENT_2", "actor_1", {"data": "orig_2"})

        # Tamper directly with the stored event in memory
        entries = self.logger._store[tenant_id]
        tampered_entry = AuditEntry(
            entry_id=entries[0].entry_id,
            sequence_number=entries[0].sequence_number,
            tenant_id=entries[0].tenant_id,
            event_type=entries[0].event_type,
            actor_id=entries[0].actor_id,
            payload_json=json.dumps({"data": "TAMPERED_FRAUD_DATA"}),
            timestamp=entries[0].timestamp,
            prev_hash=entries[0].prev_hash,
            current_hash=entries[0].current_hash,
        )
        entries[0] = tampered_entry

        with pytest.raises(AuditIntegrityError, match="Tampered payload detected"):
            self.logger.verify_integrity(tenant_id)
