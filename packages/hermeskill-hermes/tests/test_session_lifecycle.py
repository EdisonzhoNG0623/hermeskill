from unittest.mock import MagicMock, patch
from uuid import uuid4

from hermeskill.policies import resolve_policy
from hermeskill.watcher import WatcherState
from hermeskill_hermes.plugin import HermeskillPlugin


def _plugin_with_state() -> tuple[HermeskillPlugin, WatcherState]:
    client = MagicMock()
    client.aclose = MagicMock(return_value=None)

    plugin = HermeskillPlugin(
        name="test",
        policy="coding-default",
        client=client,
        forced_offline=True,
    )

    state = WatcherState(
        agent_id=uuid4(),
        name="test",
        policy=resolve_policy("coding-default"),
    )
    state.offline = True

    plugin._state = state
    return plugin, state


def test_session_end_preserves_process_lifetime_resources() -> None:
    plugin, _state = _plugin_with_state()

    loop_thread = MagicMock()
    plugin._loop_thread = loop_thread

    plugin.session_end()

    assert plugin._loop_thread is loop_thread
    assert plugin._state is not None
    loop_thread.close.assert_not_called()


def test_session_finalize_preserves_resources_for_reset() -> None:
    plugin, state = _plugin_with_state()

    loop_thread = MagicMock()
    plugin._loop_thread = loop_thread

    with patch(
        "hermeskill_hermes.plugin.unregister_watcher"
    ) as unregister:
        plugin.session_finalize()

    assert plugin._loop_thread is loop_thread
    assert plugin._state is state

    loop_thread.close.assert_not_called()
    unregister.assert_not_called()
    plugin._client.aclose.assert_not_called()


def test_shutdown_closes_process_lifetime_resources() -> None:
    plugin, state = _plugin_with_state()

    loop_thread = MagicMock()
    plugin._loop_thread = loop_thread

    with (
        patch(
            "hermeskill_hermes.plugin.BackgroundWorker.stop",
            new=MagicMock(return_value=None),
        ),
        patch(
            "hermeskill_hermes.plugin.KillPendingPoller.stop",
            new=MagicMock(return_value=None),
        ),
        patch(
            "hermeskill_hermes.plugin.unregister_watcher"
        ) as unregister,
    ):
        plugin.shutdown()

    unregister.assert_called_once_with(state.agent_id)
    loop_thread.close.assert_called_once_with()

    assert plugin._loop_thread is None
    assert plugin._state is None
