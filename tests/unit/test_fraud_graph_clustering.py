"""Week 6+ Fraud Engine Hardening: Advanced Split-Transaction Graph Clustering & Velocity Rules.

Acceptance Criteria per Issue #10:
  - Policy threshold evasion attempts (e.g. 2 x $48 receipts to bypass $50 approval limit)
    flagged with SPLIT_TRANSACTION_DETECTED.
  - Temporal graph clustering for linked receipts within 48h windows.
  - Multi-custodian smurfing collusion detection.
  - Merchant category anomaly detection against historical custodian spending baselines.
  - Velocity anomaly checks (e.g. > 3 disbursements within 1 hour).
  - Tamper-evident fraud evidence packages sealed directly into the WORM audit trail.
  - Automated tests verify detection rates across synthetic fraudulent receipt datasets.
"""

from __future__ import annotations

import datetime
import json
import random
import pytest

from src.infrastructure.audit.tamper_log import AuditIntegrityError, WORMAuditLogger
from src.services.fraud.category_anomaly import (
    MerchantCategoryAlert,
    MerchantCategoryAnomalyDetector,
)
from src.services.fraud.evidence_sealer import (
    FraudEvidencePackage,
    FraudEvidenceSealer,
    SealedEvidenceReceipt,
)
from src.services.fraud.risk_scorer import (
    FraudRiskScorer,
    RiskLevel,
    RiskScoreResult,
)
from src.services.fraud.split_tx_detector import (
    POLICY_EVASION_THRESHOLD_SCALED,
    SplitTransactionAlert,
    SplitTransactionDetector,
    TransactionCluster,
    TransactionEvent,
)
from src.services.fraud.velocity_detector import (
    DisbursementVelocityDetector,
    VelocityAnomalyAlert,
)


# ---------------------------------------------------------------------------
# Test Helpers
# ---------------------------------------------------------------------------

def _make_event(
    amount_scaled: int,
    custodian_id: str = "c001",
    tenant_id: str = "tenant-A",
    tx_id: str = "tx-001",
    offset_hours: float = 0.0,
    merchant_id: str | None = None,
    merchant_name: str | None = None,
    category: str | None = None,
    receipt_hash: str | None = None,
) -> TransactionEvent:
    base_time = datetime.datetime(2026, 10, 15, 12, 0, 0, tzinfo=datetime.timezone.utc)
    return TransactionEvent(
        transaction_id=tx_id,
        tenant_id=tenant_id,
        custodian_id=custodian_id,
        amount_scaled=amount_scaled,
        timestamp=base_time + datetime.timedelta(hours=offset_hours),
        merchant_id=merchant_id,
        merchant_name=merchant_name,
        merchant_category=category,
        receipt_hash=receipt_hash,
    )


# ===========================================================================
# PART 1: Split-Transaction & Policy Evasion Tests (2 x $48 to bypass $50)
# ===========================================================================

class TestPolicyThresholdEvasion:
    """Verify policy threshold evasion detection per Issue #10 acceptance criteria."""

    def test_two_48_dollar_receipts_bypassing_50_dollar_limit(self):
        """Issue #10 Acceptance Criteria:

        2 x $48 receipts to bypass $50 approval limit must be flagged
        with SPLIT_TRANSACTION_DETECTED.
        """
        # $50 approval threshold = 500,000 base units
        threshold_scaled = POLICY_EVASION_THRESHOLD_SCALED
        detector = SplitTransactionDetector(threshold_scaled=threshold_scaled)

        # Receipt 1: $48.00 (480,000 base units) — below $50 limit
        e1 = _make_event(
            amount_scaled=480_000,
            custodian_id="cust-001",
            tx_id="TX-RECEIPT-1",
            offset_hours=0.0,
        )
        r1 = detector.record_and_check(e1)
        assert r1 is None, "First $48 receipt alone is below $50 threshold and should not alert"

        # Receipt 2: $48.00 submitted 2 hours later — combined total $96 > $50
        e2 = _make_event(
            amount_scaled=480_000,
            custodian_id="cust-001",
            tx_id="TX-RECEIPT-2",
            offset_hours=2.0,
        )
        alert = detector.record_and_check(e2)

        assert alert is not None
        assert isinstance(alert, SplitTransactionAlert)
        assert alert.alert_code == "SPLIT_TRANSACTION_DETECTED"
        assert alert.window_total_scaled == 960_000  # $96.00
        assert alert.threshold_scaled == 500_000     # $50.00
        assert "TX-RECEIPT-1" in alert.window_transaction_ids
        assert "TX-RECEIPT-2" in alert.window_transaction_ids
        assert alert.custodian_id == "cust-001"

        # Verify serialization contains required alert_code
        d = alert.to_dict()
        assert d["alert_code"] == "SPLIT_TRANSACTION_DETECTED"
        assert d["window_total_formatted"] == "$96.00"
        assert d["threshold_formatted"] == "$50.00"

    def test_three_18_dollar_receipts_bypassing_50_dollar_limit(self):
        """3 x $18 = $54 > $50 approval threshold."""
        detector = SplitTransactionDetector(threshold_scaled=500_000)
        e1 = _make_event(180_000, tx_id="TX-1", offset_hours=0.0)
        e2 = _make_event(180_000, tx_id="TX-2", offset_hours=1.0)
        e3 = _make_event(180_000, tx_id="TX-3", offset_hours=2.0)

        assert detector.record_and_check(e1) is None
        assert detector.record_and_check(e2) is None
        alert = detector.record_and_check(e3)

        assert alert is not None
        assert alert.alert_code == "SPLIT_TRANSACTION_DETECTED"
        assert alert.window_total_scaled == 540_000


# ===========================================================================
# PART 2: 48-Hour Temporal Window & Graph Clustering Tests
# ===========================================================================

class TestTemporalGraphClustering:
    """Verify temporal graph clustering across linked receipts within 48h windows."""

    def test_linked_receipts_within_48h_window_are_clustered(self):
        """Transactions within 48 hours are clustered."""
        detector = SplitTransactionDetector(
            threshold_scaled=500_000,
            window_seconds=48 * 3600,
        )
        e1 = _make_event(300_000, tx_id="TX-A", offset_hours=0.0)
        e2 = _make_event(300_000, tx_id="TX-B", offset_hours=46.0)  # 46h later, within 48h

        detector.record_and_check(e1)
        alert = detector.record_and_check(e2)

        assert alert is not None
        assert alert.alert_code == "SPLIT_TRANSACTION_DETECTED"
        assert alert.window_total_scaled == 600_000

    def test_transactions_beyond_48h_are_evicted_and_not_clustered(self):
        """Transactions past 48h must not be linked into current cluster."""
        detector = SplitTransactionDetector(
            threshold_scaled=500_000,
            window_seconds=48 * 3600,
        )
        e1 = _make_event(300_000, tx_id="TX-OLD", offset_hours=0.0)
        e2 = _make_event(300_000, tx_id="TX-NEW", offset_hours=49.0)  # 49h later (> 48h)

        detector.record_and_check(e1)
        alert = detector.record_and_check(e2)

        assert alert is None, "TX-OLD should have been evicted after 48h"

    def test_multi_custodian_smurfing_by_shared_merchant(self):
        """Two custodians collude at the same merchant within 48h to evade limits.

        CUST-1 submits $300 at MERCHANT-X.
        CUST-2 submits $300 at MERCHANT-X.
        Combined $600 exceeds $500 threshold -> Graph cluster detects smurfing collusion!
        """
        detector = SplitTransactionDetector(
            threshold_scaled=5_000_000,  # $500 threshold
            window_seconds=48 * 3600,
        )

        e1 = _make_event(
            amount_scaled=3_000_000,
            custodian_id="CUST-ALPHA",
            tx_id="TX-ALPHA-01",
            merchant_id="MERCHANT-HARDWARE-99",
            offset_hours=1.0,
        )
        e2 = _make_event(
            amount_scaled=3_000_000,
            custodian_id="CUST-BETA",
            tx_id="TX-BETA-01",
            merchant_id="MERCHANT-HARDWARE-99",
            offset_hours=5.0,
        )

        r1 = detector.record_and_check(e1)
        assert r1 is None

        alert = detector.record_and_check(e2)
        assert alert is not None
        assert alert.alert_code == "SPLIT_TRANSACTION_DETECTED"
        assert alert.is_smurfing is True
        assert "CUST-ALPHA" in alert.custodians_involved
        assert "CUST-BETA" in alert.custodians_involved
        assert "MERCHANT-HARDWARE-99" in alert.merchants_involved
        assert alert.window_total_scaled == 6_000_000

    def test_multi_hop_smurfing_graph_cluster(self):
        """3 custodians linked via transitively shared merchants forming a graph cluster."""
        detector = SplitTransactionDetector(
            threshold_scaled=5_000_000,
            window_seconds=48 * 3600,
        )
        # C1 -> M1 ($200)
        e1 = _make_event(2_000_000, custodian_id="C1", tx_id="T1", merchant_id="M1", offset_hours=0)
        # C2 -> M1 ($200) - links C1 & C2
        e2 = _make_event(2_000_000, custodian_id="C2", tx_id="T2", merchant_id="M1", offset_hours=2)
        # C2 -> M2 ($200) - links C2 & C3
        e3 = _make_event(2_000_000, custodian_id="C3", tx_id="T3", merchant_id="M2", offset_hours=4)

        detector.record_and_check(e1)
        detector.record_and_check(e2)
        # Now add C2 at M2 to create the multi-hop bridge
        e2_bridge = _make_event(1_000_000, custodian_id="C2", tx_id="T2-bridge", merchant_id="M2", offset_hours=3)
        detector.record_and_check(e2_bridge)

        alert = detector.record_and_check(e3)
        assert alert is not None
        assert alert.is_smurfing is True
        assert set(alert.custodians_involved) == {"C1", "C2", "C3"}

    def test_linked_by_identical_receipt_hash(self):
        """Collusion detected when different custodians present identical receipt hash."""
        detector = SplitTransactionDetector(threshold_scaled=500_000)
        h = "hash_abc1234567890"

        e1 = _make_event(300_000, custodian_id="CUST-1", tx_id="TX-1", receipt_hash=h, offset_hours=0)
        e2 = _make_event(300_000, custodian_id="CUST-2", tx_id="TX-2", receipt_hash=h, offset_hours=2)

        detector.record_and_check(e1)
        alert = detector.record_and_check(e2)

        assert alert is not None
        assert alert.is_smurfing is True
        assert "CUST-1" in alert.custodians_involved
        assert "CUST-2" in alert.custodians_involved

    def test_tenant_isolation_in_graph_clustering(self):
        """Cross-tenant transactions at the same merchant MUST NEVER cluster."""
        detector = SplitTransactionDetector(threshold_scaled=500_000)
        e1 = _make_event(400_000, tenant_id="TENANT-1", custodian_id="C1", tx_id="T1", merchant_id="M-GLOBAL")
        e2 = _make_event(400_000, tenant_id="TENANT-2", custodian_id="C2", tx_id="T2", merchant_id="M-GLOBAL")

        r1 = detector.record_and_check(e1)
        r2 = detector.record_and_check(e2)

        assert r1 is None
        assert r2 is None
        clusters_t1 = detector.get_clusters("TENANT-1")
        clusters_t2 = detector.get_clusters("TENANT-2")
        assert len(clusters_t1) == 0
        assert len(clusters_t2) == 0


# ===========================================================================
# PART 3: Merchant Category Anomaly Detection Tests
# ===========================================================================

class TestMerchantCategoryAnomalyDetector:
    """Verify category anomaly detection against historical baselines."""

    def setup_method(self):
        self.detector = MerchantCategoryAnomalyDetector(min_baseline_samples=5)

    def test_unseen_category_flagged_for_established_custodian(self):
        """Custodian with established office spend suddenly buys luxury/casino items."""
        # Establish baseline: 10 transactions in OFFICE and POSTAGE
        for i in range(10):
            cat = "OFFICE_SUPPLIES" if i % 2 == 0 else "POSTAGE"
            self.detector.record_baseline(
                tenant_id="TENANT-CORP",
                custodian_id="CUST-BOB",
                category=cat,
                amount_scaled=200_000,  # $20
            )

        # Normal transaction in established category -> no alert
        normal_alert = self.detector.evaluate_transaction(
            tenant_id="TENANT-CORP",
            custodian_id="CUST-BOB",
            transaction_id="TX-NORMAL",
            category="OFFICE_SUPPLIES",
            amount_scaled=250_000,
        )
        assert normal_alert is None

        # Anomalous transaction in unseen category
        alert = self.detector.evaluate_transaction(
            tenant_id="TENANT-CORP",
            custodian_id="CUST-BOB",
            transaction_id="TX-ANOMALY-1",
            category="CASINO_GAMBLING",
            amount_scaled=400_000,
        )
        assert alert is not None
        assert isinstance(alert, MerchantCategoryAlert)
        assert alert.alert_code == "MERCHANT_CATEGORY_ANOMALY"
        assert alert.category == "CASINO_GAMBLING"
        assert alert.anomaly_score >= 0.85
        assert "CASINO_GAMBLING" not in alert.baseline_categories

    def test_insufficient_baseline_samples_does_not_flag(self):
        """New custodian with only 2 transactions should not trigger false positives."""
        self.detector.record_baseline("T1", "CUST-NEW", "OFFICE", 100_000)
        self.detector.record_baseline("T1", "CUST-NEW", "TRAVEL", 100_000)

        # 3rd transaction in a 3rd category -> no alert because baseline is too small (< 5)
        alert = self.detector.evaluate_transaction("T1", "CUST-NEW", "TX-3", "MEALS", 100_000)
        assert alert is None

    def test_rare_category_with_excessive_spend_flagged(self):
        """Category exists in baseline but has < 10% frequency and transaction has huge spike."""
        history = [
            ("OFFICE", 100_000),
            ("OFFICE", 100_000),
            ("OFFICE", 100_000),
            ("OFFICE", 100_000),
            ("OFFICE", 100_000),
            ("OFFICE", 100_000),
            ("OFFICE", 100_000),
            ("OFFICE", 100_000),
            ("OFFICE", 100_000),
            ("PARKING", 50_000),  # 1 out of 10 = 10% frequency, avg $5.00
        ]
        self.detector.bulk_load_baseline("T1", "CUST-SPEND", history)

        # Normal small parking fee
        assert self.detector.evaluate_transaction("T1", "CUST-SPEND", "TX-P1", "PARKING", 60_000) is None

        # Huge parking expense $200 (40x historical average)
        alert = self.detector.evaluate_transaction("T1", "CUST-SPEND", "TX-P2", "PARKING", 2_000_000)
        assert alert is not None
        assert alert.alert_code == "MERCHANT_CATEGORY_ANOMALY"
        assert "excessive spend" in alert.reason.lower()


# ===========================================================================
# PART 4: Velocity Anomaly Detection Tests (> 3 disbursements within 1 hour)
# ===========================================================================

class TestVelocityAnomalyDetector:
    """Verify velocity checks per Issue #10: > 3 disbursements within 1 hour."""

    def test_exceeding_three_disbursements_within_one_hour_flags_alert(self):
        detector = DisbursementVelocityDetector(max_disbursements_per_hour=3)
        base = datetime.datetime(2026, 10, 15, 14, 0, 0, tzinfo=datetime.timezone.utc)

        # 1st, 2nd, 3rd disbursements in 1 hour -> Allowed
        r1 = detector.record_and_check("T1", "CUST-1", "TX-1", 100_000, base)
        r2 = detector.record_and_check("T1", "CUST-1", "TX-2", 100_000, base + datetime.timedelta(minutes=15))
        r3 = detector.record_and_check("T1", "CUST-1", "TX-3", 100_000, base + datetime.timedelta(minutes=30))
        assert r1 is None
        assert r2 is None
        assert r3 is None

        # 4th disbursement in 1 hour -> VELOCITY_ANOMALY_DETECTED
        r4 = detector.record_and_check("T1", "CUST-1", "TX-4", 100_000, base + datetime.timedelta(minutes=45))
        assert r4 is not None
        assert isinstance(r4, VelocityAnomalyAlert)
        assert r4.alert_code == "VELOCITY_ANOMALY_DETECTED"
        assert r4.hourly_count == 4
        assert r4.max_allowed == 3
        assert len(r4.transaction_ids) == 4

        # 5th disbursement -> still flagged
        r5 = detector.record_and_check("T1", "CUST-1", "TX-5", 100_000, base + datetime.timedelta(minutes=50))
        assert r5 is not None
        assert r5.hourly_count == 5

    def test_disbursements_spaced_beyond_one_hour_do_not_flag(self):
        """Transactions older than 1 hour are evicted from the rolling window."""
        detector = DisbursementVelocityDetector(max_disbursements_per_hour=3)
        base = datetime.datetime(2026, 10, 15, 14, 0, 0, tzinfo=datetime.timezone.utc)

        detector.record_and_check("T1", "CUST-1", "TX-1", 100_000, base)
        detector.record_and_check("T1", "CUST-1", "TX-2", 100_000, base + datetime.timedelta(minutes=20))
        detector.record_and_check("T1", "CUST-1", "TX-3", 100_000, base + datetime.timedelta(minutes=40))

        # 4th transaction arrives at +75 minutes (> 1 hour after TX-1)
        # TX-1 is evicted -> active count is 3 <= 3 -> No alert!
        r4 = detector.record_and_check("T1", "CUST-1", "TX-4", 100_000, base + datetime.timedelta(minutes=75))
        assert r4 is None
        assert detector.get_hourly_count("T1", "CUST-1") == 3


# ===========================================================================
# PART 5: Tamper-Evident Fraud Evidence Sealing into WORM Audit Trail
# ===========================================================================

class TestFraudEvidenceWORMSealing:
    """Verify tamper-evident evidence packages sealed into WORM audit log."""

    def setup_method(self):
        self.worm = WORMAuditLogger(hmac_secret=b"test-secret-worm-audit-32-bytes!")

    def test_seal_split_transaction_evidence_into_worm_log(self):
        """Fraud evidence package sealed into WORM audit chain maintains hash chain integrity."""
        detector = SplitTransactionDetector(threshold_scaled=500_000)
        e1 = _make_event(480_000, tx_id="TX-1", offset_hours=0)
        e2 = _make_event(480_000, tx_id="TX-2", offset_hours=1)
        detector.record_and_check(e1)
        alert = detector.record_and_check(e2)
        assert alert is not None

        # Seal into WORM audit trail
        receipt = FraudEvidenceSealer.seal_split_transaction_alert(self.worm, alert)
        assert isinstance(receipt, SealedEvidenceReceipt)
        assert receipt.sequence_number == 1
        assert receipt.evidence_package.incident_type == "SPLIT_TRANSACTION_DETECTED"
        assert len(receipt.evidence_package.package_sha256) == 64

        # Cryptographic chain verification
        assert self.worm.verify_integrity(alert.tenant_id) is True
        assert FraudEvidenceSealer.verify_sealed_evidence(self.worm, receipt) is True

    def test_seal_velocity_anomaly_evidence(self):
        vel_alert = VelocityAnomalyAlert(
            tenant_id="TENANT-VEL",
            custodian_id="CUST-FAST",
            transaction_id="TX-99",
            hourly_count=5,
            max_allowed=3,
            window_seconds=3600,
            window_total_scaled=1_500_000,
            transaction_ids=["T1", "T2", "T3", "T4", "TX-99"],
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        receipt = FraudEvidenceSealer.seal_velocity_alert(self.worm, vel_alert)
        assert receipt.sequence_number == 1
        assert self.worm.verify_integrity("TENANT-VEL") is True
        assert FraudEvidenceSealer.verify_sealed_evidence(self.worm, receipt) is True

    def test_worm_tamper_detection_triggers_error(self):
        """Tampering with sealed evidence payload triggers AuditIntegrityError."""
        package = FraudEvidenceSealer.create_package(
            incident_type="TEST_INCIDENT",
            tenant_id="TENANT-TAMPER",
            custodians_involved=["C1"],
            transaction_ids=["T1"],
            evidence_payload={"flagged": True, "amount": 1000},
        )
        assert isinstance(package, FraudEvidencePackage)
        receipt = FraudEvidenceSealer.seal_package(self.worm, package)
        assert FraudEvidenceSealer.verify_sealed_evidence(self.worm, receipt) is True

        # Tamper directly with the underlying audit store entry
        entries = self.worm._store["TENANT-TAMPER"]
        tampered_entry = entries[0]
        # Mutate payload in entry
        tampered_payload = json.dumps({"flagged": False, "amount": 0})
        object.__setattr__(tampered_entry, "payload_json", tampered_payload)

        # Integrity check must fail
        with pytest.raises(AuditIntegrityError):
            self.worm.verify_integrity("TENANT-TAMPER")


# ===========================================================================
# PART 6: Synthetic Fraud Dataset Benchmark & Acceptance Rate
# ===========================================================================

class TestSyntheticFraudDetectionRate:
    """Roadmap Acceptance: Automated tests verify detection rates across synthetic fraudulent datasets."""

    def test_100_percent_detection_rate_on_synthetic_split_evasion(self):
        """Generate 50 synthetic policy threshold evasion scenarios ($50 approval limit, 2x $48).

        Every single attempt must be flagged with SPLIT_TRANSACTION_DETECTED.
        """
        detector = SplitTransactionDetector(threshold_scaled=500_000)
        detected_count = 0
        n_scenarios = 50

        for i in range(n_scenarios):
            custodian = f"CUST-SYN-{i}"
            tenant = "TENANT-SYNTHETIC"
            detector.clear_custodian(tenant, custodian)

            # Two receipts of $48 each within 24h
            t1 = _make_event(
                amount_scaled=480_000,
                custodian_id=custodian,
                tenant_id=tenant,
                tx_id=f"SYN-{i}-A",
                offset_hours=float(i % 10),
            )
            t2 = _make_event(
                amount_scaled=480_000,
                custodian_id=custodian,
                tenant_id=tenant,
                tx_id=f"SYN-{i}-B",
                offset_hours=float(i % 10) + 1.5,
            )

            detector.record_and_check(t1)
            alert = detector.record_and_check(t2)

            if alert is not None and alert.alert_code == "SPLIT_TRANSACTION_DETECTED":
                detected_count += 1

        detection_rate = detected_count / n_scenarios
        assert detection_rate == 1.0, f"Expected 100% detection rate on split evasion, got {detection_rate:.1%}"

    def test_100_percent_detection_rate_on_synthetic_smurfing_rings(self):
        """Generate 30 synthetic smurfing rings across colluding custodians at common merchants."""
        detector = SplitTransactionDetector(threshold_scaled=1_000_000)  # $100 limit
        detected_rings = 0
        n_rings = 30

        for ring_idx in range(n_rings):
            tenant = f"TENANT-RING-{ring_idx}"
            merchant = f"SHELL-MERCHANT-{ring_idx}"

            # Custodian A: $60
            eA = _make_event(
                amount_scaled=600_000,
                custodian_id=f"COLLUDER-{ring_idx}-A",
                tenant_id=tenant,
                tx_id=f"RING-{ring_idx}-A",
                merchant_id=merchant,
                offset_hours=1.0,
            )
            # Custodian B: $60 (Combined = $120 > $100)
            eB = _make_event(
                amount_scaled=600_000,
                custodian_id=f"COLLUDER-{ring_idx}-B",
                tenant_id=tenant,
                tx_id=f"RING-{ring_idx}-B",
                merchant_id=merchant,
                offset_hours=3.0,
            )

            detector.record_and_check(eA)
            alert = detector.record_and_check(eB)

            if alert is not None and alert.is_smurfing:
                detected_rings += 1

        ring_detection_rate = detected_rings / n_rings
        assert ring_detection_rate == 1.0, f"Expected 100% smurfing ring detection, got {ring_detection_rate:.1%}"

    def test_control_group_false_positive_rate_below_two_percent(self):
        """500 control transactions under threshold and within normal limits -> FPR < 2%."""
        random.seed(99)
        detector = SplitTransactionDetector(threshold_scaled=5_000_000)
        false_positives = 0
        n_control = 500

        for i in range(n_control):
            custodian = f"CUST-CLEAN-{i % 50}"
            amount = random.randint(100_000, 400_000)  # $10 - $40
            event = _make_event(
                amount_scaled=amount,
                custodian_id=custodian,
                tenant_id="TENANT-CLEAN",
                tx_id=f"CLEAN-{i}",
                offset_hours=float(i * 3),  # Well spaced out (every 3 hours)
            )
            alert = detector.record_and_check(event)
            if alert is not None:
                false_positives += 1

        fpr = false_positives / n_control
        assert fpr < 0.02, f"False positive rate {fpr:.2%} exceeds 2.0% requirement"


# ===========================================================================
# PART 7: FraudRiskScorer Multi-Signal Integration Tests
# ===========================================================================

class TestFraudRiskScorerIntegration:
    """Verify composite fraud risk scoring with category anomaly and smurfing signals."""

    def setup_method(self):
        self.scorer = FraudRiskScorer()

    def test_category_anomaly_adds_15_points(self):
        result = self.scorer.score(
            transaction_id="TX-CAT-1",
            tenant_id="TENANT-1",
            custodian_id="CUST-1",
            amount_scaled=500_000,
            category_anomaly=True,
        )
        assert isinstance(result, RiskScoreResult)
        assert result.category_anomaly_flag is True
        assert result.composite_score == 15
        assert result.risk_level == RiskLevel.LOW
        assert "MERCHANT_CATEGORY_ANOMALY" in result.triggered_signals

    def test_smurfing_collusion_adds_30_points(self):
        result = self.scorer.score(
            transaction_id="TX-SMURF-1",
            tenant_id="TENANT-1",
            custodian_id="CUST-1",
            amount_scaled=500_000,
            split_window_total_scaled=6_000_000,
            split_threshold_scaled=5_000_000,
            is_smurfing=True,
        )
        assert result.smurfing_flag is True
        assert result.split_transaction_flag is True
        assert result.composite_score == 30
        assert any("SMURFING_COLLUSION" in s for s in result.triggered_signals)

    def test_velocity_threshold_triggers_at_four_disbursements(self):
        # 3 disbursements/hour is allowed; 4 disbursements triggers velocity
        res_3 = self.scorer.score(
            transaction_id="TX-V3",
            tenant_id="TENANT-1",
            custodian_id="CUST-1",
            amount_scaled=100_000,
            hourly_tx_count=3,
        )
        assert res_3.velocity_flag is False

        res_4 = self.scorer.score(
            transaction_id="TX-V4",
            tenant_id="TENANT-1",
            custodian_id="CUST-1",
            amount_scaled=100_000,
            hourly_tx_count=4,
        )
        assert res_4.velocity_flag is True
        assert res_4.composite_score == 10

    def test_combined_signals_escalate_to_critical_and_block(self):
        """Duplicate (50) + Smurfing (30) + Category Anomaly (15) = 95 -> CRITICAL (blocked)."""
        result = self.scorer.score(
            transaction_id="TX-CRIT-1",
            tenant_id="TENANT-1",
            custodian_id="CUST-1",
            amount_scaled=1_000_000,
            duplicate_hamming_distance=1,
            split_window_total_scaled=6_000_000,
            split_threshold_scaled=5_000_000,
            is_smurfing=True,
            category_anomaly=True,
        )
        assert result.composite_score == 95
        assert result.risk_level == RiskLevel.CRITICAL
        assert result.is_blocked is True
        d = result.to_dict()
        assert d["category_anomaly_flag"] is True
        assert d["smurfing_flag"] is True


class TestTransactionClusterModel:
    """Verify TransactionCluster data model and serialization."""

    def test_cluster_to_dict_and_properties(self):
        t1 = _make_event(300_000, custodian_id="C1", tx_id="T1", offset_hours=0)
        t2 = _make_event(300_000, custodian_id="C2", tx_id="T2", offset_hours=2)
        cluster = TransactionCluster(
            cluster_id="CLUST-001",
            tenant_id="T-01",
            transactions=[t1, t2],
            total_amount_scaled=600_000,
            custodians_involved=["C1", "C2"],
            merchants_involved=["M-1"],
            window_start=t1.timestamp,
            window_end=t2.timestamp,
            is_smurfing=True,
            link_reasons=["SAME_MERCHANT_ID(M-1)"],
        )
        assert cluster.total_amount_float == 60.0
        assert cluster.transaction_ids == ["T1", "T2"]
        d = cluster.to_dict()
        assert d["cluster_id"] == "CLUST-001"
        assert d["total_amount_formatted"] == "$60.00"
        assert d["is_smurfing"] is True
