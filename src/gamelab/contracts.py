"""The shared shapes between the session, the families and the environments (plan §5).

Concept types, not a public wire format. Every one turns into small JSON (`to_json`) because the
session keeps its record in the task's state. A family's core never sees these objects: it reads and
writes plain JSON states and actions (plan §3.2); the family module wraps them.

The fields marked "extension" in the plan exist from the start so a later family (a long plan of
moves, a check every few moves, meta actions such as undo) fits without changing this contract; the
first families support only their smallest value and the session refuses the rest.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Protocol


class Outcome(StrEnum):
    """How a session ended (plan §6.7). Each is its own report; none stands in for another."""
    GOAL_REACHED = "goal_reached"
    GAME_OVER = "game_over"
    BUDGET_EXHAUSTED = "budget_exhausted"
    USER_STOPPED = "user_stopped"
    NEEDS_RECOVERY = "needs_recovery"
    UNSUPPORTED = "unsupported"


class EnvironmentUnavailable(Exception):
    """The adapter cannot provide the safety contract needed before an input."""


class Delivery(StrEnum):
    DELIVERED = "delivered"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class Effect(StrEnum):
    CONFIRMED = "confirmed"
    ABSENT = "absent"
    INCONSISTENT = "inconsistent"
    UNKNOWN = "unknown"


class GoalStatus(StrEnum):
    REACHED = "reached"
    NOT_REACHED = "not_reached"
    UNKNOWN = "unknown"


class Neutral(StrEnum):
    """Whether no input is left held (a key down, a pointer pressed). Never the game undone."""
    CONFIRMED = "confirmed"
    UNKNOWN = "unknown"


class Objective(StrEnum):
    PLAY_UNTIL_END = "play_until_end"     # play the game on the screen until it ends
    REACH_VALUE = "reach_value"           # a score or tile the family can read
    ACTIONS = "actions"                   # a number of confirmed moves: one move is target_value 1


class Scope(StrEnum):
    CURRENT_GAME = "current_game"
    NEW_GAME = "new_game"
    CURRENT_LEVEL = "current_level"


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
    objective: Objective | str
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

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> GoalSpec:
        return cls(objective=Objective(data["objective"]), target_value=data.get("target_value"),
                   scope=Scope(data.get("scope", Scope.CURRENT_GAME)),
                   stop_conditions=tuple(data.get("stop_conditions") or ()),
                   allowed_meta_actions=frozenset(data.get("allowed_meta_actions") or ()),
                   budget_ref=data.get("budget_ref"))


@dataclass(frozen=True)
class Budget:
    max_actions: int            # inputs sent, whatever came of them
    compute: int                # what a policy may spend on one decision (search nodes)
    max_seconds: float | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Budget:
        return cls(**data)


@dataclass(frozen=True)
class ActionPlan:
    id: str
    based_on_state: str
    rule_revision: int
    policy_revision: str
    # extension: a sequence of logical actions; only a sequence of one is carried out today
    steps: tuple[dict[str, Any], ...]
    # extension: moves between checks against the screen; the family decides, 1 today
    verify_every: int
    preconditions: dict[str, Any] = field(default_factory=dict)
    expected_effect: dict[str, Any] = field(default_factory=dict)
    validity: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {**asdict(self), "steps": list(self.steps)}


@dataclass(frozen=True)
class PreparedInput:
    """One logical action bound to its target, surface epoch and the one input observation it may use."""
    action_id: str
    attempt_id: str
    target: str
    surface_epoch: int
    input_ref: str
    steps: tuple[dict[str, Any], ...]
    expires_at: float | None
    clock_id: str


@dataclass(frozen=True)
class InputReceipt:
    action_id: str
    attempt_id: str
    target: str
    surface_epoch: int
    delivery: Delivery
    # extension: how many steps of the sequence reached the surface; None when that is unknown
    delivered_steps: int | None
    input_neutral: Neutral
    timing: dict[str, float] = field(default_factory=dict)
    observation_ids: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {**asdict(self), "delivery": str(self.delivery), "input_neutral": str(self.input_neutral),
                "observation_ids": list(self.observation_ids)}

    @classmethod
    def unknown(cls, action_id: str, attempt_id: str, target: str, surface_epoch: int) -> InputReceipt:
        """What is known of an input whose answer never came back: nothing."""
        return cls(action_id=action_id, attempt_id=attempt_id, target=target, surface_epoch=surface_epoch,
                   delivery=Delivery.UNKNOWN, delivered_steps=None, input_neutral=Neutral.UNKNOWN)


@dataclass(frozen=True)
class Assessment:
    effect: Effect
    goal: GoalStatus
    terminal: bool | None       # None: the screen did not say
    evidence: str = ""
    next_step: str = ""


class FamilyModule(Protocol):
    """What the session asks of a family (plan §5.2). `judge` reads goal and end from one state, for
    the first look before any move; `assess` judges a move from the states around it."""
    id: str
    baseline_policy: str
    meta_actions: frozenset[str]
    action_kinds: frozenset[str]

    def describe(self) -> dict[str, Any]: ...
    def estimate(self, observation: Observation) -> StateEstimate | NeedObservation: ...
    # excluded: moves to leave out, ones the screen showed had no effect from this very state
    def propose(self, state: StateEstimate, goal: GoalSpec, policy: str, compute: int,
                excluded: tuple[dict[str, Any], ...] = ()) -> ActionPlan | NeedObservation: ...
    def assess(self, before: StateEstimate, plan: ActionPlan, receipt: InputReceipt, after: StateEstimate,
               goal: GoalSpec) -> Assessment: ...
    def judge(self, state: StateEstimate, goal: GoalSpec) -> tuple[GoalStatus, bool]: ...
    # a move's partly unread result that the rules alone show ends the game (an end banner over the tray)
    def ended(self, before: StateEstimate, plan: ActionPlan, after: StateEstimate | NeedObservation) -> bool: ...


class EnvironmentAdapter(Protocol):
    """A surface the session plays on: it observes, sends one prepared input and releases input.
    It knows nothing of strategy and never claims a goal (plan §3)."""

    async def observe(self, target: str, purpose: str) -> Observation: ...
    # whether this observation may still back one input: asks for input authority, then checks it
    # is the surface's latest, unexpired and unconsumed observation (the surface re-checks on send)
    async def ready(self, observation: Observation) -> bool: ...
    async def execute(self, prepared: PreparedInput, context: dict[str, Any]) -> InputReceipt: ...
    async def neutralize(self, context: dict[str, Any]) -> Neutral: ...
    async def lookup(self, action_id: str) -> InputReceipt | None: ...
    # whether the last look shows the button a meta action presses (keep playing after a win)
    def offers(self, kind: str) -> bool: ...
    # a meta action the task did not allow at its start, asked of the person before its input
    async def allow_meta(self, kind: str) -> None: ...
