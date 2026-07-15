"""Regression tests for Hermeskill Session Isolation v1.

These tests intentionally exercise the adapter at the Hermes-session boundary:
all accounting and lifecycle actions must be keyed by the hook's session_id.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import hermeskill_hermes
from hermeskill.policies import resolve_policy
from hermeskill.watcher import WatcherState
from hermeskill_hermes.plugin import HermeskillPlugin


def _state(*, name: str = "test") -> WatcherState:
    state = WatcherState(
        agent_id=uuid4(),
        name=name,
        policy=resolve_policy("permissive"),
    )
    state.offline = True
    return state


def _plugin_with_sessions(*session_ids: str) -> tuple[HermeskillPlugin, dict[str, WatcherState]]:
    client = MagicMock()
    plugin = HermeskillPlugin(
        name="test",
        policy="permissive",
        client=client,
        forced_offline=True,
    )
    states = {session_id: _state(name=session_id) for session_id in session_ids}
    plugin._states = states
    return plugin, states


def test_usage_is_accounted_only_to_hook_session_id() -> None:
    plugin, states = _plugin_with_sessions("session-A", "session-B", "session-C")

    plugin.post_api_request(
        session_id="session-A", model="model", usage={"input_tokens": 101, "output_tokens": 11}, api_duration=0
    )
    plugin.post_api_request(
        session_id="session-B", model="model", usage={"input_tokens": 202, "output_tokens": 22}, api_duration=0
    )
    plugin.post_api_request(
        session_id="session-C", model="model", usage={"input_tokens": 303, "output_tokens": 33}, api_duration=0
    )

    assert (states["session-A"].total_input_tokens, states["session-A"].total_output_tokens) == (101, 11)
    assert (states["session-B"].total_input_tokens, states["session-B"].total_output_tokens) == (202, 22)
    assert (states["session-C"].total_input_tokens, states["session-C"].total_output_tokens) == (303, 33)


def test_terminated_session_blocks_only_its_own_tools() -> None:
    plugin, states = _plugin_with_sessions("session-A", "session-B", "session-C")
    states["session-A"].terminate_requested = True
    states["session-A"].terminate_reason = "token cap"

    assert plugin.pre_tool_call(session_id="session-A", tool_name="read_file", args={}) is not None
    assert plugin.pre_tool_call(session_id="session-B", tool_name="read_file", args={}) is None
    assert plugin.pre_tool_call(session_id="session-C", tool_name="read_file", args={}) is None
    assert states["session-B"].terminate_requested is False
    assert states["session-C"].terminate_requested is False


def test_finalize_then_reset_creates_clean_independent_state() -> None:
    plugin, states = _plugin_with_sessions("old-session")
    plugin.start()
    old = states["old-session"]
    old.total_input_tokens = 999
    old.terminate_requested = True
    old.terminate_reason = "token cap"
    plugin._pending_approval["old-session"] = ("approval-old", "hash")

    with (
        patch("hermeskill_hermes.plugin.unregister_watcher"),
        patch.object(plugin, "_create_session_state", new=AsyncMock(return_value=_state(name="new-session"))),
    ):
        plugin.session_finalize(session_id="old-session", reason="new_session")
        plugin.session_reset(session_id="new-session", old_session_id="old-session")

    assert "old-session" not in plugin._states
    assert "new-session" in plugin._states
    new = plugin._states["new-session"]
    assert new.total_input_tokens == 0
    assert new.total_output_tokens == 0
    assert new.tool_call_count == 0
    assert not new.loop_signatures
    assert new.symptoms_log == []
    assert new.terminate_requested is False
    assert new.terminate_reason is None
    assert "old-session" not in plugin._pending_approval
    assert plugin._loop_thread is not None
    plugin.shutdown()


def test_duplicate_finalize_reset_and_unknown_finalize_are_idempotent() -> None:
    plugin, _states = _plugin_with_sessions("session-A")
    with patch("hermeskill_hermes.plugin.unregister_watcher") as unregister:
        plugin.session_finalize(session_id="missing", reason="new_session")
        plugin.session_finalize(session_id="session-A", reason="new_session")
        plugin.session_finalize(session_id="session-A", reason="new_session")
    assert unregister.call_count == 1


def test_interleaved_hooks_cannot_cross_contaminate() -> None:
    plugin, states = _plugin_with_sessions("session-A", "session-B", "session-C")
    plugin.post_api_request(session_id="session-A", model="model", usage={"input_tokens": 10}, api_duration=0)
    plugin.post_api_request(session_id="session-B", model="model", usage={"input_tokens": 20}, api_duration=0)
    plugin.pre_tool_call(session_id="session-A", tool_name="read_file", args={"path": "/a"})
    plugin.post_api_request(session_id="session-C", model="model", usage={"input_tokens": 30}, api_duration=0)
    plugin.pre_tool_call(session_id="session-B", tool_name="read_file", args={"path": "/b"})

    assert [states[key].total_input_tokens for key in ("session-A", "session-B", "session-C")] == [10, 20, 30]
    assert [states[key].tool_call_count for key in ("session-A", "session-B", "session-C")] == [1, 1, 0]


def test_missing_session_id_fails_open_without_creating_global_bucket(caplog) -> None:
    plugin, states = _plugin_with_sessions("session-A", "session-B")

    assert plugin.post_api_request(session_id="", model="model", usage={"input_tokens": 99}, api_duration=0) is None
    assert [state.total_input_tokens for state in states.values()] == [0, 0]
    assert set(plugin._states) == {"session-A", "session-B"}
    assert "missing session_id" in caplog.text


def test_shutdown_finalizes_all_sessions_and_closes_shared_resources_once() -> None:
    plugin, _states = _plugin_with_sessions("session-A", "session-B")
    loop_thread = MagicMock()
    plugin._loop_thread = loop_thread

    with (
        patch("hermeskill_hermes.plugin.BackgroundWorker.stop", new=MagicMock(return_value=None)),
        patch("hermeskill_hermes.plugin.KillPendingPoller.stop", new=MagicMock(return_value=None)),
        patch("hermeskill_hermes.plugin.unregister_watcher") as unregister,
    ):
        plugin.shutdown()
        plugin.shutdown()

    assert unregister.call_count == 2
    assert loop_thread.close.call_count == 1
    assert plugin._states == {}


def test_production_hook_missing_session_id_never_uses_compat(monkeypatch) -> None:
    plugin, _states = _plugin_with_sessions()
    compat = _state(name="compat")
    plugin._state = compat
    monkeypatch.setattr(hermeskill_hermes, "_current_plugin", plugin)

    hermeskill_hermes._on_post_api_request(
        session_id="", model="model", usage={"input_tokens": 99, "output_tokens": 1}
    )

    assert set(plugin._states) == {"compat:direct"}
    assert plugin._states == {"compat:direct": compat}
    assert (compat.total_input_tokens, compat.total_output_tokens) == (0, 0)


def test_direct_call_without_session_id_uses_only_compat_direct() -> None:
    plugin, states = _plugin_with_sessions("telegram-topic-A")
    compat = _state(name="compat")
    plugin._state = compat

    plugin.post_api_request("model", {"input_tokens": 7, "output_tokens": 3}, 0)

    assert states["telegram-topic-A"].total_input_tokens == 0
    assert plugin._states["compat:direct"] is compat
    assert (compat.total_input_tokens, compat.total_output_tokens) == (7, 3)


def test_sync_register_twice_reuses_process_router(monkeypatch) -> None:
    router = MagicMock()
    router._shutdown = False
    first, second = MagicMock(), MagicMock()
    monkeypatch.setattr(hermeskill_hermes, "_current_plugin", router)

    hermeskill_hermes.register(first)
    hermeskill_hermes.register(second)

    assert hermeskill_hermes._current_plugin is router
    assert first.register_hook.call_count == second.register_hook.call_count == 7


async def test_async_register_twice_reuses_process_router(monkeypatch) -> None:
    router = MagicMock()
    router._shutdown = False
    first, second = MagicMock(), MagicMock()
    monkeypatch.setattr(hermeskill_hermes, "_current_plugin", router)

    await hermeskill_hermes.async_register(first)
    await hermeskill_hermes.async_register(second)

    assert hermeskill_hermes._current_plugin is router
    assert first.register_hook.call_count == second.register_hook.call_count == 7


async def test_sync_async_register_interleave_reuses_process_router(monkeypatch) -> None:
    router = MagicMock()
    router._shutdown = False
    sync_ctx, async_ctx = MagicMock(), MagicMock()
    monkeypatch.setattr(hermeskill_hermes, "_current_plugin", router)

    hermeskill_hermes.register(sync_ctx)
    await hermeskill_hermes.async_register(async_ctx)

    assert hermeskill_hermes._current_plugin is router
    assert sync_ctx.register_hook.call_count == async_ctx.register_hook.call_count == 7
