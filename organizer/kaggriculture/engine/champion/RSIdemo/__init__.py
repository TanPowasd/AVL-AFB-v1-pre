"""RSIdemo: a small, observation-driven Kaggriculture strategy.

The public entry point is :func:`agent`, which follows the same contract as
the other submissions in this repository.
"""

from .agent import RSIPolicy, agent

__all__ = ["RSIPolicy", "agent"]
