"""Split Transaction Detector — Sliding-Window Temporal Fraud Analysis.

Detects threshold-bypassing by flagging when the SAME custodian submits
>= 2 transactions within a 24-hour window whose combined sum exceeds an
approval threshold.

Algorithm:
  - For each new transaction event (tenant_id, custodian_id, amount_scaled, timestamp):
    1. Evict all events older than WINDOW_SECONDS from the deque.
    2. Compute the running window sum including the new event.
    3. If running_sum > threshold_scaled AND count >= MIN_TX_COUNT => flag.
    4. Append the new event to the sliding window.

All monetary values use 64-bit integer fixed-point (scaled x10^4) to avoid
float rounding errors per Section 0 invariant.
"""

from __future__ import annotations

import collections
import datetime
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WINDOW_SECONDS: int = 24 * 3600        # 24-hour rolling window
DEFAULT_THRESHOLD_SCALED: int = 5_000_000  # $500.00 in scaled units (x10^4)
MIN_TX_COUNT: int = 2                   # Minimum transactions to trigger flag


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TransactionEvent:
    """Minimal transaction event record for split-transaction analysis."""
    transaction_id: str
    tenant_id: str
    custodian_id: str
    amount_scaled: int          # 64-bit integer fixed-point (x10^4)
    timestamp: datetime.datetime

    def __post_init__(self) -> None:
        if self.amount_scaled <= 0:
            raise ValueError(
                f"TransactionEvent amount must be positive, got {self.amount_scaled}"
            )


@dataclass
class SplitTransactionAlert:
    """Alert raised when split-transaction pattern is detected."""
    tenant_id: str
    custodian_id: str
    window_transaction_ids: List[str]
    window_total_scaled: int
    threshold_scaled: int
    window_start: datetime.datetime
    window_end: datetime.datetime

    @property
    def window_total_float(self) -> float:
        return self.window_total_scaled / 10_000.0

    @property
    def threshold_float(self) -> float:
        return self.threshold_scaled / 10_000.0

    def to_dict(self) -> dict:
        return {
            "alert_type": "SPLIT_TRANSACTION",
            "tenant_id": self.tenant_id,
            "custodian_id": self.custodian_id,
            "window_transaction_ids": self.window_transaction_ids,
            "window_total_scaled": self.window_total_scaled,
            "window_total_formatted": f"${self.window_total_float:.2f}",
            "threshold_scaled": self.threshold_scaled,
            "threshold_formatted": f"${self.threshold_float:.2f}",
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
        }


# ---------------------------------------------------------------------------
# Sliding Window Implementation
# ---------------------------------------------------------------------------

# Key: (tenant_id, custodian_id) -> deque of TransactionEvent
_WindowKey = Tuple[str, str]


class SplitTransactionDetector:
    """Sliding-window detector for split-transaction threshold bypass.

    Thread-safety: Not thread-safe by design. In production, use one instance
    per async event loop coroutine, or wrap with asyncio.Lock.
    """

    def __init__(
        self,
        threshold_scaled: int = DEFAULT_THRESHOLD_SCALED,
        window_seconds: int = WINDOW_SECONDS,
        min_tx_count: int = MIN_TX_COUNT,
    ):
        if threshold_scaled <= 0:
            raise ValueError("threshold_scaled must be positive")
        self.threshold_scaled = threshold_scaled
        self.window_seconds = window_seconds
        self.min_tx_count = min_tx_count
        # Per (tenant, custodian) sliding window
        self._windows: Dict[_WindowKey, Deque[TransactionEvent]] = {}

    def _evict_expired(
        self,
        window: Deque[TransactionEvent],
        reference_time: datetime.datetime,
    ) -> None:
        """Remove events older than window_seconds relative to reference_time."""
        cutoff = reference_time - datetime.timedelta(seconds=self.window_seconds)
        while window and window[0].timestamp < cutoff:
            window.popleft()

    def record_and_check(self, event: TransactionEvent) -> Optional[SplitTransactionAlert]:
        """Record a transaction event and check for split-transaction pattern.

        Args:
            event: Incoming transaction event with tenant, custodian, amount, timestamp.

        Returns:
            SplitTransactionAlert if the pattern is detected, else None.
        """
        key: _WindowKey = (event.tenant_id, event.custodian_id)
        window = self._windows.setdefault(key, collections.deque())

        # 1. Evict expired events
        self._evict_expired(window, event.timestamp)

        # 2. Append the new event
        window.append(event)

        # 3. Evaluate: do we have >= min_tx_count events AND window_sum > threshold?
        if len(window) >= self.min_tx_count:
            window_sum = sum(e.amount_scaled for e in window)
            if window_sum > self.threshold_scaled:
                tx_ids = [e.transaction_id for e in window]
                return SplitTransactionAlert(
                    tenant_id=event.tenant_id,
                    custodian_id=event.custodian_id,
                    window_transaction_ids=tx_ids,
                    window_total_scaled=window_sum,
                    threshold_scaled=self.threshold_scaled,
                    window_start=window[0].timestamp,
                    window_end=window[-1].timestamp,
                )

        return None

    def clear_custodian(self, tenant_id: str, custodian_id: str) -> None:
        """Clear the sliding window for a specific tenant+custodian."""
        self._windows.pop((tenant_id, custodian_id), None)

    def window_sum(self, tenant_id: str, custodian_id: str, reference_time: Optional[datetime.datetime] = None) -> int:
        """Return current window sum (scaled) for a tenant+custodian pair."""
        key = (tenant_id, custodian_id)
        window = self._windows.get(key)
        if not window:
            return 0
        if reference_time:
            self._evict_expired(window, reference_time)
        return sum(e.amount_scaled for e in window)
