# ADR-003: Hybrid gRPC and REST Float Allocation API Architecture

## Context & Problem Statement

PettyFlow requires high-throughput interfaces for custodians and enterprise clients to request, allocate, disburse, and query petty cash floats. The API layer must support:

1. **High-Performance Internal Microservices**: Low-latency gRPC protocol for inter-service communication and ledger posting triggers.
2. **Enterprise REST / OpenAPI Integration**: Standard HTTP/JSON interfaces for web dashboards, mobile applications, and third-party integrations.
3. **Strict Validation & Integer Scaling**: Input payloads must enforce ISO-4217 currency codes, UUIDv4 tenant IDs, and non-negative amounts converted to scaled 64-bit integers ($10^4$).

---

## Decision Drivers

- **Performance**: End-to-end gRPC handler latency $< 2.0 \text{ ms}$ (p99); REST lookup latency $< 10 \text{ ms}$ (p99).
- **Type Safety & Schema Governance**: Protocol Buffers (`proto/pettyflow/v1/float_service.proto`) as the canonical schema definition for internal services.
- **Interoperability**: FastAPI REST wrappers automatically exposing OpenAPI 3.0 specs at `/docs`.
- **Financial Invariants**: All monetary amounts validated before reaching domain services.

---

## Considered Options

### Option 1: Dual Handler Architecture with Shared Domain Service (Chosen)
Define gRPC services in Protobuf and generate Python stubs. Implement gRPC handlers in `src/api/grpc/float_handler.py` and FastAPI REST endpoints in `src/api/rest/float_router.py`, both delegating business operations to a unified, thread-safe `FundService` domain layer.

**Pros:**
- Direct, un-marshaled gRPC performance for high-throughput backend services.
- Standard HTTP/JSON REST API for web/mobile applications with auto-generated OpenAPI documentation.
- Shared domain logic guarantees consistent business rule evaluation across protocols.

**Cons:**
- Requires maintaining both gRPC handler bindings and FastAPI route definitions.

---

## Decision Outcome

**Chosen Option: Option 1 — Dual Handler Architecture with Shared Domain Service**

The `FundService` domain class (`src/domain/funds/service.py`) acts as the single entry point for float allocations, balance updates, and fund lifecycle management. Both `FloatServiceHandler` (gRPC) and `float_router` (FastAPI REST) consume `FundService`.

Input values are converted to 64-bit integer fixed-point units ($10^4$) at the API boundary, guaranteeing that no floating-point arithmetic enters the domain layer.

---

## Consequences

### Positive
- gRPC services achieve sub-2ms response times for internal cluster traffic.
- OpenAPI 3.0 specification is automatically generated and accessible at `/docs`.
- Full integration test coverage across both gRPC (`tests/integration/test_float_grpc.py`) and REST (`tests/integration/test_float_rest.py`).

### Negative
- Schema changes must be synchronized between `float_service.proto` and Pydantic REST models.

---

*ADR Status: ACCEPTED*  
*Decision Date: 2026-08-19*  
*Deciders: James (Product Owner), Antigravity AI (Engineering)*
