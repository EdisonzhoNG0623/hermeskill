from __future__ import annotations

from .async_executor import AsyncExecutor
from .l3_executor import L3Executor
from .types import ExecutionMode, ExecutionResult


class ExecutionManager:

    def __init__(self):
        self.normal_executor = AsyncExecutor()
        self.l3_executor = L3Executor()

    def execute(
        self,
        target,
        *,
        mode: ExecutionMode = ExecutionMode.NORMAL,
        args=(),
        kwargs=None,
    ) -> ExecutionResult:

        kwargs = kwargs or {}

        if mode == ExecutionMode.NORMAL:
            return self.normal_executor.execute(
                target,
                *args,
                **kwargs,
            )

        if mode == ExecutionMode.L3_ISOLATED:
            return self.l3_executor.execute(
                target,
                *args,
                **kwargs,
            )

        raise ValueError(
            f"Unsupported execution mode: {mode}"
        )
