from hermeskill.capability import (
    CapabilityGateway,
    CapabilityRegistry,
    CapabilityResolver,
    PermissionDecision,
)


def hello():
    return "ok"


def test_gateway_allow():

    registry = CapabilityRegistry(
        "config/capabilities.yaml"
    )

    resolver = CapabilityResolver(
        registry,
        {
            "tech-ops": [
                "filesystem.read"
            ]
        },
    )

    gateway = CapabilityGateway(
        resolver
    )

    result = gateway.execute(
        profile="tech-ops",
        capability="filesystem.read",
        target=hello,
    )

    assert result == "ok"



def test_gateway_deny():

    registry = CapabilityRegistry(
        "config/capabilities.yaml"
    )

    resolver = CapabilityResolver(
        registry,
        {
            "stock-research": [
                "memory.read"
            ]
        },
    )

    gateway = CapabilityGateway(
        resolver
    )

    try:
        gateway.execute(
            profile="stock-research",
            capability="docker.restart",
            target=hello,
        )

        assert False

    except PermissionError:
        assert True
