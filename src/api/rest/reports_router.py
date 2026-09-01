"""Financial Reports & Statement Exporter REST API Router.

Provides CSV and PDF export endpoints for executive financial statements,
departmental breakdowns, and multi-currency ledger balances.
"""

from __future__ import annotations

import csv
import io
import datetime
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel

from src.services.analytics.spend_aggregator import (
    SpendAggregator,
    SpendRecord,
)

# ---------------------------------------------------------------------------
# Router Factory
# ---------------------------------------------------------------------------

def create_reports_router(
    aggregator: Optional[SpendAggregator] = None,
    mock_records: Optional[List[SpendRecord]] = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/reports", tags=["Financial Reports"])
    _aggregator = aggregator or SpendAggregator()
    _records: List[SpendRecord] = mock_records if mock_records is not None else []

    @router.post("/records", status_code=status.HTTP_201_CREATED)
    def add_spend_record(record: dict):
        spend_rec = SpendRecord(
            transaction_id=record["transaction_id"],
            tenant_id=record["tenant_id"],
            department=record["department"],
            location=record["location"],
            category=record["category"],
            amount_scaled=record["amount_scaled"],
            currency=record.get("currency", "USD"),
        )
        _records.append(spend_rec)
        return {"status": "RECORD_ADDED", "transaction_id": spend_rec.transaction_id}

    @router.get("/spend-summary", status_code=status.HTTP_200_OK)
    def get_spend_summary(tenant_id: str = Query(..., description="Tenant UUID")):
        summary = _aggregator.get_executive_summary(_records, tenant_id)
        return summary

    @router.get("/export/csv")
    def export_csv_statement(tenant_id: str = Query(..., description="Tenant UUID")):
        tenant_recs = [r for r in _records if r.tenant_id == tenant_id]
        
        output = io.StringIO()
        writer = csv.writer(output)
        # Header
        writer.writerow([
            "Transaction ID",
            "Tenant ID",
            "Timestamp",
            "Department",
            "Location",
            "Category",
            "Amount (Scaled)",
            "Amount (USD)",
            "Currency",
        ])

        for r in tenant_recs:
            writer.writerow([
                r.transaction_id,
                r.tenant_id,
                r.timestamp,
                r.department,
                r.location,
                r.category,
                r.amount_scaled,
                f"{r.amount_scaled / 10_000:.2f}",
                r.currency,
            ])

        csv_data = output.getvalue()
        filename = f"pettyflow_statement_{tenant_id}_{datetime.date.today().isoformat()}.csv"
        return Response(
            content=csv_data,
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    @router.get("/export/pdf")
    def export_pdf_statement(tenant_id: str = Query(..., description="Tenant UUID")):
        """Render a concise PDF financial statement."""
        from reportlab.lib.pagesizes import letter
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

        tenant_recs = [r for r in _records if r.tenant_id == tenant_id]
        summary = _aggregator.get_executive_summary(_records, tenant_id)

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=letter,
            rightMargin=36,
            leftMargin=36,
            topMargin=36,
            bottomMargin=36,
        )

        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            "ReportTitle",
            parent=styles["Heading1"],
            fontSize=18,
            textColor=colors.HexColor("#6B21A8"),
            spaceAfter=12,
        )
        body_style = styles["Normal"]

        story = []
        story.append(Paragraph("PETTYFLOW EXECUTIVE FINANCIAL STATEMENT", title_style))
        story.append(Paragraph(f"<b>Tenant ID:</b> {tenant_id} | <b>Date:</b> {datetime.date.today().isoformat()}", body_style))
        story.append(Paragraph(f"<b>Total Spend:</b> {summary['total_spend_formatted']} | <b>Transactions:</b> {summary['total_transactions']}", body_style))
        story.append(Spacer(1, 14))

        # Table data
        table_data = [["Transaction ID", "Department", "Location", "Category", "Amount"]]
        for r in tenant_recs[:30]:  # Up to 30 items
            table_data.append([
                r.transaction_id,
                r.department,
                r.location,
                r.category,
                f"${r.amount_scaled / 10_000:.2f}",
            ])

        if len(table_data) > 1:
            t = Table(table_data, colWidths=[120, 100, 100, 120, 80])
            t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#6B21A8")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ]))
            story.append(t)

        doc.build(story)
        pdf_bytes = buffer.getvalue()
        buffer.close()

        filename = f"pettyflow_statement_{tenant_id}_{datetime.date.today().isoformat()}.pdf"
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    return router
