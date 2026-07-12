from __future__ import annotations

from pathlib import Path

import yaml


class CapabilityInventory:


    def __init__(
        self,
        path: str,
    ):
        self.path = Path(path)
        self.data = self._load()


    def _load(self):

        with self.path.open() as f:
            data = yaml.safe_load(f)

        return data.get(
            "capabilities",
            {},
        )


    def get(self, capability: str):

        return self.data.get(
            capability
        )


    def all(self):

        return list(
            self.data.keys()
        )
