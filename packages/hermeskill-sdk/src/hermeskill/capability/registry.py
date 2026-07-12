from __future__ import annotations

from pathlib import Path

import yaml

from .models import Capability


class CapabilityRegistry:

    def __init__(self, path: str):
        self.path = Path(path)
        self._capabilities = self._load()

    def _load(self) -> dict[str, Capability]:
        with self.path.open() as f:
            data = yaml.safe_load(f)

        result = {}

        for name, item in data["capabilities"].items():
            result[name] = Capability(
                name=name,
                risk=item["risk"],
                domain=item["domain"],
                description=item["description"],
            )

        return result

    def get(self, name: str) -> Capability | None:
        return self._capabilities.get(name)

    def all(self) -> list[Capability]:
        return list(self._capabilities.values())
