# PettyFlow Database Migration & Rollback Runbook

## 1. Overview & Objectives

This runbook establishes operational procedures for executing schema migrations and rollback operations on PettyFlow database instances (staging and production), guaranteeing:
- **Zero Financial Drift**: All ledger debits and credits maintain mathematical parity ($ \sum \text{Debit} = \sum \text{Credit} $) before, during, and after migrations.
- **Atomic Operations**: Migrations execute within savepoints/transactions; failed statements abort cleanly without partial schema mutation.
- **Reversibility**: Every forward migration (`V<N>__<name>.sql`) must have a tested undo/rollback script (`U<N>__<name>.sql`).
- **Expand/Contract Compatibility**: New columns and tables are added in an expanding phase before legacy readers transition.

---

## 2. Migration Architecture & Artifacts

| Migration Version | Type | Script Path | Description |
|---|---|---|---|
| `V001` | Baseline | `migrations/V001__init_pettyflow_schema.sql` | Core tenants, accounts, funds, partitioned postings, ledger blocks, and audit trail. |
| `U001` | Rollback | `migrations/U001__init_pettyflow_schema.sql` | Reverts V001 baseline (disposable staging only). |
| `V002` | Expansion | `migrations/V002__add_idempotency_and_settlement.sql` | Adds idempotency records, webhook provider events, and settlements. |
| `U002` | Rollback | `migrations/U002__add_idempotency_and_settlement.sql` | Drops settlements, provider events, and idempotency records cleanly. |

---

## 3. Pre-Migration Checklist & Verification

Before initiating any migration:

1. **Pre-Migration Snapshot**: Compute the cryptographic ledger snapshot checksum across all active tenant accounts:
   ```python
   from src.infrastructure.migrations.engine import MigrationEngine
   engine = MigrationEngine("migrations")
   before_snapshot = engine.compute_ledger_snapshot(tenant_id, postings, hash_head)
   assert before_snapshot.is_balanced
   ```
2. **Backup Verification**: Ensure full point-in-time recovery (PITR) snapshot or physical database backup is completed and verified.
3. **Dry-Run Validation**: Execute migration dry-run in staging:
   ```python
   success, msg = engine.dry_run_migration(migration)
   assert success
   ```

---

## 4. Execution Procedures

### 4.1 Forward Migration (Apply)
```bash
# Automated via MigrationEngine or CI/CD deployment pipeline
python -m pytest tests/unit/test_migration_and_rollback.py
```

### 4.2 Rollback Procedure (Undo)
If an deployment anomaly, provider outage, or verification failure is detected:
1. Stop application traffic to affected workers/services.
2. Trigger automated rollback through `MigrationEngine.rollback_migration(migration)` or execute matching `U<N>__<name>.sql` script.
3. Verify ledger continuity by comparing pre-migration snapshot digest with post-rollback snapshot digest:
   ```python
   after_snapshot = engine.compute_ledger_snapshot(tenant_id, postings, hash_head)
   assert engine.verify_state_continuity(before_snapshot, after_snapshot)
   ```
4. Verify tracking status in `schema_migrations`:
   ```sql
   SELECT version, name, status, applied_at FROM schema_migrations;
   ```

---

## 5. Recovery Objectives & SLA Thresholds

- **RTO (Recovery Time Objective)**: $\le 5\text{ minutes}$ for migration rollback.
- **RPO (Recovery Point Objective)**: $0\text{ seconds}$ (zero ledger data loss).
- **Ledger Invariant Check**: $ \text{Total Debits} - \text{Total Credits} = 0 $.
