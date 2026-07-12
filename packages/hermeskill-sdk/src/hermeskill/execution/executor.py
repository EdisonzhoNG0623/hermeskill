from __future__ import annotations

from abc import ABC, abstractmethod

from .types import ExecutionResult


class Executor(ABC):

    @abstractmethod
    def execute(self, target, *args, **kwargs) -> ExecutionResult:
        raise NotImplementedError
