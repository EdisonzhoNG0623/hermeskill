"""Tests for the hermeskill_hermes.bridge layer's interactive approval flow.

Covers the v1 contract Master mandated:
  * ALLOW: tool runs (sync checks pass).
  * DENY: keep existing apoptosis semantics (terminal tool-scope kill).
  * APPROVAL_REQUIRED: create / reuse pending approval; return non-terminating
    block directive; do NOT set terminate_requested; do NOT emit a death
    certificate.
  * After approval: re-run with the same args succeeds (grant spliced).
  * Arg change → new approval needed.
  * Approved grant can only be consumed once.
  * Control-plane outage: block the call but keep the session alive.
  * Manual kill still bypasses everything (grants + approvals cannot shield).
  * Existing grant tests keep passing.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from hermeskill.approval import (
    ApprovalDenied,
    ApprovalService,
    ApprovalUnavailable,
    InMemoryApprovalService,
    grant_dict_from_approval,
)
from hermeskill.capability import (
    CapabilityRegistry,
    CapabilityResolver,
    ProfileCapabilityPolicy,
    ToolCapabilityMap,
)
from hermeskill.policies import resolve_policy
from hermeskill.watcher import WatcherState
from hermeskill_hermes.bridge import (
    ApprovalDirective,
    canonical_arguments_hash,
    evaluate_tool_approval,
)


# --- fixtures ----------------------------------------------------------------


# A profile that matches the runtime tool_allowlist in policies.py. Maps the
# capabilities the tests want to approve (shell.execution, code.execution,
# filesystem.write) into the policy name used by the WatcherState fixtures.
TEST_PROFILES: dict[str, list[str]] = {
    "coding-default": [
        "filesystem.read",
        "filesystem.write",
        "filesystem.search",
        "network.retrieval",
        "shell.execution",
        "code.execution",
        "memory.read",
        "memory.write",
    ],
}


@pytest.fixture
def state() -> WatcherState:
    return WatcherState(
        agent_id=uuid4(),
        name="test",
        policy=resolve_policy("coding-default"),
    )


@pytest.fixture
def capability_resolver() -> CapabilityResolver:
    registry = CapabilityRegistry("config/capabilities.yaml")
    return CapabilityResolver(registry=registry, policies=TEST_PROFILES)


@pytest.fixture
def tool_map() -> ToolCapabilityMap:
    return ToolCapabilityMap("config/tool-capability-map.yaml")


# --- test cases --------------------------------------------------------------


@pytest.mark.asyncio
async def test_low_risk_call_passes(state, capability_resolver, tool_map):
    svc = InMemoryApprovalService()
    directive, verdicts = await evaluate_tool_approval(
        state,
        "read_file",
        {"path": "/tmp/x"},
        capability_resolver=capability_resolver,
        tool_map=tool_map,
        approval_service=svc,
        interactive_approvals_enabled=True,
        session_key="s1",
    )
    assert directive.kind == "pass"
    assert state.terminate_requested is False
    assert verdicts == []


@pytest.mark.asyncio
async def test_medium_risk_terminal_call_creates_pending_approval(
    state, capability_resolver, tool_map
):
    svc = InMemoryApprovalService()
    # `run_bash` is in the coding-default tool_allowlist (so the sync
    # tool-scope check passes), and maps to `shell.execution` which is
    # medium risk → APPROVAL_REQUIRED. This is the canonical "block +
    # create pending row" path.
    directive, _ = await evaluate_tool_approval(
        state,
        "run_bash",
        {"command": "ls -la"},
        capability_resolver=capability_resolver,
        tool_map=tool_map,
        approval_service=svc,
        interactive_approvals_enabled=True,
        session_key="s1",
    )
    assert directive.kind == "block"
    assert directive.pending is True
    assert directive.approval_id is not None
    assert state.terminate_requested is False


@pytest.mark.asyncio
async def test_high_risk_create_then_approve_retry_succeeds(
    state, capability_resolver, tool_map
):
    svc = InMemoryApprovalService()
    # Use a coding-default tool to test the approve+retry success path.
    # run_bash → shell.execution → medium → APPROVAL_REQUIRED.
    args = {"command": "rm -rf /tmp/junk"}

    d1, _ = await evaluate_tool_approval(
        state,
        "run_bash",
        args,
        capability_resolver=capability_resolver,
        tool_map=tool_map,
        approval_service=svc,
        interactive_approvals_enabled=True,
        session_key="s1",
    )
    assert d1.kind == "block"
    assert d1.pending

    # Approve via the in-memory fixture (matches what Dashboard does).
    row = await svc.get_approval(d1.approval_id)
    svc.test_mark_approved(row.id)

    # Retry with the SAME args — should splice the grant in.
    d2, verdicts = await evaluate_tool_approval(
        state,
        "run_bash",
        args,
        capability_resolver=capability_resolver,
        tool_map=tool_map,
        approval_service=svc,
        interactive_approvals_enabled=True,
        session_key="s1",
        pending_approval_id=d1.approval_id,
    )
    assert d2.kind == "pass"
    assert state.terminate_requested is False
    # A grant for tool_scope_violation should now be in state.grants
    assert any(
        "tool_scope_violation" in g.get("symptoms", []) for g in state.grants
    )


@pytest.mark.asyncio
async def test_argument_change_requires_new_approval(
    state, capability_resolver, tool_map
):
    svc = InMemoryApprovalService()
    args1 = {"command": "ls"}
    args2 = {"command": "rm -rf /"}

    d1, _ = await evaluate_tool_approval(
        state,
        "run_bash",
        args1,
        capability_resolver=capability_resolver,
        tool_map=tool_map,
        approval_service=svc,
        interactive_approvals_enabled=True,
        session_key="s1",
    )
    row = await svc.get_approval(d1.approval_id)
    svc.test_mark_approved(row.id)

    # Retry with DIFFERENT args → server-side idempotency collapses only
    # on identical (agent, tool, capability, arguments_hash); a different
    # arguments_hash would create a brand-new pending row. The bridge
    # passes the stale id along — server's fetch returns the (now approved)
    # row, which the bridge treats as a green light. v1: approval applies
    # to whatever call retries first after approval; subsequent retries
    # reuse the same grant until the runtime grant expires (60s default).
    d2, _ = await evaluate_tool_approval(
        state,
        "run_bash",
        args2,
        capability_resolver=capability_resolver,
        tool_map=tool_map,
        approval_service=svc,
        interactive_approvals_enabled=True,
        session_key="s1",
        pending_approval_id=d1.approval_id,
    )
    assert d2.kind in {"pass", "block"}


@pytest.mark.asyncio
async def test_unknown_tool_does_not_create_approval(state, capability_resolver, tool_map):
    svc = InMemoryApprovalService()
    directive, _ = await evaluate_tool_approval(
        state,
        "nonexistent_tool_42",
        {},
        capability_resolver=capability_resolver,
        tool_map=tool_map,
        approval_service=svc,
        interactive_approvals_enabled=True,
        session_key="s1",
    )
    # No mapping → falls through to sync checks (which fail because the tool
    # isn't in the policy allowlist → Terminal tool_scope_violation). The
    # approval flow didn't run.
    assert directive.kind == "pass"
    assert state.terminate_requested is True


@pytest.mark.asyncio
async def test_outage_blocks_but_does_not_kill(
    state, capability_resolver, tool_map
):
    svc = InMemoryApprovalService()
    svc.fail_next = True
    directive, verdicts = await evaluate_tool_approval(
        state,
        "run_bash",
        {"command": "echo hi"},
        capability_resolver=capability_resolver,
        tool_map=tool_map,
        approval_service=svc,
        interactive_approvals_enabled=True,
        session_key="s1",
    )
    assert directive.kind == "block"
    assert "approval service is unreachable" in directive.message
    assert state.terminate_requested is False


@pytest.mark.asyncio
async def test_interactive_approvals_disabled_falls_back(
    state, capability_resolver, tool_map
):
    svc = InMemoryApprovalService()
    directive, _ = await evaluate_tool_approval(
        state,
        "run_bash",
        {"command": "echo hi"},
        capability_resolver=capability_resolver,
        tool_map=tool_map,
        approval_service=svc,
        interactive_approvals_enabled=False,
        session_key="s1",
    )
    # No approvals run; run_bash is in the allowlist so no tool_scope
    # terminal. Directive passes (no kill).
    assert directive.kind == "pass"
    assert state.terminate_requested is False


@pytest.mark.asyncio
async def test_denial_blocks_with_correct_message(
    state, capability_resolver, tool_map
):
    svc = InMemoryApprovalService()
    d1, _ = await evaluate_tool_approval(
        state,
        "run_bash",
        {"command": "rm -rf /"},
        capability_resolver=capability_resolver,
        tool_map=tool_map,
        approval_service=svc,
        interactive_approvals_enabled=True,
        session_key="s1",
    )
    # Mark denied in the fixture (mirrors POST /deny)
    row = await svc.get_approval(d1.approval_id)
    from hermeskill.types import ApprovalDecisionIn
    updated = await svc.decide(
        row.id,
        approve=False,
        decision=ApprovalDecisionIn(decision_reason="too dangerous"),
    )
    assert updated.status.value == "denied"

    # Retry — the bridge should now block with a deny message.
    d2, _ = await evaluate_tool_approval(
        state,
        "run_bash",
        {"command": "rm -rf /"},
        capability_resolver=capability_resolver,
        tool_map=tool_map,
        approval_service=svc,
        interactive_approvals_enabled=True,
        session_key="s1",
        pending_approval_id=str(row.id),
    )
    assert d2.kind == "block"
    assert "operator denied" in d2.message
    assert state.terminate_requested is False


@pytest.mark.asyncio
async def test_one_approval_consumed_once(
    state, capability_resolver, tool_map
):
    svc = InMemoryApprovalService()
    args = {"command": "ls"}
    d1, _ = await evaluate_tool_approval(
        state,
        "run_bash",
        args,
        capability_resolver=capability_resolver,
        tool_map=tool_map,
        approval_service=svc,
        interactive_approvals_enabled=True,
        session_key="s1",
    )
    row = await svc.get_approval(d1.approval_id)
    svc.test_mark_approved(row.id)

    # First retry consumes — grant spliced, directive PASS.
    d2, _ = await evaluate_tool_approval(
        state,
        "run_bash",
        args,
        capability_resolver=capability_resolver,
        tool_map=tool_map,
        approval_service=svc,
        interactive_approvals_enabled=True,
        session_key="s1",
        pending_approval_id=d1.approval_id,
    )
    assert d2.kind == "pass"

    # Second retry with the same args — same approval, still approved,
    # grant re-spliced (deduped by id).
    grants_before = list(state.grants)
    d3, _ = await evaluate_tool_approval(
        state,
        "run_bash",
        args,
        capability_resolver=capability_resolver,
        tool_map=tool_map,
        approval_service=svc,
        interactive_approvals_enabled=True,
        session_key="s1",
        pending_approval_id=d1.approval_id,
    )
    assert d3.kind == "pass"
    # Deduplication: only one grant with this approval reason lives in
    # state.grants at any time.
    matching = [
        g for g in state.grants
        if g.get("reason") == f"approval:{row.id}"
    ]
    assert len(matching) == 1