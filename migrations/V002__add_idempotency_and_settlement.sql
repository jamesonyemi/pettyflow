-- PettyFlow Database Schema Migration V002
-- Idempotency Records, Provider Event Deduplication, and Settlement Lifecycles

-- 1. Idempotency Records Table
CREATE TABLE IF NOT EXISTS idempotency_records (
    tenant_id UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    idempotency_key VARCHAR(255) NOT NULL,
    request_fingerprint VARCHAR(64) NOT NULL,
    result_json TEXT NOT NULL DEFAULT '',
    status VARCHAR(50) NOT NULL CHECK (status IN ('in_progress', 'completed', 'failed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (tenant_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_idempotency_records_created
ON idempotency_records (created_at);

-- 2. Provider Events Table (Webhook Callback Deduplication)
CREATE TABLE IF NOT EXISTS provider_events (
    tenant_id UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    provider VARCHAR(100) NOT NULL,
    event_id VARCHAR(255) NOT NULL,
    payload_fingerprint VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (tenant_id, provider, event_id)
);

CREATE INDEX IF NOT EXISTS idx_provider_events_created
ON provider_events (created_at);

-- 3. Settlements Table
CREATE TABLE IF NOT EXISTS settlements (
    settlement_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    disbursement_id VARCHAR(255) NOT NULL,
    provider VARCHAR(100) NOT NULL,
    amount_scaled BIGINT NOT NULL CHECK (amount_scaled > 0),
    status VARCHAR(50) NOT NULL CHECK (status IN ('pending', 'processing', 'completed', 'failed', 'reversed', 'manual_review')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_settlements_tenant_disbursement
ON settlements (tenant_id, disbursement_id);
