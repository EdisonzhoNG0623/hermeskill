# ADR: Runtime Capability Shadow Mode Policy

Date: 2026-07-12

Status: Proposed


# Context

Capability Permission Architecture v1 is complete.

However, immediately enforcing capability decisions in Hermes runtime
creates migration risk.

Existing permission systems remain active:

- tool allowlists
- skill permissions
- profile policies


# Decision

Runtime integration starts with Shadow Mode.


Shadow Mode behavior:

1. Observe capability requests
2. Resolve permission decisions
3. Generate audit evidence
4. Do not block execution


# Runtime Flow


Agent Request

|

Capability Observer

|

Capability Resolver

|

Audit Record


Original execution continues unchanged.



# Allowed Shadow Mode Actions

The system MAY:

- classify requests
- generate audit records
- measure coverage
- identify missing capabilities


The system MUST NOT:

- deny execution
- modify tool routing
- change profiles
- change runtime policies



# Migration Goal

Shadow Mode collects enough evidence to safely enable enforcement.


# Exit Criteria

Shadow Mode completes when:

- major tools have capability mappings
- unknown capability rate is understood
- false positive risk is evaluated
- rollback path is validated


# Non Goals

This ADR does not:

- enable runtime enforcement
- replace existing permission systems
- change Hermes behavior


# Status

Shadow mode policy defined.
