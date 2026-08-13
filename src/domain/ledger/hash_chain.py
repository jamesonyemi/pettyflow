"""
PettyFlow Cryptographic Ledger Hash Chain Engine
Provides HMAC-SHA256 immutable block chaining and tamper detection.
High-performance implementation targeted for sub-millisecond execution.
"""

import hmac
from typing import List
from dataclasses import dataclass
from src.domain.ledger.entry import TransactionBatch, EntryType

class ChainTamperedException(Exception):
    """Raised when ledger cryptographic integrity is compromised."""
    pass

GENESIS_PREVIOUS_HASH = b"0" * 32  # 32 zero bytes for Genesis Block
DEBIT_BYTES = b"DEBIT"
CREDIT_BYTES = b"CREDIT"

_ENCODE_CACHE = {}

def _get_bytes(s) -> bytes:
    if type(s) is bytes:
        return s
    b = _ENCODE_CACHE.get(s)
    if b is None:
        b = s.encode('utf-8')
        _ENCODE_CACHE[s] = b
    return b

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
    Compute canonical byte payload for HMAC signing with zero-alloc C formatting.
    """
    tenant_b = tenant_id.encode('utf-8') if type(tenant_id) is str else tenant_id
    tx_b = tx_id.encode('utf-8') if type(tx_id) is str else tx_id
    desc_b = description.encode('utf-8') if type(description) is str else description
    ts_b = timestamp.encode('utf-8') if type(timestamp) is str else timestamp

    legs_parts = []
    for leg in legs:
        if type(leg) is dict:
            acct_raw = leg['acct']
            type_raw = leg['type']
            amt = leg['amt']
        else:
            acct_raw = leg.account_id
            type_raw = leg.entry_type._value_ if type(leg.entry_type) is EntryType else leg.entry_type
            amt = leg.amount_scaled

        acct = acct_raw.encode('utf-8') if type(acct_raw) is str else acct_raw
        if type_raw == "DEBIT" or type_raw is EntryType.DEBIT:
            type_b = DEBIT_BYTES
        elif type_raw == "CREDIT" or type_raw is EntryType.CREDIT:
            type_b = CREDIT_BYTES
        elif type(type_raw) is bytes:
            type_b = type_raw
        else:
            type_b = type_raw.encode('utf-8')

        legs_parts.append(b"%b:%b:%d" % (acct, type_b, amt))

    legs_bytes = b";".join(legs_parts)

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
        """Compute HMAC-SHA256 signature using optimized C implementation."""
        return hmac.digest(self.secret_key, canonical_bytes, "sha256")

    def append_transaction(self, tx: TransactionBatch) -> LedgerBlock:
        """
        Validates transaction balance and appends a cryptographically signed block to the chain.
        """
        tx.validate_balance()

        previous_hash = self.blocks[-1].current_hash if self.blocks else GENESIS_PREVIOUS_HASH
        seq_num = len(self.blocks) + 1
        ts_val = tx.timestamp
        ts_str = ts_val if type(ts_val) is str else ts_val.isoformat()

        tenant_id = tx.tenant_id
        tx_id = tx.transaction_id
        desc = tx.description

        tenant_b = _get_bytes(tenant_id)
        tx_b = _get_bytes(tx_id)
        desc_b = _get_bytes(desc)
        ts_b = _get_bytes(ts_str)

        legs_parts = []
        legs_serialized = []
        for leg in tx.legs:
            acct_raw = leg.account_id
            type_raw = leg.entry_type._value_ if type(leg.entry_type) is EntryType else leg.entry_type
            amt = leg.amount_scaled

            acct_b = _get_bytes(acct_raw)
            if type_raw == "DEBIT" or type_raw is EntryType.DEBIT:
                type_b = DEBIT_BYTES
            elif type_raw == "CREDIT" or type_raw is EntryType.CREDIT:
                type_b = CREDIT_BYTES
            else:
                type_b = _get_bytes(type_raw)

            legs_parts.append(b"%b:%b:%d" % (acct_b, type_b, amt))
            legs_serialized.append({"acct": acct_raw, "type": type_raw, "amt": amt})

        legs_bytes = b";".join(legs_parts)

        canonical_payload = b"%d|%b|%b|%b|%b|%b|%b" % (
            seq_num,
            tenant_b,
            tx_b,
            desc_b,
            previous_hash,
            ts_b,
            legs_bytes
        )

        current_hash = hmac.digest(self.secret_key, canonical_payload, "sha256")

        block = LedgerBlock(
            sequence_number=seq_num,
            tenant_id=tenant_id,
            transaction_id=tx_id,
            description=desc,
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
