# ADR: Control Plane Execution Awareness

Date: 2026-07-12

Status: Proposed


# Context

ExecutionManager introduces multiple execution strategies:

- NORMAL
- L3_ISOLATED


Future control plane components may need visibility into execution state.

However, execution lifecycle ownership must remain inside the execution layer.


# Decision

Control Plane MAY observe execution metadata.

Control Plane MUST NOT control execution strategy directly.


# Ownership Boundary


Control Plane responsibilities:

- display execution mode
- record execution events
- provide audit visibility


Execution Layer responsibilities:

- select executor
- manage process lifecycle
- enforce termination policy


# Data Model


Future execution events MAY include:


execution_id

mode:

- normal
- l3_isolated


status:

- started
- completed
- failed
- killed


termination_reason:

- completed
- heartbeat_loss
- wall_clock
- sigkill


duration


# Forbidden Behavior


Control Plane MUST NOT:

- invoke ProcessSupervisor
- bypass ExecutionManager
- automatically upgrade workloads to L3
- change execution policy


# Rationale

Separating observation from execution control prevents:

- hidden runtime behavior changes
- accidental L3 activation
- policy bypass


# Non-Goals

This ADR does not:

- add telemetry implementation
- modify control plane code
- enable runtime integration


# Future Work

Before implementation:

1. Define event schema
2. Define storage strategy
3. Add observability tests
4. Review privacy requirements


# Status

Control plane awareness remains a future design capability.

No runtime behavior changed.
