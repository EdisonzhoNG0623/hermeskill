# L3 Execution Architecture

Date: 2026-07-12

Status: Reference Architecture


# Overview

L3 Execution provides an isolated execution capability for workloads that
require stronger termination guarantees than in-process cancellation can
provide.


The architecture introduces:

- ProcessSupervisor
- ExecutionManager
- L3Executor


The capability exists as an optional execution boundary.

It does not replace the normal execution path.


# Architecture


## Normal Execution


Hermes Runtime

|

v

ExecutionManager

|

v

AsyncExecutor

|

v

Direct Execution



## L3 Isolated Execution


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

|

v

Child Process



# Capability Layers


## L1

In-process cancellation.

Purpose:

- cooperative shutdown
- async task cancellation


## L2

Watchdog based cancellation.

Purpose:

- detect unhealthy execution
- terminate cooperative failures


## L3

Process isolation.

Purpose:

- enforce termination externally
- handle non-cooperative workloads


# Design Principles


## Explicit Activation

L3 execution requires explicit selection.

Normal execution remains default.


## Separation of Responsibility


ExecutionManager:

- selects execution strategy


ProcessSupervisor:

- manages process lifecycle


Control Plane:

- observes execution state



# Validation Status


Implementation:

COMPLETE


Validation:

COMPLETE


Architecture Review:

COMPLETE


Runtime Integration:

NOT ENABLED



# Documentation Map


## Implementation

- ProcessSupervisor


## Validation

- docs/reports/l3-supervisor-validation.md


## Adoption Policy

- docs/decisions/l3-process-supervisor-adoption.md


## Execution Boundary

- docs/decisions/execution-runtime-integration-contract.md


## Runtime Proposal

- docs/rfcs/runtime-execution-integration-rfc.md


## Control Plane

- docs/decisions/control-plane-execution-awareness.md


## Telemetry

- docs/decisions/execution-telemetry-schema.md



# Runtime Safety


Current production behavior:

UNCHANGED


No changes to:

- routing
- providers
- profiles
- memory
- scheduling


# Future Integration Path


Before runtime adoption:


1. Approval of runtime RFC

2. Workload classification policy

3. Integration implementation

4. Runtime tests

5. Operational validation



# Final State


L3 Execution is frozen as a reference architecture.

The capability is available for controlled future adoption.

Production activation requires separate authorization.
