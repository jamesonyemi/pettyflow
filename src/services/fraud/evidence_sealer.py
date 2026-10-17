"""Tamper-Evident Fraud Evidence Packaging and WORM Audit Sealing.

Seals immutable, cryptographically hashed fraud evidence packages directly into
the WORM (Write-Once-Read-Many) audit trail for compliance, regulatory auditability,
and tamper detection.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.infrastructure.audit.tamper_log import AuditEntry, WORMAuditLogger
from src.services.fraud.category_anomaly import MerchantCategoryAlert
from src.services.fraud.split_tx_detector import SplitTransactionAlert
from src.services.fraud.velocity_detector import VelocityAnomalyAlert


@dataclass(frozen=True)
class FraudEvidencePackage:
    """Tamper-evident canonical fraud evidence package."""
    evidence_id: str
    incident_type: str
    tenant_id: str
    custodians_involved: List[str]
    transaction_ids: List[str]
    evidence_payload: Dict[str, Any]
    created_at: str
    package_sha256: str

    def to_dict(self) -> dict:
        return {
            "evidence_id": self.evidence_id,
            "incident_type": self.incident_type,
            "tenant_id": self.tenant_id,
            "custodians_involved": self.custodians_involved,
            "transaction_ids": self.transaction_ids,
            "evidence_payload": self.evidence_payload,
            "created_at": self.created_at,
            "package_sha256": self.package_sha256,
        }


@dataclass(frozen=True)
class SealedEvidenceReceipt:
    """Receipt proving fraud evidence was appended into the WORM audit trail."""
    evidence_package: FraudEvidencePackage
    audit_entry: AuditEntry

    @property
    def sequence_number(self) -> int:
        return self.audit_entry.sequence_number

    @property
    def current_hash(self) -> str:
        return self.audit_entry.current_hash

    @property
    def prev_hash(self) -> str:
        return self.audit_entry.prev_hash

    def to_dict(self) -> dict:
        return {
            "evidence_id": self.evidence_package.evidence_id,
            "incident_type": self.evidence_package.incident_type,
            "tenant_id": self.evidence_package.tenant_id,
            "sequence_number": self.sequence_number,
            "current_hash": self.current_hash,
            "prev_hash": self.prev_hash,
            "package_sha256": self.evidence_package.package_sha256,
            "audit_entry_id": self.audit_entry.entry_id,
        }


class FraudEvidenceSealer:
    """Factory and sealer for tamper-evident fraud evidence packages into WORM log."""

    EVENT_TYPE: str = "FRAUD_EVIDENCE_SEALED"
    ACTOR_ID: str = "FRAUD_DETECTION_ENGINE"

    @staticmethod
    def _compute_sha256(payload: Dict[str, Any]) -> str:
        canonical_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    @classmethod
    def create_package(
        cls,
        incident_type: str,
        tenant_id: str,
        custodians_involved: List[str],
        transaction_ids: List[str],
        evidence_payload: Dict[str, Any],
        evidence_id: Optional[str] = None,
    ) -> FraudEvidencePackage:
        """Create a cryptographic tamper-evident package from incident details."""
        if not evidence_id:
            evidence_id = f"EV-{uuid.uuid4().hex[:12].upper()}"
        ts = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # Compute package sha256 over canonical representation of content
        content_for_hash = {
            "evidence_id": evidence_id,
            "incident_type": incident_type,
            "tenant_id": tenant_id,
            "custodians_involved": sorted(custodians_involved),
            "transaction_ids": sorted(transaction_ids),
            "evidence_payload": evidence_payload,
            "created_at": ts,
        }
        pkg_hash = cls._compute_sha256(content_for_hash)

        return FraudEvidencePackage(
            evidence_id=evidence_id,
            incident_type=incident_type,
            tenant_id=tenant_id,
            custodians_involved=sorted(custodians_involved),
            transaction_ids=sorted(transaction_ids),
            evidence_payload=evidence_payload,
            created_at=ts,
            package_sha256=pkg_hash,
        )

    @classmethod
    def seal_package(
        cls,
        worm_logger: WORMAuditLogger,
        package: FraudEvidencePackage,
    ) -> SealedEvidenceReceipt:
        """Seal the fraud evidence package directly into the tenant's WORM audit chain."""
        entry = worm_logger.append_event(
            tenant_id=package.tenant_id,
            event_type=cls.EVENT_TYPE,
            actor_id=cls.ACTOR_ID,
            payload=package.to_dict(),
        )
        return SealedEvidenceReceipt(
            evidence_package=package,
            audit_entry=entry,
        )

    @classmethod
    def seal_split_transaction_alert(
        cls,
        worm_logger: WORMAuditLogger,
        alert: SplitTransactionAlert,
    ) -> SealedEvidenceReceipt:
        """Helper to package and seal a SplitTransactionAlert."""
        package = cls.create_package(
            incident_type=alert.alert_code,
            tenant_id=alert.tenant_id,
            custodians_involved=alert.custodians_involved,
            transaction_ids=alert.window_transaction_ids,
            evidence_payload=alert.to_dict(),
        )
        return cls.seal_package(worm_logger, package)

    @classmethod
    def seal_velocity_alert(
        cls,
        worm_logger: WORMAuditLogger,
        alert: VelocityAnomalyAlert,
    ) -> SealedEvidenceReceipt:
        """Helper to package and seal a VelocityAnomalyAlert."""
        package = cls.create_package(
            incident_type=alert.alert_code,
            tenant_id=alert.tenant_id,
            custodians_involved=[alert.custodian_id],
            transaction_ids=alert.transaction_ids,
            evidence_payload=alert.to_dict(),
        )
        return cls.seal_package(worm_logger, package)

    @classmethod
    def seal_category_alert(
        cls,
        worm_logger: WORMAuditLogger,
        alert: MerchantCategoryAlert,
    ) -> SealedEvidenceReceipt:
        """Helper to package and seal a MerchantCategoryAlert."""
        package = cls.create_package(
            incident_type=alert.alert_code,
            tenant_id=alert.tenant_id,
            custodians_involved=[alert.custodian_id],
            transaction_ids=[alert.transaction_id],
            evidence_payload=alert.to_dict(),
        )
        return cls.seal_package(worm_logger, package)

    @classmethod
    def verify_sealed_evidence(
        cls,
        worm_logger: WORMAuditLogger,
        receipt: SealedEvidenceReceipt,
    ) -> bool:
        """Verify that the sealed evidence exists in WORM logger and chain is intact."""
        # 1. Verify WORM audit chain cryptographic integrity
        worm_logger.verify_integrity(receipt.evidence_package.tenant_id)

        # 2. Check entry exists and payload matches
        entries = worm_logger.get_entries(receipt.evidence_package.tenant_id)
        matching_entries = [
            e for e in entries
            if e.entry_id == receipt.audit_entry.entry_id
        ]
        if not matching_entries:
            return False

        entry = matching_entries[0]
        payload = json.loads(entry.payload_json)
        return payload.get("package_sha256") == receipt.evidence_package.package_sha256
