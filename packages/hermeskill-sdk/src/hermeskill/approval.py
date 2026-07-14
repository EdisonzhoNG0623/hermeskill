"""ApprovalService: the bridge between the SDK and the Control Plane.

The bridge layer (hermeskill_hermes.bridge) calls into an `ApprovalService`
abstracted behind this Protocol — *not* a raw `HermeskillClient`. This keeps
the bridge framework-agnostic and testable: in unit tests we inject an
`InMemoryApprovalService`; in production the plugin wires in an
`HTTPApprovalService` that talks to the Control Plane over the same
authorization bearer as the rest of the SDK.

Concurrency model:
  * `request_approval` is idempotent — repeated calls with the same
    `(agent_id, session_key, tool_name, capability, arguments_hash)`
    tuple return the same `ApprovalRequestOut`. The SDK retries a
    blocked call until it sees `approved_at` populated server-side, so
    the server has to be tolerant of retry storms.
  * `apply_runtime_grant` is the success-side helper: when an approval
    is granted, the server creates a short-lived `apoptosis_grants`
    row for `tool_scope_violation`. The HTTP service surfaces that
    grant to the SDK as a dict the bridge can splice into
    `state.grants` without a heartbeat round-trip.

Failure semantics (encoded in `ApprovalUnavailable`):
  * When the Control Plane is unreachable AND a high-risk tool call
    needs approval, the service raises `ApprovalUnavailable`. The
    bridge catches it and returns a *non-terminating* block directive
    — the tool does not run, but the session stays alive. This is
    the v1 fail-closed-but-don't-kill behaviour Master mandated.
"""

from __future__ import annotations

import abc
import logging
from typing import Any
from uuid import UUID

from hermeskill.types import (
    ApprovalDecisionIn,
    ApprovalRequestIn,
    ApprovalRequestOut,
    ApprovalStatus,
)
from hermeskill.client import HermeskillClient, TransportError

logger = logging.getLogger("hermeskill.approval.service")

DEFAULT_GRANT_DURATION_SECONDS = 60


class ApprovalUnavailable(Exception):
    """Raised when the Control Plane is unreachable for an approval request.

    The bridge turns this into a non-terminating block directive: the
    tool is NOT executed, the session is NOT killed. The user-facing
    message asks the operator to confirm the Control Plane is healthy.
    """


class ApprovalDenied(Exception):
    """Operator explicitly denied this tool call.

    Bridge translates this into a *terminating* block directive (because
    a denied approval is an explicit operator decision). The agent
    should stop trying this tool; the session stays alive (different
    from apoptosis, which kills for symptom-level protection).
    """


class ApprovalService(abc.ABC):
    """Abstract interface used by the bridge layer."""

    @abc.abstractmethod
    async def request_approval(
        self,
        *,
        agent_id: UUID | str,
        payload: ApprovalRequestIn,
    ) -> ApprovalRequestOut:
        """Create or reuse a pending approval row.

        Idempotent on `(agent_id, tool_name, capability, arguments_hash)`.
        """

    @abc.abstractmethod
    async def get_approval(
        self,
        approval_id: UUID | str,
    ) -> ApprovalRequestOut:
        """Fetch current state of an approval (used by retry-after-block)."""

    @abc.abstractmethod
    async def decide(
        self,
        approval_id: UUID | str,
        *,
        approve: bool,
        decision: ApprovalDecisionIn,
    ) -> ApprovalRequestOut:
        """Operator action — approve or deny. Server-side only."""


class HTTPApprovalService(ApprovalService):
    """`ApprovalService` implementation backed by the Control Plane REST API.

    Uses the same `HermeskillClient` the rest of the SDK uses; auth +
    transport error mapping are reused via `_request`. Network failures
    raise `ApprovalUnavailable` so the bridge can apply fail-closed-
    but-don't-kill semantics consistently.
    """

    def __init__(
        self,
        client: HermeskillClient,
        *,
        grant_duration_seconds: int = DEFAULT_GRANT_DURATION_SECONDS,
    ) -> None:
        self._client = client
        self._grant_duration_seconds = grant_duration_seconds

    async def request_approval(
        self,
        *,
        agent_id: UUID | str,
        payload: ApprovalRequestIn,
    ) -> ApprovalRequestOut:
        body = payload.model_dump(mode="json")
        try:
            data = await self._client._request(
                "POST",
                f"/agents/{agent_id}/approval-requests",
                json=body,
            )
        except TransportError as exc:
            raise ApprovalUnavailable(str(exc)) from exc
        return ApprovalRequestOut.model_validate(data)

    async def get_approval(
        self,
        approval_id: UUID | str,
    ) -> ApprovalRequestOut:
        try:
            data = await self._client._request(
                "GET",
                f"/approval-requests/{approval_id}",
            )
        except TransportError as exc:
            raise ApprovalUnavailable(str(exc)) from exc
        return ApprovalRequestOut.model_validate(data)

    async def decide(
        self,
        approval_id: UUID | str,
        *,
        approve: bool,
        decision: ApprovalDecisionIn,
    ) -> ApprovalRequestOut:
        path = f"/approval-requests/{approval_id}/{'approve' if approve else 'deny'}"
        try:
            data = await self._client._request(
                "POST",
                path,
                json=decision.model_dump(mode="json"),
            )
        except TransportError as exc:
            raise ApprovalUnavailable(str(exc)) from exc
        return ApprovalRequestOut.model_validate(data)

    @property
    def grant_duration_seconds(self) -> int:
        return self._grant_duration_seconds


class InMemoryApprovalService(ApprovalService):
    """Test double — no HTTP, no Control Plane, deterministic decisions.

    Used by unit tests for the bridge and the plugin. The fixture wires
    `auto_decide` to either auto-approve or auto-deny so tests can drive
    every branch without spinning up Postgres.
    """

    def __init__(self) -> None:
        self._rows: dict[UUID, ApprovalRequestOut] = {}
        self._idempotency_index: dict[tuple[str, str, str, str, str], UUID] = {}
        self.auto_decide: str | None = None  # "approve" | "deny" | None
        self.decisions: list[tuple[UUID, bool, str]] = []
        self.fail_next: bool = False
        self.grant_duration_seconds: int = DEFAULT_GRANT_DURATION_SECONDS

    def _key(
        self,
        agent_id: UUID | str,
        payload: ApprovalRequestIn,
    ) -> tuple[str, str, str, str, str]:
        return (
            str(agent_id),
            payload.session_key or "",
            payload.tool_name,
            payload.capability,
            payload.arguments_hash,
        )

    async def request_approval(
        self,
        *,
        agent_id: UUID | str,
        payload: ApprovalRequestIn,
    ) -> ApprovalRequestOut:
        if self.fail_next:
            self.fail_next = False
            raise ApprovalUnavailable("simulated control plane outage")
        key = self._key(agent_id, payload)
        existing_id = self._idempotency_index.get(key)
        if existing_id is not None:
            return self._rows[existing_id]
        from datetime import UTC, datetime, timedelta
        from uuid import uuid4
        row = ApprovalRequestOut(
            id=uuid4(),
            agent_id=UUID(str(agent_id)),
            session_key=payload.session_key,
            tool_name=payload.tool_name,
            capability=payload.capability,
            risk=payload.risk,
            arguments_hash=payload.arguments_hash,
            arguments_preview=payload.arguments_preview,
            reason=payload.reason,
            status=ApprovalStatus.PENDING,
            requested_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
            decided_at=None,
            decided_by=None,
            decision_reason=None,
            grant_id=None,
            grant_duration_seconds=self.grant_duration_seconds,
        )
        self._rows[row.id] = row
        self._idempotency_index[key] = row.id
        return row

    async def get_approval(
        self,
        approval_id: UUID | str,
    ) -> ApprovalRequestOut:
        if self.fail_next:
            self.fail_next = False
            raise ApprovalUnavailable("simulated control plane outage")
        return self._rows[UUID(str(approval_id))]

    async def decide(
        self,
        approval_id: UUID | str,
        *,
        approve: bool,
        decision: ApprovalDecisionIn,
    ) -> ApprovalRequestOut:
        if self.fail_next:
            self.fail_next = False
            raise ApprovalUnavailable("simulated control plane outage")
        approval_id = UUID(str(approval_id))
        row = self._rows[approval_id]
        from datetime import UTC, datetime
        from uuid import uuid4
        new_status = "approved" if approve else "denied"
        grant_id = uuid4() if approve else None
        updated = row.model_copy(
            update={
                "status": ApprovalStatus.APPROVED if approve else ApprovalStatus.DENIED,
                "decided_at": datetime.now(UTC),
                "decided_by": uuid4(),
                "decision_reason": decision.decision_reason or None,
                "grant_id": grant_id,
            }
        )
        self._rows[approval_id] = updated
        self.decisions.append((approval_id, approve, decision.decision_reason or ""))
        return updated

    def test_mark_approved(
        self,
        approval_id: UUID,
        *,
        grant_id: UUID | None = None,
    ) -> None:
        """Test helper — pretend the operator just clicked approve."""
        from datetime import UTC, datetime
        from uuid import uuid4
        row = self._rows[approval_id]
        updated = row.model_copy(
            update={
                "status": ApprovalStatus.APPROVED,
                "decided_at": datetime.now(UTC),
                "decided_by": uuid4(),
                "decision_reason": "test fixture",
                "grant_id": grant_id or uuid4(),
            }
        )
        self._rows[approval_id] = updated

    def test_runtime_grant_dict(
        self,
        approval: ApprovalRequestOut,
    ) -> dict[str, Any]:
        """Return the runtime grant dict the bridge splices into state.grants.

        Mirrors the wire shape the SDK already consumes from
        `HeartbeatOut.active_grants` (see watcher.py:_heartbeat_one).
        """
        from datetime import UTC, datetime, timedelta
        return {
            "id": str(approval.grant_id) if approval.grant_id else "",
            "symptoms": ["tool_scope_violation"],
            "expires_at": (
                datetime.now(UTC) + timedelta(seconds=self.grant_duration_seconds)
            ).isoformat(),
            "reason": f"approval:{approval.id}",
        }


def grant_dict_from_approval(
    approval: ApprovalRequestOut,
    *,
    duration_seconds: int = DEFAULT_GRANT_DURATION_SECONDS,
) -> dict[str, Any]:
    """Build the runtime grant dict from an approved `ApprovalRequestOut`.

    The bridge calls this after seeing `status == "approved"` to splice the
    short-lived grant into `state.grants`. The dict shape is the same one
    `HeartbeatOut.active_grants` carries, so the existing `apply_grants()`
    consumes it with no changes.
    """
    from datetime import UTC, datetime, timedelta
    return {
        "id": str(approval.grant_id) if approval.grant_id else "",
        "symptoms": ["tool_scope_violation"],
        "expires_at": (
            datetime.now(UTC) + timedelta(seconds=duration_seconds)
        ).isoformat(),
        "reason": f"approval:{approval.id}",
    }