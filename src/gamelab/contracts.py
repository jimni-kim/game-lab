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
    # a condition the person set for stopping was met (PLAN §25, R1): the work is handed back there
    STOPPED = "stopped"


class Scope(StrEnum):
    """Where the work starts (PLAN §25, R1 and R4)."""
    # the screen as it stands: nothing is started again, the game or level on it is played on (이어받기)
    CURRENT_GAME = "current_game"
    # the app or page started again first, by the host, as a person opening it would
    NEW_GAME = "new_game"


# what the person may ask for: the game on the screen to its end, a score or tile the family reads, a number of
# confirmed moves, a number of levels cleared one after another (PLAN §25, R1), or levels cleared up to one of them
# (`until_level`: target_value is the number of the last level to clear, as the game shows it on the screen)
OBJECTIVES = ("play_until_end", "reach_value", "actions", "levels", "until_level")
# the things beside the board that cost or use up something of the player's, each done only when the person allowed
# it by name (PLAN §25, R2). Nothing that costs real money is among them: that is never done
META_ACTIONS = {
    "boosters": "use a booster, power-up or other helper the game holds or offers outside the board's own pieces",
    "currency": "spend coins, gems, stars, energy or another of the game's currencies, on a purchase or on a task "
                "of its map or story",
    "continue": "take extra moves, time or a life to keep a level going after it ran out",
    "retry": "play a lost level again (it may cost a life)",
}
# what the person need not allow: a booster the game hands over for nothing and will not go on without (a
# tutorial that waits for it to be used), as the model says of the input (`forced_free`). Nothing else is so
FORCED_FREE = frozenset({"boosters"})
# a stop condition the code checks itself: the work ends at the first level or game lost, even when `retry` is
# allowed. Any other condition is the person's own words, told to every model that looks at the screen, which may
# answer that it is met
LOST = "lost"


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
    # actions: a number of confirmed moves (one move is target_value 1); levels: that many levels cleared;
    # until_level: levels cleared up to the one the game numbers target_value on its screen
    objective: str
    target_value: int | None = None
    scope: Scope | str = Scope.CURRENT_GAME
    # when to stop and hand the work back short of the objective: LOST, or the person's own words
    stop_conditions: tuple[str, ...] = ()
    # the meta actions the person allowed, by name (META_ACTIONS); none unless they said so
    allowed_meta_actions: frozenset[str] = frozenset()
    budget_ref: str | None = None
    # how the person wants a story's choices made, in their own words ("the kind way"); None when they said
    # nothing of it: a choice that cannot be taken back is then not made, the work is handed back there (PLAN §27)
    choices: str | None = None

    def __post_init__(self) -> None:
        unknown = set(self.allowed_meta_actions) - set(META_ACTIONS)
        if unknown:
            raise ValueError(f"unknown meta actions: {sorted(unknown)}")

    def to_json(self) -> dict[str, Any]:
        return {"objective": str(self.objective), "target_value": self.target_value, "scope": str(self.scope),
                "stop_conditions": list(self.stop_conditions),
                "allowed_meta_actions": sorted(self.allowed_meta_actions), "budget_ref": self.budget_ref,
                **({"choices": self.choices} if self.choices else {})}


@dataclass(frozen=True)
class Budget:
    max_actions: int            # inputs sent, whatever came of them
    compute: int                # what a policy may spend on one decision (search nodes)
    max_seconds: float | None = None
    # the tokens every model question of one game may send together (None: not counted against the game)
    max_input_tokens: int | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)
