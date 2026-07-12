from __future__ import annotations

from .executor import Executor
from .types import ExecutionMode, ExecutionResult


class AsyncExecutor(Executor):

    def execute(self, target, *args, **kwargs) -> ExecutionResult:
        try:
            result = target(*args, **kwargs)

            return ExecutionResult(
                mode=ExecutionMode.NORMAL,
                success=True,
                value=result,
            )

        except Exception as exc:
            return ExecutionResult(
                mode=ExecutionMode.NORMAL,
                success=False,
                error=str(exc),
            )
