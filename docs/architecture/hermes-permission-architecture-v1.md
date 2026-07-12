# Hermes Permission Architecture v1

Date: 2026-07-12

Status: Frozen Reference Architecture


# Overview

Hermes Permission Architecture v1 introduces a unified capability-based
permission model.

The purpose is to provide:

- explicit capability ownership
- consistent authorization decisions
- risk-based approval handling
- auditable permission evidence


The architecture does not immediately modify runtime behavior.


# Architecture


Agent

|

Capability Request

|

Capability Resolver

|

Decision

|

+----------------+
|                |
ALLOW            APPROVAL_REQUIRED
|
v
Capability Gateway
|
v
Tool / Skill / Execution


All permission decisions generate audit evidence.


# Core Components


## Capability Registry

Purpose:

Define available capabilities.

Responsibilities:

- capability naming
- risk classification
- ownership metadata


Examples:

- docker.restart
- execution.l3
- filesystem.read



## Profile Capability Policy

Purpose:

Define what each agent identity may request.


Example:


Profile:

tech-ops


Capabilities:

- docker.inspect
- docker.restart
- execution.l3



## Capability Resolver

Purpose:

Evaluate permission requests.


Input:

- profile
- capability


Output:

- ALLOW
- DENY
- APPROVAL_REQUIRED



## Capability Gateway

Purpose:

Enforce permission decisions before execution.


Responsibilities:

- check authorization
- block denied actions
- forward allowed actions



## Approval Layer

Purpose:

Handle risky operations.


Examples:

- docker.restart
- git.push
- execution.l3



## Audit Evidence

Purpose:

Record permission decisions.


Records include:

- timestamp
- profile
- capability
- decision
- risk
- reason



# Risk Model


## Low Risk

Automatically allowed.


Examples:

- filesystem.read
- memory.read
- network.inspect



## Medium Risk

Requires approval.


Examples:

- filesystem.write
- git.write



## High Risk

Requires approval.


Examples:

- docker.restart
- git.push
- execution.l3



# Integration Strategy


## Phase 1

Shadow mode.

Capability decisions are recorded but execution behavior remains unchanged.



## Phase 2

Enable low-risk enforcement.



## Phase 3

Enable approval workflow.



## Phase 4

Full runtime permission boundary.



# Safety Guarantees


The architecture preserves:

- existing tool allowlists
- existing profile isolation
- existing runtime controls


No automatic permission escalation is introduced.



# Relationship With L3 Execution


L3 ProcessSupervisor is represented as capability:

execution.l3


The capability model controls who may request isolated execution.

The ProcessSupervisor controls how execution is terminated safely.



# Non Goals


This architecture does not:

- replace Hermes runtime
- replace providers
- modify routing
- change memory behavior
- automatically enable permissions



# Current Status


Architecture:

COMPLETE


Implementation:

CORE COMPLETE


Runtime Enforcement:

PENDING MIGRATION


Production Activation:

REQUIRES APPROVAL



# Final Statement


Hermes now has a unified permission architecture foundation.

Future runtime integrations should extend this architecture instead of
creating independent permission mechanisms.
