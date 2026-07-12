# ADR: Capability Permission Model

Date: 2026-07-12

Status: Proposed


# Context

Hermes currently contains multiple permission boundaries:

- tool allowlists
- skill requirements
- profile configuration
- runtime policies

These mechanisms currently operate independently.

This creates several risks:

- inconsistent permission decisions
- unclear ownership of capabilities
- skills requesting unavailable permissions
- profiles having ambiguous authority boundaries


# Decision

Introduce a unified Capability Permission Model.

Capabilities become the common language between:

- Profiles
- Skills
- Tools
- Execution layers


The model follows:

Profile

|

v

Capability Policy

|

v

Permission Resolver

|

v

Tool / Skill / Execution


# Capability Naming Convention

Capabilities use:

domain.action


Examples:

filesystem.read

filesystem.write

docker.inspect

docker.restart

memory.read

memory.write

execution.normal

execution.l3

network.inspect

git.read

git.write

git.push


# Capability Registry

Capabilities are defined centrally.

Each capability contains:

- name
- risk level
- description
- ownership domain


Example:

filesystem.write:

risk:
medium

description:
Modify files


# Permission Decision Model


Resolver returns one of:

ALLOW

DENY

APPROVAL_REQUIRED


Example:

Request:

profile:
tech-ops

capability:
docker.restart


Decision:

ALLOW


# Ownership Boundary


## Profile

Defines what capabilities an agent identity may request.


## Capability Registry

Defines what capabilities exist.


## Permission Resolver

Evaluates policy.


## Tools

Execute only after permission decision.


# Initial Risk Levels


## Low

Examples:

- filesystem.read
- memory.read
- network.inspect


## Medium

Examples:

- filesystem.write
- memory.write
- git.write


## High

Examples:

- docker.restart
- git.push
- execution.l3


# Non-Goals

This ADR does not:

- replace current tool routing
- enable capability enforcement globally
- modify existing profiles
- introduce approval UI
- change runtime behavior


# Migration Strategy


Phase 1:

Create capability vocabulary and registry.


Phase 2:

Add resolver API.


Phase 3:

Map profiles to capabilities.


Phase 4:

Integrate with tools and skills.


# Success Criteria

Capability Permission Model v1 is complete when:

- capabilities have unique names
- profiles can declare permissions
- resolver can return decisions
- tests cover allow/deny cases


# Status

Permission architecture defined.

Implementation requires separate changes.
