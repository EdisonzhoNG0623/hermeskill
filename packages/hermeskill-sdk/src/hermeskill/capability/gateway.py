from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Any

from .models import PermissionDecision
from .resolver import CapabilityResolver


@dataclass(frozen=True)
class CapabilityCheck:
    profile: str
    capability: str


class CapabilityGateway:

    def __init__(
        self,
        resolver: CapabilityResolver,
    ):
        self.resolver = resolver


    def execute(
        self,
        *,
        profile: str,
        capability: str,
        target: Callable[..., Any],
        args: tuple = (),
        kwargs: dict | None = None,
    ) -> Any:

        result = self.resolver.check(
            profile=profile,
            capability=capability,
        )

        if result.decision != PermissionDecision.ALLOW:
            raise PermissionError(
                f"Capability denied: {capability}; "
                f"reason={result.reason}"
            )

        return target(
            *args,
            **(kwargs or {}),
        )
