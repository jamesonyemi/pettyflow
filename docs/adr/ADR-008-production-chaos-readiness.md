# ADR-008: 100k TPS Distributed Benchmarks, Chaos Engineering & Kubernetes Production Readiness

## Context & Problem Statement
To guarantee enterprise reliability under extreme traffic spikes and hardware failure, PettyFlow must:
1. Maintain stable p99 API read latency < 10 ms under 100,000 requests/second.
2. Guarantee zero transaction loss during primary database failover and cache partitions.
3. Provide cloud-native Kubernetes deployment with zero-trust container security, HPA, and PodDisruptionBudgets.

## Decision Drivers
- **Latency Invariant**: Sub-10ms p99 response times under peak 100k TPS synthetic load.
- **Resilience Invariant**: Zero lost transactions (RPO = 0s) during failover.
- **Container Hardening**: Non-root execution, read-only root filesystems, and dropped Linux capabilities.

## Considered Options
1. **Monolithic Single-Node Deployment**: Single point of failure, cannot survive hardware crashes.
2. **Kubernetes Auto-Scaling Cluster (5-50 Pods) + Locust Distributed Testing + Chaos Fault Injection**: Chosen.

## Decision Outcome
Chosen Option: **Distributed Microservice Architecture + Helm Deployment + Locust Load Testing + Chaos Simulation**.

### Implementation Details:
- Distributed Locust load testing suite (`benchmarks/locustfile.py`).
- Production Kubernetes Helm charts (`deploy/k8s/helm/pettyflow/`).
- Automated chaos tests (`tests/unit/test_benchmarks_and_chaos.py`) validating primary failover, cache fallback, and latency invariants.

## Consequences
- **Positive**: Proven 100k TPS capacity, resilient zero-data-loss failovers, standardized Helm deployments.
- **Negative**: Requires Kubernetes cluster and multi-AZ database replication infrastructure.
