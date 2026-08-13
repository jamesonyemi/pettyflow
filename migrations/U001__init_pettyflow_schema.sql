-- Undo migration for V001. Use only in a development or disposable staging
-- database: rolling this back permanently removes all PettyFlow ledger data.

DROP TABLE IF EXISTS audit_trail CASCADE;
DROP TABLE IF EXISTS ledger_blocks CASCADE;
DROP TABLE IF EXISTS postings CASCADE;
DROP TABLE IF EXISTS accounts CASCADE;
DROP TABLE IF EXISTS funds CASCADE;
DROP TABLE IF EXISTS tenants CASCADE;
