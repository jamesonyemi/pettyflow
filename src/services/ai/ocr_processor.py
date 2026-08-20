"""Receipt OCR Extraction and Structured Parsing Engine.

Extracts expenditure metadata from receipt image text / layout stream:
- Merchant Name
- Tax ID / VAT Registration
- ISO Date / Time
- Itemized Line Items
- Subtotal, Tax, Tip, Total (scaled 64-bit integers, x10^4)
- Mathematical Invariant Verification: Subtotal + Tax + Tip == Total
"""

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any


@dataclass
class ReceiptLineItem:
    """Individual line item parsed from a receipt."""
    description: str
    amount_scaled: int  # 64-bit integer fixed-point (x10^4)

    @property
    def amount_float(self) -> float:
        return self.amount_scaled / 10000.0


@dataclass
class ReceiptExtractionResult:
    """Structured extraction output from receipt OCR processing."""
    merchant_name: str
    tax_id: Optional[str]
    transaction_date: str
    line_items: List[ReceiptLineItem] = field(default_factory=list)
    subtotal_scaled: int = 0
    tax_scaled: int = 0
    tip_scaled: int = 0
    total_scaled: int = 0
    math_validated: bool = False
    confidence_score: float = 0.95
    processing_time_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "merchant_name": self.merchant_name,
            "tax_id": self.tax_id,
            "transaction_date": self.transaction_date,
            "line_items": [
                {
                    "description": item.description,
                    "amount_scaled": item.amount_scaled,
                    "amount_formatted": f"${item.amount_float:.2f}",
                }
                for item in self.line_items
            ],
            "subtotal_scaled": self.subtotal_scaled,
            "subtotal_formatted": f"${self.subtotal_scaled / 10000.0:.2f}",
            "tax_scaled": self.tax_scaled,
            "tax_formatted": f"${self.tax_scaled / 10000.0:.2f}",
            "tip_scaled": self.tip_scaled,
            "tip_formatted": f"${self.tip_scaled / 10000.0:.2f}",
            "total_scaled": self.total_scaled,
            "total_formatted": f"${self.total_scaled / 10000.0:.2f}",
            "math_validated": self.math_validated,
            "confidence_score": self.confidence_score,
            "processing_time_ms": self.processing_time_ms,
        }


class ReceiptOCRProcessor:
    """Parses raw text extracted from vision/OCR receipt models into validated financial data."""

    # Patterns for key fields
    TAX_ID_PATTERNS = [
        re.compile(r"(?:TAX\s*ID|VAT\s*(?:NO|ID)?|GSTIN|EIN|REG\s*NO)\s*[:#]?\s*([A-Z0-9-]{5,18})", re.IGNORECASE),
        re.compile(r"\b([0-9]{2}-[0-9]{7})\b"),  # US EIN format XX-XXXXXXX
    ]

    DATE_PATTERNS = [
        re.compile(r"\b(\d{4}[-/]\d{2}[-/]\d{2})\b"),  # YYYY-MM-DD
        re.compile(r"\b(\d{2}[-/]\d{2}[-/]\d{4})\b"),  # MM/DD/YYYY or DD/MM/YYYY
        re.compile(r"\b(\d{2}[-/][A-Za-z]{3}[-/]\d{4})\b"),  # DD-Mon-YYYY
    ]

    AMOUNT_PATTERN = re.compile(r"\$?\s*(\d{1,6}\.\d{2})\b")

    def __init__(self):
        pass

    @staticmethod
    def _parse_scaled_amount(amount_str: str) -> int:
        """Convert float-like string '$12.34' to 64-bit integer fixed-point (scaled x10^4)."""
        clean_str = amount_str.replace("$", "").replace(",", "").strip()
        try:
            val = float(clean_str)
            return int(round(val * 10000))
        except ValueError:
            return 0

    def process_text(self, text: str) -> ReceiptExtractionResult:
        """Parse raw extracted OCR text stream into structured ReceiptExtractionResult.

        Args:
            text: Extracted text lines from OCR image model.

        Returns:
            ReceiptExtractionResult with validated subtotal + tax + tip == total invariant.
        """
        start_time = time.perf_counter()
        lines = [line.strip() for line in text.split("\n") if line.strip()]

        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if not lines:
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            return ReceiptExtractionResult(
                merchant_name="Unknown Merchant",
                tax_id=None,
                transaction_date=now_str,
                processing_time_ms=elapsed_ms,
                confidence_score=0.0,
            )

        # 1. Merchant Name: Typically top non-empty header line
        merchant_name = lines[0]
        for line in lines[:3]:
            if not any(kw in line.upper() for kw in ["TAX", "WELCOME", "RECEIPT", "DATE", "TEL"]):
                merchant_name = line
                break

        # 2. Tax ID search
        tax_id = None
        for line in lines:
            for pattern in self.TAX_ID_PATTERNS:
                match = pattern.search(line)
                if match:
                    tax_id = match.group(1)
                    break
            if tax_id:
                break

        # 3. Transaction Date search
        transaction_date = now_str
        for line in lines:
            for pattern in self.DATE_PATTERNS:
                match = pattern.search(line)
                if match:
                    raw_date = match.group(1)
                    try:
                        # Attempt standard YYYY-MM-DD parsing normalization
                        if "-" in raw_date and len(raw_date.split("-")[0]) == 4:
                            transaction_date = raw_date
                        elif "/" in raw_date:
                            parts = raw_date.split("/")
                            if len(parts[2]) == 4:
                                transaction_date = f"{parts[2]}-{int(parts[0]):02d}-{int(parts[1]):02d}"
                        else:
                            transaction_date = raw_date
                    except Exception:
                        transaction_date = raw_date
                    break
            if transaction_date != now_str:
                break

        # 4. Amounts & Totals parsing
        subtotal_scaled = 0
        tax_scaled = 0
        tip_scaled = 0
        total_scaled = 0
        line_items: List[ReceiptLineItem] = []

        for line in lines:
            upper_line = line.upper()

            # Check key financial total keywords
            if "SUBTOTAL" in upper_line or "SUB TOTAL" in upper_line:
                match = self.AMOUNT_PATTERN.search(line)
                if match:
                    subtotal_scaled = self._parse_scaled_amount(match.group(1))
            elif "TAX" in upper_line or "VAT" in upper_line or "GST" in upper_line:
                if "TAX ID" not in upper_line and "VAT NO" not in upper_line:
                    match = self.AMOUNT_PATTERN.search(line)
                    if match:
                        tax_scaled = self._parse_scaled_amount(match.group(1))
            elif "TIP" in upper_line or "GRATUITY" in upper_line:
                match = self.AMOUNT_PATTERN.search(line)
                if match:
                    tip_scaled = self._parse_scaled_amount(match.group(1))
            elif "TOTAL" in upper_line or "AMOUNT DUE" in upper_line or "GRAND TOTAL" in upper_line:
                if "SUBTOTAL" not in upper_line and "SUB TOTAL" not in upper_line:
                    match = self.AMOUNT_PATTERN.search(line)
                    if match:
                        total_scaled = self._parse_scaled_amount(match.group(1))
            else:
                # Check for itemized line item pattern (e.g. "Coffee $4.50" or "Sandwich 12.99")
                match = self.AMOUNT_PATTERN.search(line)
                if match:
                    amt_str = match.group(1)
                    desc = line[: match.start()].strip()
                    if desc and len(desc) > 1 and not any(kw in desc.upper() for kw in ["CASH", "CHANGE", "CARD", "VISA"]):
                        line_items.append(
                            ReceiptLineItem(
                                description=desc,
                                amount_scaled=self._parse_scaled_amount(amt_str),
                            )
                        )

        # 5. Fallback arithmetic inference if total/subtotal missing
        if total_scaled == 0 and line_items:
            total_scaled = sum(item.amount_scaled for item in line_items) + tax_scaled + tip_scaled

        if subtotal_scaled == 0 and line_items:
            subtotal_scaled = sum(item.amount_scaled for item in line_items)

        # 6. Verify mathematical invariant: Subtotal + Tax + Tip == Total
        calculated_total = subtotal_scaled + tax_scaled + tip_scaled
        math_validated = (calculated_total == total_scaled) and (total_scaled > 0)

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        confidence = 0.98 if math_validated else 0.85

        return ReceiptExtractionResult(
            merchant_name=merchant_name,
            tax_id=tax_id,
            transaction_date=transaction_date,
            line_items=line_items,
            subtotal_scaled=subtotal_scaled,
            tax_scaled=tax_scaled,
            tip_scaled=tip_scaled,
            total_scaled=total_scaled,
            math_validated=math_validated,
            confidence_score=confidence,
            processing_time_ms=elapsed_ms,
        )
