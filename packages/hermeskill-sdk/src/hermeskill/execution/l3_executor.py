from __future__ import annotations

from .executor import Executor
from .types import ExecutionMode, ExecutionResult


class L3Executor(Executor):

    def execute(self, target, *args, **kwargs) -> ExecutionResult:
        raise NotImplementedError(
            "L3 execution adapter is not enabled yet"
        )
