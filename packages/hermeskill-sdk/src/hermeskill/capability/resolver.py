from __future__ import annotations

from .approval import evaluate_risk
from .models import (
    CapabilityResult,
    PermissionDecision,
)
from .registry import CapabilityRegistry


class CapabilityResolver:

    def __init__(
        self,
        registry: CapabilityRegistry,
        policies: dict[str, list[str]],
    ):
        self.registry = registry
        self.policies = policies

    def check(
        self,
        *,
        profile: str,
        capability: str,
    ) -> CapabilityResult:

        item = self.registry.get(capability)

        if item is None:
            return CapabilityResult(
                capability=capability,
                decision=PermissionDecision.DENY,
                reason="unknown capability",
            )

        allowed = self.policies.get(profile, [])

        if capability in allowed:
            decision = evaluate_risk(item.risk)

            return CapabilityResult(
                capability=capability,
                decision=decision,
                risk=item.risk,
                reason="profile grants capability",
            )

        return CapabilityResult(
            capability=capability,
            decision=PermissionDecision.DENY,
            risk=item.risk,
            reason="profile does not grant capability",
        )
