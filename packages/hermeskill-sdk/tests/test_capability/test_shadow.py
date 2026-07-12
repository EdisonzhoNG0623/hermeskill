from hermeskill.capability import (
    CapabilityShadowObserver,
    CapabilityRegistry,
    CapabilityResolver,
)


def test_shadow_mode_does_not_enforce():

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

    observer = CapabilityShadowObserver(
        resolver
    )

    result = observer.observe(
        profile="tech-ops",
        capability="docker.restart",
    )


    assert (
        result.record.capability
        == "docker.restart"
    )

    assert result.enforced is False
