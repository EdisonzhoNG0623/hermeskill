"""
Execution abstraction layer.

This module defines execution strategy boundaries.

No runtime routing changes are introduced.
"""

from .manager import ExecutionManager
from .types import ExecutionMode, ExecutionResult

__all__ = [
    "ExecutionManager",
    "ExecutionMode",
    "ExecutionResult",
]
