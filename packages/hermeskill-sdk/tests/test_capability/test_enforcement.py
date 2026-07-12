from hermeskill.capability import (
    CapabilityEnforcer,
    PermissionDecision,
)


def test_pilot_enforcement_allows_low_risk():


    enforcer = CapabilityEnforcer(
        {
            "filesystem.read": True
        }
    )


    result = enforcer.evaluate(
        capability="filesystem.read",
        decision=PermissionDecision.ALLOW,
    )


    assert result.allowed is True
    assert result.enforced is True



def test_non_pilot_capability_keeps_shadow_mode():


    enforcer = CapabilityEnforcer(
        {
            "filesystem.read": True
        }
    )


    result = enforcer.evaluate(
        capability="docker.restart",
        decision=PermissionDecision.APPROVAL_REQUIRED,
    )


    assert result.allowed is True
    assert result.enforced is False
