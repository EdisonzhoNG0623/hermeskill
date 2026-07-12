from hermeskill.capability import ProfileCapabilityPolicy


def test_profile_policy_load():

    policy = ProfileCapabilityPolicy(
        "config/profile-capabilities.yaml"
    )

    caps = policy.capabilities(
        "tech-ops"
    )

    assert "docker.restart" in caps
    assert "execution.l3" in caps


def test_unknown_profile_empty():

    policy = ProfileCapabilityPolicy(
        "config/profile-capabilities.yaml"
    )

    caps = policy.capabilities(
        "unknown"
    )

    assert caps == []
