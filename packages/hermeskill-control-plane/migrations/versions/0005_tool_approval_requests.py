"""tool_approval_requests table (v1 interactive approval bridge)

Revision ID: 0005_tool_approval_requests
Revises: 0004_apoptosis_grants
Create Date: 2026-07-15

Adds the audit-side table for the interactive-tool-approval bridge. Runtime
authorisation still flows through `apoptosis_grants` (the approve handler
inserts a short-lived grant for `tool_scope_violation`); this table is the
durable record of "who approved what and when", queryable from the Hermes
Dashboard.

Design notes:
  * `arguments_hash` is the canonical SHA-256 of the tool arguments, computed
    by the SDK before submission. Used for idempotency + argument-match
    before granting.
  * `arguments_preview` carries the SDK-redacted JSONB view; the server
    never sees plaintext secrets.
  * The idempotency index covers the
    `(agent_id, tool_name, capability, arguments_hash, status)` lookup the
    SDK does every time it retries a blocked call.
  * `grant_id` points to the `apoptosis_grants` row created on approve;
    null on deny/expired/pending. ON DELETE SET NULL so cleaning up grants
    (or revoking them post-hoc) does not orphan the audit trail.
  * No new runtime-token / consume / approval_token_hash columns — those
    were explicitly rejected in favour of reusing the existing grant model.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_tool_approval_requests"
down_revision: str | Sequence[str] | None = "0004_apoptosis_grants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tool_approval_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("session_key", sa.String(200), nullable=True),
        sa.Column("tool_name", sa.String(120), nullable=False),
        sa.Column("capability", sa.String(120), nullable=False),
        sa.Column("risk", sa.String(20), nullable=False),
        sa.Column("arguments_hash", sa.String(64), nullable=False),
        # Already-redacted JSONB view. Server-side validation is structural
        # only; secret-stripping is the SDK's responsibility.
        sa.Column(
            "arguments_preview",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("reason", sa.String(2000), nullable=False),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "decided_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("api_keys.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("decision_reason", sa.String(2000), nullable=True),
        sa.Column(
            "grant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("apoptosis_grants.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    # Hot path 1: "is there a pending approval for this exact call?"
    # The SDK retries the same blocked call until approved/denied, so this
    # lookup fires every retry. Status kept in the index so a SELECT against
    # it can be served by the index alone (no row fetch needed to know it's
    # not there).
    op.create_index(
        "ix_tool_approval_requests_idempotency",
        "tool_approval_requests",
        [
            "agent_id",
            "tool_name",
            "capability",
            "arguments_hash",
            "status",
        ],
    )
    # Hot path 2: Dashboard "show pending approvals for this agent, newest
    # first". Composite index supports the sort.
    op.create_index(
        "ix_tool_approval_requests_agent_status",
        "tool_approval_requests",
        ["agent_id", "status", "requested_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_tool_approval_requests_agent_status",
        table_name="tool_approval_requests",
    )
    op.drop_index(
        "ix_tool_approval_requests_idempotency",
        table_name="tool_approval_requests",
    )
    op.drop_table("tool_approval_requests")