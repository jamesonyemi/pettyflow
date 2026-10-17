"""Merchant Category Anomaly Detector — Baseline Profile Fraud Analysis.

Detects spending anomalies where custodian transactions deviate significantly
from their established merchant category profiles and spending baselines.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class CustodianCategoryBaseline:
    """Historical spending profile for a custodian across merchant categories."""
    total_tx_count: int = 0
    total_spend_scaled: int = 0
    category_counts: Dict[str, int] = field(default_factory=dict)
    category_spend_scaled: Dict[str, int] = field(default_factory=dict)

    def record(self, category: str, amount_scaled: int) -> None:
        norm_cat = category.strip().upper()
        self.total_tx_count += 1
        self.total_spend_scaled += amount_scaled
        self.category_counts[norm_cat] = self.category_counts.get(norm_cat, 0) + 1
        self.category_spend_scaled[norm_cat] = (
            self.category_spend_scaled.get(norm_cat, 0) + amount_scaled
        )

    def get_category_frequency(self, category: str) -> float:
        if self.total_tx_count == 0:
            return 0.0
        norm_cat = category.strip().upper()
        return self.category_counts.get(norm_cat, 0) / self.total_tx_count

    def get_category_spend_share(self, category: str) -> float:
        if self.total_spend_scaled == 0:
            return 0.0
        norm_cat = category.strip().upper()
        return self.category_spend_scaled.get(norm_cat, 0) / self.total_spend_scaled


@dataclass
class MerchantCategoryAlert:
    """Alert raised when transaction deviates from historical category baseline."""
    tenant_id: str
    custodian_id: str
    transaction_id: str
    category: str
    amount_scaled: int
    anomaly_score: float             # 0.0 - 1.0 confidence score
    reason: str
    alert_code: str = "MERCHANT_CATEGORY_ANOMALY"
    baseline_categories: List[str] = field(default_factory=list)
    historical_tx_count: int = 0

    @property
    def amount_float(self) -> float:
        return self.amount_scaled / 10_000.0

    def to_dict(self) -> dict:
        return {
            "alert_code": self.alert_code,
            "tenant_id": self.tenant_id,
            "custodian_id": self.custodian_id,
            "transaction_id": self.transaction_id,
            "category": self.category,
            "amount_scaled": self.amount_scaled,
            "amount_formatted": f"${self.amount_float:.2f}",
            "anomaly_score": round(self.anomaly_score, 4),
            "reason": self.reason,
            "baseline_categories": self.baseline_categories,
            "historical_tx_count": self.historical_tx_count,
        }


class MerchantCategoryAnomalyDetector:
    """Detects merchant category anomalies against custodian historical baselines."""

    def __init__(
        self,
        min_baseline_samples: int = 5,
        max_spend_deviation_multiplier: float = 3.0,
    ):
        self.min_baseline_samples = min_baseline_samples
        self.max_spend_deviation_multiplier = max_spend_deviation_multiplier
        # Key: (tenant_id, custodian_id)
        self._baselines: Dict[Tuple[str, str], CustodianCategoryBaseline] = {}

    def record_baseline(
        self,
        tenant_id: str,
        custodian_id: str,
        category: str,
        amount_scaled: int,
    ) -> None:
        """Record a verified historical transaction into the custodian's profile."""
        key = (tenant_id, custodian_id)
        baseline = self._baselines.setdefault(key, CustodianCategoryBaseline())
        baseline.record(category, amount_scaled)

    def bulk_load_baseline(
        self,
        tenant_id: str,
        custodian_id: str,
        history: List[Tuple[str, int]],
    ) -> None:
        """Load multiple historical (category, amount_scaled) tuples."""
        key = (tenant_id, custodian_id)
        baseline = self._baselines.setdefault(key, CustodianCategoryBaseline())
        for category, amount_scaled in history:
            baseline.record(category, amount_scaled)

    def evaluate_transaction(
        self,
        tenant_id: str,
        custodian_id: str,
        transaction_id: str,
        category: str,
        amount_scaled: int,
        auto_update_baseline: bool = False,
    ) -> Optional[MerchantCategoryAlert]:
        """Evaluate an incoming transaction against the custodian's baseline.

        Returns:
            MerchantCategoryAlert if anomalous, otherwise None.
        """
        key = (tenant_id, custodian_id)
        baseline = self._baselines.get(key)

        # If insufficient historical baseline data, don't flag false positives
        if not baseline or baseline.total_tx_count < self.min_baseline_samples:
            if auto_update_baseline:
                self.record_baseline(tenant_id, custodian_id, category, amount_scaled)
            return None

        norm_cat = category.strip().upper()
        known_categories = list(baseline.category_counts.keys())

        # Anomaly Condition 1: Completely unseen category for established custodian
        if norm_cat not in baseline.category_counts:
            alert = MerchantCategoryAlert(
                tenant_id=tenant_id,
                custodian_id=custodian_id,
                transaction_id=transaction_id,
                category=norm_cat,
                amount_scaled=amount_scaled,
                anomaly_score=0.90,
                reason=f"Unseen category '{norm_cat}' outside established profile",
                baseline_categories=sorted(known_categories),
                historical_tx_count=baseline.total_tx_count,
            )
            if auto_update_baseline:
                baseline.record(category, amount_scaled)
            return alert

        # Anomaly Condition 2: Historical category has very low frequency and abnormal spend
        cat_freq = baseline.get_category_frequency(norm_cat)
        avg_spend_cat = (
            baseline.category_spend_scaled[norm_cat] / baseline.category_counts[norm_cat]
        )
        if cat_freq <= 0.10 and amount_scaled > (avg_spend_cat * self.max_spend_deviation_multiplier):
            alert = MerchantCategoryAlert(
                tenant_id=tenant_id,
                custodian_id=custodian_id,
                transaction_id=transaction_id,
                category=norm_cat,
                amount_scaled=amount_scaled,
                anomaly_score=0.75,
                reason=(
                    f"Rare category '{norm_cat}' (frequency {cat_freq:.1%}) with excessive spend "
                    f"({amount_scaled / 10_000:.2f} vs avg {avg_spend_cat / 10_000:.2f})"
                ),
                baseline_categories=sorted(known_categories),
                historical_tx_count=baseline.total_tx_count,
            )
            if auto_update_baseline:
                baseline.record(category, amount_scaled)
            return alert

        if auto_update_baseline:
            baseline.record(category, amount_scaled)

        return None

    def clear_custodian(self, tenant_id: str, custodian_id: str) -> None:
        """Clear baseline profile for a custodian."""
        self._baselines.pop((tenant_id, custodian_id), None)
