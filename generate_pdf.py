import sys
import os
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether, HRFlowable
)
from reportlab.pdfgen import canvas

class NumberedCanvas(canvas.Canvas):
    """
    Two-pass canvas to dynamically compute and render total page numbers
    along with professional header and footer rules.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count):
        if self._pageNumber == 1:
            # Suppress headers and footers on cover page
            return

        self.saveState()
        self.setFont("Helvetica-Bold", 8)
        self.setFillColor(colors.HexColor("#6B21A8"))
        
        # Header text & line
        self.drawString(54, 750, "PETTYFLOW SYSTEM ARCHITECTURE & PRODUCT SPECIFICATION")
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#6B7280"))
        self.drawRightString(612 - 54, 750, "JEFF DEAN DESIGN STANDARD")
        
        self.setStrokeColor(colors.HexColor("#E9D5FF"))
        self.setLineWidth(0.75)
        self.line(54, 742, 612 - 54, 742)

        # Footer line & text
        self.line(54, 50, 612 - 54, 50)
        self.drawString(54, 38, "CONFIDENTIAL & PROPRIETARY — PETTYFLOW INC.")
        
        page_str = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(612 - 54, 38, page_str)
        self.restoreState()


def build_pdf(filename="PETTYFLOW_PRODUCT_PLANNING_AND_ARCHITECTURE.pdf"):
    doc = SimpleDocTemplate(
        filename,
        pagesize=letter,
        leftMargin=54,
        rightMargin=54,
        topMargin=64,
        bottomMargin=64
    )

    styles = getSampleStyleSheet()

    # Define Custom Palette
    PRIMARY = colors.HexColor("#581C87")     # Deep Purple
    SECONDARY = colors.HexColor("#7E22CE")   # Purple Accent
    LIGHT_BG = colors.HexColor("#F3E8FF")    # Soft Purple Fill
    DARK_TEXT = colors.HexColor("#111827")   # Dark Neutral
    MUTED_TEXT = colors.HexColor("#4B5563")  # Slate Gray
    BORDER_COLOR = colors.HexColor("#DDD6FE")# Soft Border

    # Custom Typography Styles
    title_style = ParagraphStyle(
        'CoverTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=28,
        leading=34,
        textColor=PRIMARY,
        spaceAfter=12
    )

    subtitle_style = ParagraphStyle(
        'CoverSubtitle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=14,
        leading=18,
        textColor=MUTED_TEXT,
        spaceAfter=24
    )

    h1_style = ParagraphStyle(
        'Heading1_Custom',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=16,
        leading=20,
        textColor=PRIMARY,
        spaceBefore=16,
        spaceAfter=8,
        keepWithNext=True
    )

    h2_style = ParagraphStyle(
        'Heading2_Custom',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=12,
        leading=16,
        textColor=SECONDARY,
        spaceBefore=12,
        spaceAfter=6,
        keepWithNext=True
    )

    body_style = ParagraphStyle(
        'Body_Custom',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9.5,
        leading=13.5,
        textColor=DARK_TEXT,
        spaceAfter=8
    )

    bullet_style = ParagraphStyle(
        'Bullet_Custom',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=13,
        textColor=DARK_TEXT,
        leftIndent=12,
        spaceAfter=4
    )

    code_style = ParagraphStyle(
        'Code_Custom',
        parent=styles['Normal'],
        fontName='Courier',
        fontSize=8,
        leading=10.5,
        textColor=colors.HexColor("#374151"),
        backColor=colors.HexColor("#F9FAFB"),
        borderColor=colors.HexColor("#E5E7EB"),
        borderWidth=0.5,
        borderPadding=6,
        spaceBefore=6,
        spaceAfter=8
    )

    table_header_style = ParagraphStyle(
        'TableHeader',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=9,
        leading=11,
        textColor=PRIMARY
    )

    table_cell_style = ParagraphStyle(
        'TableCell',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8.5,
        leading=11.5,
        textColor=DARK_TEXT
    )

    story = []

    # ==========================================
    # COVER PAGE
    # ==========================================
    story.append(Spacer(1, 40))
    story.append(Paragraph("PETTYFLOW", title_style))
    story.append(Paragraph("Enterprise Petty Cash System & Distributed Financial Ledger Platform", subtitle_style))
    
    story.append(HRFlowable(width="100%", thickness=3, color=PRIMARY, spaceBefore=0, spaceAfter=20))

    cover_meta = [
        [Paragraph("<b>Document Type:</b>", table_cell_style), Paragraph("Product Planning & Systems Architecture Specification", table_cell_style)],
        [Paragraph("<b>Engineering Standard:</b>", table_cell_style), Paragraph("Jeff Dean High-Performance Systems Paradigm", table_cell_style)],
        [Paragraph("<b>Author / Team:</b>", table_cell_style), Paragraph("Core Financial Infrastructure Group", table_cell_style)],
        [Paragraph("<b>Ledger Invariant:</b>", table_cell_style), Paragraph("Cryptographic HMAC-SHA256 Immutable Double-Entry", table_cell_style)],
        [Paragraph("<b>Latency Goal:</b>", table_cell_style), Paragraph("Sub-2ms Write Commitment (p99)", table_cell_style)],
        [Paragraph("<b>Target Throughput:</b>", table_cell_style), Paragraph("100,000 TPS Distributed Peak Capacity", table_cell_style)],
        [Paragraph("<b>Version:</b>", table_cell_style), Paragraph("1.0.0-RELEASE", table_cell_style)]
    ]
    t_cover = Table(cover_meta, colWidths=[130, 374])
    t_cover.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), LIGHT_BG),
        ('PADDING', (0,0), (-1,-1), 8),
        ('LINEBELOW', (0,0), (-1,-2), 0.5, BORDER_COLOR),
        ('BOX', (0,0), (-1,-1), 1, PRIMARY),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))
    story.append(t_cover)

    story.append(Spacer(1, 40))
    
    exec_summary_box = [
        [Paragraph("<b>EXECUTIVE ARCHITECTURAL SUMMARY</b>", h2_style)],
        [Paragraph(
            "PettyFlow is designed to solve systemic enterprise petty cash leakage, receipt fraud, and slow float replenishment cycles. "
            "Built upon Jeff Dean systems principles, PettyFlow guarantees strict double-entry balance invariants (&Sigma; Debits &equiv; &Sigma; Credits), "
            "tamper-evident SHA-256 cryptographic chain audits, zero-trust multi-tenancy, and sub-second AI receipt OCR extraction. "
            "This document establishes the product requirements, domain models, database DDLs, gRPC/REST APIs, and infrastructure specs.",
            body_style
        )]
    ]
    t_exec = Table(exec_summary_box, colWidths=[504])
    t_exec.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#FAF5FF")),
        ('PADDING', (0,0), (-1,-1), 12),
        ('BOX', (0,0), (-1,-1), 1, SECONDARY),
    ]))
    story.append(t_exec)

    story.append(PageBreak())

    # ==========================================
    # SECTION 1: JEFF DEAN LATENCY & THROUGHPUT BUDGETS
    # ==========================================
    story.append(Paragraph("1. System Latency & Performance Budgets", h1_style))
    story.append(Paragraph(
        "To achieve enterprise-scale throughput and zero loss of financial auditability, PettyFlow defines rigid latency boundaries "
        "modelled after Jeff Dean's 'Numbers Every Engineer Should Know'. All system components must fit within these explicit budgets:",
        body_style
    ))

    latency_data = [
        [Paragraph("Operation", table_header_style), Paragraph("Latency Target (p50)", table_header_style), Paragraph("Latency Target (p99)", table_header_style), Paragraph("Architecture Mechanism", table_header_style)],
        [Paragraph("L1/L2 Account State Lookup", table_cell_style), Paragraph("50 ns", table_cell_style), Paragraph("150 ns", table_cell_style), Paragraph("CPU L1/L2 cache ring buffer", table_cell_style)],
        [Paragraph("In-Memory Balance Verification", table_cell_style), Paragraph("15 &mu;s", table_cell_style), Paragraph("50 &mu;s", table_cell_style), Paragraph("Lock-free memory map", table_cell_style)],
        [Paragraph("Double-Entry Ledger Commit", table_cell_style), Paragraph("600 &mu;s", table_cell_style), Paragraph("1.8 ms", table_cell_style), Paragraph("Append-only WAL + HMAC", table_cell_style)],
        [Paragraph("Redis Cache Balance Invalidation", table_cell_style), Paragraph("200 &mu;s", table_cell_style), Paragraph("800 &mu;s", table_cell_style), Paragraph("Async pipeline LUA script", table_cell_style)],
        [Paragraph("AI OCR Receipt Extraction", table_cell_style), Paragraph("850 ms", table_cell_style), Paragraph("1.4 s", table_cell_style), Paragraph("TensorRT / VLM async worker", table_cell_style)],
        [Paragraph("Perceptual Fraud Screening", table_cell_style), Paragraph("12 ms", table_cell_style), Paragraph("35 ms", table_cell_style), Paragraph("dHash bit vector comparison", table_cell_style)],
        [Paragraph("Cross-Region Replica Sync", table_cell_style), Paragraph("18 ms", table_cell_style), Paragraph("45 ms", table_cell_style), Paragraph("Postgres streaming replication", table_cell_style)],
    ]
    t_latency = Table(latency_data, colWidths=[140, 95, 95, 174])
    t_latency.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), LIGHT_BG),
        ('PADDING', (0,0), (-1,-1), 6),
        ('GRID', (0,0), (-1,-1), 0.5, BORDER_COLOR),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))
    story.append(t_latency)
    story.append(Spacer(1, 14))

    # ==========================================
    # SECTION 2: DOMAIN MODEL & DOUBLE-ENTRY LEDGER
    # ==========================================
    story.append(Paragraph("2. Double-Entry Cryptographic Ledger Engine", h1_style))
    story.append(Paragraph(
        "PettyFlow models all financial activity using strict double-entry accounting. Money cannot be created or destroyed, only moved between accounts. "
        "Every posting consists of matched debit and credit legs:",
        body_style
    ))
    
    story.append(Paragraph("Fundamental Ledger Invariants:", h2_style))
    story.append(Paragraph("&bull; <b>Zero-Sum Equation:</b> &sum; Debits - &sum; Credits = 0 for every transaction UUID.", bullet_style))
    story.append(Paragraph("&bull; <b>Fixed-Point Arithmetic:</b> Amounts stored as 64-bit integers scaled by 10,000 (0.0001 precision).", bullet_style))
    story.append(Paragraph("&bull; <b>Cryptographic Hash Chain:</b> Entry H<sub>n</sub> = HMAC-SHA256(H<sub>n-1</sub> || EntryPayload || Timestamp, Key<sub>Tenant</sub>).", bullet_style))

    story.append(Spacer(1, 8))
    story.append(Paragraph("Account Classifications & Balance Invariants", h2_style))
    
    acct_data = [
        [Paragraph("Account Category", table_header_style), Paragraph("Normal Balance", table_header_style), Paragraph("Debit Impact (+)", table_header_style), Paragraph("Credit Impact (-)", table_header_style), Paragraph("PettyFlow Example", table_header_style)],
        [Paragraph("ASSET", table_cell_style), Paragraph("Debit", table_cell_style), Paragraph("Increases Balance", table_cell_style), Paragraph("Decreases Balance", table_cell_style), Paragraph("Custodial Physical Cash Float", table_cell_style)],
        [Paragraph("EXPENSE", table_cell_style), Paragraph("Debit", table_cell_style), Paragraph("Increases Expense", table_cell_style), Paragraph("Decreases Expense", table_cell_style), Paragraph("Office Supplies / Fuel Receipt", table_cell_style)],
        [Paragraph("LIABILITY", table_cell_style), Paragraph("Credit", table_cell_style), Paragraph("Decreases Liability", table_cell_style), Paragraph("Increases Liability", table_cell_style), Paragraph("Pending Employee Reimbursement", table_cell_style)],
        [Paragraph("EQUITY", table_cell_style), Paragraph("Credit", table_cell_style), Paragraph("Decreases Capital", table_cell_style), Paragraph("Increases Capital", table_cell_style), Paragraph("Corporate Float Capital Allocation", table_cell_style)]
    ]
    t_acct = Table(acct_data, colWidths=[90, 85, 105, 105, 119])
    t_acct.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), LIGHT_BG),
        ('PADDING', (0,0), (-1,-1), 5),
        ('GRID', (0,0), (-1,-1), 0.5, BORDER_COLOR),
    ]))
    story.append(t_acct)
    story.append(Spacer(1, 14))

    # ==========================================
    # SECTION 3: DATABASE ARCHITECTURE
    # ==========================================
    story.append(Paragraph("3. Relational & TimescaleDB Database Schema", h1_style))
    story.append(Paragraph(
        "PettyFlow uses PostgreSQL for transactional metadata and partitioned TimescaleDB tables for audit logging. "
        "The schema below enforces multi-tenant isolation via compound foreign keys and immutability triggers:",
        body_style
    ))

    sql_snippet = """-- Core Multi-Tenant Double-Entry Postings Table
CREATE TABLE pettyflow_postings (
    posting_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      UUID NOT NULL REFERENCES pettyflow_tenants(tenant_id),
    transaction_id UUID NOT NULL,
    account_id     UUID NOT NULL REFERENCES pettyflow_accounts(account_id),
    amount_scaled  BIGINT NOT NULL, -- Scaled by 10,000 (e.g. $12.50 -> 125000)
    entry_type     VARCHAR(6) NOT NULL CHECK (entry_type IN ('DEBIT', 'CREDIT')),
    previous_hash  BYTEA NOT NULL,
    current_hash   BYTEA NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
) PARTITION BY RANGE (created_at);

-- Immutability Guard: Prevent UPDATE or DELETE on posting records
CREATE RULE prevent_postings_alter AS ON UPDATE TO pettyflow_postings DO INSTEAD NOTHING;
CREATE RULE prevent_postings_delete AS ON DELETE TO pettyflow_postings DO INSTEAD NOTHING;"""

    story.append(Paragraph(sql_snippet.replace('\n', '<br/>').replace(' ', '&nbsp;'), code_style))
    story.append(PageBreak())

    # ==========================================
    # SECTION 4: API ARCHITECTURE
    # ==========================================
    story.append(Paragraph("4. High-Throughput API Specifications", h1_style))
    story.append(Paragraph(
        "PettyFlow exposes gRPC endpoints for inter-service communication and OpenAPI REST endpoints for mobile & web apps.",
        body_style
    ))

    story.append(Paragraph("Core Protobuf Service Definition (`float_service.proto`):", h2_style))
    
    proto_snippet = """syntax = "proto3";
package pettyflow.v1;

service FloatService {
  rpc CreateFloatAllocation (CreateFloatRequest) returns (CreateFloatResponse);
  rpc SubmitDisbursement (DisbursementRequest) returns (DisbursementResponse);
  rpc VerifyLedgerIntegrity (IntegrityCheckRequest) returns (IntegrityCheckResponse);
}

message DisbursementRequest {
  string tenant_id = 1;
  string fund_id = 2;
  string custodian_id = 3;
  int64 amount_scaled = 4;
  string currency = 5;
  bytes receipt_hash = 6;
}"""

    story.append(Paragraph(proto_snippet.replace('\n', '<br/>').replace(' ', '&nbsp;'), code_style))
    story.append(Spacer(1, 10))

    # ==========================================
    # SECTION 5: AI OCR RECEIPT & FRAUD ENGINE
    # ==========================================
    story.append(Paragraph("5. AI OCR & Machine Learning Fraud Detection", h1_style))
    story.append(Paragraph(
        "To mitigate cash leakage and receipt forgery, PettyFlow deploys a dual-stage AI verification pipeline:",
        body_style
    ))

    ai_table_data = [
        [Paragraph("Pipeline Stage", table_header_style), Paragraph("Model / Tech Stack", table_header_style), Paragraph("Function & Invariant", table_header_style)],
        [Paragraph("1. Image Preprocessing", table_cell_style), Paragraph("OpenCV / CUDA", table_cell_style), Paragraph("Deskew, contrast normalisation, noise reduction", table_cell_style)],
        [Paragraph("2. Document OCR", table_cell_style), Paragraph("TrOCR / Vision LLM", table_cell_style), Paragraph("Extract merchant, date, tax ID, items, subtotal, tax, total", table_cell_style)],
        [Paragraph("3. Math Validation", table_cell_style), Paragraph("Python SymPy / Deterministic", table_cell_style), Paragraph("Verify Subtotal + Tax + Tip &equiv; Total", table_cell_style)],
        [Paragraph("4. Perceptual Dup Check", table_cell_style), Paragraph("dHash / Hamming Distance", table_cell_style), Paragraph("Flag receipt photos matching prior submissions (&le; 5 bits)", table_cell_style)],
        [Paragraph("5. Split Tx Detection", table_cell_style), Paragraph("Sliding Window Aggregator", table_cell_style), Paragraph("Detect multiple disbursements designed to bypass approval limits", table_cell_style)]
    ]
    t_ai = Table(ai_table_data, colWidths=[110, 130, 264])
    t_ai.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), LIGHT_BG),
        ('PADDING', (0,0), (-1,-1), 6),
        ('GRID', (0,0), (-1,-1), 0.5, BORDER_COLOR),
    ]))
    story.append(t_ai)
    story.append(Spacer(1, 14))

    # ==========================================
    # SECTION 6: SECURITY & COMPLIANCE
    # ==========================================
    story.append(Paragraph("6. Zero-Trust Security & Compliance Framework", h1_style))
    story.append(Paragraph(
        "PettyFlow enforces SOC2 Type II, PCI-DSS v4.0, and GDPR compliance throughout the architecture:",
        body_style
    ))
    story.append(Paragraph("&bull; <b>Envelope Encryption:</b> Data at rest encrypted with AES-256-GCM via AWS KMS / HashiCorp Vault. Master key rotated every 90 days.", bullet_style))
    story.append(Paragraph("&bull; <b>Tenant Context Scoping:</b> Strict multi-tenancy enforced at ORM level via non-bypassable session variables (`SET LOCAL app.current_tenant`).", bullet_style))
    story.append(Paragraph("&bull; <b>Immutable WORM Logs:</b> Audit logs streamed to AWS S3 Object Lock in Compliance Mode (Write Once, Read Many).", bullet_style))

    story.append(Spacer(1, 14))

    # ==========================================
    # SECTION 7: 12-WEEK AI ROADMAP SUMMARY
    # ==========================================
    story.append(Paragraph("7. 12-Week AI Deliverable Roadmap Summary", h1_style))
    story.append(Paragraph(
        "The following weekly milestones govern the AI code generation and deployment schedule:",
        body_style
    ))

    roadmap_table = [
        [Paragraph("Week", table_header_style), Paragraph("Deliverable Focus", table_header_style), Paragraph("Key Engineering Artifacts", table_header_style)],
        [Paragraph("Week 1", table_cell_style), Paragraph("Core Double-Entry Ledger", table_cell_style), Paragraph("Domain ledger, HMAC chain, invariant unit tests", table_cell_style)],
        [Paragraph("Week 2", table_cell_style), Paragraph("Database & Cache Tier", table_cell_style), Paragraph("PostgreSQL partitioning, Redis LUA balance cache", table_cell_style)],
        [Paragraph("Week 3", table_cell_style), Paragraph("Float Allocation APIs", table_cell_style), Paragraph("gRPC protobuf services, FastAPI REST wrapper", table_cell_style)],
        [Paragraph("Week 4", table_cell_style), Paragraph("Approval Workflow Engine", table_cell_style), Paragraph("Deterministic state machine, rule evaluator", table_cell_style)],
        [Paragraph("Week 5", table_cell_style), Paragraph("AI OCR Receipt Ingestion", table_cell_style), Paragraph("TrOCR parser pipeline, image preprocessor", table_cell_style)],
        [Paragraph("Week 6", table_cell_style), Paragraph("ML Fraud Screening", table_cell_style), Paragraph("Perceptual hash, split transaction detector", table_cell_style)],
        [Paragraph("Week 7", table_cell_style), Paragraph("Card & Wallet Adapters", table_cell_style), Paragraph("Stripe Virtual Cards, Mobile Money API", table_cell_style)],
        [Paragraph("Week 8", table_cell_style), Paragraph("ERP & Bank Connectors", table_cell_style), Paragraph("SAP S/4HANA, NetSuite, ISO 20022 bank feed", table_cell_style)],
        [Paragraph("Week 9", table_cell_style), Paragraph("Reconciliation Engine", table_cell_style), Paragraph("3-Way matching, daily variance closing", table_cell_style)],
        [Paragraph("Week 10", table_cell_style), Paragraph("Zero-Trust & KMS Security", table_cell_style), Paragraph("Envelope encryption, SOC2 WORM audit log", table_cell_style)],
        [Paragraph("Week 11", table_cell_style), Paragraph("Analytics & Multi-Currency", table_cell_style), Paragraph("ECB rate sync, real-time analytics dashboard", table_cell_style)],
        [Paragraph("Week 12", table_cell_style), Paragraph("100k TPS Load & Launch", table_cell_style), Paragraph("Locust stress tests, Kubernetes Helm deploy", table_cell_style)]
    ]
    t_road = Table(roadmap_table, colWidths=[55, 140, 309])
    t_road.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), LIGHT_BG),
        ('PADDING', (0,0), (-1,-1), 5),
        ('GRID', (0,0), (-1,-1), 0.5, BORDER_COLOR),
    ]))
    story.append(t_road)

    doc.build(story, canvasmaker=NumberedCanvas)
    print(f"Successfully generated {filename}")

if __name__ == "__main__":
    out_path = "PETTYFLOW_PRODUCT_PLANNING_AND_ARCHITECTURE.pdf"
    if len(sys.argv) > 1:
        out_path = sys.argv[1]
    build_pdf(out_path)
