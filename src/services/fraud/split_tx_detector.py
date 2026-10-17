"""Split Transaction Detector — Sliding-Window Temporal Fraud Analysis & Graph Clustering.

Detects policy evasion, threshold-bypassing, and smurfing across custodians:
- Single custodian splitting transactions to stay under approval limits (e.g., 2 x $48 to bypass $50 limit).
- Multi-custodian smurfing collusion linked by merchant or receipt similarity within 48-hour windows.
- Temporal graph clustering across linked transaction events.

All monetary values use 64-bit integer fixed-point (scaled x10^4) to avoid
float rounding errors per Section 0 invariant.
"""

from __future__ import annotations

import collections
import datetime
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WINDOW_SECONDS: int = 48 * 3600               # 48-hour rolling window per Roadmap
DEFAULT_THRESHOLD_SCALED: int = 5_000_000     # $500.00 in scaled units (x10^4)
POLICY_EVASION_THRESHOLD_SCALED: int = 500_000  # $50.00 approval limit
MIN_TX_COUNT: int = 2                         # Minimum transactions to trigger flag


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TransactionEvent:
    """Minimal transaction event record for split-transaction and graph clustering analysis."""
    transaction_id: str
    tenant_id: str
    custodian_id: str
    amount_scaled: int          # 64-bit integer fixed-point (x10^4)
    timestamp: datetime.datetime
    merchant_id: Optional[str] = None
    merchant_name: Optional[str] = None
    merchant_category: Optional[str] = None
    receipt_hash: Optional[str] = None

    def __post_init__(self) -> None:
        if self.amount_scaled <= 0:
            raise ValueError(
                f"TransactionEvent amount must be positive, got {self.amount_scaled}"
            )


@dataclass
class TransactionCluster:
    """Cluster of linked receipts discovered via temporal graph clustering."""
    cluster_id: str
    tenant_id: str
    transactions: List[TransactionEvent]
    total_amount_scaled: int
    custodians_involved: List[str]
    merchants_involved: List[str]
    window_start: datetime.datetime
    window_end: datetime.datetime
    is_smurfing: bool
    link_reasons: List[str]

    @property
    def total_amount_float(self) -> float:
        return self.total_amount_scaled / 10_000.0

    @property
    def transaction_ids(self) -> List[str]:
        return [tx.transaction_id for tx in self.transactions]

    def to_dict(self) -> dict:
        return {
            "cluster_id": self.cluster_id,
            "tenant_id": self.tenant_id,
            "transaction_ids": self.transaction_ids,
            "total_amount_scaled": self.total_amount_scaled,
            "total_amount_formatted": f"${self.total_amount_float:.2f}",
            "custodians_involved": self.custodians_involved,
            "merchants_involved": self.merchants_involved,
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "is_smurfing": self.is_smurfing,
            "link_reasons": self.link_reasons,
        }


@dataclass
class SplitTransactionAlert:
    """Alert raised when split-transaction or smurfing pattern is detected."""
    tenant_id: str
    custodian_id: str
    window_transaction_ids: List[str]
    window_total_scaled: int
    threshold_scaled: int
    window_start: datetime.datetime
    window_end: datetime.datetime
    alert_code: str = "SPLIT_TRANSACTION_DETECTED"
    alert_type: str = "SPLIT_TRANSACTION"
    cluster_id: Optional[str] = None
    is_smurfing: bool = False
    custodians_involved: List[str] = field(default_factory=list)
    merchants_involved: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.custodians_involved:
            self.custodians_involved = [self.custodian_id]

    @property
    def window_total_float(self) -> float:
        return self.window_total_scaled / 10_000.0

    @property
    def threshold_float(self) -> float:
        return self.threshold_scaled / 10_000.0

    def to_dict(self) -> dict:
        return {
            "alert_type": self.alert_type,
            "alert_code": self.alert_code,
            "tenant_id": self.tenant_id,
            "custodian_id": self.custodian_id,
            "window_transaction_ids": self.window_transaction_ids,
            "window_total_scaled": self.window_total_scaled,
            "window_total_formatted": f"${self.window_total_float:.2f}",
            "threshold_scaled": self.threshold_scaled,
            "threshold_formatted": f"${self.threshold_float:.2f}",
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "cluster_id": self.cluster_id,
            "is_smurfing": self.is_smurfing,
            "custodians_involved": self.custodians_involved,
            "merchants_involved": self.merchants_involved,
        }


# ---------------------------------------------------------------------------
# Sliding Window & Graph Clustering Implementation
# ---------------------------------------------------------------------------

_WindowKey = Tuple[str, str]


class SplitTransactionDetector:
    """Sliding-window detector with temporal graph clustering for fraud analysis.

    Supports:
      - Temporal graph clustering for linked receipts within 48-hour windows.
      - Smurfing detection across multiple colluding custodians sharing merchants.
      - Classic single-custodian threshold evasion detection.
    """

    def __init__(
        self,
        threshold_scaled: int = DEFAULT_THRESHOLD_SCALED,
        window_seconds: int = WINDOW_SECONDS,
        min_tx_count: int = MIN_TX_COUNT,
        enable_graph_clustering: bool = True,
    ):
        if threshold_scaled <= 0:
            raise ValueError("threshold_scaled must be positive")
        self.threshold_scaled = threshold_scaled
        self.window_seconds = window_seconds
        self.min_tx_count = min_tx_count
        self.enable_graph_clustering = enable_graph_clustering

        # Per (tenant, custodian) sliding window
        self._windows: Dict[_WindowKey, Deque[TransactionEvent]] = {}
        # Per tenant window for graph clustering across custodians
        self._tenant_windows: Dict[str, Deque[TransactionEvent]] = {}

    def _evict_expired(
        self,
        window: Deque[TransactionEvent],
        reference_time: datetime.datetime,
    ) -> None:
        """Remove events older than window_seconds relative to reference_time."""
        cutoff = reference_time - datetime.timedelta(seconds=self.window_seconds)
        while window and window[0].timestamp < cutoff:
            window.popleft()

    def _are_events_linked(self, e1: TransactionEvent, e2: TransactionEvent) -> Tuple[bool, Optional[str]]:
        """Determine if two transaction events are linked by custodian, merchant, or receipt."""
        time_diff = abs((e1.timestamp - e2.timestamp).total_seconds())
        if time_diff > self.window_seconds:
            return False, None

        if e1.custodian_id == e2.custodian_id:
            return True, f"SAME_CUSTODIAN({e1.custodian_id})"

        if e1.merchant_id and e2.merchant_id and e1.merchant_id == e2.merchant_id:
            return True, f"SAME_MERCHANT_ID({e1.merchant_id})"

        if e1.merchant_name and e2.merchant_name:
            m1 = e1.merchant_name.strip().lower()
            m2 = e2.merchant_name.strip().lower()
            if m1 == m2 and m1 != "":
                return True, f"SAME_MERCHANT_NAME({e1.merchant_name})"

        if e1.receipt_hash and e2.receipt_hash and e1.receipt_hash == e2.receipt_hash:
            return True, f"IDENTICAL_RECEIPT_HASH({e1.receipt_hash})"

        return False, None

    def get_clusters(
        self,
        tenant_id: str,
        reference_time: Optional[datetime.datetime] = None,
    ) -> List[TransactionCluster]:
        """Perform temporal graph clustering on active transactions in the tenant window."""
        window = self._tenant_windows.get(tenant_id)
        if not window:
            return []

        if reference_time:
            self._evict_expired(window, reference_time)

        events = list(window)
        n = len(events)
        if n < 2:
            return []

        # Build adjacency graph
        adj: Dict[int, List[Tuple[int, str]]] = {i: [] for i in range(n)}
        for i in range(n):
            for j in range(i + 1, n):
                is_linked, reason = self._are_events_linked(events[i], events[j])
                if is_linked and reason:
                    adj[i].append((j, reason))
                    adj[j].append((i, reason))

        # Find connected components (clusters)
        visited: Set[int] = set()
        clusters: List[TransactionCluster] = []

        for i in range(n):
            if i in visited:
                continue

            queue = [i]
            component_indices = [i]
            visited.add(i)
            link_reasons: Set[str] = set()

            for current in queue:
                for neighbor, reason in adj[current]:
                    link_reasons.add(reason)
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append(neighbor)
                        component_indices.append(neighbor)

            # Only consider clusters with at least 2 events
            if len(component_indices) >= self.min_tx_count:
                component_events = [events[idx] for idx in component_indices]
                total_scaled = sum(e.amount_scaled for e in component_events)
                custodians = sorted(list({e.custodian_id for e in component_events}))
                merchants = sorted(list({e.merchant_id for e in component_events if e.merchant_id}))
                timestamps = [e.timestamp for e in component_events]

                c_id = f"CLUST-{tenant_id}-{min(timestamps).strftime('%Y%m%d%H%M')}-{len(clusters)+1}"
                cluster = TransactionCluster(
                    cluster_id=c_id,
                    tenant_id=tenant_id,
                    transactions=component_events,
                    total_amount_scaled=total_scaled,
                    custodians_involved=custodians,
                    merchants_involved=merchants,
                    window_start=min(timestamps),
                    window_end=max(timestamps),
                    is_smurfing=len(custodians) > 1,
                    link_reasons=sorted(list(link_reasons)),
                )
                clusters.append(cluster)

        return clusters

    def record_and_check(self, event: TransactionEvent) -> Optional[SplitTransactionAlert]:
        """Record a transaction event and check for split-transaction / smurfing patterns.

        Args:
            event: Incoming transaction event with tenant, custodian, amount, timestamp.

        Returns:
            SplitTransactionAlert if policy threshold evasion or smurfing is detected, else None.
        """
        # 1. Update custodian-specific window
        key: _WindowKey = (event.tenant_id, event.custodian_id)
        custodian_window = self._windows.setdefault(key, collections.deque())
        self._evict_expired(custodian_window, event.timestamp)
        custodian_window.append(event)

        # 2. Update tenant-wide window for multi-custodian graph clustering
        tenant_window = self._tenant_windows.setdefault(event.tenant_id, collections.deque())
        self._evict_expired(tenant_window, event.timestamp)
        tenant_window.append(event)

        # 3. If graph clustering is enabled, evaluate clusters in tenant window
        if self.enable_graph_clustering:
            clusters = self.get_clusters(event.tenant_id, reference_time=event.timestamp)
            for cluster in clusters:
                # Check if current event belongs to this cluster and cluster exceeds threshold
                if event.transaction_id in cluster.transaction_ids:
                    if (
                        len(cluster.transactions) >= self.min_tx_count
                        and cluster.total_amount_scaled > self.threshold_scaled
                    ):
                        return SplitTransactionAlert(
                            tenant_id=event.tenant_id,
                            custodian_id=event.custodian_id,
                            window_transaction_ids=cluster.transaction_ids,
                            window_total_scaled=cluster.total_amount_scaled,
                            threshold_scaled=self.threshold_scaled,
                            window_start=cluster.window_start,
                            window_end=cluster.window_end,
                            alert_code="SPLIT_TRANSACTION_DETECTED",
                            cluster_id=cluster.cluster_id,
                            is_smurfing=cluster.is_smurfing,
                            custodians_involved=cluster.custodians_involved,
                            merchants_involved=cluster.merchants_involved,
                        )

        # 4. Fallback / direct custodian window evaluation
        if len(custodian_window) >= self.min_tx_count:
            window_sum = sum(e.amount_scaled for e in custodian_window)
            if window_sum > self.threshold_scaled:
                tx_ids = [e.transaction_id for e in custodian_window]
                return SplitTransactionAlert(
                    tenant_id=event.tenant_id,
                    custodian_id=event.custodian_id,
                    window_transaction_ids=tx_ids,
                    window_total_scaled=window_sum,
                    threshold_scaled=self.threshold_scaled,
                    window_start=custodian_window[0].timestamp,
                    window_end=custodian_window[-1].timestamp,
                    alert_code="SPLIT_TRANSACTION_DETECTED",
                    custodians_involved=[event.custodian_id],
                )

        return None

    def clear_custodian(self, tenant_id: str, custodian_id: str) -> None:
        """Clear the sliding window for a specific tenant+custodian."""
        self._windows.pop((tenant_id, custodian_id), None)
        tenant_window = self._tenant_windows.get(tenant_id)
        if tenant_window:
            filtered = [e for e in tenant_window if e.custodian_id != custodian_id]
            self._tenant_windows[tenant_id] = collections.deque(filtered)

    def clear_tenant(self, tenant_id: str) -> None:
        """Clear all windows for a given tenant."""
        self._tenant_windows.pop(tenant_id, None)
        keys_to_remove = [k for k in self._windows if k[0] == tenant_id]
        for k in keys_to_remove:
            self._windows.pop(k, None)

    def window_sum(
        self,
        tenant_id: str,
        custodian_id: str,
        reference_time: Optional[datetime.datetime] = None,
    ) -> int:
        """Return current window sum (scaled) for a tenant+custodian pair."""
        key = (tenant_id, custodian_id)
        window = self._windows.get(key)
        if not window:
            return 0
        if reference_time:
            self._evict_expired(window, reference_time)
        return sum(e.amount_scaled for e in window)
