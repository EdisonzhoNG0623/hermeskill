# L3 Supervisor Runtime Integration Design

Date: 2026-07-12

Status: Draft

Related:

- docs/reports/l3-supervisor-validation.md
- docs/decisions/l3-process-supervisor-adoption.md


# Objective

Define how Hermes runtime may adopt L3 ProcessSupervisor
without replacing existing execution mechanisms.


# Design Principle

L3 ProcessSupervisor is an escalation capability.

It is not the default execution path.

Existing L1/L2 cancellation mechanisms remain the primary
runtime control path.


# Current Runtime Model

Normal execution:

Request

↓

Hermes Runtime

↓

Async Task

↓

Tool / Agent Execution


# Target Runtime Model

Request

↓

Execution Classification

↓

+-----------------------+
|                       |
v                       v

Normal Executor      Isolated Executor

Async Task           ProcessSupervisor

L1/L2                SIGTERM → SIGKILL


# Execution Classification


## Default Path

The following remain normal execution:

- chat completion
- routing decisions
- memory operations
- lightweight tools
- normal agent responses


## L3 Candidates

The following may use L3:

- untrusted code execution
- experimental skills
- sandbox workloads
- long-running isolated jobs
- workloads requiring hard termination guarantees


# Architecture Boundary


Future integration should introduce an abstraction layer:


ExecutionManager

|

+-- AsyncExecutor

|

+-- ProcessSupervisorExecutor


Runtime components should not directly instantiate
ProcessSupervisor.


# Failure Model


L3 termination represents execution containment.

It should produce an auditable event:

ExecutionKilled

Fields:

- workload identity
- trigger reason
- duration
- escalation path
- final signal


# Performance Considerations


L3 introduces:

- process startup overhead
- serialization requirements
- additional memory isolation


Therefore L3 selection requires explicit classification.


# Non Goals


This design does not:

- enable L3 globally
- replace L1/L2
- modify current Hermes routing
- change production behavior


# Future Work


Before implementation:

1. Define Execution Classification API
2. Define Executor interface
3. Add runtime integration tests
4. Benchmark process overhead
5. Create implementation RFC


# Status

Design proposal only.

No runtime behavior changes.
