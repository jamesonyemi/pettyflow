---
name: "AI/Vision: Receipt Parsing Pipeline Hardening"
about: "Hardened multi-modal receipt parsing pipeline with line-item and vendor normalization."
title: "[AI/OCR] Receipt Parsing Pipeline Hardening & Line-Item Extraction"
labels: ["area:ai", "ocr"]
assignees: []
---

### Executive Overview
Integrate a production receipt parsing model (LayoutLM / Cloud Vision / multimodal vision) to extract vendor, date, line items, and taxes from receipts.

### Scope & Deliverables
- [ ] Connect production OCR backend in `src/services/ai/ocr_processor.py`.
- [ ] Extract structured items: Vendor Name, Tax ID, Date, Currency, Itemized Lines, Tax Amount, Total.
- [ ] Implement adaptive image enhancement heuristics in `src/services/ai/preprocessor.py` for crumpled / low-contrast receipt uploads.
- [ ] Add perceptual hash calculation (`src/services/fraud/perceptual_hash.py`) for duplicate receipt detection.

### Acceptance Criteria
- Parsing accuracy $> 95\%$ on standard POS and thermal paper receipts.
- Duplicate receipt image upload flags immediately with `DUPLICATE_RECEIPT_DETECTED`.
