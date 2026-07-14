"""ToolApprovalRequest model (v1 interactive approval bridge).

One row per (agent, tool, capability, arguments_hash) tuple that's
awaiting operator approval. Idempotent on the same tuple: a second
post of the same payload returns the existing row instead of creating
a new one (the SDK retries the same blocked call every turn).

The row is the **audit** record of the approval event. Runtime
authorisation flows through `apoptosis_grants` (tool_scope_violation
grant, ~60s) created by the approve handler — that grant is what
`apply_grants()` consumes on the next tool call. The approval row
itself is never consumed by the runtime.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class ToolApprovalRequest(Base):
    """Pending / approved / denied / expired operator-approval requests.

    `arguments_preview` is the *already-redacted* JSON view the SDK ships;
    we never see the raw arguments. `arguments_hash` is the canonical
    SHA-256 of the canonical-JSON raw arguments (after stable key sort),
    computed by the SDK. Together they let the operator identify the call
    without leaking secrets.
    """

    __tablename__ = "tool_approval_requests"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    agent_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    session_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    tool_name: Mapped[str] = mapped_column(String(120), nullable=False)
    capability: Mapped[str] = mapped_column(String(120), nullable=False)
    risk: Mapped[str] = mapped_column(String(20), nullable=False)
    arguments_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    arguments_preview: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    reason: Mapped[str] = mapped_column(String(2000), nullable=False)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")

    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    decided_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("api_keys.id", ondelete="SET NULL"),
        nullable=True,
    )
    decision_reason: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    # When status == "approved", the approve handler created this grant.
    # Nullable: deny / expired rows leave it null.
    grant_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("apoptosis_grants.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        # Hot path: "is there a pending approval for this exact call?"
        Index(
            "ix_tool_approval_requests_idempotency",
            "agent_id",
            "tool_name",
            "capability",
            "arguments_hash",
            "status",
        ),
        Index(
            "ix_tool_approval_requests_agent_status",
            "agent_id",
            "status",
            "requested_at",
        ),
    )