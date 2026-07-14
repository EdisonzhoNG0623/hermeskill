from __future__ import annotations

from pathlib import Path

import yaml


class ToolCapabilityMap:
    """Maps Hermes tool names to capabilities.

    Two query shapes:
      * `capability(tool, action)` — the original `(tool, action)` paired form,
        kept for backward compatibility with the inventory / shadow / gateway
        test scaffolding.
      * `capability_for_tool(tool)` — single-action form used by the Hermes
        `pre_tool_call` bridge, where the hook payload only carries a single
        `tool_name` (no separate `action`).

    The v1 single-action form reads `tools.<tool_name>.tool.capability` from the
    YAML and is the authoritative lookup for the interactive-approval bridge.
    """

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
        """Legacy paired-form lookup: `(tool, action) -> capability`."""

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


    def capability_for_tool(
        self,
        tool_name: str,
    ) -> str | None:
        """Single-action lookup: Hermes `tool_name` -> capability.

        Reads `tools[tool_name].tool.capability`. Returns None when the
        tool is unmapped (caller must default to DENY).
        """

        if not tool_name:
            return None

        entry = self.data.get(tool_name)
        if not isinstance(entry, dict):
            return None

        tool_entry = entry.get("tool")
        if not isinstance(tool_entry, dict):
            return None

        return tool_entry.get("capability")