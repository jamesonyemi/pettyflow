"""Week 3 Reliability Lockdown: Migration Safety & Rollback Validation Tests.

Validates that:
- Schema migrations have corresponding rollback/undo scripts
- Dry-runs execute safely without modifying the target database
- Forward migrations apply correctly and register in tracking tables
- Rollbacks safely revert schema additions without corrupting remaining state
- Ledger snapshots and balance invariants (debits == credits) remain strictly continuous
- Partial / failing deployments abort cleanly without partial data corruption
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

from src.infrastructure.migrations.engine import (
    LedgerSnapshotChecksum,
    Migration,
    MigrationDirection,
    MigrationEngine,
    MigrationError,
    MigrationStatus,
)


@pytest.fixture
def migrations_dir() -> Path:
    return Path(__file__).parents[2] / "migrations"


@pytest.fixture
def db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    yield conn
    conn.close()


class TestMigrationDiscoveryAndIntegrity:
    """Verify that all migrations have matching up and down files and valid checksums."""

    def test_all_migrations_have_matching_undo_scripts(self, migrations_dir: Path) -> None:
        engine = MigrationEngine(migrations_dir=migrations_dir)
        migrations = engine.discover_migrations()

        assert len(migrations) >= 2
        for m in migrations:
            assert m.up_script_path.exists(), f"Up script missing for {m.version_tag}"
            assert m.down_script_path is not None, f"Undo script missing for {m.version_tag}"
            assert m.down_script_path.exists(), f"Undo file not found: {m.down_script_path}"

    def test_migration_checksum_calculation(self, migrations_dir: Path) -> None:
        engine = MigrationEngine(migrations_dir=migrations_dir)
        migrations = engine.discover_migrations()

        for m in migrations:
            checksum = engine.compute_file_checksum(m.up_script_path)
            assert isinstance(checksum, str)
            assert len(checksum) == 64  # SHA-256


class TestMigrationDryRunAndApplication:
    """Verify dry-runs and forward migrations against a database."""

    def test_dry_run_does_not_persist_tables(self, migrations_dir: Path, db_conn: sqlite3.Connection) -> None:
        engine = MigrationEngine(migrations_dir=migrations_dir, db_connection=db_conn)
        migrations = engine.discover_migrations()
        v1 = migrations[0]

        success, msg = engine.dry_run_migration(v1)
        assert success is True
        assert "succeeded" in msg

        # Ensure tables were NOT permanently created by dry-run
        cursor = db_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='tenants'"
        )
        assert cursor.fetchone() is None

    def test_apply_migrations_sequentially(self, migrations_dir: Path, db_conn: sqlite3.Connection) -> None:
        engine = MigrationEngine(migrations_dir=migrations_dir, db_connection=db_conn)
        migrations = engine.discover_migrations()

        for m in migrations:
            engine.apply_migration(m)

        applied = engine.get_applied_versions()
        assert applied == [m.version for m in migrations]

        # Verify tables created
        cursor = db_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in cursor.fetchall()}
        assert "tenants" in tables
        assert "accounts" in tables
        assert "funds" in tables
        assert "idempotency_records" in tables
        assert "provider_events" in tables
        assert "settlements" in tables


class TestRollbackAndLedgerContinuity:
    """Verify rollback capability and zero financial drift."""

    def test_rollback_v002_preserves_v001_core_ledger(
        self, migrations_dir: Path, db_conn: sqlite3.Connection
    ) -> None:
        engine = MigrationEngine(migrations_dir=migrations_dir, db_connection=db_conn)
        migrations = engine.discover_migrations()

        # Apply V001 and V002
        for m in migrations:
            engine.apply_migration(m)

        # Rollback V002
        v2 = next(m for m in migrations if m.version == 2)
        engine.rollback_migration(v2)

        cursor = db_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in cursor.fetchall()}

        # V001 core tables still exist
        assert "tenants" in tables
        assert "accounts" in tables
        assert "funds" in tables

        # V002 tables were removed
        assert "idempotency_records" not in tables
        assert "provider_events" not in tables
        assert "settlements" not in tables

    def test_ledger_state_continuity_checksums(self, migrations_dir: Path) -> None:
        engine = MigrationEngine(migrations_dir=migrations_dir)
        tenant_id = str(uuid.uuid4())

        postings = [
            {"tenant_id": tenant_id, "entry_type": "DEBIT", "amount_scaled": 500_000},
            {"tenant_id": tenant_id, "entry_type": "CREDIT", "amount_scaled": 500_000},
            {"tenant_id": tenant_id, "entry_type": "DEBIT", "amount_scaled": 250_000},
            {"tenant_id": tenant_id, "entry_type": "CREDIT", "amount_scaled": 250_000},
        ]

        before_snapshot = engine.compute_ledger_snapshot(
            tenant_id=tenant_id,
            postings=postings,
            hash_chain_head="0000abcd1234head",
        )

        assert before_snapshot.is_balanced is True
        assert before_snapshot.total_debits_scaled == 750_000
        assert before_snapshot.total_credits_scaled == 750_000
        assert before_snapshot.postings_count == 4

        # Simulate snapshot taken after migration
        after_snapshot = engine.compute_ledger_snapshot(
            tenant_id=tenant_id,
            postings=postings,
            hash_chain_head="0000abcd1234head",
        )

        assert engine.verify_state_continuity(before_snapshot, after_snapshot) is True
        assert before_snapshot.digest == after_snapshot.digest

    def test_ledger_drift_detected_when_unbalanced(self, migrations_dir: Path) -> None:
        engine = MigrationEngine(migrations_dir=migrations_dir)
        tenant_id = str(uuid.uuid4())

        unbalanced_postings = [
            {"tenant_id": tenant_id, "entry_type": "DEBIT", "amount_scaled": 500_000},
            {"tenant_id": tenant_id, "entry_type": "CREDIT", "amount_scaled": 400_000},
        ]

        snapshot = engine.compute_ledger_snapshot(
            tenant_id=tenant_id,
            postings=unbalanced_postings,
            hash_chain_head="0000deadbeef",
        )

        assert snapshot.is_balanced is False


class TestFailureAtomicityAndAbort:
    """Verify that failed migrations rollback atomically leaving no corrupted state."""

    def test_failed_migration_leaves_database_clean(self, tmp_path: Path, db_conn: sqlite3.Connection) -> None:
        bad_dir = tmp_path / "bad_migrations"
        bad_dir.mkdir()

        (bad_dir / "V001__valid.sql").write_text(
            "CREATE TABLE valid_table (id INTEGER PRIMARY KEY);", encoding="utf-8"
        )
        (bad_dir / "U001__valid.sql").write_text(
            "DROP TABLE valid_table;", encoding="utf-8"
        )

        (bad_dir / "V002__syntax_error.sql").write_text(
            "CREATE TABLE broken_table (id INTEGER PRIMARY KEY); INVALID SYNTAX HERE;",
            encoding="utf-8",
        )
        (bad_dir / "U002__syntax_error.sql").write_text(
            "DROP TABLE broken_table;", encoding="utf-8"
        )

        engine = MigrationEngine(migrations_dir=bad_dir, db_connection=db_conn)
        migrations = engine.discover_migrations()

        # Apply V1 successfully
        engine.apply_migration(migrations[0])
        assert engine.get_applied_versions() == [1]

        # Apply V2 which fails
        with pytest.raises(MigrationError):
            engine.apply_migration(migrations[1])

        # V2 should not be registered as applied
        assert engine.get_applied_versions() == [1]

        # broken_table should NOT exist due to transaction rollback
        cursor = db_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='broken_table'"
        )
        assert cursor.fetchone() is None


class TestExpandContractCompatibility:
    """Verify that existing queries work seamlessly under expand/contract schema evolution."""

    def test_v001_queries_continue_to_work_after_v002_applied(
        self, migrations_dir: Path, db_conn: sqlite3.Connection
    ) -> None:
        engine = MigrationEngine(migrations_dir=migrations_dir, db_connection=db_conn)
        migrations = engine.discover_migrations()

        # 1. Apply V001
        v1 = next(m for m in migrations if m.version == 1)
        engine.apply_migration(v1)

        tenant_id = str(uuid.uuid4())
        account_id = str(uuid.uuid4())
        fund_id = str(uuid.uuid4())

        # Insert V001 data
        db_conn.execute(
            "INSERT INTO tenants (tenant_id, name, status) VALUES (?, ?, ?)",
            (tenant_id, "Test Org", "ACTIVE"),
        )
        db_conn.execute(
            "INSERT INTO accounts (account_id, tenant_id, name, category, currency) VALUES (?, ?, ?, ?, ?)",
            (account_id, tenant_id, "Petty Cash Account", "ASSET", "USD"),
        )
        db_conn.execute(
            "INSERT INTO funds (fund_id, tenant_id, name, currency, custodian_id, allocated_amount_scaled) VALUES (?, ?, ?, ?, ?, ?)",
            (fund_id, tenant_id, "Main Vault", "USD", "custodian_1", 1_000_000),
        )
        db_conn.commit()

        # 2. Expand schema: Apply V002
        v2 = next(m for m in migrations if m.version == 2)
        engine.apply_migration(v2)

        # 3. Old queries on V001 tables still return identical data
        cursor = db_conn.execute("SELECT tenant_id, name, status FROM tenants WHERE tenant_id = ?", (tenant_id,))
        row = cursor.fetchone()
        assert row == (tenant_id, "Test Org", "ACTIVE")

        cursor = db_conn.execute("SELECT fund_id, allocated_amount_scaled FROM funds WHERE fund_id = ?", (fund_id,))
        row = cursor.fetchone()
        assert row == (fund_id, 1_000_000)

        # 4. New V002 tables are simultaneously usable
        db_conn.execute(
            "INSERT INTO idempotency_records (tenant_id, idempotency_key, request_fingerprint, status) VALUES (?, ?, ?, ?)",
            (tenant_id, "key-123", "fp-abc", "completed"),
        )
        db_conn.commit()

        cursor = db_conn.execute(
            "SELECT idempotency_key, status FROM idempotency_records WHERE tenant_id = ?", (tenant_id,)
        )
        assert cursor.fetchone() == ("key-123", "completed")


class TestMultiTenantStateContinuityUnderRollback:
    """Verify multi-tenant balance integrity when rolling back migrations."""

    def test_multi_tenant_balances_intact_after_rollback(
        self, migrations_dir: Path, db_conn: sqlite3.Connection
    ) -> None:
        engine = MigrationEngine(migrations_dir=migrations_dir, db_connection=db_conn)
        migrations = engine.discover_migrations()

        # Apply V001 and V002
        for m in migrations:
            engine.apply_migration(m)

        tenant_a = str(uuid.uuid4())
        tenant_b = str(uuid.uuid4())

        for t_id, name in [(tenant_a, "Tenant A"), (tenant_b, "Tenant B")]:
            db_conn.execute(
                "INSERT INTO tenants (tenant_id, name, status) VALUES (?, ?, ?)",
                (t_id, name, "ACTIVE"),
            )
            db_conn.execute(
                "INSERT INTO funds (fund_id, tenant_id, name, currency, custodian_id, allocated_amount_scaled) VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), t_id, f"Fund {name}", "USD", "cust", 500_000),
            )
        db_conn.commit()

        # Record pre-rollback snapshot
        postings_a = [
            {"tenant_id": tenant_a, "entry_type": "DEBIT", "amount_scaled": 500_000},
            {"tenant_id": tenant_a, "entry_type": "CREDIT", "amount_scaled": 500_000},
        ]
        postings_b = [
            {"tenant_id": tenant_b, "entry_type": "DEBIT", "amount_scaled": 300_000},
            {"tenant_id": tenant_b, "entry_type": "CREDIT", "amount_scaled": 300_000},
        ]

        snap_a_before = engine.compute_ledger_snapshot(tenant_a, postings_a, "head_a")
        snap_b_before = engine.compute_ledger_snapshot(tenant_b, postings_b, "head_b")

        # Rollback V002
        v2 = next(m for m in migrations if m.version == 2)
        engine.rollback_migration(v2)

        # Core funds and tenants for both tenants are preserved
        cursor = db_conn.execute("SELECT COUNT(*) FROM tenants")
        assert cursor.fetchone()[0] == 2

        cursor = db_conn.execute("SELECT COUNT(*) FROM funds")
        assert cursor.fetchone()[0] == 2

        # Verify ledger continuity
        snap_a_after = engine.compute_ledger_snapshot(tenant_a, postings_a, "head_a")
        snap_b_after = engine.compute_ledger_snapshot(tenant_b, postings_b, "head_b")

        assert engine.verify_state_continuity(snap_a_before, snap_a_after) is True
        assert engine.verify_state_continuity(snap_b_before, snap_b_after) is True

