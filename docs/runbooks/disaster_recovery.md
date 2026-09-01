# PettyFlow Production Disaster Recovery Runbook

**Document Owner**: PettyFlow Site Reliability & Platform Engineering  
**Standard**: Jeff Dean High-Performance & Zero-Data-Loss Standards  
**Target RPO (Recovery Point Objective)**: 0 seconds (Zero Transaction Loss)  
**Target RTO (Recovery Time Objective)**: < 30 seconds

---

## 1. Primary Database Failover (PostgreSQL / TimescaleDB)

### Scenario
Primary database node becomes unresponsive or crashes due to hardware failure.

### Automatic Failover Mechanism (Patroni / PgBouncer)
1. **Health Check Detection**: Consul/etcd detects primary node heartbeat loss (> 3000 ms).
2. **Leader Election**: Standby replica with the highest LSN (Log Sequence Number) is automatically promoted to primary.
3. **Traffic Rerouting**: PgBouncer/Kubernetes Service endpoints update DNS/Virtual IP to the new primary.

### Manual Failover Procedure (if automated promotion stalls)
```bash
# 1. Inspect current cluster status
patronictl -c /etc/patroni/patroni.yml topology

# 2. Execute manual failover to replica-02
patronictl -c /etc/patroni/patroni.yml failover --candidate postgres-replica-02 --force

# 3. Verify write traffic routing and sequence alignment
kubectl logs -n pettyflow -l app.kubernetes.io/name=pettyflow --tail=100 | grep "DB_CONN_OK"
```

### Invariant Verification
Run the tamper verification script to ensure no partial transaction batches were committed:
```bash
python -m scripts.verify_ledger_integrity --tenant-id all
```

---

## 2. Redis Cluster Outage & Cache Desynchronization

### Scenario
Redis cluster loses quorum or experiences node partitions.

### Fail-Safe Fallback Behavior
- PettyFlow services automatically activate circuit breaker mode, falling back to direct TimescaleDB append-only balance reads.
- Database connection pools scale dynamically (`DB_MAX_OVERFLOW=10`).

### Cache Rehydration Procedure
```bash
# 1. Verify Redis cluster health
redis-cli -c -h redis-cluster.pettyflow.svc cluster info

# 2. Re-warm Redis balance cache from primary ledger balances
python -m scripts.rehydrate_cache --batch-size 5000
```

---

## 3. KMS Master Key Rotation (Zero Downtime)

### Scenario
Annual security audit or compromised CMK requires immediate master key rotation.

### Procedure
1. Provision new 256-bit Key Encryption Key (KEK) in AWS KMS / Vault with identifier `kms-master-pettyflow-v2`.
2. Deploy new application configuration with dual-key decryption support (reads decrypt with `v1` or `v2`, new writes use `v2`).
3. Run background re-encryption worker:
```bash
python -m scripts.rotate_kms_envelopes --old-key v1 --new-key v2 --tenant-id all
```
4. Revoke `v1` after 100% of encrypted envelopes are migrated to `v2`.

---

## 4. Point-In-Time Recovery (PITR) & WORM Chain Audit

### Scenario
Catastrophic volume loss requiring full restore from WAL (Write-Ahead Log) archives.

### Recovery Steps
1. Restore base backup to recovery target timestamp `TARGET_TIME`.
2. Apply continuous WAL archives from S3/GCS bucket.
3. Run WORM cryptographic audit log validator:
```bash
python -m scripts.verify_worm_audit_chain --tenant-id all
```
4. Confirm `HMAC-SHA256` continuous link across all restored transaction batches.
