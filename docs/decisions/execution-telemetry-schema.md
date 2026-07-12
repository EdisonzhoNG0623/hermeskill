# ADR: Execution Telemetry Schema

Date: 2026-07-12

Status: Proposed


# Context

ExecutionManager introduces multiple execution strategies.

Future systems may require visibility into:

- execution mode
- execution lifecycle
- termination reason
- execution cost


Observability should be based on execution events rather than direct
inspection of executor internals.


# Decision

Execution layer MAY emit structured execution events.

Events are observational only.

They MUST NOT influence execution behavior.


# Event Model


## ExecutionStarted

Generated when execution begins.


Fields:

execution_id

mode:

- normal
- l3_isolated

executor:

- async_executor
- l3_executor

timestamp



## ExecutionCompleted

Generated when execution completes successfully.


Fields:

execution_id

mode

duration_ms

timestamp



## ExecutionFailed

Generated when execution fails.


Fields:

execution_id

mode

error_type

error_message

duration_ms

timestamp



## ExecutionTerminated

Generated when execution is forcibly terminated.


Fields:

execution_id

mode

termination_reason:

- heartbeat_loss
- wall_clock
- sigkill

duration_ms

timestamp



# Example


execution_id:

exec_12345


mode:

l3_isolated


executor:

l3_executor


termination_reason:

sigkill


# Ownership


Execution Layer owns:

- event generation
- execution facts
- termination information


Control Plane owns:

- storage
- visualization
- querying


# Forbidden Behavior


Telemetry MUST NOT:

- trigger execution decisions
- enable L3 automatically
- modify routing
- modify policies


# Non-Goals

This document does not:

- implement telemetry
- define storage backend
- add database tables
- change runtime execution


# Future Work

Before implementation:

1. Define event transport
2. Define storage schema
3. Add telemetry tests
4. Review retention policy


# Status

Telemetry schema is defined as a future observability contract.

No runtime behavior changed.
