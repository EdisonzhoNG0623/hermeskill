"""Approval request router (v1 interactive approval bridge).

Endpoints (all customer-scoped via `require_principal`):

    POST /agents/{agent_id}/approval-requests
        SDK → server. Creates a pending row (or returns existing one
        for the same idempotency tuple). Auth: developer OR operator.
        Idempotent on (agent_id, tool_name, capability, arguments_hash).

    GET /approval-requests
        Operator only. Lists approval rows across the caller's customer,
        newest first. Supports `status=pending` and `agent_id` filters.

    GET /approval-requests/{id}
        Principal. 404 across customers. Includes the matched grant_id
        when approved.

    POST /approval-requests/{id}/approve
        Operator only. Marks approved, sets decided_by / decided_at /
        decision_reason, creates a short-lived `apoptosis_grants` row
        for `tool_scope_violation` and links it via `grant_id`.
        Returns the approval row + the grant id.

    POST /approval-requests/{id}/deny
        Operator only. Marks denied. No grant created.

Idempotency is computed by a SELECT-then-INSERT inside a transaction
with `SERIALIZABLE`-style behaviour provided by Postgres's default
`READ COMMITTED` + a unique-shaped index lookup. Concurrent dup
inserts collapse to the first writer; the second writer sees the
existing pending row and returns it.

Server TTL for pending rows is configurable via
`HERMESKILL_APPROVAL_TTL_SECONDS` (default 600). Expired rows are
never auto-decided — the SDK treats them as pending-deny on next
fetch (the bridge maps that to a block directive with an "expired"
message; the agent can re-request with a fresh row).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from hermeskill.types import (
    ApprovalDecisionIn,
    ApprovalRequestIn,
    ApprovalRequestOut,
    SymptomType,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from control_plane.api.agents import _load_agent_owned_by
from control_plane.auth import Principal, require_operator, require_principal
from control_plane.db.models import (
    Agent,
    ApoptosisGrant,
    ToolApprovalRequest,
)
from control_plane.db.session import get_session
from control_plane.settings import settings

router = APIRouter(prefix="/agents", tags=["approvals"])
top_router = APIRouter(prefix="/approval-requests", tags=["approvals"])

DEFAULT_GRANT_DURATION_SECONDS = 60
MAX_GRANT_DURATION_SECONDS = 300


# --- helpers -----------------------------------------------------------------


def _normalize_dt(value: datetime | None) -> datetime | None:
    """Coerce a possibly-naive SQLite readback to UTC.

    Mirrors the same fix in api/grants.py: Postgres returns tz-aware
    datetimes, aiosqlite returns naive. The DB column is always stored
    in UTC, so naive readbacks get UTC tagged on before we compare
    against an aware `now`.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _approval_out(
    row: ToolApprovalRequest,
    *,
    grant_duration_seconds: int | None = None,
) -> ApprovalRequestOut:
    return ApprovalRequestOut(
        id=row.id,
        agent_id=row.agent_id,
        session_key=row.session_key,
        tool_name=row.tool_name,
        capability=row.capability,
        risk=row.risk,
        arguments_hash=row.arguments_hash,
        arguments_preview=row.arguments_preview or {},
        reason=row.reason,
        status=row.status,
        requested_at=row.requested_at,
        expires_at=_normalize_dt(row.expires_at) or row.expires_at,
        decided_at=_normalize_dt(row.decided_at),
        decided_by=row.decided_by,
        decision_reason=row.decision_reason,
        grant_id=row.grant_id,
        grant_duration_seconds=grant_duration_seconds,
    )


# --- nested under /agents ---------------------------------------------------


@router.post(
    "/{agent_id}/approval-requests",
    status_code=status.HTTP_201_CREATED,
    response_model=ApprovalRequestOut,
    responses={
        403: {"description": "agent not visible to caller"},
        404: {"description": "agent not found"},
    },
)
async def create_approval_request(
    agent_id: UUID,
    payload: ApprovalRequestIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    principal: Annotated[Principal, Depends(require_principal)],
) -> ApprovalRequestOut:
    """Idempotent on (agent, tool, capability, arguments_hash, pending)."""
    agent = await _load_agent_owned_by(session, agent_id, principal.customer_id)

    ttl_seconds = getattr(settings, "approval_ttl_seconds", 600)
    ttl_seconds = max(60, min(ttl_seconds, 3600))

    # Fast path: existing pending row for this exact tuple?
    existing = (
        await session.execute(
            select(ToolApprovalRequest).where(
                ToolApprovalRequest.agent_id == agent_id,
                ToolApprovalRequest.tool_name == payload.tool_name,
                ToolApprovalRequest.capability == payload.capability,
                ToolApprovalRequest.arguments_hash == payload.arguments_hash,
                ToolApprovalRequest.status == "pending",
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return _approval_out(existing)

    now = datetime.now(UTC)
    row = ToolApprovalRequest(
        id=uuid4(),
        agent_id=agent_id,
        session_key=payload.session_key,
        tool_name=payload.tool_name,
        capability=payload.capability,
        risk=payload.risk,
        arguments_hash=payload.arguments_hash,
        arguments_preview=payload.arguments_preview,
        reason=payload.reason,
        status="pending",
        requested_at=now,
        expires_at=now + timedelta(seconds=ttl_seconds),
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        # Lost a race against another concurrent SDK retry. Roll back and
        # fetch the winner's row.
        await session.rollback()
        existing = (
            await session.execute(
                select(ToolApprovalRequest).where(
                    ToolApprovalRequest.agent_id == agent_id,
                    ToolApprovalRequest.tool_name == payload.tool_name,
                    ToolApprovalRequest.capability == payload.capability,
                    ToolApprovalRequest.arguments_hash == payload.arguments_hash,
                    ToolApprovalRequest.status == "pending",
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="approval race: failed to resolve existing row",
            )
        return _approval_out(existing)
    await session.refresh(row)
    return _approval_out(row)


# --- top-level approval-requests routes ------------------------------------


@top_router.get("", response_model=list[ApprovalRequestOut])
async def list_approval_requests(
    session: Annotated[AsyncSession, Depends(get_session)],
    principal: Annotated[Principal, Depends(require_operator)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    agent_id: Annotated[UUID | None, Query()] = None,
) -> list[ApprovalRequestOut]:
    """Operator-only listing, scoped to the caller's customer.

    Newest first; supports `status=pending` (Dashboard badge query) and
    `agent_id` (drill-down from the agents page).
    """
    stmt = (
        select(ToolApprovalRequest)
        .join(Agent, Agent.id == ToolApprovalRequest.agent_id)
        .where(Agent.customer_id == principal.customer_id)
        .order_by(ToolApprovalRequest.requested_at.desc())
    )
    if status_filter is not None:
        if status_filter not in {"pending", "approved", "denied", "expired"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"unknown status filter: {status_filter!r}",
            )
        stmt = stmt.where(ToolApprovalRequest.status == status_filter)
    if agent_id is not None:
        stmt = stmt.where(ToolApprovalRequest.agent_id == agent_id)
    rows = (await session.execute(stmt)).scalars().all()
    return [_approval_out(r) for r in rows]


@top_router.get("/{approval_id}", response_model=ApprovalRequestOut)
async def get_approval_request(
    approval_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    principal: Annotated[Principal, Depends(require_principal)],
) -> ApprovalRequestOut:
    stmt = (
        select(ToolApprovalRequest, Agent)
        .join(Agent, Agent.id == ToolApprovalRequest.agent_id)
        .where(
            ToolApprovalRequest.id == approval_id,
            Agent.customer_id == principal.customer_id,
        )
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="approval request not found",
        )
    approval: ToolApprovalRequest = row[0]
    return _approval_out(approval)


async def _load_approval_owned(
    session: AsyncSession,
    approval_id: UUID,
    customer_id: UUID,
) -> ToolApprovalRequest:
    stmt = (
        select(ToolApprovalRequest, Agent)
        .join(Agent, Agent.id == ToolApprovalRequest.agent_id)
        .where(
            ToolApprovalRequest.id == approval_id,
            Agent.customer_id == customer_id,
        )
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="approval request not found",
        )
    return row[0]


@top_router.post(
    "/{approval_id}/approve",
    response_model=ApprovalRequestOut,
    responses={
        403: {"description": "operator role required"},
        404: {"description": "approval not found"},
        409: {"description": "approval already decided or expired"},
    },
)
async def approve_approval_request(
    approval_id: UUID,
    payload: ApprovalDecisionIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    principal: Annotated[Principal, Depends(require_operator)],
) -> ApprovalRequestOut:
    """Approve and create a runtime grant for `tool_scope_violation`.

    The grant is short-lived (default 60s, capped at 300s); the bridge
    splices it into `state.grants` on the next call so `apply_grants()`
    demotes the resulting Terminal into a Warning, allowing the tool
    to run. The grant is the runtime surface; the approval row is the
    audit surface — both are kept independently.
    """
    approval = await _load_approval_owned(
        session, approval_id, principal.customer_id
    )
    if approval.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"approval is {approval.status!r}, cannot approve",
        )

    now = datetime.now(UTC)
    if _normalize_dt(approval.expires_at) <= now:
        approval.status = "expired"
        approval.decided_at = now
        approval.decision_reason = "expired before decision"
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="approval expired",
        )

    duration = min(
        getattr(settings, "approval_grant_duration_seconds", DEFAULT_GRANT_DURATION_SECONDS),
        MAX_GRANT_DURATION_SECONDS,
    )

    grant = ApoptosisGrant(
        id=uuid4(),
        agent_id=approval.agent_id,
        symptoms=[SymptomType.TOOL_SCOPE_VIOLATION.value],
        reason=f"approval:{approval.id}",
        issued_by=principal.api_key_id,
        expires_at=now + timedelta(seconds=duration),
    )
    session.add(grant)
    await session.flush()  # populate grant.id without committing yet

    approval.status = "approved"
    approval.decided_at = now
    approval.decided_by = principal.api_key_id
    approval.decision_reason = payload.decision_reason or None
    approval.grant_id = grant.id
    await session.commit()
    await session.refresh(approval)
    return _approval_out(approval, grant_duration_seconds=duration)


@top_router.post(
    "/{approval_id}/deny",
    response_model=ApprovalRequestOut,
    responses={
        403: {"description": "operator role required"},
        404: {"description": "approval not found"},
        409: {"description": "approval already decided"},
    },
)
async def deny_approval_request(
    approval_id: UUID,
    payload: ApprovalDecisionIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    principal: Annotated[Principal, Depends(require_operator)],
) -> ApprovalRequestOut:
    """Deny an approval request. No runtime grant is created."""
    approval = await _load_approval_owned(
        session, approval_id, principal.customer_id
    )
    if approval.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"approval is {approval.status!r}, cannot deny",
        )
    now = datetime.now(UTC)
    approval.status = "denied"
    approval.decided_at = now
    approval.decided_by = principal.api_key_id
    approval.decision_reason = payload.decision_reason or None
    await session.commit()
    await session.refresh(approval)
    return _approval_out(approval)