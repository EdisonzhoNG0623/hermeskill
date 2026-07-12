from __future__ import annotations

from dataclasses import dataclass

from .audit import (
    PermissionAuditRecord,
    create_audit_record,
)


@dataclass
class ShadowDecision:

    record: PermissionAuditRecord
    enforced: bool = False



class CapabilityShadowObserver:


    def __init__(
        self,
        resolver,
    ):
        self.resolver = resolver


    def observe(
        self,
        *,
        profile: str,
        capability: str,
    ) -> ShadowDecision:

        result = self.resolver.check(
            profile=profile,
            capability=capability,
        )

        record = create_audit_record(
            profile=profile,
            result=result,
        )

        return ShadowDecision(
            record=record,
            enforced=False,
        )
