"""Unit tests for ReceiptOCRProcessor."""

import pytest
from src.services.ai.ocr_processor import ReceiptOCRProcessor


def test_ocr_processor_empty_text():
    processor = ReceiptOCRProcessor()
    res = processor.process_text("")
    assert res.merchant_name == "Unknown Merchant"
    assert res.confidence_score == 0.0
    assert res.math_validated is False


def test_ocr_processor_standard_receipt():
    processor = ReceiptOCRProcessor()
    sample_ocr = """
    SUPERMARKET EXPRESS
    TAX ID: US-12345678
    DATE: 2026-08-20
    Organic Milk $4.50
    Whole Wheat Bread $3.50
    Subtotal $8.00
    Tax $0.80
    Tip $1.20
    Total $10.00
    """

    res = processor.process_text(sample_ocr)

    assert res.merchant_name == "SUPERMARKET EXPRESS"
    assert res.tax_id == "US-12345678"
    assert res.transaction_date == "2026-08-20"
    assert len(res.line_items) == 2
    assert res.subtotal_scaled == 80000  # $8.00 * 10000
    assert res.tax_scaled == 8000  # $0.80 * 10000
    assert res.tip_scaled == 12000  # $1.20 * 10000
    assert res.total_scaled == 100000  # $10.00 * 10000
    assert res.math_validated is True
    assert res.confidence_score >= 0.95
    assert res.processing_time_ms < 1800.0  # < 1.8s SLA


def test_ocr_processor_math_mismatch():
    processor = ReceiptOCRProcessor()
    bad_ocr = """
    CAFE CORNER
    Coffee $5.00
    Subtotal $5.00
    Tax $1.00
    Total $10.00
    """

    res = processor.process_text(bad_ocr)

    # 5.00 + 1.00 = 6.00 != 10.00 -> math_validated should be False
    assert res.subtotal_scaled == 50000
    assert res.tax_scaled == 10000
    assert res.total_scaled == 100000
    assert res.math_validated is False
    assert res.confidence_score < 0.90
