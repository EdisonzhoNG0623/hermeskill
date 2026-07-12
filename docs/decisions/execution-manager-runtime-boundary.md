# ADR: ExecutionManager Runtime Boundary

Date: 2026-07-12

Status: Proposed

Related:

- c175543 fix: validate L3 supervisor SIGTERM escalation
- 22b7cd9 docs: add L3 supervisor validation report
- 9201c82 docs: add L3 supervisor adoption ADR


# Context

The ExecutionManager abstraction introduces a unified execution boundary
supporting multiple execution strategies.

Current strategies:

- Normal async execution
- L3 isolated process execution

The capability exists at the SDK layer.

It must not automatically alter Hermes runtime behavior.


# Decision

ExecutionManager remains an explicit SDK capability.

Hermes runtime continues using existing execution paths.


# Default Execution

Normal path:

Hermes Runtime

|

v

Existing Async Execution


# Explicit L3 Execution

Escalation path:

Caller

|

v

ExecutionManager

|

v

L3Executor

|

v

ProcessSupervisor


# Rules


## Default Behavior

Normal workloads MUST continue using existing execution mechanisms.


## L3 Usage

L3 execution MUST:

- be explicitly requested
- have a clear workload justification
- pass through approved integration points


## Forbidden Behavior

The following are not allowed:

- automatic escalation of normal tasks to L3
- replacing existing runtime execution
- modifying routing decisions
- enabling L3 globally


# Rationale

L3 provides stronger termination guarantees but introduces:

- process startup overhead
- serialization constraints
- operational complexity


Therefore L3 is a specialized isolation capability, not a default runtime model.


# Non-Goals

This ADR does not:

- integrate ExecutionManager into Hermes runtime
- change provider routing
- change profiles
- change task scheduling


# Future Work

Before runtime adoption:

1. Define integration contract
2. Define workload classification
3. Add runtime integration tests
4. Evaluate production overhead


# Status

Boundary established.

Runtime integration requires separate approval.
