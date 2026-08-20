"""Integration tests for receipt extraction API endpoint."""

import io
import time
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from src.api.app import create_app
from src.api.rest.receipt_router import create_receipt_router

client = TestClient(create_app())


def _make_test_image_bytes() -> bytes:
    img = Image.new("RGB", (300, 300), color="white")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


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
    img_bytes = _make_test_image_bytes()
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


def test_receipt_extract_custom_vision_extractor():
    def custom_vision(png_bytes: bytes) -> str:
        return "CUSTOM CAFE\nTotal $5.00\nSubtotal $5.00\n"

    custom_app = FastAPI()
    custom_app.include_router(create_receipt_router(vision_extractor=custom_vision))
    custom_client = TestClient(custom_app)

    response = custom_client.post(
        "/v1/receipts/extract",
        files={"file": ("receipt.png", _make_test_image_bytes(), "image/png")},
    )
    assert response.status_code == 200
    assert response.json()["merchant_name"] == "CUSTOM CAFE"


def test_receipt_extract_vision_timeout_returns_504():
    def slow_vision(png_bytes: bytes) -> str:
        time.sleep(0.5)
        return "SLOW CAFE\nTotal $5.00\n"

    custom_app = FastAPI()
    custom_app.include_router(
        create_receipt_router(vision_extractor=slow_vision, extractor_timeout_seconds=0.1)
    )
    custom_client = TestClient(custom_app)

    response = custom_client.post(
        "/v1/receipts/extract",
        files={"file": ("receipt.png", _make_test_image_bytes(), "image/png")},
    )
    assert response.status_code == 504
    assert "timed out" in response.json()["detail"]


def test_receipt_extract_vision_error_returns_502():
    def failing_vision(png_bytes: bytes) -> str:
        raise RuntimeError("Cloud Vision API connection reset")

    custom_app = FastAPI()
    custom_app.include_router(create_receipt_router(vision_extractor=failing_vision))
    custom_client = TestClient(custom_app)

    response = custom_client.post(
        "/v1/receipts/extract",
        files={"file": ("receipt.png", _make_test_image_bytes(), "image/png")},
    )
    assert response.status_code == 502
    assert "adapter failed" in response.json()["detail"]
