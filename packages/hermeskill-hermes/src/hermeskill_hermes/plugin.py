"""Hermes plugin hook handlers for Hermeskill.

This module wires the Hermes hook API to the Hermeskill apoptosis engine. One
``HermeskillPlugin`` instance is created per Hermes session by ``register()`` in
``__init__.py``.

Kill path (cooperative, via ``pre_tool_call`` block directive)
--------------------------------------------------------------

Hermes v0.14 hooks are non-blocking — the runtime catches ``Exception`` from
hook callbacks and logs it without crashing the agent (see
``hermes_cli/plugins.py::invoke_hook`` and the matching
``get_pre_tool_call_block_message`` consumer in ``agent/tool_executor.py``).

The canonical way for a plugin to refuse a tool call is to return a dict
from ``pre_tool_call``::

    {"action": "block", "message": "Reason the tool was blocked"}

Hermes wraps that message into a tool error response (``{"error": ...}``)
and feeds it to the LLM instead of running the tool. PR #26759 explicitly
describes this as the canonical interception path for "rate limiting,
security restrictions, approval workflows" — and apoptosis fits squarely
in that bucket.

Effect when Hermeskill fires:
  1. ``pre_tool_call`` notices ``state.terminate_requested`` is True
     (set earlier by a symptom check or the manual-kill poller)
  2. We return the block directive
  3. The agent reads "hermeskill: <reason>" as a tool error and the next
     LLM turn typically concludes the session ("I cannot continue;
     terminating") because every subsequent tool call also blocks
  4. When the agent's loop ends naturally, Hermes fires ``on_session_end``
     and we POST the death certificate

Steer path (soft intervention, same block primitive)
-----------------------------------------------------

Loop detection is graduated. Before a loop crosses the kill threshold, a
``Steer`` verdict (see ``hermeskill.checks``) is returned from
``bridge.on_pre_tool_call`` *without* setting ``terminate_requested``. The
plugin turns it into the same ``{"action": "block", ...}`` directive — but the
message asks the agent to change approach rather than to stop, so the one
looping call is refused while the session continues. If the agent keeps
repeating the identical call, the loop count climbs to ``max_loop_repeats`` and
the kill path above fires. ``state.steer_count`` tracks how many nudges landed
(surfaced in the live vitals snapshot).

Why block-only and not ``ctx.register_tool(override=True)`` with SystemExit:
  - block-directive is the documented and tested Hermes path; tool_override
    in v0.14 means "swap a tool's implementation" (per PR #26759) and would
    require us to fabricate a schema for every potentially-called tool
  - cooperative semantics match our SDK's "L1 cooperative termination"
    contract: we stop further harm immediately (no tool execution after
    kill) but let the agent's natural loop wind down
  - no SystemExit-across-thread weirdness

Background worker lifecycle
---------------------------

``register()`` calls ``hermeskill.watcher.ensure_worker_started(client)``.
This starts the shared per-process ``BackgroundWorker`` (heartbeats + event
drain) and the ``KillPendingPoller`` (manual-kill delivery). Both
singletons survive across Hermes sessions in the same process — safe
because they only reference the module-level ``_REGISTRY``.

On ``on_session_end``, the plugin calls ``BackgroundWorker.stop()`` to
flush remaining events before Hermes tears the session down.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import logging
from pathlib import Path
import sys
import threading
import time
from collections.abc import Coroutine
from typing import Any
from uuid import UUID, uuid4

from hermeskill.apoptosis import (
    build_death_certificate,
    build_kill_event_payload,
)
from hermeskill.approval import (
    ApprovalService,
    HTTPApprovalService,
)
from hermeskill.capability import (
    CapabilityRegistry,
    CapabilityResolver,
    ProfileCapabilityPolicy,
    ToolCapabilityMap,
)
from hermeskill.certificate import render_certificate, save_certificate
from hermeskill.checks import Steer
from hermeskill.client import HermeskillClient, TransportError
from hermeskill.policies import resolve_policy
from hermeskill.types import Policy
from hermeskill.vitals import (
    Status,
    delete_snapshot,
    snapshot_from_state,
    sweep_live_dir,
    write_snapshot,
)
from hermeskill.watcher import (
    BackgroundWorker,
    KillPendingPoller,
    WatcherState,
    ensure_worker_started,
    register_watcher,
    unregister_watcher,
)

from hermeskill_hermes.bridge import (
    ApprovalDirective,
    evaluate_tool_approval,
    on_post_api_request,
    on_post_tool_call,
    on_pre_llm_call,
)
from hermeskill_hermes.bridge import (
    on_session_end as bridge_on_session_end,
)

logger = logging.getLogger("hermeskill_hermes.plugin")

# Resolve configuration relative to the Hermeskill repository, never the
# caller's current working directory. In the editable production install:
#
#   <repo>/packages/hermeskill-hermes/src/hermeskill_hermes/plugin.py
#
# Path.parents[4] therefore resolves to <repo>.
_HERMESKILL_REPO_ROOT = Path(__file__).resolve().parents[4]
_HERMESKILL_CONFIG_DIR = _HERMESKILL_REPO_ROOT / "config"


def _hermeskill_config_path(filename: str) -> Path:
    """Return a required Hermeskill config file with a clear failure message."""
    path = _HERMESKILL_CONFIG_DIR / filename
    if not path.is_file():
        raise FileNotFoundError(
            f"Hermeskill configuration file not found: {path}"
        )
    return path


class _SessionLoop:
    """A dedicated asyncio event loop on its own daemon thread, alive for the
    whole Hermes session.

    Hermes drives our hooks **synchronously** — ``register()`` and
    ``on_session_end`` are plain function calls, not awaited. But Hermeskill's I/O
    (agent registration, the heartbeat/event-drain worker, the kill poller, and
    the death-cert POST) is all ``async`` and shares one ``httpx.AsyncClient``.

    An ``httpx.AsyncClient`` binds to the event loop that first drives it and
    cannot be reused from another loop. The original design called
    ``asyncio.run()`` once in ``register()`` and again in ``on_session_end()``;
    that opened two *different* loops, each closed on return, so:

      * the ``BackgroundWorker``, created via ``loop.create_task`` on the first
        (immediately-closed) loop, never ticked — heartbeats and event drains
        silently never ran during the session; and
      * the death-cert POST on the second loop reused the client whose
        connection pool belonged to the first, now-closed loop, raising
        ``RuntimeError: Event loop is closed``.

    Running one ``run_forever`` loop on a background thread for the session's
    lifetime fixes both: the worker actually runs, and every async call —
    including teardown — happens on the one loop that owns the client.
    """

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever,
            name="hermeskill-session-loop",
            daemon=True,
        )
        self._thread.start()

    def submit(self, coro: Coroutine[Any, Any, Any]) -> concurrent.futures.Future[Any]:
        """Schedule a coroutine on the session loop; return a concurrent Future."""
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def run(self, coro: Coroutine[Any, Any, Any], *, timeout: float | None = None) -> Any:
        """Schedule a coroutine and block the calling thread until it completes."""
        return self.submit(coro).result(timeout)

    def close(self) -> None:
        """Stop the loop and join its thread. Idempotent and best-effort."""
        if not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5.0)
        if not self._thread.is_alive() and not self._loop.is_closed():
            with contextlib.suppress(Exception):
                self._loop.close()


class HermeskillPlugin:
    """One per Hermes session. Owns the WatcherState lifecycle for that session."""

    def __init__(
        self,
        *,
        name: str,
        policy: str,
        metadata: dict[str, Any] | None = None,
        client: HermeskillClient,
        forced_offline: bool = False,
        local_cert: bool = True,
        live_vitals: bool = True,
        approval_service: ApprovalService | None = None,
        capability_resolver: CapabilityResolver | None = None,
        tool_map: ToolCapabilityMap | None = None,
        interactive_approvals_enabled: bool = False,
        approval_grant_duration_seconds: int = 60,
    ) -> None:
        self._client = client
        self._name = name
        self._policy_name = policy
        self._metadata = metadata or {}
        self._state: WatcherState | None = None
        self._loop_thread: _SessionLoop | None = None
        # forced_offline: no API key was configured, so skip every control-plane
        # call from the start (registration, worker, poller, death-cert POST) —
        # the client carries an empty key and must never hit the network.
        self._forced_offline = forced_offline
        # local_cert: render + save the death certificate locally on a kill.
        self._local_cert = local_cert
        # live_vitals: write the per-tick snapshot for `hermeskill monitor`.
        self._live_vitals = live_vitals

        # v1 — interactive tool approval bridge wiring. The plugin is the
        # only place that touches HTTP — the bridge layer just sees an
        # `ApprovalService` interface. Tests can inject an
        # `InMemoryApprovalService` without spinning up Postgres.
        if approval_service is None and not forced_offline:
            approval_service = HTTPApprovalService(
                client=client,
                grant_duration_seconds=approval_grant_duration_seconds,
            )
        self._approval_service = approval_service
        self._interactive_approvals_enabled = interactive_approvals_enabled

        # Capability resolver + tool map default to the workspace-shipped
        # YAMLs. Tests can inject their own; production relies on the
        # defaults living next to pyproject.toml.
        if capability_resolver is None:
            registry = CapabilityRegistry(
                _hermeskill_config_path("capabilities.yaml")
            )
            profile_policy = ProfileCapabilityPolicy(
                _hermeskill_config_path("profile-capabilities.yaml")
            )
            capability_resolver = CapabilityResolver(
                registry=registry,
                policies=profile_policy._profiles,  # type: ignore[attr-defined]
            )
        self._capability_resolver = capability_resolver
        self._tool_map = tool_map or ToolCapabilityMap(
            _hermeskill_config_path("tool-capability-map.yaml")
        )

        # Pending-approval tracker — keyed by agent_id; survives across
        # retries of the same blocked tool call so we don't spam the
        # server with duplicate creates. Plugin-owned (not WatcherState)
        # so we don't perturb the dataclass.
        self._pending_approval: dict[str, tuple[str, str]] = {}
        # value = (approval_id, arguments_hash)

    def start(self) -> None:
        """Synchronous entry point used by Hermes' ``register()``.

        Spins up the session loop thread and runs :meth:`setup` on it, blocking
        the calling thread until registration completes (or fails). Safe to call
        from a thread with no running event loop (Hermes' case) or one running a
        *different* loop — the work happens on our own loop, never the caller's.
        """
        self._loop_thread = _SessionLoop()
        self._loop_thread.run(self.setup())

    async def astart(self) -> None:
        """Async entry point for callers already inside a running event loop.

        Identical to :meth:`start` but awaits setup via ``wrap_future`` so the
        caller's loop is never blocked.
        """
        self._loop_thread = _SessionLoop()
        await asyncio.wrap_future(self._loop_thread.submit(self.setup()))

    async def setup(self) -> None:
        """Register the agent with the control plane and wire up the watcher.

        Fail-open on connectivity. If the control plane is unreachable at
        registration time we DO NOT abort — a safety supervisor that fails
        to load is the worst outcome, because Hermes' loader would then run
        the agent with zero hooks and zero supervision, silently. Instead we
        mint a local agent_id, wire the watcher anyway, and mark the session
        offline. Local symptom checks (loop / token_runaway / wall_clock /
        tool_scope) run entirely in-process and need no control plane; only
        operator visibility, manual kill, grants, and death-cert archival are
        degraded until the control plane returns.
        """
        resolved_policy = resolve_policy(self._policy_name)

        agent_id, offline = await self._register_agent(resolved_policy)

        state = WatcherState(
            agent_id=agent_id,
            name=self._name,
            policy=resolved_policy,
        )
        state.offline = offline
        # NB: no L2 watchdog is armed here. The SDK's `Watchdog` cancels the
        # agent's *asyncio task* from outside its loop — but Hermes' agent loop
        # is synchronous (it drives our hooks via plain `cb(**kwargs)` calls,
        # see hermes_cli/plugins.py::invoke_hook) and exposes no cancellable
        # task to arm against. So in the Hermes integration L1 (the cooperative
        # block directive below) is the enforcing layer, and ProcessSupervisor
        # (hard SIGTERM→SIGKILL) is the escape hatch for an agent wedged in
        # CPU-bound/sync code. `state.watchdog` stays None.
        register_watcher(state)
        # Clear this id's file (belt-and-suspenders; ids are per-session) and
        # sweep long-dead files from prior sessions so the live dir doesn't
        # accumulate — and so `hermeskill monitor` isn't shown a stale corpse.
        delete_snapshot(state.agent_id)
        sweep_live_dir()
        # The background worker + kill poller only talk to the control plane
        # (heartbeats, event drain, manual-kill delivery). Offline they can
        # never succeed and would log a connection-refused traceback every
        # few seconds, so don't boot them. In-process symptom checks run
        # independently — the kill path (L1 cooperative block) is unaffected.
        if not offline:
            ensure_worker_started(self._client)
        self._state = state

        state.record_lifecycle(
            "registered", agent_id=str(state.agent_id), offline=offline
        )
        # Emit the first snapshot immediately so `hermeskill monitor` shows the
        # agent the moment it registers, before the first tool/LLM boundary.
        self._write_vitals()
        if offline:
            logger.warning(
                "hermeskill: control plane unreachable; watching %r in LOCAL-ONLY "
                "mode (local id=%s, policy=%s) — symptom checks active, but "
                "operator visibility, manual kill, grants, and death-cert "
                "archival are unavailable until the control plane returns.",
                self._name,
                state.agent_id,
                resolved_policy.name,
            )
        else:
            logger.info(
                "hermeskill: watching %r (id=%s, policy=%s)",
                self._name,
                state.agent_id,
                resolved_policy.name,
            )

    def session_reset(self, *, session_id: str = "") -> None:
        """Rotate per-session supervision state after Hermes ``/new``.

        The plugin infrastructure remains alive: the shared HTTP client,
        background worker, kill poller, and session event loop are retained.
        Only the old WatcherState is finalized and replaced with a fresh one.
        """
        loop_thread = self._loop_thread
        if loop_thread is None:
            logger.warning(
                "hermeskill: session reset received without an active "
                "session loop; rebuilding it (session_id=%r)",
                session_id,
            )
            loop_thread = _SessionLoop()
            self._loop_thread = loop_thread

        try:
            loop_thread.run(
                self._reset_session_state(session_id=session_id),
                timeout=35.0,
            )
        except Exception:
            # A lifecycle hook must never crash the Hermes host. Keep the old
            # state active if rotation fails rather than leaving supervision off.
            logger.exception(
                "hermeskill: failed to rotate watcher on session reset "
                "(session_id=%r)",
                session_id,
            )

    async def _reset_session_state(self, *, session_id: str = "") -> None:
        """Finalize the current watcher and create a fresh watcher.

        This deliberately does not stop BackgroundWorker/KillPendingPoller,
        close the HTTP client, or tear down ``_SessionLoop``. Those resources
        belong to the plugin process and are closed only by ``session_finalize()``.
        """
        old_state = self._state

        # Register the replacement first. If control-plane registration fails
        # transiently, _register_agent() returns a local offline UUID, preserving
        # local supervision.
        resolved_policy = resolve_policy(self._policy_name)
        agent_id, offline = await self._register_agent(resolved_policy)

        new_state = WatcherState(
            agent_id=agent_id,
            name=self._name,
            policy=resolved_policy,
        )
        new_state.offline = offline

        # Finalize the old watcher only after a replacement is ready.
        if old_state is not None:
            old_state.record_lifecycle(
                "session_reset",
                session_id=session_id,
                replacement_agent_id=str(new_state.agent_id),
            )

            # Preserve the final audit snapshot. Do not generate a death
            # certificate: /new is a clean lifecycle transition, not a kill.
            previous_state = self._state
            try:
                self._state = old_state
                self._write_vitals(status="ended_clean")
            finally:
                self._state = previous_state

            if old_state.agent_id:
                unregister_watcher(old_state.agent_id)

        # Invocation-level approvals must never leak across /new.
        self._pending_approval.clear()

        register_watcher(new_state)
        delete_snapshot(new_state.agent_id)
        sweep_live_dir()

        self._state = new_state
        new_state.record_lifecycle(
            "registered_after_session_reset",
            agent_id=str(new_state.agent_id),
            previous_agent_id=(
                str(old_state.agent_id) if old_state is not None else None
            ),
            session_id=session_id,
            offline=offline,
        )
        self._write_vitals()

        logger.info(
            "hermeskill: watcher rotated on session reset "
            "(session_id=%r, old_agent=%s, new_agent=%s, offline=%s)",
            session_id,
            old_state.agent_id if old_state is not None else None,
            new_state.agent_id,
            offline,
        )

    async def _register_agent(self, policy: Policy) -> tuple[UUID, bool]:
        """Register with the control plane, falling back to local-only mode.

        Returns ``(agent_id, offline)``. On a transport failure (control
        plane down / unreachable) we mint a local UUID and return
        ``offline=True`` so :meth:`setup` can still wire all hooks. Other
        errors (auth, server 5xx) are NOT swallowed here — those signal a
        misconfiguration the operator must fix, not a transient outage.
        """
        # No API key configured → don't even attempt registration. Hitting a
        # reachable control plane with an empty key would 401 (AuthError),
        # which we deliberately DON'T swallow; forcing offline up front keeps
        # the keyless path clean.
        if self._forced_offline:
            return uuid4(), True
        try:
            registration = await self._client.register_agent(
                name=self._name,
                policy_name=policy.name,
                metadata=self._metadata,
            )
        except TransportError:
            return uuid4(), True
        return registration.agent_id, False

    # --- hook handlers -------------------------------------------------------

    def pre_tool_call(self, tool_name: str, args: Any) -> dict[str, str] | None:
        """Pre-tool checkpoint. Returns Hermes' block directive if kill is armed.

        Returning a dict with action="block" tells Hermes to refuse the tool
        call and surface the message as the tool's error result. Returning
        None lets the tool proceed normally.
        """
        if self._state is None:
            return None

        # Fast path: if kill is already armed, block before running checks.
        # Saves a bit of work and guarantees the directive shape is identical
        # across the "armed earlier" and "armed by this call's check" branches.
        if self._state.terminate_requested:
            return self._block_directive(
                self._state.terminate_reason or "hermeskill termination"
            )

        # v1 — interactive approval bridge. Runs the async evaluation on
        # the session loop and blocks until it returns. Returns one of:
        #   * (None, [])              → no approval needed, no verdicts → fall through
        #   * (None, [verdicts])     → approval already granted; verdicts to apply
        #   * (block_dict, pruned)   → call must be blocked (pending / denied / outage)
        agent_key = str(self._state.agent_id)
        pending = self._pending_approval.get(agent_key)
        pending_approval_id = pending[0] if pending else None
        directive = ApprovalDirective.pass_()
        verdicts: list = []
        if (
            self._loop_thread is not None
            and self._interactive_approvals_enabled
            and self._approval_service is not None
        ):
            try:
                directive, verdicts = self._loop_thread.run(
                    evaluate_tool_approval(
                        self._state,
                        tool_name,
                        args,
                        capability_resolver=self._capability_resolver,
                        tool_map=self._tool_map,
                        approval_service=self._approval_service,
                        interactive_approvals_enabled=self._interactive_approvals_enabled,
                        session_key=getattr(self._state, "session_key", None),
                        pending_approval_id=pending_approval_id,
                    ),
                    timeout=10.0,
                )
            except Exception:
                # Approval pipeline is best-effort. Failure to talk to the
                # approval service should never crash the agent — degrade to
                # the sync checks (which ran inside evaluate_tool_approval
                # before the I/O attempt) and continue.
                logger.exception(
                    "hermeskill: approval evaluation crashed for agent %s; "
                    "falling through to sync checks",
                    self._state.agent_id,
                )
                directive = ApprovalDirective.pass_()
                verdicts = []
        else:
            # No loop / approvals disabled / no service — fall back to sync
            # checks only. This is the legacy behaviour path and matches
            # what production sees when HERMESKILL_INTERACTIVE_APPROVALS_ENABLED=false.
            from hermeskill_hermes.bridge import on_pre_tool_call as _on_pre_tool_call
            verdicts = _on_pre_tool_call(self._state, tool_name, args)

        self._write_vitals()

        if directive.kind == "block":
            # Track the pending approval id so retries hit the fetch path.
            if directive.pending and directive.approval_id:
                from hermeskill_hermes.bridge import canonical_arguments_hash
                self._pending_approval[agent_key] = (
                    directive.approval_id,
                    canonical_arguments_hash(args),
                )
            else:
                # Denied or expired — clear the tracker so the agent can
                # try a different tool without us getting in the way.
                self._pending_approval.pop(agent_key, None)
            return {"action": "block", "message": directive.message}

        # Approval passed (or wasn't needed) — clear the tracker.
        self._pending_approval.pop(agent_key, None)

        # Kill takes precedence over a steer (a Terminal and a Steer are
        # mutually exclusive for the loop check, but check the flag first so
        # the directive shape is identical to the already-armed fast path).
        if self._state.terminate_requested:
            return self._block_directive(
                self._state.terminate_reason or "hermeskill termination"
            )

        # Soft intervention: block this one repeated call and inject a
        # corrective nudge, but let the session continue.
        steer = next((v for v in verdicts if isinstance(v, Steer)), None)
        if steer is not None:
            return self._steer_directive(steer)

        return None

    def post_tool_call(self, tool_name: str, args: Any, result: Any) -> None:
        if self._state is None:
            return
        on_post_tool_call(self._state, tool_name, args, result)
        self._write_vitals()
        # If kill became armed during the tool, the next pre_tool_call will
        # block. No additional escalation needed here.

    def pre_llm_call(self, model: str) -> None:
        if self._state is None:
            return
        on_pre_llm_call(self._state, model)
        self._write_vitals()

    def post_api_request(
        self,
        model: str,
        usage: dict[str, Any],
        api_duration: float,
    ) -> None:
        if self._state is None:
            return
        input_tokens, output_tokens = _extract_token_counts(usage)
        on_post_api_request(self._state, model, input_tokens, output_tokens)
        self._write_vitals()

    def session_end(self) -> None:
        """Finalize the current Hermes conversation without tearing down plugin IO.

        Hermes may emit ``on_session_end`` immediately before
        ``on_session_reset`` during ``/new``. Therefore this hook must not stop
        the shared session loop, background worker, kill poller, or HTTP client.
        Those process-lifetime resources are released by ``session_finalize()``.
        """
        state = self._state
        if state is None:
            return

        bridge_on_session_end(state)

        cert_text: str | None = None
        if state.terminate_requested:
            if self._local_cert:
                cert_text = self._emit_local_cert()

            if not state.offline:
                # Normal production sessions already own a persistent loop.
                # Some unit tests and defensive teardown paths inject state
                # directly without calling start()/setup(); preserve the
                # historical best-effort death-cert POST by using a temporary
                # loop in that case. Never close the persistent session loop
                # here because /new may immediately emit on_session_reset.
                loop_thread = self._loop_thread
                owns_temporary_loop = loop_thread is None

                if loop_thread is None:
                    loop_thread = _SessionLoop()

                try:
                    with contextlib.suppress(Exception):
                        loop_thread.run(
                            self._post_death_cert_best_effort(),
                            timeout=35.0,
                        )
                finally:
                    if owns_temporary_loop:
                        loop_thread.close()

        terminal_status: Status = (
            "terminated" if state.terminate_requested else "ended_clean"
        )
        self._write_vitals(
            status=terminal_status,
            certificate_text=cert_text,
        )

        state.record_lifecycle(
            "hermes_session_ended",
            status=terminal_status,
        )

        # Do not unregister or destroy the watcher here. During /new,
        # session_reset() immediately consumes this state as old_state and
        # replaces it atomically. Unregistering is performed there.
        #
        # Likewise, do not close worker/client/loop here. on_session_end is a
        # conversation boundary, not necessarily a process boundary.

    def session_finalize(self) -> None:
        """Finalize one Hermes conversation without closing plugin resources.

        Hermes fires ``on_session_finalize`` for ordinary session boundaries,
        including immediately before ``on_session_reset`` during ``/new``.
        The shared event loop, HTTP client, background worker, and kill poller
        therefore remain alive for the lifetime of the Hermes process.
        """
        state = self._state
        if state is None:
            return

        terminal_status: Status = (
            "terminated" if state.terminate_requested else "ended_clean"
        )
        self._write_vitals(status=terminal_status)

        state.record_lifecycle(
            "hermes_session_finalized",
            status=terminal_status,
        )

        # Do not stop workers, close the HTTP client, close _SessionLoop, clear
        # _state, or unregister the watcher here. During /new,
        # on_session_reset follows immediately and rotates this watcher
        # atomically.

    def shutdown(self) -> None:
        """Release process-lifetime Hermeskill resources idempotently."""
        state = self._state
        loop_thread = self._loop_thread

        if loop_thread is not None:
            with contextlib.suppress(Exception):
                loop_thread.run(BackgroundWorker.stop(), timeout=35.0)
            with contextlib.suppress(Exception):
                loop_thread.run(KillPendingPoller.stop(), timeout=10.0)
            with contextlib.suppress(Exception):
                loop_thread.run(self._client.aclose(), timeout=10.0)

        if state is not None and state.agent_id:
            unregister_watcher(state.agent_id)

        if loop_thread is not None:
            with contextlib.suppress(Exception):
                loop_thread.close()

        self._loop_thread = None
        self._state = None
        self._pending_approval.clear()

    # --- helpers -------------------------------------------------------------

    def _block_directive(self, reason: str) -> dict[str, str]:
        """Build the Hermes block directive for an apoptosis kill.

        Hermes wraps ``message`` into ``{"error": message}`` and surfaces it
        as the tool's result. The wording asks the agent to stop — we cannot
        force the session to end, but the harm (further tool execution) is
        already prevented because the tool didn't run.
        """
        return {
            "action": "block",
            "message": (
                f"hermeskill apoptosis: this agent has been terminated by the "
                f"supervisor. Reason: {reason}. Do not retry; do not call "
                "other tools; end the session cleanly."
            ),
        }

    def _steer_directive(self, steer: Steer) -> dict[str, str]:
        """Build the Hermes block directive for a loop **steer**.

        Same transport as the kill block (Hermes surfaces ``message`` as the
        tool's error result), but the intent is the opposite: we want the agent
        to *recover*, not stop. The wording blocks the one looping call and
        tells the agent to change approach. Crucially we do NOT set
        ``terminate_requested`` — the session continues, and if the agent keeps
        repeating the identical call the loop count climbs to the kill
        threshold and apoptosis fires on its own.
        """
        remaining = steer.detail.get("remaining_before_kill")
        tail = (
            f" {remaining} more identical repeat(s) will trigger termination."
            if isinstance(remaining, int)
            else ""
        )
        return {
            "action": "block",
            "message": (
                f"hermeskill loop-steer: this looks like a loop — {steer.reason}. "
                "This repeated call was blocked. Change approach: re-read the "
                "task, try a different tool or different arguments, or explain "
                f"what is blocking you. Do NOT repeat the identical call.{tail}"
            ),
        }

    # --- local death cert ----------------------------------------------------

    def _emit_local_cert(self) -> str | None:
        """Render the death certificate to stderr and save it under
        ``~/.hermeskill/kills/``. Returns the rendered cert text (so the caller can
        splice it into the live-vitals snapshot) or ``None`` if rendering failed.
        Synchronous and best-effort — a rendering hiccup must never escape a
        Hermes hook."""
        if self._state is None:
            return None
        # Windows consoles default to cp1252, which can't encode the cert's
        # box-drawing glyphs. Reconfigure stderr to UTF-8 (best-effort, with
        # replacement) so the write never raises — same guard the CLI uses.
        reconfigure = getattr(sys.stderr, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(Exception):
                reconfigure(encoding="utf-8", errors="replace")
        try:
            cert = build_death_certificate(self._state)
            cost_line = _format_cost_line(self._state)
            cert_text = render_certificate(cert, cost_line=cost_line)
            sys.stderr.write("\n" + cert_text + "\n")
            sys.stderr.flush()
        except Exception:
            logger.exception(
                "hermeskill: failed to render death certificate for agent %s",
                self._state.agent_id,
            )
            return None
        try:
            path = save_certificate(cert, cost_line=cost_line)
            sys.stderr.write(f"hermeskill: death certificate saved to {path}\n")
            sys.stderr.flush()
        except Exception:
            logger.exception(
                "hermeskill: failed to save death certificate for agent %s",
                self._state.agent_id,
            )
        return cert_text

    # --- live vitals ---------------------------------------------------------

    def _write_vitals(
        self, status: Status = "running", certificate_text: str | None = None
    ) -> None:
        """Write the live-vitals snapshot for `hermeskill monitor`. Best-effort:
        a vitals hiccup must never escape a Hermes hook (same contract as
        :meth:`_emit_local_cert`), so every failure is swallowed. Skipped
        entirely when live vitals are disabled (``HERMESKILL_LIVE=0``)."""
        if self._state is None or not self._live_vitals:
            return
        with contextlib.suppress(Exception):
            write_snapshot(
                snapshot_from_state(
                    self._state, status=status, certificate_text=certificate_text
                )
            )

    # --- death cert posting --------------------------------------------------

    async def _post_death_cert_best_effort(self) -> None:
        if self._state is None:
            return
        t0 = time.monotonic()
        try:
            payload = build_kill_event_payload(self._state)
        except Exception:
            logger.exception(
                "hermeskill: failed to build death certificate for agent %s",
                self._state.agent_id,
            )
            return
        try:
            result = await self._client.post_kill_event(self._state.agent_id, payload)
        except Exception:
            logger.exception(
                "hermeskill: failed to POST death certificate for agent %s",
                self._state.agent_id,
            )
            self._state.record_shutdown_step(
                "death_cert_post_failed",
                duration_ms=(time.monotonic() - t0) * 1000,
            )
            return
        duration_ms = (time.monotonic() - t0) * 1000
        kill_event_id = result if isinstance(result, int) else result.id
        self._state.record_shutdown_step(
            "death_cert_posted" if not isinstance(result, int) else "death_cert_post_skipped_409",
            duration_ms=duration_ms,
            kill_event_id=kill_event_id,
        )
        logger.info(
            "hermeskill: death certificate posted for agent %s (kill_event=%s)",
            self._state.agent_id,
            kill_event_id,
        )


# --- cost formatting ---------------------------------------------------------


def _format_cost_line(state: WatcherState) -> str:
    """One-line cost summary for the local death cert, e.g.
    ``$0.42  ·  18.2k in / 2.1k out``. Reads the watcher's cumulative
    token/cost counters (which the cert itself doesn't carry)."""

    def _k(n: int) -> str:
        return f"{n / 1000:.1f}k" if n >= 1000 else str(n)

    return (
        f"${state.total_cost_usd:.2f}  ·  "
        f"{_k(state.total_input_tokens)} in / {_k(state.total_output_tokens)} out"
    )


# --- token extraction --------------------------------------------------------


def _extract_token_counts(usage: dict[str, Any]) -> tuple[int, int]:
    """Pull (input_tokens, output_tokens) from a Hermes post_api_request usage dict.

    Hermes' canonical shape is ``{"input_tokens": N, "output_tokens": M}``
    (see hermes_cli/hooks.py::_DEFAULT_PAYLOADS). We also tolerate OpenAI-
    style aliases (``prompt_tokens``/``completion_tokens``) in case Hermes
    surfaces a provider response shape unchanged for some backends.
    """
    if not usage:
        return 0, 0
    try:
        inp = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
        out = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
    except (TypeError, ValueError):
        return 0, 0
    return inp, out
