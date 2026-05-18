"""Tests for the `stasis` CLI commands (fleet, logs).

Patches StasisClient.from_config to return a mock-transport-backed client so
no live server is needed.
"""

import json as _json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx
import pytest
import stasis_agent.cli as cli_mod
from rich.console import Console
from stasis_agent.cli import app
from stasis_agent.client import StasisClient
from typer.testing import CliRunner

runner = CliRunner()


def _client_with(handler: Any) -> StasisClient:
    return StasisClient(
        base_url="http://test",
        api_key="sk_test",
        transport=httpx.MockTransport(handler),
    )


@pytest.fixture(autouse=True)
def _patch_from_config(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Each test installs its own handler by setting `_handler` on the module.

    Also forces a wide Rich console so table cells don't get truncated by
    CliRunner's narrow default terminal width — otherwise assertions on cell
    contents like "coding-default" fail because Rich renders "cod…".
    """
    cli_mod._handler = None  # type: ignore[attr-defined]
    monkeypatch.setattr(cli_mod, "console", Console(width=200))

    def fake(*args: Any, **kwargs: Any) -> StasisClient:
        handler = cli_mod._handler  # type: ignore[attr-defined]
        if handler is None:

            def default(_req: httpx.Request) -> httpx.Response:
                return httpx.Response(500, json={"detail": "no handler set"})

            handler = default
        return _client_with(handler)

    monkeypatch.setattr(cli_mod.StasisClient, "from_config", staticmethod(fake))
    yield
    cli_mod._handler = None  # type: ignore[attr-defined]


def _set_handler(handler: Any) -> None:
    cli_mod._handler = handler  # type: ignore[attr-defined]


# --- fleet --------------------------------------------------------------


def test_fleet_renders_table_of_agents() -> None:
    now = datetime.now(UTC).isoformat()
    agents = [
        {
            "id": str(uuid4()),
            "name": "alpha",
            "policy_name": "coding-default",
            "status": "running",
            "registered_at": now,
            "last_heartbeat_at": now,
            "terminated_at": None,
        },
        {
            "id": str(uuid4()),
            "name": "beta",
            "policy_name": "strict",
            "status": "terminated",
            "registered_at": now,
            "last_heartbeat_at": None,
            "terminated_at": now,
        },
    ]

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=agents)

    _set_handler(handler)
    result = runner.invoke(app, ["fleet"])
    assert result.exit_code == 0, result.stdout
    assert "alpha" in result.stdout
    assert "beta" in result.stdout
    assert "coding-default" in result.stdout
    assert "running" in result.stdout
    assert "terminated" in result.stdout


def test_fleet_empty_message() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    _set_handler(handler)
    result = runner.invoke(app, ["fleet"])
    assert result.exit_code == 0
    assert "no agents" in result.stdout


def test_fleet_auth_error_exits_2() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "bad key"})

    _set_handler(handler)
    result = runner.invoke(app, ["fleet"])
    assert result.exit_code == 2
    # auth-error message goes to stderr; CliRunner captures both via .output
    assert "auth error" in (result.stderr or "") + result.output


def test_fleet_transport_error_exits_5() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    _set_handler(handler)
    result = runner.invoke(app, ["fleet"])
    assert result.exit_code == 5


# --- logs ---------------------------------------------------------------


def test_logs_renders_recent_events_oldest_first() -> None:
    aid = str(uuid4())
    now = datetime.now(UTC).isoformat()
    events_page = {
        # Descending order from the server (newest first)
        "events": [
            {
                "id": 3,
                "agent_id": aid,
                "type": "tool_call",
                "payload": {"tool": "write_file"},
                "created_at": now,
            },
            {
                "id": 2,
                "agent_id": aid,
                "type": "tool_call",
                "payload": {"tool": "read_file"},
                "created_at": now,
            },
            {
                "id": 1,
                "agent_id": aid,
                "type": "lifecycle",
                "payload": {"phase": "registered"},
                "created_at": now,
            },
        ],
        "next_before_id": None,
        "last_id": 3,
    }

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=events_page)

    _set_handler(handler)
    result = runner.invoke(app, ["logs", aid])
    assert result.exit_code == 0, result.stdout

    # Verify oldest-first ordering in the printed output
    out = result.stdout
    idx_registered = out.find("registered")
    idx_read = out.find("read_file")
    idx_write = out.find("write_file")
    assert idx_registered < idx_read < idx_write, f"order wrong:\n{out}"


def test_logs_unknown_agent_exits_4() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "agent not found"})

    _set_handler(handler)
    result = runner.invoke(app, ["logs", str(uuid4())])
    assert result.exit_code == 4


def test_logs_llm_event_shows_cost() -> None:
    aid = str(uuid4())
    now = datetime.now(UTC).isoformat()
    page = {
        "events": [
            {
                "id": 1,
                "agent_id": aid,
                "type": "llm_call",
                "payload": {
                    "model": "claude-haiku-4-5",
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cost_usd": 0.000350,
                },
                "created_at": now,
            }
        ],
        "next_before_id": None,
        "last_id": 1,
    }

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=page)

    _set_handler(handler)
    result = runner.invoke(app, ["logs", aid])
    assert result.exit_code == 0
    assert "claude-haiku-4-5" in result.stdout
    assert "$0.0003" in result.stdout  # 4-decimal cost format


def test_logs_follow_polls_until_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    """--follow polls with after_id; we simulate one poll then KeyboardInterrupt."""
    aid = str(uuid4())
    now = datetime.now(UTC).isoformat()
    call_count = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        params = dict(req.url.params)
        # First call: initial page, returns one event.
        if "after_id" not in params:
            return httpx.Response(
                200,
                json={
                    "events": [
                        {
                            "id": 1,
                            "agent_id": aid,
                            "type": "lifecycle",
                            "payload": {"phase": "registered"},
                            "created_at": now,
                        }
                    ],
                    "next_before_id": None,
                    "last_id": 1,
                },
            )
        # Second call (poll): one new event, then we'll signal stop.
        return httpx.Response(
            200,
            json={
                "events": [
                    {
                        "id": 2,
                        "agent_id": aid,
                        "type": "tool_call",
                        "payload": {"tool": "ping"},
                        "created_at": now,
                    }
                ],
                "next_before_id": None,
                "last_id": 2,
            },
        )

    _set_handler(handler)

    # Force the sleep to raise KeyboardInterrupt on the second iteration
    # (first iteration of the while loop, after the initial page).
    real_sleep = __import__("asyncio").sleep

    async def fake_sleep(_seconds: float) -> None:
        # Allow one poll cycle, then bail.
        if call_count["n"] >= 2:
            raise KeyboardInterrupt
        await real_sleep(0)

    monkeypatch.setattr("stasis_agent.cli.asyncio.sleep", fake_sleep)

    result = runner.invoke(app, ["logs", aid, "--follow", "--interval", "0.001"])
    assert result.exit_code == 0, result.stdout
    assert "registered" in result.stdout
    assert "ping" in result.stdout


# --- version + placeholders ---------------------------------------------


def test_version_flag_prints_version_only() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    # Output should be a single line with the version
    assert result.stdout.strip().count("\n") == 0


def test_kill_command_says_not_implemented() -> None:
    result = runner.invoke(app, ["kill", str(uuid4()), "--reason", "deploy"])
    assert result.exit_code == 1
    out = (result.stdout or "") + (result.stderr or "")
    assert "M4" in out


def test_grant_command_says_not_implemented() -> None:
    result = runner.invoke(
        app,
        ["grant", str(uuid4()), "--symptoms", "x", "--duration", "1h", "--reason", "y"],
    )
    assert result.exit_code == 1


# --- helper sanity ------------------------------------------------------


def test_set_handler_isolates_between_tests() -> None:
    """Regression guard: after a test, _handler must reset to None (autouse fixture)."""
    assert cli_mod._handler is None  # type: ignore[attr-defined]
    _set_handler(lambda req: httpx.Response(418))
    assert cli_mod._handler is not None  # type: ignore[attr-defined]
    # When this test exits, the fixture will reset _handler to None.
    # Silence unused-import lint
    _ = _json
