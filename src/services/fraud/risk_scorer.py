"""Composite Fraud Risk Scorer — 0 to 100 Risk Score Engine.

Aggregates multiple fraud signals into a single composite risk score:

  Signal                      | Max Weight
  --------------------------- | ----------
  Duplicate Receipt (dHash)   | 50 pts
  Split Transaction           | 30 pts
  Amount Anomaly (z-score)    | 10 pts
  Velocity (tx count/hour)    | 10 pts

Risk Score Bands:
  0  – 20  : LOW      — No action required
  21 – 49  : MEDIUM   — Flag for manual review
  50 – 79  : HIGH     — Automatic hold + notify approver
  80 – 100 : CRITICAL — Automatic block + audit escalation

All amounts use 64-bit integer fixed-point (scaled x10^4) per Section 0.
"""

from __future__ import annotations

import datetime
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


# ---------------------------------------------------------------------------
# Risk Level Classification
# ---------------------------------------------------------------------------

class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


def score_to_level(score: int) -> RiskLevel:
    """Map composite score (0-100) to a RiskLevel band."""
    if score <= 20:
        return RiskLevel.LOW
    elif score <= 49:
        return RiskLevel.MEDIUM
    elif score <= 79:
        return RiskLevel.HIGH
    else:
        return RiskLevel.CRITICAL


# ---------------------------------------------------------------------------
# Risk Score Result
# ---------------------------------------------------------------------------

@dataclass
class RiskScoreResult:
    """Composite fraud risk assessment for a single transaction."""
    transaction_id: str
    tenant_id: str
    custodian_id: str
    composite_score: int                  # 0-100
    risk_level: RiskLevel
    duplicate_receipt_flag: bool
    duplicate_hamming_distance: Optional[int]
    split_transaction_flag: bool
    split_window_total_scaled: Optional[int]
    amount_anomaly_flag: bool
    amount_zscore: Optional[float]
    velocity_flag: bool
    hourly_tx_count: int
    triggered_signals: List[str] = field(default_factory=list)
    evaluated_at: str = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )

    @property
    def is_blocked(self) -> bool:
        return self.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)

    def to_dict(self) -> dict:
        return {
            "transaction_id": self.transaction_id,
            "tenant_id": self.tenant_id,
            "custodian_id": self.custodian_id,
            "composite_score": self.composite_score,
            "risk_level": self.risk_level.value,
            "is_blocked": self.is_blocked,
            "duplicate_receipt_flag": self.duplicate_receipt_flag,
            "duplicate_hamming_distance": self.duplicate_hamming_distance,
            "split_transaction_flag": self.split_transaction_flag,
            "split_window_total_formatted": (
                f"${self.split_window_total_scaled / 10_000:.2f}"
                if self.split_window_total_scaled is not None else None
            ),
            "amount_anomaly_flag": self.amount_anomaly_flag,
            "amount_zscore": round(self.amount_zscore, 4) if self.amount_zscore is not None else None,
            "velocity_flag": self.velocity_flag,
            "hourly_tx_count": self.hourly_tx_count,
            "triggered_signals": self.triggered_signals,
            "evaluated_at": self.evaluated_at,
        }


# ---------------------------------------------------------------------------
# Composite Risk Scorer
# ---------------------------------------------------------------------------

# Weight constants
_WEIGHT_DUPLICATE = 50
_WEIGHT_SPLIT = 30
_WEIGHT_AMOUNT_ANOMALY = 10
_WEIGHT_VELOCITY = 10

# Thresholds
_ZSCORE_THRESHOLD = 2.5           # Flag if |z-score| >= 2.5 sigma
_VELOCITY_THRESHOLD = 5           # Flag if > 5 transactions/hour
_DUPLICATE_BIT_THRESHOLD = 5      # Hamming distance <= this = duplicate


class FraudRiskScorer:
    """Composite fraud risk score engine.

    Usage:
        scorer = FraudRiskScorer()
        result = scorer.score(
            transaction_id="tx-001",
            tenant_id="tenant-abc",
            custodian_id="custodian-xyz",
            amount_scaled=1_500_000,       # $150.00
            duplicate_hamming_distance=3,  # Near-duplicate image
            split_window_total_scaled=None,
            historical_amounts_scaled=[...],
            hourly_tx_count=2,
        )
    """

    def __init__(
        self,
        zscore_threshold: float = _ZSCORE_THRESHOLD,
        velocity_threshold: int = _VELOCITY_THRESHOLD,
        duplicate_bit_threshold: int = _DUPLICATE_BIT_THRESHOLD,
    ):
        self.zscore_threshold = zscore_threshold
        self.velocity_threshold = velocity_threshold
        self.duplicate_bit_threshold = duplicate_bit_threshold

    @staticmethod
    def _compute_zscore(
        value: int,
        historical_values: List[int],
    ) -> Optional[float]:
        """Compute z-score of value against historical distribution.

        Returns None if fewer than 2 historical samples (insufficient data).
        """
        n = len(historical_values)
        if n < 2:
            return None
        mean = sum(historical_values) / n
        variance = sum((x - mean) ** 2 for x in historical_values) / (n - 1)
        std = math.sqrt(variance) if variance > 0 else 0.0
        if std == 0.0:
            if value == mean:
                return 0.0
            return float("inf") if value > mean else float("-inf")
        return (value - mean) / std

    def score(
        self,
        transaction_id: str,
        tenant_id: str,
        custodian_id: str,
        amount_scaled: int,
        duplicate_hamming_distance: Optional[int] = None,
        split_window_total_scaled: Optional[int] = None,
        split_threshold_scaled: Optional[int] = None,
        historical_amounts_scaled: Optional[List[int]] = None,
        hourly_tx_count: int = 0,
    ) -> RiskScoreResult:
        """Compute composite risk score from all available fraud signals.

        Args:
            transaction_id: Unique transaction ID.
            tenant_id: Tenant scope (multi-tenancy isolation).
            custodian_id: Custodian performing the transaction.
            amount_scaled: Transaction amount (64-bit int, x10^4).
            duplicate_hamming_distance: Hamming distance to nearest image hash (None = no match).
            split_window_total_scaled: Running window total if split-tx detector fired.
            split_threshold_scaled: Split threshold (to confirm exceeded).
            historical_amounts_scaled: Past amounts for z-score anomaly detection.
            hourly_tx_count: Number of transactions by this custodian in the last hour.

        Returns:
            RiskScoreResult with composite score 0-100 and risk level.
        """
        raw_score = 0
        signals: List[str] = []

        # --- Signal 1: Duplicate Receipt Detection ---
        duplicate_flag = False
        if duplicate_hamming_distance is not None and duplicate_hamming_distance <= self.duplicate_bit_threshold:
            duplicate_flag = True
            raw_score += _WEIGHT_DUPLICATE
            signals.append(
                f"DUPLICATE_RECEIPT(hamming={duplicate_hamming_distance})"
            )

        # --- Signal 2: Split Transaction ---
        split_flag = False
        if (
            split_window_total_scaled is not None
            and split_threshold_scaled is not None
            and split_window_total_scaled > split_threshold_scaled
        ):
            split_flag = True
            raw_score += _WEIGHT_SPLIT
            signals.append(
                f"SPLIT_TRANSACTION(window_total=${split_window_total_scaled / 10_000:.2f})"
            )

        # --- Signal 3: Amount Anomaly (z-score) ---
        zscore = None
        amount_anomaly_flag = False
        if historical_amounts_scaled and len(historical_amounts_scaled) >= 2:
            zscore = self._compute_zscore(amount_scaled, historical_amounts_scaled)
            if zscore is not None and abs(zscore) >= self.zscore_threshold:
                amount_anomaly_flag = True
                raw_score += _WEIGHT_AMOUNT_ANOMALY
                signals.append(f"AMOUNT_ANOMALY(z={zscore:.2f})")

        # --- Signal 4: Velocity (transactions/hour) ---
        velocity_flag = False
        if hourly_tx_count > self.velocity_threshold:
            velocity_flag = True
            raw_score += _WEIGHT_VELOCITY
            signals.append(f"HIGH_VELOCITY(count={hourly_tx_count}/hr)")

        # Cap composite score at 100
        composite_score = min(raw_score, 100)
        risk_level = score_to_level(composite_score)

        return RiskScoreResult(
            transaction_id=transaction_id,
            tenant_id=tenant_id,
            custodian_id=custodian_id,
            composite_score=composite_score,
            risk_level=risk_level,
            duplicate_receipt_flag=duplicate_flag,
            duplicate_hamming_distance=duplicate_hamming_distance,
            split_transaction_flag=split_flag,
            split_window_total_scaled=split_window_total_scaled,
            amount_anomaly_flag=amount_anomaly_flag,
            amount_zscore=zscore,
            velocity_flag=velocity_flag,
            hourly_tx_count=hourly_tx_count,
            triggered_signals=signals,
        )
