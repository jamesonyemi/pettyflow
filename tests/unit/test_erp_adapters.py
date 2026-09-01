"""Week 8: Enterprise ERP & Bank Replenishment Integration Test Suite.

Acceptance Criteria per Roadmap:
  - Bidirectional journal posting sync verified with mock SAP/NetSuite environments.
  - Full automated bank transfer payload built according to ISO 20022 pain.001 XML standards.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
import pytest

from src.infrastructure.erp.sap_adapter import (
    SAPAdapter,
    SAPJournalEntry,
    SAPJournalLine,
    SAPPostingDirection,
)
from src.infrastructure.erp.netsuite_adapter import (
    NetSuiteAdapter,
    NetSuiteJournalEntry,
    NetSuiteJournalLine,
    NetSuiteLineType,
)
from src.infrastructure.banking.plaid_adapter import (
    ACHTransferRequest,
    ACHTransferResult,
    ACHTransferStatus,
    BankAccount,
    PlaidAdapter,
    build_pain001_xml,
    ISO20022_NAMESPACE,
)


# ===========================================================================
# PART 1: SAP Adapter Tests
# ===========================================================================

class TestSAPAdapter:
    """Verify SAP S/4HANA journal posting and bidirectional sync."""

    def setup_method(self):
        self.sap = SAPAdapter(company_code="1000", mock_mode=True)

    def _make_balanced_entry(self, amount: int = 1_000_000, ref: str = "TX-001") -> SAPJournalEntry:
        return SAPJournalEntry(
            tenant_id="TENANT-A",
            reference_document=ref,
            company_code="1000",
            posting_date="2026-08-25",
            document_date="2026-08-25",
            description="Test Journal",
            idempotency_key=ref,
            lines=[
                SAPJournalLine(
                    gl_account="101000",
                    posting_direction=SAPPostingDirection.DEBIT,
                    amount_scaled=amount,
                    currency="USD",
                ),
                SAPJournalLine(
                    gl_account="113100",
                    posting_direction=SAPPostingDirection.CREDIT,
                    amount_scaled=amount,
                    currency="USD",
                ),
            ],
        )

    def test_post_balanced_entry_succeeds(self):
        entry = self._make_balanced_entry()
        result = self.sap.post_journal_entry(entry)
        assert result.sap_document_number is not None
        assert result.synced_at is not None
        assert self.sap.sync_log_count() == 1

    def test_sap_document_number_format(self):
        entry = self._make_balanced_entry()
        result = self.sap.post_journal_entry(entry)
        # Mock SAP doc numbers start with 1900
        assert result.sap_document_number.startswith("1900")

    def test_imbalanced_entry_raises(self):
        entry = SAPJournalEntry(
            tenant_id="T1",
            reference_document="TX-BAD",
            company_code="1000",
            posting_date="2026-08-25",
            document_date="2026-08-25",
            description="Bad entry",
            lines=[
                SAPJournalLine("101000", SAPPostingDirection.DEBIT, 1_000_000),
                SAPJournalLine("113100", SAPPostingDirection.CREDIT, 500_000),  # Imbalanced
            ],
        )
        with pytest.raises(ValueError, match="imbalanced"):
            self.sap.post_journal_entry(entry)

    def test_idempotency_same_reference_returns_same_entry(self):
        entry = self._make_balanced_entry(ref="TX-IDEM")
        r1 = self.sap.post_journal_entry(entry)
        r2 = self.sap.post_journal_entry(entry)
        assert r1.sap_document_number == r2.sap_document_number
        assert self.sap.sync_log_count() == 1

    def test_get_journal_entry_by_document_number(self):
        entry = self._make_balanced_entry()
        result = self.sap.post_journal_entry(entry)
        fetched = self.sap.get_journal_entry(result.sap_document_number)
        assert fetched is not None
        assert fetched.reference_document == "TX-001"

    def test_replenishment_entry_factory(self):
        """Verify factory builds balanced petty cash replenishment entry."""
        entry = self.sap.build_replenishment_entry(
            tenant_id="T1",
            reference_document="REPL-001",
            amount_scaled=5_000_000,  # $500
        )
        assert len(entry.lines) == 2
        debit = next(l for l in entry.lines if l.posting_direction == SAPPostingDirection.DEBIT)
        credit = next(l for l in entry.lines if l.posting_direction == SAPPostingDirection.CREDIT)
        assert debit.amount_scaled == credit.amount_scaled == 5_000_000
        # Should be valid (balanced)
        assert entry.validate_balance() is True

    def test_replenishment_posts_successfully(self):
        entry = self.sap.build_replenishment_entry(
            tenant_id="T1",
            reference_document="REPL-002",
            amount_scaled=2_500_000,
        )
        result = self.sap.post_journal_entry(entry)
        assert result.sap_document_number is not None

    def test_multiple_entries_accumulate_in_sync_log(self):
        for i in range(5):
            entry = self._make_balanced_entry(ref=f"TX-{i:03d}")
            self.sap.post_journal_entry(entry)
        assert self.sap.sync_log_count() == 5

    def test_sap_line_amount_float(self):
        line = SAPJournalLine("101000", SAPPostingDirection.DEBIT, 2_500_000)
        assert line.amount_float == pytest.approx(250.0)


# ===========================================================================
# PART 2: NetSuite Adapter Tests
# ===========================================================================

class TestNetSuiteAdapter:
    """Verify NetSuite SuiteTalk journal posting and bidirectional sync."""

    def setup_method(self):
        self.ns = NetSuiteAdapter(subsidiary="1", mock_mode=True)

    def _make_balanced_entry(self, amount: int = 1_000_000, ref: str = "NS-TX-001") -> NetSuiteJournalEntry:
        return NetSuiteJournalEntry(
            tenant_id="TENANT-B",
            subsidiary="1",
            reference_document=ref,
            posting_period="Aug 2026",
            memo="Test NS Journal",
            idempotency_key=ref,
            lines=[
                NetSuiteJournalLine(
                    account_id="10100",
                    line_type=NetSuiteLineType.DEBIT,
                    amount_scaled=amount,
                    currency="USD",
                ),
                NetSuiteJournalLine(
                    account_id="10000",
                    line_type=NetSuiteLineType.CREDIT,
                    amount_scaled=amount,
                    currency="USD",
                ),
            ],
        )

    def test_post_balanced_entry_assigns_ids(self):
        entry = self._make_balanced_entry()
        result = self.ns.post_journal_entry(entry)
        assert result.netsuite_internal_id is not None
        assert result.netsuite_tran_id is not None
        assert result.netsuite_tran_id.startswith("JE-")

    def test_tran_id_is_sequential(self):
        e1 = self._make_balanced_entry(ref="REF-A")
        e2 = self._make_balanced_entry(ref="REF-B")
        r1 = self.ns.post_journal_entry(e1)
        r2 = self.ns.post_journal_entry(e2)
        n1 = int(r1.netsuite_tran_id.split("-")[1])
        n2 = int(r2.netsuite_tran_id.split("-")[1])
        assert n2 == n1 + 1

    def test_imbalanced_entry_raises(self):
        entry = NetSuiteJournalEntry(
            tenant_id="T1",
            subsidiary="1",
            reference_document="BAD-001",
            posting_period="Aug 2026",
            memo="Bad",
            lines=[
                NetSuiteJournalLine("10100", NetSuiteLineType.DEBIT, 1_000_000),
                NetSuiteJournalLine("10000", NetSuiteLineType.CREDIT, 600_000),
            ],
        )
        with pytest.raises(ValueError, match="imbalanced"):
            self.ns.post_journal_entry(entry)

    def test_idempotency_returns_same_internal_id(self):
        entry = self._make_balanced_entry(ref="IDEM-001")
        r1 = self.ns.post_journal_entry(entry)
        r2 = self.ns.post_journal_entry(entry)
        assert r1.netsuite_internal_id == r2.netsuite_internal_id
        assert self.ns.sync_log_count() == 1

    def test_is_approved_after_post(self):
        entry = self._make_balanced_entry()
        result = self.ns.post_journal_entry(entry)
        assert result.is_approved is True

    def test_replenishment_factory_is_balanced(self):
        entry = self.ns.build_replenishment_entry(
            tenant_id="T1",
            reference_document="REPL-NS-001",
            amount_scaled=3_000_000,
        )
        assert entry.validate_balance() is True
        assert len(entry.lines) == 2

    def test_get_entry_by_internal_id(self):
        entry = self._make_balanced_entry()
        result = self.ns.post_journal_entry(entry)
        fetched = self.ns.get_journal_entry(result.netsuite_internal_id)
        assert fetched is not None

    def test_line_amount_float(self):
        line = NetSuiteJournalLine("10100", NetSuiteLineType.DEBIT, 1_500_000)
        assert line.amount_float == pytest.approx(150.0)


# ===========================================================================
# PART 3: Plaid / ACH / ISO 20022 Tests
# ===========================================================================

class TestPlaidAdapter:
    """Test ISO 20022 pain.001 XML generation and ACH transfer management."""

    def setup_method(self):
        self.plaid = PlaidAdapter(mock_mode=True)
        self.initiator = BankAccount(
            account_holder="Acme Corp Treasury",
            account_number="123456789",
            routing_number="021000021",
            bank_name="JP Morgan Chase",
        )
        self.beneficiary = BankAccount(
            account_holder="Petty Cash Fund — NYC Office",
            account_number="987654321",
            routing_number="021000089",
            bank_name="Bank of America",
        )

    def _make_request(self, amount: int = 5_000_000, ref: str = "REPL-ACH-001") -> ACHTransferRequest:
        return ACHTransferRequest(
            tenant_id="TENANT-C",
            reference_document=ref,
            initiating_account=self.initiator,
            beneficiary_account=self.beneficiary,
            amount_scaled=amount,
        )

    def test_transfer_submission_succeeds(self):
        req = self._make_request()
        result = self.plaid.initiate_transfer(req)
        assert result.status == ACHTransferStatus.SUBMITTED
        assert result.transfer_id.startswith("ach_")

    def test_pain001_xml_is_non_empty(self):
        """Roadmap Acceptance: ISO 20022 pain.001 XML is built."""
        req = self._make_request()
        result = self.plaid.initiate_transfer(req)
        assert len(result.pain001_xml) > 100

    def test_pain001_xml_is_valid_xml(self):
        """Verify pain.001 XML is well-formed parseable XML."""
        req = self._make_request()
        result = self.plaid.initiate_transfer(req)
        root = ET.fromstring(result.pain001_xml)
        assert root.tag.endswith("Document")

    def test_pain001_xml_contains_namespace(self):
        req = self._make_request()
        result = self.plaid.initiate_transfer(req)
        assert ISO20022_NAMESPACE in result.pain001_xml

    def test_pain001_xml_message_id_present(self):
        req = self._make_request()
        result = self.plaid.initiate_transfer(req)
        root = ET.fromstring(result.pain001_xml)
        # Find MsgId element
        ns = {"iso": ISO20022_NAMESPACE}
        # Parse without namespace prefix since we build without NS prefix in tag
        xml_str = result.pain001_xml
        assert result.message_id in xml_str

    def test_pain001_xml_contains_amount(self):
        req = self._make_request(amount=5_000_000)  # $500.00
        result = self.plaid.initiate_transfer(req)
        assert "500.00" in result.pain001_xml

    def test_pain001_xml_contains_reference_document(self):
        req = self._make_request(ref="REPL-REF-XYZ")
        result = self.plaid.initiate_transfer(req)
        assert "REPL-REF-XYZ" in result.pain001_xml

    def test_pain001_xml_contains_initiator_name(self):
        req = self._make_request()
        result = self.plaid.initiate_transfer(req)
        assert "Acme Corp Treasury" in result.pain001_xml

    def test_pain001_xml_contains_beneficiary_name(self):
        req = self._make_request()
        result = self.plaid.initiate_transfer(req)
        assert "Petty Cash Fund" in result.pain001_xml

    def test_pain001_xml_contains_routing_numbers(self):
        req = self._make_request()
        result = self.plaid.initiate_transfer(req)
        assert "021000021" in result.pain001_xml  # Initiator routing
        assert "021000089" in result.pain001_xml  # Beneficiary routing

    def test_pain001_xml_service_level_present(self):
        req = self._make_request()
        result = self.plaid.initiate_transfer(req)
        assert "NURG" in result.pain001_xml

    def test_idempotency_returns_same_transfer(self):
        req = self._make_request()
        r1 = self.plaid.initiate_transfer(req)
        r2 = self.plaid.initiate_transfer(req)
        assert r1.transfer_id == r2.transfer_id
        assert self.plaid.count_transfers() == 1

    def test_get_transfer_by_id(self):
        req = self._make_request()
        created = self.plaid.initiate_transfer(req)
        fetched = self.plaid.get_transfer(created.transfer_id)
        assert fetched is not None
        assert fetched.transfer_id == created.transfer_id

    def test_to_dict_has_required_keys(self):
        req = self._make_request()
        result = self.plaid.initiate_transfer(req)
        d = result.to_dict()
        for key in ["transfer_id", "message_id", "amount_formatted", "status", "pain001_xml_length"]:
            assert key in d

    def test_amount_formatted_correct(self):
        req = self._make_request(amount=2_500_000)  # $250.00
        result = self.plaid.initiate_transfer(req)
        assert result.to_dict()["amount_formatted"] == "$250.00"

    def test_negative_amount_raises(self):
        with pytest.raises(ValueError):
            ACHTransferRequest(
                tenant_id="T1",
                reference_document="REF",
                initiating_account=self.initiator,
                beneficiary_account=self.beneficiary,
                amount_scaled=-500,
            )

    def test_multiple_transfers_accumulate(self):
        for i in range(5):
            req = self._make_request(ref=f"REPL-{i:03d}", amount=1_000_000 + i)
            self.plaid.initiate_transfer(req)
        assert self.plaid.count_transfers() == 5

    def test_build_pain001_xml_direct(self):
        """Test the XML builder function directly."""
        req = self._make_request(amount=1_000_000)
        xml_str = build_pain001_xml(
            message_id="TESTMSG001",
            payment_info_id="TESTPMT001",
            creation_datetime="2026-08-25T10:00:00",
            request=req,
        )
        root = ET.fromstring(xml_str)
        assert root.tag.endswith("Document")
