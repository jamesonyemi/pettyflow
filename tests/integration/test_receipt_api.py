"""Integration tests for receipt extraction API endpoint."""

import io
from fastapi.testclient import TestClient
from PIL import Image

from src.api.app import create_app

client = TestClient(create_app())


def test_receipt_extract_raw_text():
    response = client.post(
        "/v1/receipts/extract",
        data={
            "raw_text_override": (
                "BISTRO DELIGHT\n"
                "TAX ID: VAT-998877\n"
                "DATE: 2026-08-20\n"
                "Lunch Special $15.00\n"
                "Subtotal $15.00\n"
                "Tax $1.50\n"
                "Tip $3.00\n"
                "Total $19.50\n"
            )
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["merchant_name"] == "BISTRO DELIGHT"
    assert data["tax_id"] == "VAT-998877"
    assert data["subtotal_scaled"] == 150000
    assert data["tax_scaled"] == 15000
    assert data["tip_scaled"] == 30000
    assert data["total_scaled"] == 195000
    assert data["math_validated"] is True


def test_receipt_extract_image_upload():
    img = Image.new("RGB", (300, 300), color="white")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    img_bytes = buffer.getvalue()

    response = client.post(
        "/v1/receipts/extract",
        files={"file": ("receipt.png", img_bytes, "image/png")},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["merchant_name"] is not None
    assert "total_scaled" in data
    assert data["processing_time_ms"] < 1800.0


def test_receipt_extract_missing_file_and_text():
    response = client.post("/v1/receipts/extract")
    assert response.status_code == 400
