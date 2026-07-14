"""Thin adapter: Hermes hook payloads → WatcherState mutations.

Each function in this module translates one Hermes lifecycle event into the
appropriate ``WatcherState`` mutation (record_tool_call, record_llm_call, etc.)
and then runs the Hermeskill checks, returning a kill verdict if apoptosis fires.

These are **pure functions over state** — they do not talk to the control
plane, do not raise, and do not side-effect outside the passed ``state``. The
plugin layer (``plugin.py``) owns control-plane interaction and translates a
kill verdict into Hermes' block directive.

Hermes hook payload shapes (v0.14, from ``hermes_cli/hooks.py::_DEFAULT_PAYLOADS``):

    pre_tool_call(*, tool_name, args, session_id, task_id, tool_call_id)
    post_tool_call(*, tool_name, args, session_id, task_id, tool_call_id,
                   result, duration_ms)
    pre_llm_call(*, session_id, user_message, conversation_history,
                 is_first_turn, model, platform)
    post_api_request(*, session_id, task_id, platform, model, provider,
                 base_url, api_mode, api_call_count, api_duration,
                 finish_reason, message_count, response_model,
                 usage, assistant_content_chars, assistant_tool_call_count)
    on_session_end(*, session_id)

We register against ``post_api_request`` rather than ``post_llm_call`` because
the canonical ``post_llm_call`` payload carries no token-usage information
in v0.14 — usage lands on ``post_api_request.usage``.

v1 — interactive tool approval bridge
------------------------------------

When the bridge detects an APPROVAL_REQUIRED decision it returns an
``ApprovalDirective`` to the plugin instead of letting the call fall through
to the existing tool-scope check. The runtime authoriser is the existing
``apoptosis_grants`` mechanism:

  1. SDK creates a pending approval row, returns ``ApprovalDirective(kind=BLOCK)``
     so Hermes refuses the tool but the session stays alive.
  2. Operator approves in Dashboard → server creates a short-lived grant
     for `tool_scope_violation` and links it via `grant_id`.
  3. SDK fetches the (now-approved) approval row, splices the runtime
     grant into `state.grants` via ``apply_grants``, and lets the tool run.

The bridge does NOT speak HTTP — that's the ``ApprovalService``'s job.
This keeps the bridge framework-agnostic and unit-testable.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from hermeskill.approval import (
    ApprovalDenied,
    ApprovalService,
    ApprovalUnavailable,
    grant_dict_from_approval,
)
from hermeskill.capability import (
    CapabilityResolver,
    ToolCapabilityMap,
    redact_arguments,
)
from hermeskill.checks import (
    Steer,
    Terminal,
    Warning,
    apply_grants,
    check_tool_scope,
    run_all,
)
from hermeskill.types import (
    ApprovalRequestIn,
    ApprovalRequestOut,
    ApprovalStatus,
)
from hermeskill.watcher import WatcherState

logger = logging.getLogger("hermeskill_hermes.bridge")

# Agents for which check execution has already raised once. We log the first
# failure with a full traceback, then stay silent for that agent so a
# reliably-broken check (e.g. a malformed policy) can't flood the log at every
# tool boundary. Only ever gains an entry on the pathological path.
_CHECK_FAILURE_LOGGED: set[UUID] = set()


def _run_checks_failopen(
    state: WatcherState,
    seed: list[Terminal | Warning | Steer] | None = None,
) -> list[Terminal | Warning | Steer]:
    """Run `run_all` + `apply_grants`, but never raise.

    The module contract is that these functions "do not raise"; this enforces
    it. A bug in a check must not propagate out of a Hermes hook (Hermes would
    log it, drop our verdict, and run the tool — silently disabling supervision
    on every call). On failure we degrade to **fail-open** for this one call
    (no new verdict) rather than crashing the agent, and log once per agent.

    ``seed`` carries verdicts already computed by the caller (e.g. a tool-scope
    Terminal) so they survive even if `run_all` itself throws.
    """
    verdicts: list[Terminal | Warning | Steer] = list(seed) if seed else []
    try:
        verdicts.extend(run_all(state, state.policy))
    except Exception:
        _log_check_failure_once(state, "run_all")
    try:
        return apply_grants(verdicts, state.grants)
    except Exception:
        _log_check_failure_once(state, "apply_grants")
        return verdicts


def _log_check_failure_once(state: WatcherState, where: str) -> None:
    if state.agent_id in _CHECK_FAILURE_LOGGED:
        return
    _CHECK_FAILURE_LOGGED.add(state.agent_id)
    logger.exception(
        "bridge: check execution (%s) raised for agent %s; supervision "
        "degraded to fail-open for this call. Logged once per agent.",
        where,
        state.agent_id,
    )


def canonical_arguments_hash(args: Any) -> str:
    """Stable SHA-256 hex digest of the tool arguments.

    Used as the idempotency key for the approval row. The bridge must
    compute the same hash for the same arguments across retries, so
    the canonical form is: ``json.dumps(args, sort_keys=True, default=str,
    separators=(',', ':'))`` then ``sha256``.
    """
    canonical = json.dumps(
        args,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class ApprovalDirective:
    """Outcome of the approval-bridge phase, separate from the sync verdicts.

    Kinds:
      * ``PASS`` — approval not required, or already approved and runtime
        grant has been spliced into ``state.grants``. Sync verdicts are
        returned alongside and should be applied normally.
      * ``BLOCK`` — the call must be refused. ``message`` is the user-facing
        text. ``session_lost`` is False for v1 (we never kill on approval
        flow); True only for explicit operator deny (which the plugin
        surfaces as a corrective nudge, not apoptosis).

    The plugin turns ``BLOCK`` into Hermes' ``{"action": "block", ...}``
    directive. PASS is the empty case — no directive needed.
    """

    kind: str  # "pass" | "block"
    message: str = ""
    approval_id: str | None = None
    pending: bool = False

    @classmethod
    def pass_(cls) -> "ApprovalDirective":
        return cls(kind="pass")

    @classmethod
    def block(
        cls,
        message: str,
        *,
        approval_id: str | None = None,
        pending: bool = False,
    ) -> "ApprovalDirective":
        return cls(kind="block", message=message, approval_id=approval_id, pending=pending)


def on_pre_tool_call(
    state: WatcherState,
    tool_name: str,
    args: Any,
) -> list[Terminal | Warning | Steer]:
    """Pre-tool boundary checkpoint.

    1. Tool-scope check — fires BEFORE the tool runs (scope violation is
       recorded and the Terminal is returned for the caller to act on).
    2. Record the call (loop ring buffer + event queue).
    3. Run state checks (loop, cost, wall-clock).

    Returns all non-Healthy verdicts (with grants applied). An empty list
    means all checks passed. The caller (plugin.py) translates a Terminal into
    Hermes' block directive (kill) and a Steer into a corrective block (nudge,
    no kill).
    """
    verdicts: list[Terminal | Warning | Steer] = []

    # Scope check runs first — before recording, so a scope violation
    # doesn't pollute the loop ring buffer with tools we shouldn't be
    # tracking. Must run before record_tool_call for this ordering to hold.
    scope = check_tool_scope(tool_name, state.policy)
    if isinstance(scope, (Terminal, Warning)):
        verdicts.append(scope)

    try:
        state.record_tool_call(tool_name, args)
    except Exception:
        logger.exception("bridge.on_pre_tool_call: failed to record tool call")

    all_verdicts = _run_checks_failopen(state, seed=verdicts)

    for v in all_verdicts:
        if isinstance(v, Terminal):
            severity = "terminal"
        elif isinstance(v, Steer):
            severity = "steer"
        else:
            severity = "warning"
        try:
            state.record_symptom(
                symptom=v.symptom,
                severity=severity,
                reason=v.reason,
                detail=v.detail,
            )
        except Exception:
            logger.exception("bridge.on_pre_tool_call: failed to record symptom")

        if isinstance(v, Terminal) and not state.terminate_requested:
            state.request_termination(v.reason)
            logger.warning(
                "hermeskill: agent %s entering apoptosis: %s (%s)",
                state.agent_id,
                v.symptom.value,
                v.reason,
            )
        elif isinstance(v, Steer):
            # Soft intervention: count it, but do NOT terminate. The plugin
            # turns this into a corrective block directive; the session lives.
            state.steer_count += 1
            logger.info(
                "hermeskill: agent %s steered (%s): %s",
                state.agent_id,
                v.symptom.value,
                v.reason,
            )

    return all_verdicts


async def evaluate_tool_approval(
    state: WatcherState,
    tool_name: str,
    args: Any,
    *,
    capability_resolver: CapabilityResolver | None,
    tool_map: ToolCapabilityMap | None,
    approval_service: ApprovalService | None,
    interactive_approvals_enabled: bool,
    session_key: str | None,
    pending_approval_id: str | None = None,
) -> tuple[ApprovalDirective, list[Terminal | Warning | Steer]]:
    """Run the approval phase, returning (directive, sync_verdicts).

    ``directive.kind == "pass"`` → the call should be allowed to proceed
    subject to the returned sync verdicts. ``directive.kind == "block"``
    → the plugin returns a Hermes block directive with ``directive.message``;
    the sync verdicts must still be returned for symptom recording but
    any tool-scope Terminal should be dropped (we don't kill the agent
    just because it tried a high-risk tool).

    ``pending_approval_id`` is the plugin's tracked id from the previous
    retry — when present we short-circuit the create path with a fetch,
    matching the server's idempotency on the (agent, tool, capability,
    arguments_hash) tuple.
    """
    sync_verdicts = on_pre_tool_call(state, tool_name, args)

    if not interactive_approvals_enabled:
        return ApprovalDirective.pass_(), sync_verdicts
    if state.terminate_requested:
        return ApprovalDirective.pass_(), sync_verdicts
    if capability_resolver is None or tool_map is None or approval_service is None:
        return ApprovalDirective.pass_(), sync_verdicts

    capability = tool_map.capability_for_tool(tool_name)
    if capability is None:
        return ApprovalDirective.pass_(), sync_verdicts

    profile = state.policy.name or "default"
    result = capability_resolver.check(profile=profile, capability=capability)
    if result.decision.value != "APPROVAL_REQUIRED":
        return ApprovalDirective.pass_(), sync_verdicts

    cap_item = capability_resolver.registry.get(capability)
    if cap_item is None:
        return ApprovalDirective.pass_(), sync_verdicts

    args_hash = canonical_arguments_hash(args)
    payload = ApprovalRequestIn(
        tool_name=tool_name,
        capability=capability,
        risk=cap_item.risk,
        arguments_hash=args_hash,
        arguments_preview=redact_arguments(args),
        session_key=session_key,
        reason=(
            f"capability {capability!r} requires operator approval "
            f"(profile={profile})"
        ),
    )

    try:
        if pending_approval_id:
            try:
                approval = await approval_service.get_approval(pending_approval_id)
            except (KeyError, Exception):  # stale id; create fresh
                approval = await approval_service.request_approval(
                    agent_id=state.agent_id, payload=payload
                )
        else:
            approval = await approval_service.request_approval(
                agent_id=state.agent_id, payload=payload
            )
    except ApprovalUnavailable as exc:
        # Fail closed but do NOT kill. Keep any sync verdicts that aren't
        # tool-scope Terminals (so loop / cost / wall-clock still surface)
        # and tell the plugin to return a non-terminating block directive.
        pruned = _drop_tool_scope_terminals(sync_verdicts)
        return (
            ApprovalDirective.block(
                message=(
                    "hermeskill: this tool call needs operator approval, but "
                    "the approval service is unreachable. The call has been "
                    "blocked; the session is alive. "
                    f"(reason: {exc})"
                ),
            ),
            pruned,
        )

    if approval.status == ApprovalStatus.APPROVED:
        grant_dict = grant_dict_from_approval(
            approval,
            duration_seconds=getattr(
                approval_service, "grant_duration_seconds", 60
            ),
        )
        state.grants = [
            g for g in state.grants if g.get("id") != grant_dict["id"]
        ]
        state.grants.insert(0, grant_dict)
        final = apply_grants(sync_verdicts, state.grants)
        return ApprovalDirective.pass_(), final

    if approval.status == ApprovalStatus.DENIED:
        pruned = _drop_tool_scope_terminals(sync_verdicts)
        return (
            ApprovalDirective.block(
                message=(
                    f"hermeskill: operator denied approval for {tool_name} "
                    f"(approval_id={approval.id}); do not retry this call. "
                    "Use a different tool or different arguments instead."
                ),
                approval_id=str(approval.id),
                pending=False,
            ),
            pruned,
        )

    # Pending — first arrival or retry. Block, but keep the session alive.
    pruned = _drop_tool_scope_terminals(sync_verdicts)
    return (
        ApprovalDirective.block(
            message=(
                f"hermeskill: this tool call needs operator approval before it can run. "
                f"The current call has been blocked; the session is alive. "
                f"Approval request: {approval.id}. "
                "After approval, retry with the exact same arguments; "
                "do not change the tool or the arguments to bypass the gate."
            ),
            approval_id=str(approval.id),
            pending=True,
        ),
        pruned,
    )


def _drop_tool_scope_terminals(
    verdicts: list[Terminal | Warning | Steer],
) -> list[Terminal | Warning | Steer]:
    """Drop tool-scope Terminals; keep everything else (Warning, Steer, etc.).

    The approval flow never wants to flip ``terminate_requested`` — that's
    the operator's call, not a symptom. If the tool-scope check fires, we
    treat it as a transient block until the operator decides.
    """
    from hermeskill.types import SymptomType
    return [
        v for v in verdicts
        if not (isinstance(v, Terminal) and v.symptom == SymptomType.TOOL_SCOPE_VIOLATION)
    ]


def on_post_tool_call(
    state: WatcherState,
    tool_name: str,
    args: Any,
    result: Any,
) -> None:
    """Post-tool — record outcome; run checks again (cost/wall_clock may have
    ticked while the tool ran)."""
    try:
        state.record_lifecycle("tool_end", tool=tool_name)
    except Exception:
        logger.exception("bridge.on_post_tool_call: failed to record")

    verdicts = _run_checks_failopen(state)
    for v in verdicts:
        if isinstance(v, Terminal) and not state.terminate_requested:
            state.request_termination(v.reason)
            logger.warning(
                "hermeskill: agent %s entering apoptosis post-tool: %s (%s)",
                state.agent_id,
                v.symptom.value,
                v.reason,
            )


def on_pre_llm_call(state: WatcherState, model: str) -> None:
    """Pre-LLM — lifecycle marker. Token info lands on post_api_request."""
    try:
        state.record_lifecycle("llm_start", model=model)
    except Exception:
        logger.exception("bridge.on_pre_llm_call: failed to record")


def on_post_api_request(
    state: WatcherState,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """Post-API-request — update token/cost counters from the ``usage`` dict
    Hermes carries on this hook; run checks."""
    try:
        state.record_llm_call(model, input_tokens, output_tokens)
    except Exception:
        logger.exception("bridge.on_post_api_request: failed to record")

    verdicts = _run_checks_failopen(state)
    for v in verdicts:
        if isinstance(v, Terminal) and not state.terminate_requested:
            state.request_termination(v.reason)
            logger.warning(
                "hermeskill: agent %s entering apoptosis post-api-request: %s (%s)",
                state.agent_id,
                v.symptom.value,
                v.reason,
            )


def on_session_end(state: WatcherState) -> None:
    """Session teardown — record lifecycle step; the plugin layer flushes
    the event queue and tears down the background worker."""
    try:
        state.record_lifecycle("session_end")
        state.record_shutdown_step("hermes_session_ended")
    except Exception:
        logger.exception("bridge.on_session_end: failed to record")