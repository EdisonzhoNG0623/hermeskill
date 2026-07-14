# ADR: Capability Approval Policy

Date: 2026-07-12

Status: Proposed


# Context

Capability Permission Model provides basic authorization.

However, some capabilities require human confirmation before execution.

Examples:

- production changes
- destructive operations
- external side effects


# Decision

Introduce an approval decision layer.

Permission decisions become:

- ALLOW
- DENY
- APPROVAL_REQUIRED


# Decision Flow


Request

|

v

Capability Resolver

|

v

Risk Policy

|

+------------+
|            |
v            v

ALLOW     APPROVAL_REQUIRED


or


DENY



# Approval Required Examples


High-risk capabilities:

- docker.restart
- git.push
- filesystem.delete
- execution.l3


# Automatic Allow Examples


Low-risk capabilities:

- filesystem.read
- memory.read
- network.inspect


# Ownership


Capability Registry:

Defines capability risk.


Approval Policy:

Defines when approval is required.


Runtime:

Handles approval workflow.


# Non Goals


This ADR does not:

- implement approval UI
- change runtime behavior
- automatically enable approvals


# Future Work


1. Approval request object
2. Approval transport
3. User confirmation channel
4. Audit logging


# Status

Approval architecture defined.
