"""Tests for the ApprovalService abstraction + canonical-args hashing.

Covers:
  * `InMemoryApprovalService` idempotency on the
    (agent, session, tool, capability, arguments_hash) tuple
  * `canonical_arguments_hash` stability across key-ordering noise
  * `redact_arguments` covers every sensitive key
  * `redact_arguments` scrubs inline secret patterns inside string values
  * `HTTPApprovalService` is the production binding (interface conformance)
  * `grant_dict_from_approval` builds the runtime grant the bridge splices
    into `state.grants`
"""

from __future__ import annotations

import pytest
from uuid import uuid4

from hermeskill.approval import (
    ApprovalService,
    HTTPApprovalService,
    InMemoryApprovalService,
    grant_dict_from_approval,
)
from hermeskill.capability import redact_arguments
from hermeskill.types import (
    ApprovalDecisionIn,
    ApprovalRequestIn,
    ApprovalStatus,
)
from hermeskill_hermes.bridge import canonical_arguments_hash


def _payload(**overrides) -> ApprovalRequestIn:
    base: dict = dict(
        tool_name="terminal",
        capability="shell.execution",
        risk="medium",
        arguments_hash="0" * 64,
        arguments_preview={},
        session_key="sk-1",
        reason="test",
    )
    base.update(overrides)
    return ApprovalRequestIn(**base)


# --- canonical_arguments_hash ---------------------------------------------


def test_canonical_arguments_hash_stable_across_key_order():
    a = canonical_arguments_hash({"path": "/tmp", "cmd": "ls"})
    b = canonical_arguments_hash({"cmd": "ls", "path": "/tmp"})
    assert a == b
    assert len(a) == 64  # SHA-256 hex


def test_canonical_arguments_hash_changes_with_value():
    a = canonical_arguments_hash({"path": "/tmp", "cmd": "ls"})
    b = canonical_arguments_hash({"path": "/tmp", "cmd": "ls -la"})
    assert a != b


def test_canonical_arguments_hash_handles_nested():
    a = canonical_arguments_hash({"a": {"x": 1, "y": [1, 2]}})
    b = canonical_arguments_hash({"a": {"y": [1, 2], "x": 1}})
    assert a == b


# --- redaction -------------------------------------------------------------


def test_redact_sensitive_keys():
    out = redact_arguments(
        {
            "password": "hunter2",
            "api_key": "sk-abcdef0123456789abcdef",
            "token": "Bearer xyz",
            "username": "alice",
            "data": {"authorization": "Basic xyz"},
        }
    )
    assert out["password"] == "***REDACTED***"
    assert out["api_key"] == "***REDACTED***"
    assert out["token"] == "***REDACTED***"
    assert out["username"] == "alice"  # not sensitive
    assert out["data"]["authorization"] == "***REDACTED***"


def test_redact_inline_secrets_in_strings():
    out = redact_arguments(
        {"command": "curl -H 'Authorization: Bearer ghp_abcdefghijklmnopqrstuvwxyz1234567890' https://api.example.com"}
    )
    # Inline scrub
    assert "ghp_abcdefghijklmnopqrstuvwxyz1234567890" not in out["command"]
    assert "***REDACTED***" in out["command"]


def test_redact_does_not_mutate_input():
    original = {"password": "secret"}
    _ = redact_arguments(original)
    assert original["password"] == "secret"


# --- InMemoryApprovalService ----------------------------------------------


@pytest.mark.asyncio
async def test_in_memory_create_is_idempotent():
    svc = InMemoryApprovalService()
    agent = uuid4()
    payload = _payload(arguments_hash=canonical_arguments_hash({"cmd": "ls"}))
    a1 = await svc.request_approval(agent_id=agent, payload=payload)
    a2 = await svc.request_approval(agent_id=agent, payload=payload)
    assert a1.id == a2.id
    assert a1.status == ApprovalStatus.PENDING


@pytest.mark.asyncio
async def test_in_memory_different_args_creates_new():
    svc = InMemoryApprovalService()
    agent = uuid4()
    p1 = _payload(arguments_hash=canonical_arguments_hash({"cmd": "ls"}))
    p2 = _payload(arguments_hash=canonical_arguments_hash({"cmd": "rm"}))
    a1 = await svc.request_approval(agent_id=agent, payload=p1)
    a2 = await svc.request_approval(agent_id=agent, payload=p2)
    assert a1.id != a2.id


@pytest.mark.asyncio
async def test_in_memory_get_after_create():
    svc = InMemoryApprovalService()
    agent = uuid4()
    payload = _payload()
    row = await svc.request_approval(agent_id=agent, payload=payload)
    fetched = await svc.get_approval(row.id)
    assert fetched.id == row.id


@pytest.mark.asyncio
async def test_in_memory_decide_approve_marks_approved_and_grants():
    svc = InMemoryApprovalService()
    agent = uuid4()
    payload = _payload()
    row = await svc.request_approval(agent_id=agent, payload=payload)
    updated = await svc.decide(
        row.id,
        approve=True,
        decision=ApprovalDecisionIn(decision_reason="ok"),
    )
    assert updated.status == ApprovalStatus.APPROVED
    assert updated.grant_id is not None
    assert updated.decision_reason == "ok"


@pytest.mark.asyncio
async def test_in_memory_decide_deny_marks_denied_no_grant():
    svc = InMemoryApprovalService()
    agent = uuid4()
    payload = _payload()
    row = await svc.request_approval(agent_id=agent, payload=payload)
    updated = await svc.decide(
        row.id,
        approve=False,
        decision=ApprovalDecisionIn(decision_reason="no"),
    )
    assert updated.status == ApprovalStatus.DENIED
    assert updated.grant_id is None


@pytest.mark.asyncio
async def test_in_memory_fail_next_raises_unavailable():
    svc = InMemoryApprovalService()
    svc.fail_next = True
    payload = _payload()
    from hermeskill.approval import ApprovalUnavailable
    with pytest.raises(ApprovalUnavailable):
        await svc.request_approval(agent_id=uuid4(), payload=payload)


@pytest.mark.asyncio
async def test_in_memory_runtime_grant_dict_shape():
    svc = InMemoryApprovalService()
    payload = _payload()
    row = await svc.request_approval(agent_id=uuid4(), payload=payload)
    svc.test_mark_approved(row.id)
    approved = await svc.get_approval(row.id)
    g = grant_dict_from_approval(approved, duration_seconds=60)
    assert g["symptoms"] == ["tool_scope_violation"]
    assert g["reason"] == f"approval:{approved.id}"
    assert "expires_at" in g


# --- Abstract conformance --------------------------------------------------


def test_http_and_inmemory_satisfy_protocol():
    # Runtime check: both implementations satisfy ApprovalService's shape.
    assert issubclass(HTTPApprovalService, ApprovalService)
    assert issubclass(InMemoryApprovalService, ApprovalService)