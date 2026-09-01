# ADR-006: Multi-Tenant Zero-Trust Security, Envelope KMS & WORM Audit Architecture

## Context & Problem Statement
PettyFlow handles sensitive financial data (PII, tax IDs, custodian bank accounts) and enterprise transaction audit logs. We must guarantee:
1. PII is encrypted at rest using envelope encryption such that database compromise yields zero readable PII without KMS key access.
2. Cross-tenant access is cryptographically blocked at both the token validation tier and the field encryption tier.
3. Audit logs adhere to WORM (Write-Once-Read-Many) immutability standards with tamper-evident HMAC chaining.

## Decision Drivers
- **Cryptographic Isolation**: Tenant context embedded in AES-256-GCM Additional Authenticated Data (AAD).
- **Zero-Trust Token Verification**: Every API call carries cryptographic claims validated before hitting business logic.
- **WORM Audit Trail**: Monotonic sequence numbers and continuous HMAC-SHA256 linking.

## Considered Options
1. **Plain Database Column Encryption**: Shared key across all tenants risks cross-tenant exposure on database compromise.
2. **NIST Envelope Encryption (KMS/Vault) + Tenant-Bound AAD + WORM Tamper Logs**: Chosen.

## Decision Outcome
Chosen Option: **Envelope Encryption with AES-256-GCM + Tenant AAD Binding + Zero-Trust JWT Verifier + WORM Tamper Logger**.

### Security Invariants:
- Per-record 256-bit DEKs encrypted by 256-bit KEK.
- Decrypting Tenant A's ciphertext with Tenant B's credentials triggers an immediate cryptographic auth failure.
- Tampering with any historical audit entry invalidates downstream HMAC hashes.

## Consequences
- **Positive**: Compliant with SOC2, GDPR, and ISO 27001 audit standards; complete multi-tenant cryptographic isolation.
- **Negative**: Adds sub-millisecond envelope unwrapping overhead for sensitive field reads.
