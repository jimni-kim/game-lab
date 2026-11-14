"""The shared shapes between the agent's loop, the families and the adapters (plan §5).

Concept types, not a public wire format. A family's core never sees these objects: it reads and writes
plain JSON states and actions (plan §3.2); the family module wraps them.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class Outcome(StrEnum):
    """How a game ended (plan §6.7). Each is its own report; none stands in for another."""
    GOAL_REACHED = "goal_reached"
    GAME_OVER = "game_over"
    BUDGET_EXHAUSTED = "budget_exhausted"
    USER_STOPPED = "user_stopped"
    NEEDS_RECOVERY = "needs_recovery"
    # a model saw nothing more it could do, or its inputs ran out
    GAVE_UP = "gave_up"
    UNSUPPORTED = "unsupported"


class Scope(StrEnum):
    CURRENT_GAME = "current_game"
    NEW_GAME = "new_game"
    CURRENT_LEVEL = "current_level"


# why a game ends when its board is found but some of its cells are a look the reading does not know: nothing
# is in the way (no model is asked about the screen), the reading itself must learn them
CELLS_UNREAD = "cells_unread"


@dataclass(frozen=True)
class Observation:
    id: str
    sequence: int
    target: str
    surface_epoch: int
    captured_at: float          # on the surface's own monotonic clock (clock_id), never wall time
    clock_id: str
    public_facts: dict[str, Any] = field(default_factory=dict)
    image_ref: str | None = None
    geometry_ref: str | None = None
    input_ref: str | None = None   # set only on an observation taken for input: the one input it allows


@dataclass(frozen=True)
class StateEstimate:
    id: str
    observation_ids: tuple[str, ...]
    family_id: str
    state_schema: str
    rule_revision: int
    payload: dict[str, Any]
    unknown_fields: tuple[str, ...]
    evidence: str
    geometry_ref: str | None = None


@dataclass(frozen=True)
class NeedObservation:
    """The family cannot go on from what it was shown: look again, never guess."""
    reason: str


@dataclass(frozen=True)
class GoalSpec:
    # play_until_end: the game on the screen until it ends; reach_value: a score or tile the family can read;
    # actions: a number of confirmed moves (one move is target_value 1)
    objective: str
    target_value: int | None = None
    scope: Scope | str = Scope.CURRENT_GAME
    stop_conditions: tuple[str, ...] = ()
    # extension: the meta actions the person allowed, by name; a family declares which exist
    allowed_meta_actions: frozenset[str] = frozenset()
    budget_ref: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {"objective": str(self.objective), "target_value": self.target_value, "scope": str(self.scope),
                "stop_conditions": list(self.stop_conditions),
                "allowed_meta_actions": sorted(self.allowed_meta_actions), "budget_ref": self.budget_ref}


@dataclass(frozen=True)
class Budget:
    max_actions: int            # inputs sent, whatever came of them
    compute: int                # what a policy may spend on one decision (search nodes)
    max_seconds: float | None = None
    # the tokens every model question of one game may send together (None: not counted against the game)
    max_input_tokens: int | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)
