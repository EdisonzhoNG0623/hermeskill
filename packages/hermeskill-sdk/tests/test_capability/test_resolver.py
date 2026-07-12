from hermeskill.capability import (
    CapabilityRegistry,
    CapabilityResolver,
    PermissionDecision,
)


def test_low_risk_capability_allow():

    registry = CapabilityRegistry(
        "config/capabilities.yaml"
    )

    resolver = CapabilityResolver(
        registry,
        {
            "tech-ops": [
                "filesystem.read",
            ]
        },
    )

    result = resolver.check(
        profile="tech-ops",
        capability="filesystem.read",
    )

    assert result.decision == PermissionDecision.ALLOW


def test_high_risk_capability_requires_approval():

    registry = CapabilityRegistry(
        "config/capabilities.yaml"
    )

    resolver = CapabilityResolver(
        registry,
        {
            "tech-ops": [
                "docker.restart",
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


def test_unknown_capability_denied():

    registry = CapabilityRegistry(
        "config/capabilities.yaml"
    )

    resolver = CapabilityResolver(
        registry,
        {
            "tech-ops": [],
        },
    )

    result = resolver.check(
        profile="tech-ops",
        capability="unknown.action",
    )

    assert result.decision == PermissionDecision.DENY
