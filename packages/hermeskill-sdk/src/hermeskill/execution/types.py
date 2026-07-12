from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ExecutionMode(str, Enum):
    NORMAL = "normal"
    L3_ISOLATED = "l3_isolated"


@dataclass(slots=True)
class ExecutionResult:
    mode: ExecutionMode
    success: bool
    value: object | None = None
    error: str | None = None
