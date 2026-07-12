from __future__ import annotations

from .async_executor import AsyncExecutor
from .types import ExecutionMode, ExecutionResult


class ExecutionManager:

    def __init__(self):
        self.normal_executor = AsyncExecutor()

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

        raise ValueError(
            f"Unsupported execution mode: {mode}"
        )
