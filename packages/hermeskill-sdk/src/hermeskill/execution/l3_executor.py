from __future__ import annotations

from .executor import Executor
from .types import ExecutionMode, ExecutionResult

from hermeskill.supervisor import ProcessSupervisor


class L3Executor(Executor):

    def __init__(
        self,
        *,
        supervisor: ProcessSupervisor | None = None,
    ):
        self.supervisor = supervisor or ProcessSupervisor(
            grace_seconds=5.0,
        )

    def execute(
        self,
        target,
        *args,
        **kwargs,
    ) -> ExecutionResult:

        try:
            result = self.supervisor.run(
                target,
                args=args,
            )

            return ExecutionResult(
                mode=ExecutionMode.L3_ISOLATED,
                success=not result.killed,
                value=result,
            )

        except Exception as exc:
            return ExecutionResult(
                mode=ExecutionMode.L3_ISOLATED,
                success=False,
                error=str(exc),
            )
