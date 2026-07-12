# RFC: Hermes Runtime Execution Layer Integration

Date: 2026-07-12

Status: Draft


## Summary

This RFC proposes a future integration path between Hermes Runtime and
ExecutionManager.

The goal is to provide controlled access to isolated execution capability
without changing the existing runtime execution model.


## Background

Hermes currently executes workloads through existing async execution paths.

This works well for cooperative workloads.

However, some workloads may require stronger termination guarantees:

- CPU-bound execution
- blocking operations
- experimental workloads
- untrusted execution paths


ExecutionManager introduces an abstraction boundary:

Runtime

|

v

ExecutionManager

|

+----------------+

|

AsyncExecutor

|

L3Executor


## Goals

The integration should:

- preserve existing execution behavior
- provide explicit isolated execution
- maintain runtime observability
- allow future workload classification


## Non-Goals

This RFC does not propose:

- replacing current execution
- enabling L3 globally
- automatic escalation
- changing provider routing
- changing model selection


## Proposed Model


Default execution:

Runtime

|

v

ExecutionManager

|

v

AsyncExecutor


Isolated execution:

Runtime

|

v

ExecutionManager

|

v

L3Executor

|

v

ProcessSupervisor


## Workload Classification

Future integration should classify workloads before selecting L3.


Potential L3 candidates:

- sandbox execution
- experimental agents
- external code execution
- long-running isolated jobs


Not L3 candidates:

- normal conversation
- normal tool invocation
- routing decisions
- memory operations


## Safety Requirements

Any runtime integration must provide:

### Explicit Selection

Execution mode must be intentional.

Default:

NORMAL


Optional:

L3_ISOLATED


### Observability

Future integration should record:

- execution mode
- execution duration
- termination reason
- escalation events


### Rollback

Integration must support disabling L3 execution without affecting
normal execution.


## Performance Considerations

L3 introduces:

- process creation overhead
- serialization overhead
- additional lifecycle management


Performance impact must be measured before production usage.


## Implementation Requirements

Before implementation:

1. Runtime integration design review
2. Workload policy definition
3. Telemetry design
4. Integration test coverage
5. Rollback procedure


## Decision

This RFC defines a possible future integration path.

It does not authorize runtime changes.


