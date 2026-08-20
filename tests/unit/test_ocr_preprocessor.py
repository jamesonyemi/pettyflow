"""Unit tests for ReceiptImagePreprocessor."""

import io
import pytest
from PIL import Image

from src.services.ai.preprocessor import ImagePreprocessingError, ReceiptImagePreprocessor


def test_preprocessor_empty_bytes():
    preprocessor = ReceiptImagePreprocessor()
    with pytest.raises(ImagePreprocessingError, match="empty"):
        preprocessor.preprocess_image_bytes(b"")


def test_preprocessor_invalid_format():
    preprocessor = ReceiptImagePreprocessor()
    with pytest.raises(ImagePreprocessingError, match="Failed to decode"):
        preprocessor.preprocess_image_bytes(b"NOT_AN_IMAGE_PAYLOAD")


def test_preprocessor_valid_png_processing():
    preprocessor = ReceiptImagePreprocessor(target_max_dimension=512)

    # Generate sample test image in memory
    img = Image.new("RGB", (800, 600), color="white")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    raw_bytes = buffer.getvalue()

    processed_img, processed_bytes = preprocessor.preprocess_image_bytes(raw_bytes)

    assert processed_img is not None
    assert isinstance(processed_bytes, bytes)
    assert len(processed_bytes) > 0
    # Dimension should be resized to max 512 maintaining aspect ratio
    assert max(processed_img.size) <= 512


def test_preprocessor_excessive_dimensions_rejected():
    # Set low max_pixels threshold (e.g. 100,000 pixels)
    preprocessor = ReceiptImagePreprocessor(max_pixels=100_000)

    # Create 500x500 image = 250,000 pixels
    img = Image.new("RGB", (500, 500), color="white")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    raw_bytes = buffer.getvalue()

    with pytest.raises(ImagePreprocessingError, match="exceed maximum limit"):
        preprocessor.preprocess_image_bytes(raw_bytes)
