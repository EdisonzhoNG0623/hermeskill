# ADR: L3 ProcessSupervisor Adoption Policy

Date: 2026-07-12

Status: Proposed

Related:
- c175543 fix: validate L3 supervisor SIGTERM escalation
- docs/reports/l3-supervisor-validation.md


# Context

Hermes currently has multiple termination layers:

L1:
In-process async cancellation

L2:
Watchdog / apoptosis based cancellation

L3:
ProcessSupervisor based external process termination


L1/L2 are sufficient for cooperative workloads.

However, they cannot reliably terminate workloads that are:

- CPU-bound infinite loops
- synchronous blocking operations
- execution paths that never reach await points


L3 introduces a separate process boundary, allowing the supervisor to enforce termination using OS-level signals.


# Decision

L3 ProcessSupervisor is adopted as an escalation capability only.

It is NOT the default execution path.

Normal Hermes workloads continue using existing execution mechanisms.


# Adoption Rules


## Allowed L3 workloads

L3 MAY be used for:

- untrusted code execution
- experimental skills
- long-running isolated workloads
- workloads with external failure risk
- workloads requiring hard termination guarantees


## Disallowed L3 workloads

L3 MUST NOT be used for:

- normal chat completion
- routing decisions
- memory operations
- lightweight tool calls
- standard synchronous execution


# Execution Model


Normal path:

Agent
 |
 v
Async Runtime


Escalation path:

Agent
 |
 v
ProcessSupervisor
 |
 +--> SIGTERM
 |
 +--> grace period
 |
 +--> SIGKILL


# Rationale

Process isolation provides stronger termination guarantees but introduces:

- process startup overhead
- serialization requirements
- increased operational complexity


Therefore L3 should be selected deliberately based on workload characteristics.


# Non-Goals

This ADR does not:

- replace L1/L2 cancellation
- modify Hermes routing
- enable L3 globally
- introduce new runtime behavior


# Future Work

Before production integration:

1. Define runtime routing policy
2. Define workload classification rules
3. Add integration tests
4. Evaluate performance overhead


# Status

Architecture decision recorded.

Implementation requires a separate approved integration change.
