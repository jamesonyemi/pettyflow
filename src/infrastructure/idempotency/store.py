"""Tenant-scoped durable idempotency storage."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Optional


class IdempotencyConflictError(ValueError):
    """Raised when a key is reused with a different business request."""


@dataclass(frozen=True)
class StoredIdempotencyResult:
    tenant_id: str
    key: str
    request_fingerprint: str
    result_json: str


class SQLiteIdempotencyStore:
    """SQLite-backed store with atomic insert-or-read semantics.

    SQLite is used as the local durable implementation until the service's
    production database repository is wired in. The schema and uniqueness
    contract are intentionally portable to the planned relational backend.
    """

    def __init__(self, database_path: Optional[str] = None) -> None:
        configured_path = database_path or os.getenv(
            "PETTYFLOW_IDEMPOTENCY_DB", "data/idempotency.sqlite3"
        )
        self.database_path = configured_path
        if configured_path != ":memory:":
            Path(configured_path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            configured_path, check_same_thread=False, isolation_level=None
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS idempotency_records (
                tenant_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_fingerprint TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (tenant_id, idempotency_key)
            )
            """
        )
        self._lock = RLock()

    @staticmethod
    def fingerprint(request_payload: dict) -> str:
        canonical = json.dumps(request_payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def get_or_reserve(
        self,
        tenant_id: str,
        key: str,
        request_fingerprint: str,
        result_json: Optional[str] = None,
    ) -> Optional[StoredIdempotencyResult]:
        """Return an existing record or reserve a new key atomically."""
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    """
                    SELECT tenant_id, idempotency_key, request_fingerprint, result_json
                    FROM idempotency_records
                    WHERE tenant_id = ? AND idempotency_key = ?
                    """,
                    (tenant_id, key),
                ).fetchone()
                if row is not None:
                    if row[2] != request_fingerprint:
                        raise IdempotencyConflictError(
                            "idempotency key was already used for a different request"
                        )
                    self._connection.execute("COMMIT")
                    return StoredIdempotencyResult(*row)
                if result_json is None:
                    self._connection.execute("ROLLBACK")
                    return None
                self._connection.execute(
                    """
                    INSERT INTO idempotency_records
                        (tenant_id, idempotency_key, request_fingerprint, result_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    (tenant_id, key, request_fingerprint, result_json),
                )
                self._connection.execute("COMMIT")
                return None
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def save(
        self, tenant_id: str, key: str, request_fingerprint: str, result_json: str
    ) -> StoredIdempotencyResult:
        existing = self.get_or_reserve(tenant_id, key, request_fingerprint, result_json)
        if existing is not None:
            return existing
        return StoredIdempotencyResult(tenant_id, key, request_fingerprint, result_json)

    def close(self) -> None:
        self._connection.close()
