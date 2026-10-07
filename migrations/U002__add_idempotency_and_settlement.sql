-- Undo migration for V002
-- Reverts idempotency records, provider events, and settlements tables

DROP TABLE IF EXISTS settlements CASCADE;
DROP TABLE IF EXISTS provider_events CASCADE;
DROP TABLE IF EXISTS idempotency_records CASCADE;
