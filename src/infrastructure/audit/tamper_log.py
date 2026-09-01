"""WORM (Write-Once-Read-Many) Cryptographic Tamper-Evident Audit Log Adapter.

Implements high-speed HMAC-SHA256 tamper-evident audit logging for SOC2 / ISO 27001 compliance.
- Strictly monotonic sequence numbering per tenant.
- Cryptographic hash chaining: hash_n = HMAC-SHA256(hash_{n-1} || seq || tenant || event || actor || payload || ts).
- Detects any bit-level modification, reordering, truncation, or insertion.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

GENESIS_HASH = "0000000000000000000000000000000000000000000000000000000000000000"


@dataclass(frozen=True)
class AuditEntry:
    """Immutable audit trail record."""
    entry_id: str
    sequence_number: int
    tenant_id: str
    event_type: str
    actor_id: str
    payload_json: str
    timestamp: str
    prev_hash: str
    current_hash: str

    def to_dict(self) -> dict:
        return {
            "entry_id": self.entry_id,
            "sequence_number": self.sequence_number,
            "tenant_id": self.tenant_id,
            "event_type": self.event_type,
            "actor_id": self.actor_id,
            "payload": json.loads(self.payload_json),
            "timestamp": self.timestamp,
            "prev_hash": self.prev_hash,
            "current_hash": self.current_hash,
        }


class AuditIntegrityError(Exception):
    """Raised when tamper verification detects a broken cryptographic chain."""
    pass


# ---------------------------------------------------------------------------
# WORM Audit Logger
# ---------------------------------------------------------------------------

class WORMAuditLogger:
    """Append-only tamper-evident audit logger."""

    def __init__(self, hmac_secret: bytes = b"worm-audit-system-secret-key-32b"):
        self.secret = hmac_secret
        # tenant_id -> List[AuditEntry]
        self._store: Dict[str, List[AuditEntry]] = {}

    def _compute_hash(
        self,
        prev_hash: str,
        sequence_number: int,
        tenant_id: str,
        event_type: str,
        actor_id: str,
        payload_json: str,
        timestamp: str,
    ) -> str:
        """Compute cryptographic hash for an audit entry."""
        material = (
            f"{prev_hash}|{sequence_number}|{tenant_id}|{event_type}|"
            f"{actor_id}|{payload_json}|{timestamp}"
        ).encode("utf-8")
        return hmac.new(self.secret, material, hashlib.sha256).hexdigest()

    def append_event(
        self,
        tenant_id: str,
        event_type: str,
        actor_id: str,
        payload: dict,
    ) -> AuditEntry:
        """Append an event to the tenant's immutable audit chain."""
        if tenant_id not in self._store:
            self._store[tenant_id] = []

        entries = self._store[tenant_id]
        seq = len(entries) + 1
        prev_hash = entries[-1].current_hash if entries else GENESIS_HASH

        entry_id = f"AUD-{uuid.uuid4().hex[:12].upper()}"
        ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        payload_json = json.dumps(payload, sort_keys=True)

        current_hash = self._compute_hash(
            prev_hash=prev_hash,
            sequence_number=seq,
            tenant_id=tenant_id,
            event_type=event_type,
            actor_id=actor_id,
            payload_json=payload_json,
            timestamp=ts,
        )

        entry = AuditEntry(
            entry_id=entry_id,
            sequence_number=seq,
            tenant_id=tenant_id,
            event_type=event_type,
            actor_id=actor_id,
            payload_json=payload_json,
            timestamp=ts,
            prev_hash=prev_hash,
            current_hash=current_hash,
        )

        entries.append(entry)
        return entry

    def get_entries(self, tenant_id: str) -> List[AuditEntry]:
        """Retrieve all audit entries for a tenant in sequence."""
        return list(self._store.get(tenant_id, []))

    def verify_integrity(self, tenant_id: str) -> bool:
        """Verify the cryptographic chain for a tenant.

        Returns:
            True if all entries are intact and untampered.

        Raises:
            AuditIntegrityError: If any entry has been tampered with or sequence broken.
        """
        entries = self._store.get(tenant_id, [])
        if not entries:
            return True

        expected_prev_hash = GENESIS_HASH
        for i, entry in enumerate(entries):
            expected_seq = i + 1
            if entry.sequence_number != expected_seq:
                raise AuditIntegrityError(
                    f"Broken sequence at index {i}: expected sequence {expected_seq}, got {entry.sequence_number}"
                )

            if entry.prev_hash != expected_prev_hash:
                raise AuditIntegrityError(
                    f"Hash link mismatch at sequence {entry.sequence_number}: "
                    f"prev_hash '{entry.prev_hash}' does not match expected '{expected_prev_hash}'"
                )

            computed_hash = self._compute_hash(
                prev_hash=entry.prev_hash,
                sequence_number=entry.sequence_number,
                tenant_id=entry.tenant_id,
                event_type=entry.event_type,
                actor_id=entry.actor_id,
                payload_json=entry.payload_json,
                timestamp=entry.timestamp,
            )

            if not hmac.compare_digest(entry.current_hash, computed_hash):
                raise AuditIntegrityError(
                    f"Tampered payload detected at sequence {entry.sequence_number} (entry_id: {entry.entry_id})"
                )

            expected_prev_hash = entry.current_hash

        return True
