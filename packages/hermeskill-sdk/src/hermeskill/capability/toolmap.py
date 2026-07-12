from __future__ import annotations

from pathlib import Path

import yaml


class ToolCapabilityMap:


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
            "tools",
            {},
        )


    def capability(
        self,
        tool: str,
        action: str,
    ):

        tool_data = self.data.get(
            tool,
            {},
        )

        action_data = tool_data.get(
            action,
            {},
        )

        return action_data.get(
            "capability"
        )
