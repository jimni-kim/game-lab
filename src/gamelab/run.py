"""What one game leaves behind for whoever started it, and the check of what it was asked to play."""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any

from . import catalog
from .contracts import Budget, GoalSpec


@dataclass
class Run:
    record: dict[str, Any]
    # every move the game recorded (its own record keeps only the last few)
    moves: list[dict[str, Any]] = field(default_factory=list)
    geometry: dict[str, Any] | None = None
    vision: dict[str, Any] | None = None
    looks: int = 0
    last_screen: bytes | None = None
    # each time play stopped on a screen and what a model did about it (`recovery.Recovery.log`)
    recoveries: list[dict[str, Any]] = field(default_factory=list)


def check(goal: GoalSpec, budget: Budget, family_id: str, policy: str | None) -> str:
    """The policy a run plays, once its goal, budget and policy are ones the family can be run with."""
    if goal.objective in ("reach_value", "actions", "levels") and goal.target_value is None:
        raise ValueError("goal_target_missing")
    if budget.max_seconds is not None and not (math.isfinite(budget.max_seconds) and budget.max_seconds > 0):
        raise ValueError("invalid_budget")
    family = catalog.family(family_id)
    policy = policy or family.descriptor["default_policy"]
    if policy not in family.policies():
        raise ValueError("policy_unknown")
    return policy
