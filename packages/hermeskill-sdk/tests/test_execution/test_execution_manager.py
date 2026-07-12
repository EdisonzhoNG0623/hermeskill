from __future__ import annotations

from hermeskill.execution import (
    ExecutionManager,
    ExecutionMode,
)


def test_default_execution_uses_normal_executor():

    def hello():
        return "ok"

    manager = ExecutionManager()

    result = manager.execute(hello)

    assert result.success is True
    assert result.mode == ExecutionMode.NORMAL
    assert result.value == "ok"


def test_normal_execution_failure_is_captured():

    def fail():
        raise RuntimeError("boom")

    manager = ExecutionManager()

    result = manager.execute(fail)

    assert result.success is False
    assert result.mode == ExecutionMode.NORMAL
    assert "boom" in result.error
