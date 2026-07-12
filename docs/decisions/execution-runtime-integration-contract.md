# ADR: Execution Runtime Integration Contract

Date: 2026-07-12

Status: Proposed


## Related

- c175543 fix: validate L3 supervisor SIGTERM escalation
- 22b7cd9 docs: add L3 supervisor validation report
- 9201c82 docs: add L3 supervisor adoption ADR


# Context

ExecutionManager introduces a unified execution abstraction.

Current implementation supports:

- NORMAL execution
- L3 isolated execution


The capability exists independently from Hermes runtime.

A future integration requires an explicit contract to avoid unintended
runtime behavior changes.


# Decision

Hermes runtime integration MUST go through ExecutionManager.

Runtime components MUST NOT directly call:

- ProcessSupervisor
- L3Executor


The execution layer is the only boundary responsible for selecting execution
strategy.


# Contract


## Normal Execution

Default path:

Hermes Runtime

|

v

ExecutionManager

|

v

AsyncExecutor


Characteristics:

- low overhead
- existing behavior
- default execution mode


## L3 Isolated Execution

Explicit path:

Hermes Runtime

|

v

ExecutionManager

|

v

L3Executor

|

v

ProcessSupervisor


Characteristics:

- separate process boundary
- hard termination capability
- higher operational cost


# Selection Rules


NORMAL execution SHOULD be used for:

- regular agent tasks
- normal tool calls
- interactive requests


L3 execution MAY be considered for:

- untrusted workloads
- experimental execution
- workloads requiring hard termination


# Restrictions


The integration MUST NOT:

- replace NORMAL execution globally
- automatically escalate all tasks
- modify routing behavior
- modify model selection
- bypass existing governance


# Required Before Runtime Adoption


Before enabling production usage:

1. Define workload classification policy
2. Add runtime integration tests
3. Measure execution overhead
4. Review operational impact
5. Approve runtime RFC


# Non-Goals

This document does not:

- enable L3 in production
- change Hermes runtime
- change execution defaults


# Status

Integration contract defined.

Implementation requires separate approval.
