from __future__ import annotations


from dataclasses import dataclass

from .models import PermissionDecision


@dataclass
class EnforcementResult:

    allowed: bool
    decision: PermissionDecision
    enforced: bool



class CapabilityEnforcer:


    def __init__(
        self,
        pilot_policy: dict,
    ):
        self.policy = pilot_policy



    def evaluate(
        self,
        *,
        capability: str,
        decision: PermissionDecision,
    ):

        enabled = self.policy.get(
            capability,
            False,
        )


        if not enabled:

            return EnforcementResult(
                allowed=True,
                decision=decision,
                enforced=False,
            )


        return EnforcementResult(
            allowed=(
                decision
                == PermissionDecision.ALLOW
            ),
            decision=decision,
            enforced=True,
        )
