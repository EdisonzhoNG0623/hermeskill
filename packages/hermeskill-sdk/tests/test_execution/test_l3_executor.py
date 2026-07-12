from __future__ import annotations

from hermeskill.execution import (
    ExecutionManager,
    ExecutionMode,
)

from hermeskill.supervisor import ProcessSupervisor

from _execution_targets import (
    quick_task,
    endless_task,
)


def test_l3_quick_task_completes():

    manager = ExecutionManager()

    manager.l3_executor.supervisor = ProcessSupervisor(
        wall_clock_seconds=2,
        grace_seconds=0.2,
    )

    result = manager.execute(
        quick_task,
        mode=ExecutionMode.L3_ISOLATED,
    )

    assert result.mode == ExecutionMode.L3_ISOLATED
    assert result.success is True


def test_l3_kills_wedged_task():

    manager = ExecutionManager()

    manager.l3_executor.supervisor = ProcessSupervisor(
        wall_clock_seconds=0.5,
        grace_seconds=0.2,
    )

    result = manager.execute(
        endless_task,
        mode=ExecutionMode.L3_ISOLATED,
    )

    assert result.mode == ExecutionMode.L3_ISOLATED
    assert result.success is False
    assert result.value.killed is True
