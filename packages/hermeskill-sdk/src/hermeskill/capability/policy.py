from __future__ import annotations

from pathlib import Path

import yaml


class ProfileCapabilityPolicy:

    def __init__(self, path: str):
        self.path = Path(path)
        self._profiles = self._load()


    def _load(self):
        with self.path.open() as f:
            data = yaml.safe_load(f)

        return {
            name: item.get("allow", [])
            for name, item in data["profiles"].items()
        }


    def capabilities(self, profile: str) -> list[str]:
        return self._profiles.get(profile, [])
