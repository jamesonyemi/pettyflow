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


class IdempotencyInProgressError(RuntimeError):
    """Raised when another worker currently owns an idempotency key."""


class ProviderEventConflictError(ValueError):
    """Raised when an event ID is reused with a different payload."""


@dataclass(frozen=True)
class StoredIdempotencyResult:
    tenant_id: str
    key: str
    request_fingerprint: str
    result_json: str
    status: str


class SQLiteIdempotencyStore:
    """SQLite-backed store with atomic insert-or-read semantics.

    SQLite is used as the local durable implementation until the service's
    production database repository is wired in. The schema and uniqueness
    contract are intentionally portable to the planned relational backend.
    """

    def __init__(
        self, database_path: Optional[str] = None, retention_seconds: int = 7 * 86_400
    ) -> None:
        if retention_seconds <= 0:
            raise ValueError("retention_seconds must be positive")
        configured_path = database_path or os.getenv(
            "PETTYFLOW_IDEMPOTENCY_DB", "data/idempotency.sqlite3"
        )
        self.database_path = configured_path
        self.retention_seconds = retention_seconds
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
                status TEXT NOT NULL DEFAULT 'in_progress',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (tenant_id, idempotency_key)
            )
            """
        )
        columns = {
            row[1]
            for row in self._connection.execute(
                "PRAGMA table_info(idempotency_records)"
            ).fetchall()
        }
        if "status" not in columns:
            self._connection.execute(
                "ALTER TABLE idempotency_records ADD COLUMN status TEXT NOT NULL DEFAULT 'completed'"
            )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS provider_events (
                tenant_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                event_id TEXT NOT NULL,
                payload_fingerprint TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (tenant_id, provider, event_id)
            )
            """
        )
        self._lock = RLock()

    @staticmethod
    def fingerprint(request_payload: dict) -> str:
        canonical = json.dumps(request_payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def reserve(
        self,
        tenant_id: str,
        key: str,
        request_fingerprint: str,
    ) -> Optional[StoredIdempotencyResult]:
        """Reserve a key, or return the existing terminal/in-flight record."""
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(
                    """
                    DELETE FROM idempotency_records
                    WHERE created_at < datetime('now', ?)
                    """,
                    (f"-{self.retention_seconds} seconds",),
                )
                self._connection.execute(
                    """
                    DELETE FROM provider_events
                    WHERE created_at < datetime('now', ?)
                    """,
                    (f"-{self.retention_seconds} seconds",),
                )
                row = self._connection.execute(
                    """
                    SELECT tenant_id, idempotency_key, request_fingerprint, result_json, status
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
                self._connection.execute(
                    """
                    INSERT INTO idempotency_records
                        (tenant_id, idempotency_key, request_fingerprint, result_json, status)
                    VALUES (?, ?, ?, '', 'in_progress')
                    """,
                    (tenant_id, key, request_fingerprint),
                )
                self._connection.execute("COMMIT")
                return None
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def complete(
        self, tenant_id: str, key: str, request_fingerprint: str, result_json: str
    ) -> StoredIdempotencyResult:
        with self._lock:
            cursor = self._connection.execute(
                """
                UPDATE idempotency_records
                SET result_json = ?, status = 'completed'
                WHERE tenant_id = ? AND idempotency_key = ?
                  AND request_fingerprint = ? AND status = 'in_progress'
                """,
                (result_json, tenant_id, key, request_fingerprint),
            )
            if cursor.rowcount != 1:
                raise IdempotencyConflictError("idempotency record cannot be completed")
            return StoredIdempotencyResult(
                tenant_id, key, request_fingerprint, result_json, "completed"
            )

    def fail(
        self, tenant_id: str, key: str, request_fingerprint: str, result_json: str
    ) -> StoredIdempotencyResult:
        with self._lock:
            cursor = self._connection.execute(
                """
                UPDATE idempotency_records
                SET result_json = ?, status = 'failed'
                WHERE tenant_id = ? AND idempotency_key = ?
                  AND request_fingerprint = ? AND status = 'in_progress'
                """,
                (result_json, tenant_id, key, request_fingerprint),
            )
            if cursor.rowcount != 1:
                raise IdempotencyConflictError("idempotency record cannot be failed")
            return StoredIdempotencyResult(
                tenant_id, key, request_fingerprint, result_json, "failed"
            )

    def abandon(self, tenant_id: str, key: str, request_fingerprint: str) -> None:
        """Release an operation whose downstream call raised before a result existed."""
        with self._lock:
            self._connection.execute(
                """
                DELETE FROM idempotency_records
                WHERE tenant_id = ? AND idempotency_key = ?
                  AND request_fingerprint = ? AND status = 'in_progress'
                """,
                (tenant_id, key, request_fingerprint),
            )

    def claim_provider_event(
        self, tenant_id: str, provider: str, event_id: str, payload_fingerprint: str
    ) -> bool:
        """Claim an event ID; return False for an identical replay."""
        with self._lock:
            row = self._connection.execute(
                """
                SELECT payload_fingerprint FROM provider_events
                WHERE tenant_id = ? AND provider = ? AND event_id = ?
                """,
                (tenant_id, provider, event_id),
            ).fetchone()
            if row is not None:
                if row[0] != payload_fingerprint:
                    raise ProviderEventConflictError(
                        "provider event ID was already used for a different payload"
                    )
                return False
            self._connection.execute(
                """
                INSERT INTO provider_events
                    (tenant_id, provider, event_id, payload_fingerprint)
                VALUES (?, ?, ?, ?)
                """,
                (tenant_id, provider, event_id, payload_fingerprint),
            )
            return True

    def get_or_reserve(
        self,
        tenant_id: str,
        key: str,
        request_fingerprint: str,
        result_json: Optional[str] = None,
    ) -> Optional[StoredIdempotencyResult]:
        """Backward-compatible alias for callers migrating to ``reserve``."""
        if result_json is not None:
            raise ValueError("result_json is no longer accepted when reserving a key")
        return self.reserve(tenant_id, key, request_fingerprint)

    def close(self) -> None:
        self._connection.close()
