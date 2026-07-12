from hermeskill.capability import (
    ToolCapabilityMap,
)


def test_tool_mapping():

    mapping = ToolCapabilityMap(
        "config/tool-capability-map.yaml"
    )


    assert (
        mapping.capability(
            "docker",
            "restart",
        )
        == "docker.restart"
    )


    assert (
        mapping.capability(
            "memory",
            "remember",
        )
        == "memory.write"
    )
