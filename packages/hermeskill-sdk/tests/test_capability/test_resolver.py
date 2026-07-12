from hermeskill.capability import (
    CapabilityRegistry,
    CapabilityResolver,
    PermissionDecision,
)


def test_capability_allow():

    registry = CapabilityRegistry(
        "config/capabilities.yaml"
    )

    resolver = CapabilityResolver(
        registry,
        {
            "tech-ops": [
                "docker.restart",
                "filesystem.read",
            ]
        },
    )

    result = resolver.check(
        profile="tech-ops",
        capability="docker.restart",
    )

    assert result.decision == PermissionDecision.ALLOW


def test_capability_denied():

    registry = CapabilityRegistry(
        "config/capabilities.yaml"
    )

    resolver = CapabilityResolver(
        registry,
        {
            "stock-research": [
                "memory.read",
            ]
        },
    )

    result = resolver.check(
        profile="stock-research",
        capability="docker.restart",
    )

    assert result.decision == PermissionDecision.DENY
