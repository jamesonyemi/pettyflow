"""KMS & Envelope Encryption Module for Field-Level PII & Sensitive Financial Data.

Implements NIST-compliant Envelope Encryption:
- Master Key (KEK - Key Encryption Key) managed in KMS / Vault.
- Data Encryption Key (DEK) generated per field/record using AES-256-GCM (256-bit).
- Additional Authenticated Data (AAD) cryptographically binds the ciphertext to the `tenant_id`.
- Any attempt to decrypt data belonging to Tenant A using Tenant B's context fails auth tag validation.

Zero-Trust Invariant:
PII fields in PostgreSQL / TimescaleDB are stored strictly as EncryptedEnvelope payloads.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass, field
from typing import Dict, Optional
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class EncryptedEnvelope:
    """NIST-compliant envelope containing ciphertext and wrapped DEK."""
    key_id: str
    tenant_id: str
    ciphertext_b64: str          # AES-256-GCM encrypted payload
    wrapped_dek_b64: str         # KEK-wrapped DEK
    nonce_b64: str               # 96-bit AES-GCM IV/nonce
    created_at: str

    def to_json(self) -> str:
        return json.dumps({
            "key_id": self.key_id,
            "tenant_id": self.tenant_id,
            "ciphertext": self.ciphertext_b64,
            "wrapped_dek": self.wrapped_dek_b64,
            "nonce": self.nonce_b64,
            "created_at": self.created_at,
        })

    @classmethod
    def from_json(cls, data: str) -> EncryptedEnvelope:
        d = json.loads(data)
        return cls(
            key_id=d["key_id"],
            tenant_id=d["tenant_id"],
            ciphertext_b64=d["ciphertext"],
            wrapped_dek_b64=d["wrapped_dek"],
            nonce_b64=d["nonce"],
            created_at=d.get("created_at", ""),
        )


class KMSCryptoError(Exception):
    """Raised when encryption or decryption fails (e.g. auth tag mismatch or cross-tenant access)."""
    pass


# ---------------------------------------------------------------------------
# KMS Vault Provider
# ---------------------------------------------------------------------------

class KMSVault:
    """Envelope encryption provider supporting AWS KMS / HashiCorp Vault architectures."""

    def __init__(self, master_key_bytes: Optional[bytes] = None, key_id: str = "kms-master-pettyflow-v1"):
        """Initialize KMS with 256-bit Key Encryption Key (KEK)."""
        self.key_id = key_id
        # 32 bytes (256-bit) KEK
        self._kek = master_key_bytes or secrets.token_bytes(32)
        if len(self._kek) != 32:
            raise ValueError("Master key must be exactly 32 bytes (256 bits).")

    def _wrap_dek(self, dek: bytes, tenant_id: str) -> bytes:
        """Wrap (encrypt) Data Encryption Key using KEK with tenant-bound AAD."""
        aesgcm = AESGCM(self._kek)
        nonce = secrets.token_bytes(12)
        aad = f"tenant:{tenant_id}:kek_wrap".encode("utf-8")
        wrapped = aesgcm.encrypt(nonce, dek, aad)
        return nonce + wrapped

    def _unwrap_dek(self, wrapped_payload: bytes, tenant_id: str) -> bytes:
        """Unwrap (decrypt) Data Encryption Key using KEK."""
        if len(wrapped_payload) < 28:
            raise KMSCryptoError("Corrupted wrapped DEK payload.")
        nonce = wrapped_payload[:12]
        ciphertext = wrapped_payload[12:]
        aad = f"tenant:{tenant_id}:kek_wrap".encode("utf-8")
        try:
            aesgcm = AESGCM(self._kek)
            return aesgcm.decrypt(nonce, ciphertext, aad)
        except Exception as e:
            raise KMSCryptoError(f"Failed to unwrap DEK: cryptographic authentication failed: {e}")

    def encrypt_field(self, plaintext: str, tenant_id: str) -> EncryptedEnvelope:
        """Encrypt a plaintext string using envelope encryption bound to tenant_id.

        Args:
            plaintext: Sensitive string (e.g., SSN, bank account, PII).
            tenant_id: Target tenant UUID for cryptographic domain separation.

        Returns:
            EncryptedEnvelope with AES-256-GCM ciphertext and wrapped DEK.
        """
        import datetime
        # 1. Generate unique 256-bit DEK
        dek = secrets.token_bytes(32)

        # 2. Encrypt plaintext with DEK using AES-256-GCM
        nonce = secrets.token_bytes(12)
        aad = f"tenant:{tenant_id}:field_encryption".encode("utf-8")
        aesgcm = AESGCM(dek)
        ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), aad)

        # 3. Wrap DEK with KEK
        wrapped_dek = self._wrap_dek(dek, tenant_id)

        return EncryptedEnvelope(
            key_id=self.key_id,
            tenant_id=tenant_id,
            ciphertext_b64=base64.b64encode(ciphertext).decode("ascii"),
            wrapped_dek_b64=base64.b64encode(wrapped_dek).decode("ascii"),
            nonce_b64=base64.b64encode(nonce).decode("ascii"),
            created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        )

    def decrypt_field(self, envelope: EncryptedEnvelope, accessing_tenant_id: str) -> str:
        """Decrypt an EncryptedEnvelope ensuring strict tenant isolation.

        Args:
            envelope: EncryptedEnvelope to decrypt.
            accessing_tenant_id: Tenant requesting decryption.

        Returns:
            Decrypted plaintext string.

        Raises:
            KMSCryptoError: If accessing_tenant_id does not match envelope.tenant_id
                            or if ciphertext is tampered.
        """
        if envelope.tenant_id != accessing_tenant_id:
            raise KMSCryptoError(
                f"Cross-tenant access blocked: Envelope belongs to tenant '{envelope.tenant_id}' "
                f"but decryption requested by '{accessing_tenant_id}'"
            )

        try:
            wrapped_dek = base64.b64decode(envelope.wrapped_dek_b64)
            ciphertext = base64.b64decode(envelope.ciphertext_b64)
            nonce = base64.b64decode(envelope.nonce_b64)

            # Unwrap DEK
            dek = self._unwrap_dek(wrapped_dek, accessing_tenant_id)

            # Decrypt ciphertext
            aad = f"tenant:{accessing_tenant_id}:field_encryption".encode("utf-8")
            aesgcm = AESGCM(dek)
            plaintext_bytes = aesgcm.decrypt(nonce, ciphertext, aad)
            return plaintext_bytes.decode("utf-8")
        except KMSCryptoError:
            raise
        except Exception as e:
            raise KMSCryptoError(f"Decryption failed or payload tampered: {e}")
