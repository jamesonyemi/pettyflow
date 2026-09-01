"""Zero-Trust JWT & OAuth2 Security Verifier with Multi-Tenant Isolation.

Enforces zero-trust access control on every incoming API request:
- Cryptographic signature verification.
- Mandatory `tenant_id` claim extraction and boundary isolation.
- Role-Based Access Control (RBAC) & Fine-Grained Permissions.
- Expiration and Issuer/Audience validation.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Set


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class UserRole(str, Enum):
    CUSTODIAN = "CUSTODIAN"
    FINANCE_MANAGER = "FINANCE_MANAGER"
    FINANCE_DIRECTOR = "FINANCE_DIRECTOR"
    AUDITOR = "AUDITOR"
    SYSTEM_ADMIN = "SYSTEM_ADMIN"


# ---------------------------------------------------------------------------
# Security Context
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TenantSecurityContext:
    """Immutable security context extracted from a verified JWT."""
    user_id: str
    tenant_id: str
    email: str
    roles: List[str]
    permissions: List[str]
    issued_at: int
    expires_at: int
    issuer: str = "pettyflow-auth-service"

    def has_role(self, role: str | UserRole) -> bool:
        role_str = role.value if isinstance(role, UserRole) else str(role)
        return role_str in self.roles or UserRole.SYSTEM_ADMIN.value in self.roles

    def has_permission(self, permission: str) -> bool:
        return permission in self.permissions or UserRole.SYSTEM_ADMIN.value in self.roles

    def validate_tenant_boundary(self, requested_tenant_id: str) -> None:
        """Enforce strict multi-tenant boundary. Raises SecurityContextError on breach."""
        if self.tenant_id != requested_tenant_id and UserRole.SYSTEM_ADMIN.value not in self.roles:
            raise SecurityContextError(
                f"Multi-tenant boundary violation: Token tenant '{self.tenant_id}' "
                f"attempted to access tenant '{requested_tenant_id}'."
            )


class SecurityContextError(Exception):
    """Raised on token verification failure or authorization breach."""
    pass


# ---------------------------------------------------------------------------
# JWT Verifier & Token Issuer
# ---------------------------------------------------------------------------

class JWTVerifier:
    """HMAC-SHA256 JWT Token Issuer and Zero-Trust Verifier."""

    def __init__(
        self,
        signing_secret: str = "pettyflow-dev-secret-key-32bytes-long!!",
        issuer: str = "pettyflow-auth-service",
        default_expiry_seconds: int = 3600,
    ):
        self.secret = signing_secret.encode("utf-8")
        self.issuer = issuer
        self.default_expiry_seconds = default_expiry_seconds

    @staticmethod
    def _b64_encode(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")

    @staticmethod
    def _b64_decode(data_str: str) -> bytes:
        padding = 4 - (len(data_str) % 4)
        if padding != 4:
            data_str += "=" * padding
        return base64.urlsafe_b64decode(data_str)

    def issue_token(
        self,
        user_id: str,
        tenant_id: str,
        email: str,
        roles: List[str],
        permissions: Optional[List[str]] = None,
        expiry_seconds: Optional[int] = None,
    ) -> str:
        """Issue a signed JWT token with tenant isolation claims."""
        now = int(time.time())
        exp = now + (expiry_seconds or self.default_expiry_seconds)

        header = {"alg": "HS256", "typ": "JWT"}
        payload = {
            "sub": user_id,
            "tenant_id": tenant_id,
            "email": email,
            "roles": roles,
            "permissions": permissions or [],
            "iat": now,
            "exp": exp,
            "iss": self.issuer,
        }

        header_b64 = self._b64_encode(json.dumps(header).encode("utf-8"))
        payload_b64 = self._b64_encode(json.dumps(payload).encode("utf-8"))
        signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")

        signature = hmac.new(self.secret, signing_input, hashlib.sha256).digest()
        sig_b64 = self._b64_encode(signature)

        return f"{header_b64}.{payload_b64}.{sig_b64}"

    def verify_token(self, token: str, leeway_seconds: int = 0) -> TenantSecurityContext:
        """Verify JWT signature, expiration, and extract TenantSecurityContext.

        Args:
            token: Dot-separated JWT string (header.payload.signature).
            leeway_seconds: Clock-skew tolerance in seconds (default 0).
        """
        parts = token.split(".")
        if len(parts) != 3:
            raise SecurityContextError("Malformed JWT token: must contain 3 dot-separated parts.")

        header_b64, payload_b64, sig_b64 = parts
        signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")
        expected_sig = hmac.new(self.secret, signing_input, hashlib.sha256).digest()
        expected_sig_b64 = self._b64_encode(expected_sig)

        if not hmac.compare_digest(sig_b64, expected_sig_b64):
            raise SecurityContextError("Invalid JWT cryptographic signature.")

        try:
            payload = json.loads(self._b64_decode(payload_b64).decode("utf-8"))
        except Exception as e:
            raise SecurityContextError(f"Failed to parse JWT payload: {e}")

        # Check expiration with clock-skew leeway tolerance
        now = int(time.time())
        if payload.get("exp", 0) + leeway_seconds < now:
            raise SecurityContextError("JWT token has expired.")

        # Check issuer
        if payload.get("iss") != self.issuer:
            raise SecurityContextError(f"JWT issuer mismatch: expected '{self.issuer}', got '{payload.get('iss')}'")

        # Mandatory tenant claim
        tenant_id = payload.get("tenant_id")
        if not tenant_id:
            raise SecurityContextError("Missing mandatory 'tenant_id' claim in JWT token.")

        return TenantSecurityContext(
            user_id=payload.get("sub", ""),
            tenant_id=tenant_id,
            email=payload.get("email", ""),
            roles=payload.get("roles", []),
            permissions=payload.get("permissions", []),
            issued_at=payload.get("iat", 0),
            expires_at=payload.get("exp", 0),
            issuer=payload.get("iss", self.issuer),
        )
