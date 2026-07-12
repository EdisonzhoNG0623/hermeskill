# L3 Supervisor Runtime Integration Design

Date: 2026-07-12

Status: Draft

## Objective

Define how Hermes runtime may adopt L3 ProcessSupervisor
without replacing existing execution paths.

## Principle

L3 is an escalation capability, not a default executor.

## Runtime Model

Request
 |
 v
Execution Classifier
 |
 +--> Normal Runtime
 |
 +--> L3 Isolated Runtime


## Selection Rules

### L3 Allowed

- untrusted execution
- experimental skills
- long-running isolated workloads
- workloads requiring hard termination


### L3 Forbidden

- normal chat
- routing
- memory operations
- lightweight tools


## Architecture Boundary

Future integration should introduce an ExecutionManager layer.

ExecutionManager
 |
 +-- AsyncExecutor
 |
 +-- ProcessSupervisorExecutor


## Non Goals

- no global L3 enablement
- no replacement of L1/L2
- no runtime behavior changes


## Future Work

- define classifier
- benchmark overhead
- add integration tests
