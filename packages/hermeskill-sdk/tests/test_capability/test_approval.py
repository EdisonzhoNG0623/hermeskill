from hermeskill.capability import (
    CapabilityRegistry,
    CapabilityResolver,
    PermissionDecision,
)


def test_high_risk_requires_approval():

    registry = CapabilityRegistry(
        "config/capabilities.yaml"
    )

    resolver = CapabilityResolver(
        registry,
        {
            "tech-ops": [
                "docker.restart"
            ]
        },
    )

    result = resolver.check(
        profile="tech-ops",
        capability="docker.restart",
    )

    assert (
        result.decision
        == PermissionDecision.APPROVAL_REQUIRED
    )


def test_low_risk_allowed():

    registry = CapabilityRegistry(
        "config/capabilities.yaml"
    )

    resolver = CapabilityResolver(
        registry,
        {
            "default": [
                "filesystem.read"
            ]
        },
    )

    result = resolver.check(
        profile="default",
        capability="filesystem.read",
    )

    assert (
        result.decision
        == PermissionDecision.ALLOW
    )
