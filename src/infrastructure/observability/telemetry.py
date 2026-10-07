"""Operational telemetry, metrics aggregation, alert evaluation, and chaos/sandbox verification."""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


class AlertSeverity(str, Enum):
    P1_CRITICAL = "P1_CRITICAL"      # Immediate page: data drift, cryptographic mismatch, payment double-charge
    P2_HIGH = "P2_HIGH"              # 15m response: provider outage, retry storm, elevated failure rate
    P3_WARNING = "P3_WARNING"        # Next-business-day: stale FX rates, low float balance, non-blocking lag


@dataclass(frozen=True)
class AlertEvent:
    alert_id: str
    tenant_id: str
    metric_name: str
    severity: AlertSeverity
    message: str
    current_value: float
    threshold_value: float
    timestamp_utc: str
    runbook_url: str


class TelemetryRegistry:
    """In-memory telemetry and observability manager for real-time metrics,

    statistical percentiles, and proactive alert evaluation.
    """

    def __init__(self) -> None:
        # Metric store: metric_name -> list of (timestamp, value, tags_dict)
        self._metrics: Dict[str, List[Tuple[float, float, Dict[str, str]]]] = defaultdict(list)
        self._alerts_history: List[AlertEvent] = []
        self._alert_listeners: List[Callable[[AlertEvent], None]] = []

    def record_counter(self, metric_name: str, value: float = 1.0, tags: Optional[Dict[str, str]] = None) -> None:
        """Increment or record a counter metric."""
        self._metrics[metric_name].append((time.time(), float(value), tags or {}))

    def record_latency(self, metric_name: str, latency_ms: float, tags: Optional[Dict[str, str]] = None) -> None:
        """Record execution latency in milliseconds."""
        self._metrics[metric_name].append((time.time(), float(latency_ms), tags or {}))

    def get_metric_sum(self, metric_name: str, tag_filter: Optional[Dict[str, str]] = None) -> float:
        """Compute the sum of recorded values for a metric."""
        entries = self._metrics.get(metric_name, [])
        total = 0.0
        for _, val, tags in entries:
            if tag_filter:
                if all(tags.get(k) == v for k, v in tag_filter.items()):
                    total += val
            else:
                total += val
        return total

    def get_metric_count(self, metric_name: str, tag_filter: Optional[Dict[str, str]] = None) -> int:
        """Count total metric data points."""
        entries = self._metrics.get(metric_name, [])
        if not tag_filter:
            return len(entries)
        return sum(1 for _, _, tags in entries if all(tags.get(k) == v for k, v in tag_filter.items()))

    def get_latency_percentile(self, metric_name: str, percentile: float = 95.0) -> float:
        """Compute the N-th percentile latency for a metric."""
        entries = self._metrics.get(metric_name, [])
        if not entries:
            return 0.0
        values = sorted(val for _, val, _ in entries)
        idx = int((percentile / 100.0) * len(values))
        idx = min(idx, len(values) - 1)
        return values[idx]

    def add_alert_listener(self, listener: Callable[[AlertEvent], None]) -> None:
        """Register a notification callback for fired alerts."""
        self._alert_listeners.append(listener)

    def trigger_alert(
        self,
        tenant_id: str,
        metric_name: str,
        severity: AlertSeverity,
        message: str,
        current_value: float,
        threshold_value: float,
        runbook_url: str = "https://docs.pettyflow.internal/runbooks",
    ) -> AlertEvent:
        """Emit an alert event, record in audit log, and notify listeners."""
        event = AlertEvent(
            alert_id=f"alt-{len(self._alerts_history) + 1:04d}",
            tenant_id=tenant_id,
            metric_name=metric_name,
            severity=severity,
            message=message,
            current_value=current_value,
            threshold_value=threshold_value,
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            runbook_url=runbook_url,
        )
        self._alerts_history.append(event)
        for listener in self._alert_listeners:
            try:
                listener(event)
            except Exception:
                pass
        return event

    def get_active_alerts(self, min_severity: Optional[AlertSeverity] = None) -> List[AlertEvent]:
        """Return list of historical/active alerts."""
        if min_severity is None:
            return list(self._alerts_history)
        return [a for a in self._alerts_history if a.severity == min_severity]

    def evaluate_system_health(self) -> Dict[str, Any]:
        """Run system-wide operational health check and evaluate alert thresholds."""
        health = {
            "status": "HEALTHY",
            "p1_alerts_count": len(self.get_active_alerts(AlertSeverity.P1_CRITICAL)),
            "p2_alerts_count": len(self.get_active_alerts(AlertSeverity.P2_HIGH)),
            "p3_alerts_count": len(self.get_active_alerts(AlertSeverity.P3_WARNING)),
            "metrics_tracked": list(self._metrics.keys()),
        }
        if health["p1_alerts_count"] > 0:
            health["status"] = "CRITICAL"
        elif health["p2_alerts_count"] > 0:
            health["status"] = "DEGRADED"
        return health


class ProviderSandboxDrill:
    """Simulates realistic bank, ERP, and payment provider chaos scenarios

    (timeouts, outages, duplicate replay storms) to validate system resilience.
    """

    def __init__(self, telemetry: TelemetryRegistry) -> None:
        self.telemetry = telemetry

    def execute_bank_reconciliation_drill(
        self,
        tenant_id: str,
        matched_records: int,
        unmatched_records: int,
        variance_scaled: int,
    ) -> Dict[str, Any]:
        """Verify bank settlement and trigger P1 alert if ledger variance occurs."""
        self.telemetry.record_counter("bank.reconciliation.matched", matched_records, {"tenant_id": tenant_id})
        self.telemetry.record_counter("bank.reconciliation.unmatched", unmatched_records, {"tenant_id": tenant_id})

        if variance_scaled != 0:
            self.telemetry.trigger_alert(
                tenant_id=tenant_id,
                metric_name="ledger.unreconciled_variance",
                severity=AlertSeverity.P1_CRITICAL,
                message=f"Unreconciled bank ledger variance detected: {variance_scaled} scaled units.",
                current_value=float(variance_scaled),
                threshold_value=0.0,
                runbook_url="https://docs.pettyflow.internal/runbooks/ledger-variance",
            )
            return {"status": "FAILED", "reason": "LEDGER_VARIANCE", "variance_scaled": variance_scaled}

        return {"status": "SUCCESS", "matched": matched_records}

    def execute_retry_storm_drill(
        self,
        tenant_id: str,
        attempted_requests: int,
        failure_rate: float,
    ) -> Dict[str, Any]:
        """Simulate high provider retry storm and evaluate P2 alert threshold."""
        failures = int(attempted_requests * failure_rate)
        self.telemetry.record_counter("disbursement.requests.total", attempted_requests, {"tenant_id": tenant_id})
        self.telemetry.record_counter("disbursement.requests.failed", failures, {"tenant_id": tenant_id})

        if failure_rate >= 0.20:  # > 20% failure triggers P2
            self.telemetry.trigger_alert(
                tenant_id=tenant_id,
                metric_name="provider.failure_rate",
                severity=AlertSeverity.P2_HIGH,
                message=f"High provider failure rate detected: {failure_rate * 100:.1f}%.",
                current_value=failure_rate,
                threshold_value=0.20,
                runbook_url="https://docs.pettyflow.internal/runbooks/provider-outage",
            )
            return {"status": "ALERTED", "severity": "P2_HIGH", "failure_rate": failure_rate}

        return {"status": "NORMAL", "failure_rate": failure_rate}
