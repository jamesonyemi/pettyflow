"""Perceptual Hash (dHash) Image Similarity Detection for Duplicate Receipt Flagging.

Implements the difference-hash (dHash) algorithm for near-duplicate image detection:
  1. Resize image to (width+1, height) greyscale thumbnail.
  2. Compute row-wise pixel difference bits (left vs right neighbour).
  3. Pack 64 bits into an integer fingerprint.
  4. Compute Hamming distance between two fingerprints.
  5. Distance <= 5 bits triggers automatic duplicate-receipt flag.

Design decisions:
  - Pure Python + Pillow (no heavy ML deps for inference path).
  - SCALE_FACTOR 10^4 integer arithmetic throughout for monetary amounts.
  - Tenant-scoped fingerprint stores to enforce multi-tenant isolation.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from PIL import Image


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DHASH_WIDTH: int = 9    # Resize target width (produces 8-wide difference grid)
DHASH_HEIGHT: int = 8   # Resize target height
DUPLICATE_BIT_THRESHOLD: int = 5  # Hamming distance <= this = duplicate flag


# ---------------------------------------------------------------------------
# Core dHash Implementation
# ---------------------------------------------------------------------------

def compute_dhash(image_bytes: bytes) -> int:
    """Compute 64-bit dHash fingerprint from raw image bytes.

    Args:
        image_bytes: Raw bytes of a JPEG/PNG/PDF-rendered receipt image.

    Returns:
        64-bit integer dHash fingerprint.

    Raises:
        ValueError: If the image cannot be decoded.
    """
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            # Convert to greyscale and resize to (DHASH_WIDTH x DHASH_HEIGHT)
            grey = img.convert("L").resize(
                (DHASH_WIDTH, DHASH_HEIGHT), Image.Resampling.LANCZOS
            )
            if hasattr(grey, "get_flattened_data"):
                pixels = list(grey.get_flattened_data())
            else:
                pixels = list(grey.getdata())

    except Exception as exc:
        raise ValueError(f"Cannot decode image for dHash computation: {exc}") from exc

    # Build 64-bit difference hash: compare each pixel to its right neighbour
    # pixels[row * DHASH_WIDTH + col] vs pixels[row * DHASH_WIDTH + col + 1]
    bit_index = 0
    fingerprint = 0
    for row in range(DHASH_HEIGHT):
        for col in range(DHASH_WIDTH - 1):
            left = pixels[row * DHASH_WIDTH + col]
            right = pixels[row * DHASH_WIDTH + col + 1]
            if left > right:
                fingerprint |= (1 << bit_index)
            bit_index += 1

    return fingerprint


def hamming_distance(hash_a: int, hash_b: int) -> int:
    """Compute Hamming distance (number of differing bits) between two 64-bit hashes.

    Uses Brian Kernighan's bit-counting algorithm — O(k) where k = set bits.
    """
    xor = hash_a ^ hash_b
    count = 0
    while xor:
        xor &= xor - 1
        count += 1
    return count


# ---------------------------------------------------------------------------
# Duplicate Receipt Detector — Tenant-Scoped Fingerprint Store
# ---------------------------------------------------------------------------

@dataclass
class FingerprintEntry:
    """Stored fingerprint record for a receipt image."""
    receipt_id: str
    tenant_id: str
    custodian_id: str
    fingerprint: int
    transaction_date: str


@dataclass
class DuplicateFlag:
    """Result raised when a near-duplicate receipt is detected."""
    new_receipt_id: str
    existing_receipt_id: str
    hamming_distance: int
    is_duplicate: bool = True


class PerceptualHasher:
    """Stateless utility for computing and comparing perceptual hashes."""

    @staticmethod
    def hash_image(image_bytes: bytes) -> int:
        """Compute dHash fingerprint from raw image bytes."""
        return compute_dhash(image_bytes)

    @staticmethod
    def distance(hash_a: int, hash_b: int) -> int:
        """Return Hamming distance between two dHash fingerprints."""
        return hamming_distance(hash_a, hash_b)

    @staticmethod
    def is_duplicate(hash_a: int, hash_b: int, threshold: int = DUPLICATE_BIT_THRESHOLD) -> bool:
        """Return True if two hashes are within duplicate threshold."""
        return hamming_distance(hash_a, hash_b) <= threshold


class DuplicateReceiptDetector:
    """Tenant-scoped store for perceptual fingerprints with duplicate flagging.

    Invariant: all stored entries are scoped by tenant_id to prevent
    cross-tenant false positives (multi-tenancy isolation).
    """

    def __init__(self, duplicate_threshold: int = DUPLICATE_BIT_THRESHOLD):
        self.duplicate_threshold = duplicate_threshold
        # tenant_id -> list of FingerprintEntry
        self._store: Dict[str, List[FingerprintEntry]] = {}

    def register(
        self,
        tenant_id: str,
        receipt_id: str,
        custodian_id: str,
        image_bytes: bytes,
        transaction_date: str,
    ) -> Optional[DuplicateFlag]:
        """Compute fingerprint, check for near-duplicates, and register in store.

        Returns:
            DuplicateFlag if a duplicate is detected, else None.
        """
        new_fp = compute_dhash(image_bytes)
        tenant_entries = self._store.setdefault(tenant_id, [])

        # Check against all existing receipts for this tenant
        for existing in tenant_entries:
            dist = hamming_distance(new_fp, existing.fingerprint)
            if dist <= self.duplicate_threshold:
                # Register the new one anyway so future checks also catch it
                tenant_entries.append(
                    FingerprintEntry(
                        receipt_id=receipt_id,
                        tenant_id=tenant_id,
                        custodian_id=custodian_id,
                        fingerprint=new_fp,
                        transaction_date=transaction_date,
                    )
                )
                return DuplicateFlag(
                    new_receipt_id=receipt_id,
                    existing_receipt_id=existing.receipt_id,
                    hamming_distance=dist,
                )

        tenant_entries.append(
            FingerprintEntry(
                receipt_id=receipt_id,
                tenant_id=tenant_id,
                custodian_id=custodian_id,
                fingerprint=new_fp,
                transaction_date=transaction_date,
            )
        )
        return None

    def count(self, tenant_id: str) -> int:
        """Return number of registered fingerprints for a tenant."""
        return len(self._store.get(tenant_id, []))

    def clear_tenant(self, tenant_id: str) -> None:
        """Remove all fingerprint entries for a given tenant."""
        self._store.pop(tenant_id, None)
