from hermeskill.capability import (
    CapabilityResult,
    PermissionDecision,
    create_audit_record,
)


def test_permission_audit_record():

    result = CapabilityResult(
        capability="docker.restart",
        decision=PermissionDecision.APPROVAL_REQUIRED,
        risk="high",
        reason="profile grants capability",
    )

    record = create_audit_record(
        profile="tech-ops",
        result=result,
    )

    assert record.profile == "tech-ops"
    assert record.capability == "docker.restart"
    assert record.decision == "APPROVAL_REQUIRED"
    assert record.risk == "high"
