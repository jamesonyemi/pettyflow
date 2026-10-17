"""Disbursement Velocity Anomaly Detector.

Flags excessive transaction frequency by a custodian within a rolling time window
(e.g., > 3 disbursements within 1 hour) to prevent rapid fund draining and smurfing.
"""

from __future__ import annotations

import collections
import datetime
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Tuple


DEFAULT_MAX_HOURLY_DISBURSEMENTS: int = 3
DEFAULT_VELOCITY_WINDOW_SECONDS: int = 3600  # 1 hour


@dataclass(frozen=True)
class DisbursementRecord:
    """Disbursement event tracked for velocity evaluation."""
    transaction_id: str
    tenant_id: str
    custodian_id: str
    amount_scaled: int
    timestamp: datetime.datetime


@dataclass
class VelocityAnomalyAlert:
    """Alert raised when custodian exceeds hourly disbursement limits."""
    tenant_id: str
    custodian_id: str
    transaction_id: str
    hourly_count: int
    max_allowed: int
    window_seconds: int
    window_total_scaled: int
    transaction_ids: List[str]
    timestamp: datetime.datetime
    alert_code: str = "VELOCITY_ANOMALY_DETECTED"
    reason: str = field(default="")

    def __post_init__(self) -> None:
        if not self.reason:
            self.reason = (
                f"Custodian {self.custodian_id} exceeded disbursement velocity threshold: "
                f"{self.hourly_count} transactions in 1 hour (limit: {self.max_allowed})"
            )

    @property
    def window_total_float(self) -> float:
        return self.window_total_scaled / 10_000.0

    def to_dict(self) -> dict:
        return {
            "alert_code": self.alert_code,
            "tenant_id": self.tenant_id,
            "custodian_id": self.custodian_id,
            "transaction_id": self.transaction_id,
            "hourly_count": self.hourly_count,
            "max_allowed": self.max_allowed,
            "window_seconds": self.window_seconds,
            "window_total_scaled": self.window_total_scaled,
            "window_total_formatted": f"${self.window_total_float:.2f}",
            "transaction_ids": self.transaction_ids,
            "timestamp": self.timestamp.isoformat(),
            "reason": self.reason,
        }


class DisbursementVelocityDetector:
    """Sliding-window velocity detector tracking disbursements per custodian."""

    def __init__(
        self,
        max_disbursements_per_hour: int = DEFAULT_MAX_HOURLY_DISBURSEMENTS,
        window_seconds: int = DEFAULT_VELOCITY_WINDOW_SECONDS,
    ):
        if max_disbursements_per_hour <= 0:
            raise ValueError("max_disbursements_per_hour must be positive")
        self.max_disbursements_per_hour = max_disbursements_per_hour
        self.window_seconds = window_seconds
        # Key: (tenant_id, custodian_id)
        self._windows: Dict[Tuple[str, str], Deque[DisbursementRecord]] = {}

    def _evict_expired(
        self,
        window: Deque[DisbursementRecord],
        reference_time: datetime.datetime,
    ) -> None:
        cutoff = reference_time - datetime.timedelta(seconds=self.window_seconds)
        while window and window[0].timestamp < cutoff:
            window.popleft()

    def record_and_check(
        self,
        tenant_id: str,
        custodian_id: str,
        transaction_id: str,
        amount_scaled: int,
        timestamp: datetime.datetime,
    ) -> Optional[VelocityAnomalyAlert]:
        """Record disbursement and check if custodian exceeded velocity limit."""
        key = (tenant_id, custodian_id)
        window = self._windows.setdefault(key, collections.deque())

        self._evict_expired(window, timestamp)
        record = DisbursementRecord(
            transaction_id=transaction_id,
            tenant_id=tenant_id,
            custodian_id=custodian_id,
            amount_scaled=amount_scaled,
            timestamp=timestamp,
        )
        window.append(record)

        current_count = len(window)
        if current_count > self.max_disbursements_per_hour:
            tx_ids = [r.transaction_id for r in window]
            window_total = sum(r.amount_scaled for r in window)
            return VelocityAnomalyAlert(
                tenant_id=tenant_id,
                custodian_id=custodian_id,
                transaction_id=transaction_id,
                hourly_count=current_count,
                max_allowed=self.max_disbursements_per_hour,
                window_seconds=self.window_seconds,
                window_total_scaled=window_total,
                transaction_ids=tx_ids,
                timestamp=timestamp,
            )

        return None

    def get_hourly_count(
        self,
        tenant_id: str,
        custodian_id: str,
        reference_time: Optional[datetime.datetime] = None,
    ) -> int:
        key = (tenant_id, custodian_id)
        window = self._windows.get(key)
        if not window:
            return 0
        if reference_time:
            self._evict_expired(window, reference_time)
        return len(window)

    def get_hourly_total_scaled(
        self,
        tenant_id: str,
        custodian_id: str,
        reference_time: Optional[datetime.datetime] = None,
    ) -> int:
        key = (tenant_id, custodian_id)
        window = self._windows.get(key)
        if not window:
            return 0
        if reference_time:
            self._evict_expired(window, reference_time)
        return sum(r.amount_scaled for r in window)

    def clear_custodian(self, tenant_id: str, custodian_id: str) -> None:
        self._windows.pop((tenant_id, custodian_id), None)
