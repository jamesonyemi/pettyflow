"""
PettyFlow Cryptographic Ledger Hash Chain Engine
Provides HMAC-SHA256 immutable block chaining and tamper detection.
High-performance implementation targeted for sub-millisecond execution.
"""

import hmac
import hashlib
import datetime
from typing import List
from dataclasses import dataclass
from src.domain.ledger.entry import TransactionBatch, EntryType

class ChainTamperedException(Exception):
    """Raised when ledger cryptographic integrity is compromised."""
    pass

GENESIS_PREVIOUS_HASH = b"0" * 32  # 32 zero bytes for Genesis Block

def compute_canonical_bytes(
    seq_num: int,
    tenant_id: str,
    tx_id: str,
    description: str,
    previous_hash: bytes,
    timestamp: str,
    legs: List
) -> bytes:
    """
    Compute canonical byte payload for HMAC signing without redundant dict/object allocations.
    """
    legs_parts = []
    for leg in legs:
        if isinstance(leg, dict):
            acct = leg['acct'].encode('utf-8') if isinstance(leg['acct'], str) else leg['acct']
            type_str = leg['type'].encode('utf-8') if isinstance(leg['type'], str) else leg['type']
            amt = leg['amt']
        else:
            acct = leg.account_id.encode('utf-8') if isinstance(leg.account_id, str) else leg.account_id
            type_str = leg.entry_type.value.encode('utf-8') if isinstance(leg.entry_type.value, str) else leg.entry_type.value
            amt = leg.amount_scaled
        legs_parts.append(b"%b:%b:%d" % (acct, type_str, amt))

    legs_bytes = b";".join(legs_parts)

    tenant_b = tenant_id.encode('utf-8') if isinstance(tenant_id, str) else tenant_id
    tx_b = tx_id.encode('utf-8') if isinstance(tx_id, str) else tx_id
    desc_b = description.encode('utf-8') if isinstance(description, str) else description
    ts_b = timestamp.encode('utf-8') if isinstance(timestamp, str) else timestamp

    return b"%d|%b|%b|%b|%b|%b|%b" % (
        seq_num,
        tenant_b,
        tx_b,
        desc_b,
        previous_hash,
        ts_b,
        legs_bytes
    )

@dataclass
class LedgerBlock:
    sequence_number: int
    tenant_id: str
    transaction_id: str
    description: str
    legs_payload: List[dict]
    previous_hash: bytes
    current_hash: bytes
    timestamp: str

    def to_canonical_bytes(self) -> bytes:
        return compute_canonical_bytes(
            self.sequence_number,
            self.tenant_id,
            self.transaction_id,
            self.description,
            self.previous_hash,
            self.timestamp,
            self.legs_payload
        )

class CryptographicLedgerChain:
    """
    Manages an append-only sequence of LedgerBlocks signed with a secret HMAC key per tenant.
    """
    def __init__(self, tenant_id: str, secret_key: bytes):
        self.tenant_id = tenant_id
        self.secret_key = secret_key
        self.blocks: List[LedgerBlock] = []

    def compute_hmac(self, canonical_bytes: bytes) -> bytes:
        """Compute HMAC-SHA256 signature over payload bytes using tenant key."""
        return hmac.new(self.secret_key, canonical_bytes, hashlib.sha256).digest()

    def append_transaction(self, tx: TransactionBatch) -> LedgerBlock:
        """
        Validates transaction balance and appends a cryptographically signed block to the chain.
        """
        tx.validate_balance()

        previous_hash = self.blocks[-1].current_hash if self.blocks else GENESIS_PREVIOUS_HASH
        seq_num = len(self.blocks) + 1
        ts_str = tx.timestamp.isoformat()

        canonical_payload = compute_canonical_bytes(
            seq_num,
            tx.tenant_id,
            tx.transaction_id,
            tx.description,
            previous_hash,
            ts_str,
            tx.legs
        )

        current_hash = self.compute_hmac(canonical_payload)

        legs_serialized = [
            {
                "acct": leg.account_id,
                "type": leg.entry_type.value,
                "amt": leg.amount_scaled
            }
            for leg in tx.legs
        ]

        block = LedgerBlock(
            sequence_number=seq_num,
            tenant_id=tx.tenant_id,
            transaction_id=tx.transaction_id,
            description=tx.description,
            legs_payload=legs_serialized,
            previous_hash=previous_hash,
            current_hash=current_hash,
            timestamp=ts_str
        )

        self.blocks.append(block)
        return block

    def verify_integrity(self) -> bool:
        """
        Full cryptographic validation of the ledger chain.
        Returns True if valid; raises ChainTamperedException if corrupted.
        """
        expected_prev_hash = GENESIS_PREVIOUS_HASH

        for block in self.blocks:
            if block.previous_hash != expected_prev_hash:
                raise ChainTamperedException(
                    f"Chain break at block {block.sequence_number}! "
                    f"Expected previous hash {expected_prev_hash.hex()[:8]}, got {block.previous_hash.hex()[:8]}"
                )

            canonical_payload = block.to_canonical_bytes()
            computed_hash = self.compute_hmac(canonical_payload)

            if not hmac.compare_digest(computed_hash, block.current_hash):
                raise ChainTamperedException(
                    f"Tamper detected at block {block.sequence_number} (tx {block.transaction_id})! "
                    f"Computed hash {computed_hash.hex()[:8]} does not match block hash {block.current_hash.hex()[:8]}"
                )

            expected_prev_hash = block.current_hash

        return True
