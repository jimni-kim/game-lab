"""The family module the session talks to: one class over a rule model and its policies (plan §5.2;
adaptive perception plan §3).

A family is its rule model (`rules/<id>.py`: rules, legal moves, transition check, all in JSON), its
solver (`solvers/<id>.py`: the registered policies) and its descriptor (`blueprint/families/<id>.json`).
This class only turns the session's contract types into their JSON and back, so a new family is a new
rule model, solver and descriptor with no change here or in the session, as long as they offer
`RULE_FUNCTIONS`, `RULE_VALUES`, `SOLVER_FUNCTIONS` and `SOLVER_VALUES` (`catalog.descriptor` checks
them when the family is loaded).
"""
from __future__ import annotations

import hashlib
import json
from types import ModuleType
from typing import Any

from ..contracts import (
    ActionPlan,
    Assessment,
    Effect,
    GoalSpec,
    GoalStatus,
    InputReceipt,
    NeedObservation,
    Observation,
    StateEstimate,
)


def state_id(payload: dict[str, Any]) -> str:
    """A short stable name for a state: the same board always gets the same name."""
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


class GridFamily:
    def __init__(self, descriptor: dict[str, Any], model: ModuleType, solver: ModuleType,
                 profile: dict[str, Any] | None = None) -> None:
        self.descriptor = descriptor
        # the rule model the session trusts, and the policies that choose moves over it
        self.model = model
        self.solver = solver
        self.id: str = descriptor["id"]
        self.revision: int = descriptor["revision"]
        self.baseline_policy: str = descriptor["baseline_policy"]
        # the site or app profile this family plays under (its rule values); bound once per session
        self.profile = profile or {}
        self.rules = self._rules(self.profile)
        self.meta_actions = frozenset(m["id"] for m in descriptor["meta_actions"])
        # meta actions pressed at a button one of the game's scenes shows, by that scene
        self.buttons = {m["id"]: m["button_on"] for m in descriptor["meta_actions"] if "button_on" in m}
        self.action_kinds = frozenset(a["kind"] for a in descriptor["actions"]) | self.meta_actions
        self.verify_every: int = descriptor["verify_every"]

    def describe(self) -> dict[str, Any]:
        return self.descriptor

    def bind(self, profile: dict[str, Any] | None) -> GridFamily:
        return GridFamily(self.descriptor, self.model, self.solver, profile)

    def _rules(self, profile: dict[str, Any]) -> dict[str, Any]:
        """The standard rules with what a profile sets; a profile may set only the parameters the
        family implements (an unknown rule is a code change, never a setting, plan §4.1)."""
        chosen = dict(profile.get("rules") or {})
        if unknown := sorted(set(chosen) - set(self.model.DEFAULT_RULES)):
            raise ValueError(f"rules the {self.id} family does not implement: {unknown}")
        return {**self.model.DEFAULT_RULES, **chosen}

    def estimate(self, observation: Observation) -> StateEstimate | NeedObservation:
        payload = observation.public_facts.get("state")
        if not isinstance(payload, dict):
            return NeedObservation("no_state")
        if self.model.validate(payload, self.rules):
            return NeedObservation("unreadable")
        return StateEstimate(id=state_id(payload), observation_ids=(observation.id,), family_id=self.id,
                             state_schema=self.descriptor["state_schema"], rule_revision=self.revision,
                             payload=payload, unknown_fields=tuple(self.model.unknown_fields(payload)),
                             evidence=observation.id, geometry_ref=observation.geometry_ref)

    def propose(self, state: StateEstimate, goal: GoalSpec, policy: str, compute: int,
                excluded: tuple[dict[str, Any], ...] = ()) -> ActionPlan | NeedObservation:
        if state.unknown_fields:
            return NeedObservation("unknown_cells")
        chosen = self.solver.choose(state.payload, self.rules, policy, compute, list(excluded))["action"]
        if chosen is None:
            return NeedObservation("no_legal_action")
        return ActionPlan(id=f"plan-{state.id}", based_on_state=state.id, rule_revision=self.revision,
                          policy_revision=f"{self.id}.{policy}.{self.revision}", steps=(chosen,),
                          verify_every=self.verify_every)

    def explain(self, before: dict[str, Any], action: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
        """The values the rules give a move's unread cells (`[{"cell": [r, c], "value": v}]`), or []."""
        return self.model.explain(before, action, after, self.rules)

    def ended(self, before: StateEstimate, plan: ActionPlan, after: StateEstimate | NeedObservation) -> bool:
        """Whether a move's partly unread result is still, by the rules alone, the end of the game."""
        return isinstance(after, StateEstimate) and self.model.ended(before.payload, plan.steps[0], after.payload, self.rules)

    def judge(self, state: StateEstimate, goal: GoalSpec) -> tuple[GoalStatus, bool]:
        seen = self.model.status(state.payload, self.rules, _goal(goal))
        return GoalStatus(seen["goal"]), bool(seen["terminal"])

    def assess(self, before: StateEstimate, plan: ActionPlan, receipt: InputReceipt, after: StateEstimate | None,
               goal: GoalSpec) -> Assessment:
        """The move's effect from the states around it, whatever the receipt says: a board that did
        not change does not prove the input never arrived, nor does a delivery prove the effect."""
        if after is None or after.unknown_fields:
            return Assessment(effect=Effect.UNKNOWN, goal=GoalStatus.UNKNOWN, terminal=None,
                              evidence=after.id if after else "")
        effect = Effect(self.model.assess(before.payload, plan.steps[0], after.payload, self.rules)["effect"])
        reached, terminal = self.judge(after, goal)
        return Assessment(effect=effect, goal=reached, terminal=terminal, evidence=after.id)

    def unread_state(self, scene: str) -> dict[str, Any]:
        """The state of a screen whose board's place the game's own `scene` message fills."""
        return self.model.unread_state(self.rules, scene)

    # the replay environment plays the family's own game through these (seeded by the caller)
    def initial_state(self, draw: int) -> dict[str, Any]:
        return self.model.initial_state(self.rules, draw)

    def next_state(self, payload: dict[str, Any], action: dict[str, Any], draw: int) -> dict[str, Any]:
        return self.model.next_state(payload, self.rules, action, draw)

    def shown(self, payload: dict[str, Any]) -> dict[str, Any]:
        """The replay game's state as its screen shows it: what the game hides (where mines lie) left out."""
        return self.model.shown(payload)

    def hidden_state(self, payload: dict[str, Any]) -> dict[str, Any]:
        """The state as a screen that hides one of its values shows it (the value unread)."""
        return self.model.hidden_state(payload)

    def stray_state(self, payload: dict[str, Any]) -> dict[str, Any]:
        """The state with one change no move makes (a stray tap, a lag): a fault the replay injects."""
        return self.model.stray_state(payload)


def _goal(goal: GoalSpec) -> dict[str, Any]:
    return {"objective": str(goal.objective), "target_value": goal.target_value}
