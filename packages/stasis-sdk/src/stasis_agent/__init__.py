"""Stasis SDK — agent supervision via the apoptosis protocol.

Public API:

    from stasis_agent import watch, StasisTerminated

    async def main():
        graph = await watch(my_graph, name="coding-bot-v1", policy="coding-default")
        await graph.ainvoke({"task": "fix the bug"})

`watch()` registers the agent with the control plane, attaches the supervisor
callback, and starts the shared per-process background worker that handles
heartbeats and event flushing.

`checkpoint()` (M2) is for non-LangGraph custom loops; drops into your loop
between long-running steps as a synchronous "should I die?" probe.
"""

from stasis_agent._version import __version__
from stasis_agent._watch import watch
from stasis_agent.exceptions import StasisError, StasisTerminated

__all__ = [
    "StasisError",
    "StasisTerminated",
    "__version__",
    "checkpoint",
    "watch",
]


def checkpoint() -> None:
    """Cooperative termination point for non-LangGraph custom loops. Implemented in M2."""
    raise NotImplementedError("checkpoint() lands in M2")
