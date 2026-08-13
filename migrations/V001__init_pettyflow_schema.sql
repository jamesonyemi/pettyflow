-- PettyFlow Database Schema Initialization (Migration V001)
-- Enterprise Multi-Tenant Petty Cash Ledger, Postings Partitioning & TimescaleDB Audit Trail

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 1. Tenants Table
CREATE TABLE IF NOT EXISTS tenants (
    tenant_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(255) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'ACTIVE',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 2. Funds Table (Custodial Petty Cash Floats)
CREATE TABLE IF NOT EXISTS funds (
    fund_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    currency VARCHAR(3) NOT NULL DEFAULT 'USD',
    custodian_id VARCHAR(255) NOT NULL,
    allocated_amount_scaled BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 3. Accounts Table (Chart of Accounts)
CREATE TABLE IF NOT EXISTS accounts (
    account_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    category VARCHAR(50) NOT NULL CHECK (category IN ('ASSET', 'LIABILITY', 'EQUITY', 'EXPENSE', 'REVENUE')),
    currency VARCHAR(3) NOT NULL DEFAULT 'USD',
    version_id BIGINT NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 4. Postings Table (Partitioned by tenant_id and created_at range)
CREATE TABLE IF NOT EXISTS postings (
    posting_id UUID NOT NULL DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL,
    transaction_id UUID NOT NULL,
    account_id UUID NOT NULL,
    entry_type VARCHAR(10) NOT NULL CHECK (entry_type IN ('DEBIT', 'CREDIT')),
    amount_scaled BIGINT NOT NULL CHECK (amount_scaled > 0),
    sequence_number BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (posting_id, tenant_id, created_at)
) PARTITION BY RANGE (created_at);

-- Create default monthly partition for initial rollout
CREATE TABLE IF NOT EXISTS postings_default PARTITION OF postings DEFAULT;

-- Compound index for fast tenant account balance lookups
CREATE INDEX IF NOT EXISTS idx_postings_tenant_account_created 
ON postings (tenant_id, account_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_postings_transaction 
ON postings (tenant_id, transaction_id);

-- 5. Ledger Blocks Table (Cryptographic HMAC Hash Chain)
CREATE TABLE IF NOT EXISTS ledger_blocks (
    tenant_id UUID NOT NULL,
    sequence_number BIGINT NOT NULL,
    transaction_id UUID NOT NULL,
    description TEXT NOT NULL,
    legs_payload JSONB NOT NULL,
    previous_hash BYTEA NOT NULL,
    current_hash BYTEA NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (tenant_id, sequence_number)
);

CREATE INDEX IF NOT EXISTS idx_ledger_blocks_tx 
ON ledger_blocks (tenant_id, transaction_id);

-- 6. Audit Trail Table (TimescaleDB hypertable if enabled, else standard partitioned)
CREATE TABLE IF NOT EXISTS audit_trail (
    audit_id UUID NOT NULL DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL,
    actor_id VARCHAR(255) NOT NULL,
    action VARCHAR(100) NOT NULL,
    entity_type VARCHAR(100) NOT NULL,
    entity_id VARCHAR(255) NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (audit_id, tenant_id, created_at)
);

-- Convert audit_trail to hypertable if TimescaleDB is enabled
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
        PERFORM create_hypertable('audit_trail', 'created_at', if_not_exists => TRUE);
    END IF;
EXCEPTION
    WHEN OTHERS THEN
        RAISE NOTICE 'TimescaleDB hypertable creation skipped.';
END $$;
