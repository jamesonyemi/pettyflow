"""Database migration runner, dry-run simulator, and ledger rollback validation engine."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class MigrationDirection(str, Enum):
    UP = "UP"
    DOWN = "DOWN"


class MigrationStatus(str, Enum):
    PENDING = "PENDING"
    APPLIED = "APPLIED"
    ROLLED_BACK = "ROLLED_BACK"
    FAILED = "FAILED"


class MigrationError(Exception):
    """Raised when a schema migration or rollback operation fails."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    up_script_path: Path
    down_script_path: Optional[Path] = None

    @property
    def version_tag(self) -> str:
        return f"V{self.version:03d}"


@dataclass(frozen=True)
class LedgerSnapshotChecksum:
    tenant_id: str
    total_debits_scaled: int
    total_credits_scaled: int
    postings_count: int
    hash_chain_head: str
    timestamp_utc: str

    @property
    def is_balanced(self) -> bool:
        return self.total_debits_scaled == self.total_credits_scaled

    @property
    def digest(self) -> str:
        data = f"{self.tenant_id}:{self.total_debits_scaled}:{self.total_credits_scaled}:{self.postings_count}:{self.hash_chain_head}"
        return hashlib.sha256(data.encode("utf-8")).hexdigest()


class MigrationEngine:
    """Enterprise schema migration engine with dry-run verification, atomic

    rollback capability, and cryptographic ledger snapshot comparison.
    """

    def __init__(self, migrations_dir: str | Path, db_connection: Optional[sqlite3.Connection] = None):
        self.migrations_dir = Path(migrations_dir)
        self._conn = db_connection or sqlite3.connect(":memory:")
        self._init_schema_migrations_table()

    def _init_schema_migrations_table(self) -> None:
        """Create schema_migrations tracking table if not exists."""
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL,
                checksum TEXT NOT NULL,
                status TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def discover_migrations(self) -> List[Migration]:
        """Scan migrations directory and return sorted list of Migration definitions."""
        if not self.migrations_dir.exists():
            return []

        up_files = {}
        down_files = {}

        for file in self.migrations_dir.glob("*.sql"):
            match_up = re.match(r"^V(\d+)__(.+)\.sql$", file.name)
            if match_up:
                v = int(match_up.group(1))
                name = match_up.group(2)
                up_files[v] = (name, file)
                continue

            match_down = re.match(r"^U(\d+)__(.+)\.sql$", file.name)
            if match_down:
                v = int(match_down.group(1))
                name = match_down.group(2)
                down_files[v] = file

        migrations = []
        for v in sorted(up_files.keys()):
            name, up_path = up_files[v]
            down_path = down_files.get(v)
            migrations.append(
                Migration(
                    version=v,
                    name=name,
                    up_script_path=up_path,
                    down_script_path=down_path,
                )
            )
        return migrations

    def get_applied_versions(self) -> List[int]:
        """Return list of applied migration versions from tracking table."""
        cursor = self._conn.execute(
            "SELECT version FROM schema_migrations WHERE status = 'APPLIED' ORDER BY version ASC"
        )
        return [row[0] for row in cursor.fetchall()]

    def compute_file_checksum(self, path: Path) -> str:
        """Compute SHA256 checksum of migration SQL file."""
        content = path.read_bytes()
        return hashlib.sha256(content).hexdigest()

    def compute_ledger_snapshot(
        self,
        tenant_id: str,
        postings: List[Dict[str, Any]],
        hash_chain_head: str = "",
    ) -> LedgerSnapshotChecksum:
        """Calculate state checksum across all postings for a tenant."""
        total_debits = 0
        total_credits = 0
        count = 0

        for p in postings:
            if p.get("tenant_id") == tenant_id:
                count += 1
                entry_type = p.get("entry_type", "").upper()
                amt = int(p.get("amount_scaled", 0))
                if entry_type == "DEBIT":
                    total_debits += amt
                elif entry_type == "CREDIT":
                    total_credits += amt

        return LedgerSnapshotChecksum(
            tenant_id=tenant_id,
            total_debits_scaled=total_debits,
            total_credits_scaled=total_credits,
            postings_count=count,
            hash_chain_head=hash_chain_head,
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
        )

    def _execute_script_atomically(self, sql_script: str) -> None:
        """Execute multiple SQL statements sequentially within current transaction."""
        statements = []
        current = []
        for line in sql_script.splitlines():
            stripped = line.strip()
            if stripped.startswith("--") or not stripped:
                continue
            current.append(line)
            if stripped.endswith(";"):
                stmt = "\n".join(current).strip()
                if stmt:
                    statements.append(stmt)
                current = []
        if current:
            stmt = "\n".join(current).strip()
            if stmt:
                statements.append(stmt)

        for stmt in statements:
            # Strip trailing semicolon for execute
            clean_stmt = stmt.rstrip(";").strip()
            if clean_stmt:
                self._conn.execute(clean_stmt)

    def dry_run_migration(self, migration: Migration) -> Tuple[bool, str]:
        """Dry-run a migration script in a savepoint/transaction and rollback immediately."""
        script = migration.up_script_path.read_text(encoding="utf-8")
        clean_script = self._adapt_postgres_script_to_sqlite(script)

        try:
            self._conn.execute("SAVEPOINT dry_run_migration")
            self._execute_script_atomically(clean_script)
            self._conn.execute("ROLLBACK TO SAVEPOINT dry_run_migration")
            return True, f"Dry-run for {migration.version_tag} succeeded."
        except Exception as e:
            self._conn.execute("ROLLBACK TO SAVEPOINT dry_run_migration")
            return False, f"Dry-run for {migration.version_tag} failed: {str(e)}"

    def apply_migration(self, migration: Migration) -> None:
        """Apply a single forward migration atomically."""
        checksum = self.compute_file_checksum(migration.up_script_path)
        script = migration.up_script_path.read_text(encoding="utf-8")
        clean_script = self._adapt_postgres_script_to_sqlite(script)

        try:
            self._conn.execute("SAVEPOINT apply_tx")
            self._execute_script_atomically(clean_script)
            now = datetime.now(timezone.utc).isoformat()
            self._conn.execute(
                """
                INSERT INTO schema_migrations (version, name, applied_at, checksum, status)
                VALUES (?, ?, ?, ?, 'APPLIED')
                ON CONFLICT(version) DO UPDATE SET
                    status = 'APPLIED',
                    applied_at = excluded.applied_at,
                    checksum = excluded.checksum
                """,
                (migration.version, migration.name, now, checksum),
            )
            self._conn.execute("RELEASE SAVEPOINT apply_tx")
            self._conn.commit()
        except Exception as e:
            self._conn.execute("ROLLBACK TO SAVEPOINT apply_tx")
            raise MigrationError(f"Failed applying migration {migration.version_tag}: {e}") from e

    def rollback_migration(self, migration: Migration) -> None:
        """Rollback a single migration using its down/undo script."""
        if not migration.down_script_path or not migration.down_script_path.exists():
            raise MigrationError(f"Cannot rollback {migration.version_tag}: No undo script found.")

        script = migration.down_script_path.read_text(encoding="utf-8")
        clean_script = self._adapt_postgres_script_to_sqlite(script)

        try:
            self._conn.execute("SAVEPOINT rollback_tx")
            self._execute_script_atomically(clean_script)
            self._conn.execute(
                """
                UPDATE schema_migrations
                SET status = 'ROLLED_BACK'
                WHERE version = ?
                """,
                (migration.version,),
            )
            self._conn.execute("RELEASE SAVEPOINT rollback_tx")
            self._conn.commit()
        except Exception as e:
            self._conn.execute("ROLLBACK TO SAVEPOINT rollback_tx")
            raise MigrationError(f"Failed rolling back {migration.version_tag}: {e}") from e

    def verify_state_continuity(
        self,
        before_checksum: LedgerSnapshotChecksum,
        after_checksum: LedgerSnapshotChecksum,
    ) -> bool:
        """Verify that pre-migration and post-migration/rollback snapshots match."""
        return (
            before_checksum.tenant_id == after_checksum.tenant_id
            and before_checksum.total_debits_scaled == after_checksum.total_debits_scaled
            and before_checksum.total_credits_scaled == after_checksum.total_credits_scaled
            and before_checksum.postings_count == after_checksum.postings_count
            and before_checksum.hash_chain_head == after_checksum.hash_chain_head
            and before_checksum.is_balanced
            and after_checksum.is_balanced
        )

    @staticmethod
    def _adapt_postgres_script_to_sqlite(sql: str) -> str:
        """Filter PostgreSQL-only syntax (UUID extensions, PARTITION BY, DO blocks)

        to allow pure SQL tables and indexes to execute safely in SQLite test environments.
        """
        # Remove DO $$ ... $$ blocks
        sql = re.sub(r"DO\s+\$\$.*?\$\$;", "", sql, flags=re.DOTALL | re.IGNORECASE)
        # Remove CREATE EXTENSION
        sql = re.sub(r"CREATE\s+EXTENSION[^;]+;", "", sql, flags=re.IGNORECASE)
        # Remove PARTITION OF default tables
        sql = re.sub(r"CREATE\s+TABLE[^;]+PARTITION\s+OF[^;]+;", "", sql, flags=re.IGNORECASE)
        # Remove PARTITION BY clauses
        sql = re.sub(r"PARTITION\s+BY\s+[^\;]+(?=;)", "", sql, flags=re.IGNORECASE)
        # Remove postgres casts like '... '::jsonb or ::text
        sql = re.sub(r"::\w+", "", sql, flags=re.IGNORECASE)
        # Replace types
        sql = re.sub(r"\bUUID\b", "TEXT", sql, flags=re.IGNORECASE)
        sql = re.sub(r"\bTIMESTAMPTZ\b", "TEXT", sql, flags=re.IGNORECASE)
        sql = re.sub(r"\bJSONB\b", "TEXT", sql, flags=re.IGNORECASE)
        sql = re.sub(r"\bBYTEA\b", "BLOB", sql, flags=re.IGNORECASE)
        # Remove DEFAULT uuid_generate_v4()
        sql = re.sub(r"DEFAULT\s+uuid_generate_v4\(\)", "", sql, flags=re.IGNORECASE)
        # Remove CASCADE only when used in DROP TABLE statements
        sql = re.sub(r"(DROP\s+TABLE[^;]+)\bCASCADE\b", r"\1", sql, flags=re.IGNORECASE)
        return sql
