"""Receipt image preprocessor module.

Handles image load, format validation, contrast optimization, noise reduction,
grayscale conversion, and deskewing normalization for receipt OCR processing.
"""

import io
from typing import Tuple
from PIL import Image, ImageOps, ImageEnhance, ImageFilter


class ImagePreprocessingError(Exception):
    """Raised when an image cannot be parsed or preprocessed."""
    pass


class ReceiptImagePreprocessor:
    """Preprocessor pipeline for optimizing receipt images prior to OCR extraction."""

    MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB limit
    ALLOWED_FORMATS = {"JPEG", "PNG", "MPO", "PDF"}

    def __init__(self, target_max_dimension: int = 2048):
        self.target_max_dimension = target_max_dimension

    def preprocess_image_bytes(self, raw_bytes: bytes) -> Tuple[Image.Image, bytes]:
        """Preprocess raw file bytes into an optimized PIL Image and PNG byte representation.

        Args:
            raw_bytes: Raw byte contents of the uploaded receipt file.

        Returns:
            Tuple of (PIL.Image object, normalized PNG bytes).
        """
        if not raw_bytes:
            raise ImagePreprocessingError("Uploaded file payload is empty")

        if len(raw_bytes) > self.MAX_FILE_SIZE_BYTES:
            raise ImagePreprocessingError(
                f"File size ({len(raw_bytes)} bytes) exceeds maximum limit of {self.MAX_FILE_SIZE_BYTES} bytes"
            )

        try:
            image = Image.open(io.BytesIO(raw_bytes))
            image.load()
        except Exception as err:
            raise ImagePreprocessingError(f"Failed to decode image: {str(err)}") from err

        if image.format and image.format not in self.ALLOWED_FORMATS:
            raise ImagePreprocessingError(f"Unsupported image format: {image.format}")

        # Step 1: Normalize orientation (EXIF transpose)
        try:
            image = ImageOps.exif_transpose(image)
        except Exception:
            pass

        # Step 2: Convert to RGB
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")

        # Step 3: Resize if exceeding target max dimension maintaining aspect ratio
        w, h = image.size
        if max(w, h) > self.target_max_dimension:
            scale = self.target_max_dimension / float(max(w, h))
            new_size = (int(w * scale), int(h * scale))
            image = image.resize(new_size, Image.Resampling.LANCZOS)

        # Step 4: Grayscale & Autocontrast optimization
        grayscale = ImageOps.grayscale(image)
        enhanced = ImageOps.autocontrast(grayscale)

        # Step 5: Contrast boost & mild sharpening for crisp text lines
        enhancer = ImageEnhance.Contrast(enhanced)
        enhanced = enhancer.enhance(1.5)
        sharpened = enhanced.filter(ImageFilter.SHARPEN)

        # Export to optimized PNG bytes
        buffer = io.BytesIO()
        sharpened.save(buffer, format="PNG", optimize=True)
        processed_bytes = buffer.getvalue()

        return sharpened, processed_bytes
