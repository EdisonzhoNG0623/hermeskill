# RFC: Hermes L3 Supervisor Runtime Integration

Date: 2026-07-12

Status: Draft

Related:

- docs/decisions/l3-process-supervisor-adoption.md
- docs/designs/l3-supervisor-runtime-integration.md
- docs/reports/l3-supervisor-validation.md


# Summary

This RFC defines the implementation approach for integrating
L3 ProcessSupervisor into Hermes runtime.

L3 provides process-level isolation and hard termination
for workloads that cannot be safely terminated by L1/L2.


# Goals

## Primary Goals

- provide controlled access to L3 execution
- preserve existing runtime behavior
- maintain auditability
- support future execution isolation


## Non Goals

This RFC does not:

- replace async execution
- replace watchdog/apoptosis
- enable L3 for all workloads
- modify existing routing behavior


# Proposed Architecture


Request

↓

ExecutionManager

↓

+-----------------------+
|                       |
v                       v

AsyncExecutor      L3Executor

                       |

              ProcessSupervisor


# New Abstractions


## ExecutionManager

Responsible for selecting execution strategy.


Responsibilities:

- classify workload
- select executor
- record execution mode


## Executor Interface


Conceptual interface:


execute(request)

returns:

ExecutionResult


Possible implementations:

- AsyncExecutor
- ProcessSupervisorExecutor


# Selection Policy


Default:

All workloads use AsyncExecutor.


L3 requires explicit classification.


Allowed:

- untrusted execution
- experimental agents
- sandbox workloads
- long-running isolated tasks


Forbidden:

- chat completion
- routing
- memory operations
- simple tool calls


# Observability


Every L3 execution should emit:

ExecutionStarted

ExecutionCompleted

ExecutionKilled


Required metadata:

- workload id
- executor type
- trigger
- duration
- escalation result


# Rollout Plan


## Stage 1

Introduce interfaces only.

No behavior change.


## Stage 2

Add opt-in L3 execution.


## Stage 3

Evaluate:

- reliability
- latency
- resource cost


## Stage 4

Decide production adoption.


# Rollback


L3 can be disabled by removing
ProcessSupervisorExecutor routing.

Existing async execution remains unchanged.


# Open Questions


1. Which workloads qualify for automatic L3 selection?
2. Where should execution classification live?
3. How should L3 metrics integrate with Hermes telemetry?


# Decision Required

Implementation requires explicit approval after RFC review.
