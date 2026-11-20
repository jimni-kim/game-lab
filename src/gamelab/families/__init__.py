"""One class over a family's rule model and its policies (plan §5.2; adaptive perception plan §3).

A family is its rule model (`rules/<id>.py`: rules, legal moves, transition check, all in JSON), its
solver (`solvers/<id>.py`: the registered policies) and its descriptor (`blueprint/families/<id>.json`).
This class binds them to a game's rule values and turns a read state into a `StateEstimate`, so a new
family is a new rule model, solver and descriptor with no change here, as long as they offer
`RULE_FUNCTIONS`, `RULE_VALUES`, `SOLVER_FUNCTIONS` and `SOLVER_VALUES` (`catalog.descriptor` checks
them when the family is loaded).
"""
from __future__ import annotations

import hashlib
import json
from types import ModuleType
from typing import Any

from .. import solvers
from ..contracts import NeedObservation, Observation, StateEstimate


def state_id(payload: dict[str, Any]) -> str:
    """A short stable name for a state: the same board always gets the same name."""
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


class GridFamily:
    def __init__(self, descriptor: dict[str, Any], model: ModuleType, solver: ModuleType,
                 profile: dict[str, Any] | None = None) -> None:
        self.descriptor = descriptor
        # the rule model the harness trusts, and the policies that choose moves over it
        self.model = model
        self.solver = solver
        self.id: str = descriptor["id"]
        self.revision: int = descriptor["revision"]
        self.baseline_policy: str = descriptor["baseline_policy"]
        # what this family plays under (`{"rules": ...}`: the game's rule values); bound once per game
        self.profile = profile or {}
        self.rules = self._rules(self.profile)
        self.meta_actions = frozenset(m["id"] for m in descriptor["meta_actions"])

    def policies(self) -> list[str]:
        """Every policy a run may name: the family's own, then the general solvers its rule model fits."""
        return [*self.descriptor["policies"], *solvers.generic_policies(self.model)]

    def bind(self, profile: dict[str, Any] | None) -> GridFamily:
        return GridFamily(self.descriptor, self.model, self.solver, profile)

    def _rules(self, profile: dict[str, Any]) -> dict[str, Any]:
        """The standard rules with what is bound; only the parameters the family implements may be set
        (an unknown rule is a code change, never a setting, plan §4.1)."""
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

    def recall(self, memory: dict[str, Any] | None, payload: dict[str, Any]) -> dict[str, Any] | None:
        """What the family's solver keeps of a game once it has seen `payload` too (JSON, the solver's own:
        the cards a stock has shown); None for a solver that keeps nothing. A game starts with None."""
        recall = getattr(self.solver, "recall", None)
        return recall(memory, payload, self.rules) if recall is not None else None

    def spent(self, memory: dict[str, Any] | None) -> bool:
        """Whether what the solver kept shows a game with nothing left to gain though it still offers moves."""
        spent = getattr(self.solver, "spent", None)
        return bool(memory is not None and spent is not None and spent(memory, self.rules))

    def choose(self, payload: dict[str, Any], policy: str, compute: int,
               excluded: tuple[dict[str, Any], ...] = (), memory: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """The move `policy` picks on a fully read state: the family's own solver's, or a general solver's
        over the rule model alone. `memory` is what `recall` kept of this game, for a solver that keeps any."""
        if policy in solvers.GENERIC:
            return solvers.GENERIC[policy].choose(payload, self.rules, self.model, compute, list(excluded))["action"]
        if memory is not None:
            return self.solver.choose(payload, self.rules, policy, compute, list(excluded), memory)["action"]
        return self.solver.choose(payload, self.rules, policy, compute, list(excluded))["action"]

    def rank(self, payload: dict[str, Any], compute: int, excluded: tuple[dict[str, Any], ...] = ()) -> list[dict[str, Any]]:
        """The best moves on a fully read state as the family's solver weighs them, best first, each with what
        the rules say it does (`action`, `cleared`, `made`, `taken`, `towards_goals`); none when the solver ranks none."""
        rank = getattr(self.solver, "rank", None)
        return rank(payload, self.rules, compute, list(excluded)) if rank is not None else []

    def unread_state(self, scene: str) -> dict[str, Any]:
        """The state of a screen whose board's place the game's own `scene` message fills."""
        return self.model.unread_state(self.rules, scene)

    # a simulated game (`eval.policies`) is the family's own, played through these (seeded by the caller)
    def initial_state(self, draw: int) -> dict[str, Any]:
        return self.model.initial_state(self.rules, draw)

    def next_state(self, payload: dict[str, Any], action: dict[str, Any], draw: int) -> dict[str, Any]:
        return self.model.next_state(payload, self.rules, action, draw)

    def shown(self, payload: dict[str, Any]) -> dict[str, Any]:
        """A simulated game's state as its screen shows it: what the game hides (where mines lie) left out."""
        return self.model.shown(payload)
