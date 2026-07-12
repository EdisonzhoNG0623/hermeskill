# RFC: Runtime Capability Permission Integration

Date: 2026-07-12

Status: Proposed


# Summary

This document defines how Hermes runtime may integrate the Capability
Permission Model.

The integration is designed as a gradual migration.

Current runtime behavior remains unchanged.


# Current State

Hermes currently has:

- tool allowlists
- skill permissions
- profile configuration
- runtime policies


These mechanisms remain active during migration.


# Goal

Introduce a unified permission decision path:

Agent

|

Capability Gateway

|

Permission Resolver

|

Tool / Skill Execution



# Integration Principles


## No Immediate Enforcement

Capability checks should initially operate in observation mode.

The system records:

- requested capability
- decision
- reason


Execution behavior remains unchanged.


## Explicit Migration

Each tool integration requires:

- capability mapping
- risk classification
- test coverage
- rollback path


## Existing Safety Preserved

Existing:

- allowlists
- approvals
- runtime restrictions

remain authoritative until migration completes.


# Migration Phases


## Phase 1 — Shadow Mode

Capability Gateway observes requests.

Actions:

- resolve capability
- create audit record
- do not block execution


## Phase 2 — Low Risk Enforcement

Enable enforcement for:

- filesystem.read
- memory.read
- network.inspect


## Phase 3 — High Risk Review

Require approval for:

- docker.restart
- git.push
- execution.l3


## Phase 4 — Full Integration

Capability Gateway becomes the primary permission boundary.


# First Integration Candidates


Recommended order:


1. Memory tools

Reason:

Low risk, clear ownership.


2. Filesystem tools

Reason:

Existing permission concepts exist.


3. Docker operations

Reason:

High impact, requires approval.


4. Execution isolation

Reason:

Connects with L3 ProcessSupervisor.


# Rollback Strategy


If issues occur:

- disable Capability Gateway enforcement
- keep audit mode enabled
- restore existing allowlists


No data migration required.


# Non Goals

This RFC does not:

- modify Hermes runtime
- replace existing tools
- change profiles
- enable global enforcement


# Success Criteria

Integration is complete when:

- all tools map to capabilities
- permissions are auditable
- approval flow is connected
- runtime behavior is validated


# Status

Integration plan defined.

Implementation requires separate authorization.
