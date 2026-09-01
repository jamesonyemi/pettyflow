"""High-Performance Real-Time Financial Spend Aggregation Engine.

Processes enterprise petty cash transaction streams and computes multi-dimensional
aggregations (Department, Category, Location, Time) with sub-150ms query latency.

All monetary amounts use 64-bit integer fixed-point arithmetic (scaled x10^4).
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Sequence


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SpendRecord:
    """Individual itemized petty cash spend record with slots."""
    transaction_id: str
    tenant_id: str
    department: str
    location: str
    category: str
    amount_scaled: int                # 64-bit int fixed-point (x10^4)
    currency: str = "USD"
    timestamp: str = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )


@dataclass(slots=True)
class SpendColumnarBatch:
    """Columnar batch representation for zero-overhead vector aggregations."""
    tenant_id: str
    departments: List[str]
    locations: List[str]
    categories: List[str]
    amounts_scaled: List[int]

    def __len__(self) -> int:
        return len(self.amounts_scaled)


@dataclass
class AggregatedMetric:
    """Aggregated spend metric for a specific dimension slice."""
    dimension_key: str
    total_spend_scaled: int
    transaction_count: int
    min_spend_scaled: int
    max_spend_scaled: int
    percentage_of_total: float = 0.0

    @property
    def average_ticket_scaled(self) -> int:
        return self.total_spend_scaled // self.transaction_count if self.transaction_count > 0 else 0

    @property
    def total_spend_float(self) -> float:
        return self.total_spend_scaled / 10_000.0

    @property
    def average_ticket_float(self) -> float:
        return self.average_ticket_scaled / 10_000.0

    def to_dict(self) -> dict:
        return {
            "dimension_key": self.dimension_key,
            "total_spend_scaled": self.total_spend_scaled,
            "total_spend_formatted": f"${self.total_spend_float:.2f}",
            "transaction_count": self.transaction_count,
            "average_ticket_formatted": f"${self.average_ticket_float:.2f}",
            "min_spend_formatted": f"${self.min_spend_scaled / 10_000:.2f}",
            "max_spend_formatted": f"${self.max_spend_scaled / 10_000:.2f}",
            "percentage_of_total": round(self.percentage_of_total, 2),
        }


# ---------------------------------------------------------------------------
# Spend Aggregator
# ---------------------------------------------------------------------------

class SpendAggregator:
    """Vectorized in-memory aggregation engine for sub-millisecond query execution."""

    def aggregate_columnar(self, batch: SpendColumnarBatch) -> dict:
        """Process columnar rows with pre-initialized dictionary accumulators."""
        depts = batch.departments
        locs = batch.locations
        cats = batch.categories
        amts = batch.amounts_scaled
        n = len(amts)

        total_spend = sum(amts)
        # Pre-initialize dimension dictionaries to avoid in-loop containment branching
        dept_accum: Dict[str, List[int]] = {}
        cat_accum: Dict[str, List[int]] = {}
        loc_accum: Dict[str, List[int]] = {}

        for d in set(depts):
            dept_accum[d] = [0, 0, 999_999_999_999, 0]
        for c in set(cats):
            cat_accum[c] = [0, 0, 999_999_999_999, 0]
        for loc in set(locs):
            loc_accum[loc] = [0, 0, 999_999_999_999, 0]

        # Fast direct iteration
        for d, c, loc, amt in zip(depts, cats, locs, amts):
            ed = dept_accum[d]
            ed[0] += amt
            ed[1] += 1
            if amt < ed[2]: ed[2] = amt
            if amt > ed[3]: ed[3] = amt

            ec = cat_accum[c]
            ec[0] += amt
            ec[1] += 1
            if amt < ec[2]: ec[2] = amt
            if amt > ec[3]: ec[3] = amt

            el = loc_accum[loc]
            el[0] += amt
            el[1] += 1
            if amt < el[2]: el[2] = amt
            if amt > el[3]: el[3] = amt

        dept_metrics = {
            k: AggregatedMetric(k, tot, cnt, mn if cnt > 0 else 0, mx, (tot / total_spend * 100.0) if total_spend > 0 else 0.0).to_dict()
            for k, (tot, cnt, mn, mx) in dept_accum.items()
        }
        cat_metrics = {
            k: AggregatedMetric(k, tot, cnt, mn if cnt > 0 else 0, mx, (tot / total_spend * 100.0) if total_spend > 0 else 0.0).to_dict()
            for k, (tot, cnt, mn, mx) in cat_accum.items()
        }
        loc_metrics = {
            k: AggregatedMetric(k, tot, cnt, mn if cnt > 0 else 0, mx, (tot / total_spend * 100.0) if total_spend > 0 else 0.0).to_dict()
            for k, (tot, cnt, mn, mx) in loc_accum.items()
        }

        return {
            "tenant_id": batch.tenant_id,
            "total_spend_scaled": total_spend,
            "total_spend_formatted": f"${total_spend / 10_000:.2f}",
            "total_transactions": n,
            "average_ticket_formatted": f"${(total_spend // n) / 10_000:.2f}" if n > 0 else "$0.00",
            "by_department": dept_metrics,
            "by_category": cat_metrics,
            "by_location": loc_metrics,
        }

    def get_executive_summary(self, records: Sequence[SpendRecord], tenant_id: str) -> dict:
        """Generate comprehensive executive analytics summary across records."""
        total_spend = 0
        total_tx = 0

        dept_accum: Dict[str, List[int]] = {}
        cat_accum: Dict[str, List[int]] = {}
        loc_accum: Dict[str, List[int]] = {}

        for r in records:
            if r.tenant_id != tenant_id:
                continue

            amt = r.amount_scaled
            total_spend += amt
            total_tx += 1

            d = r.department
            if d in dept_accum:
                e = dept_accum[d]
                e[0] += amt
                e[1] += 1
                if amt < e[2]: e[2] = amt
                if amt > e[3]: e[3] = amt
            else:
                dept_accum[d] = [amt, 1, amt, amt]

            c = r.category
            if c in cat_accum:
                e = cat_accum[c]
                e[0] += amt
                e[1] += 1
                if amt < e[2]: e[2] = amt
                if amt > e[3]: e[3] = amt
            else:
                cat_accum[c] = [amt, 1, amt, amt]

            loc = r.location
            if loc in loc_accum:
                e = loc_accum[loc]
                e[0] += amt
                e[1] += 1
                if amt < e[2]: e[2] = amt
                if amt > e[3]: e[3] = amt
            else:
                loc_accum[loc] = [amt, 1, amt, amt]

        dept_metrics = {
            k: AggregatedMetric(k, tot, cnt, mn, mx, (tot / total_spend * 100.0) if total_spend > 0 else 0.0).to_dict()
            for k, (tot, cnt, mn, mx) in dept_accum.items()
        }
        cat_metrics = {
            k: AggregatedMetric(k, tot, cnt, mn, mx, (tot / total_spend * 100.0) if total_spend > 0 else 0.0).to_dict()
            for k, (tot, cnt, mn, mx) in cat_accum.items()
        }
        loc_metrics = {
            k: AggregatedMetric(k, tot, cnt, mn, mx, (tot / total_spend * 100.0) if total_spend > 0 else 0.0).to_dict()
            for k, (tot, cnt, mn, mx) in loc_accum.items()
        }

        return {
            "tenant_id": tenant_id,
            "total_spend_scaled": total_spend,
            "total_spend_formatted": f"${total_spend / 10_000:.2f}",
            "total_transactions": total_tx,
            "average_ticket_formatted": f"${(total_spend // total_tx) / 10_000:.2f}" if total_tx > 0 else "$0.00",
            "by_department": dept_metrics,
            "by_category": cat_metrics,
            "by_location": loc_metrics,
        }
