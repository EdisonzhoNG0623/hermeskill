"""Tool-approval request/response types.

Approval is the *audit* surface of the interactive-tool-approval bridge
(Hermeskill v1). The runtime surface is the existing `ApoptosisGrant`
mechanism — when an operator approves a tool call, the approval handler
issues a short-lived grant for `tool_scope_violation` so the next call
sails through `apply_grants()`. The approval row itself is the durable
record of *who approved what and when*.

This split is deliberate:

  * ApprovalRequest = audit. Created by the SDK on every high-risk tool
    call; never auto-consumed; never blocks retries; visible in the
    Dashboard forever (within retention).

  * ApoptosisGrant = runtime. Created by the approval handler; mutates
    `state.grants`; consumed by `apply_grants()` on the very next tool
    call; expires after `grant_duration_seconds` (60s default).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"


class ApprovalRequestIn(BaseModel):
    """POST body for `/agents/{id}/approval-requests` (SDK → Control Plane).

    Idempotency is computed server-side from the canonical tuple
    `(agent_id, session_key, tool_name, capability, arguments_hash)` — the
    SDK does not need to (and should not) generate an idempotency key.
    """

    model_config = ConfigDict(extra="forbid")

    tool_name: str = Field(min_length=1, max_length=120)
    capability: str = Field(min_length=1, max_length=120)
    risk: str = Field(min_length=1, max_length=20)
    arguments_hash: str = Field(
        min_length=64,
        max_length=64,
        description="SHA-256 hex of the canonical-JSON tool arguments",
    )
    arguments_preview: dict = Field(
        default_factory=dict,
        description="Already-redacted preview of the tool arguments for the Dashboard",
    )
    session_key: str | None = Field(
        default=None,
        max_length=200,
        description="Optional Hermes session key (for Dashboard breadcrumb)",
    )
    reason: str = Field(
        min_length=1,
        max_length=2000,
        description="Why this tool call needs human approval (SDK-supplied context)",
    )


class ApprovalDecisionIn(BaseModel):
    """POST body for `/approval-requests/{id}/approve` and `/deny`."""

    model_config = ConfigDict(extra="forbid")

    decision_reason: str = Field(default="", max_length=2000)


class ApprovalRequestOut(BaseModel):
    """A single approval request as the SDK + Dashboard see it.

    `arguments_preview` is the redacted view; the server never sees the
    raw arguments. `grant_id` is set server-side when the request is
    approved — it points to the `apoptosis_grants` row that the runtime
    will pick up on the next call. `grant_duration_seconds` is the
    matching runtime grant's TTL.
    """

    id: UUID
    agent_id: UUID
    session_key: str | None
    tool_name: str
    capability: str
    risk: str
    arguments_hash: str
    arguments_preview: dict
    reason: str
    status: ApprovalStatus
    requested_at: datetime
    expires_at: datetime
    decided_at: datetime | None
    decided_by: UUID | None
    decision_reason: str | None
    grant_id: UUID | None = None
    grant_duration_seconds: int | None = None


class ApprovalRequestListOut(BaseModel):
    """List response wrapper — kept for future pagination metadata."""

    items: list[ApprovalRequestOut]