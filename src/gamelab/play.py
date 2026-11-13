"""One game on a screen, from a look to its end: the pieces put together for a run.

A run names a registered profile, or only the game's family for a game no profile registers (its reading
is then made from a model's look the code confirms, `discovery`, and its first moves are trial moves).
Nothing is kept beyond the run: its record, the measured layout, a profile made for it and the model
reader's record come back in `Run`, for whoever started it to write down.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass, field
import math
import time
from typing import Any
import uuid

from . import catalog, profiles
from .adapters.reference import ReferenceAdapter
from .contracts import Budget, GoalSpec, Outcome
from .environments.screen import ScreenEnvironment, Surface, Watch
from .model import Model
from .session import GameSession, SettlingPolicy, ended
from .vision import DEFAULT_VISION, CorrectionReader, VisionBudget

# the first moves of a game played by a profile made in the run that must each do what the rules predict
# before play goes on: a misread value, a wrong size or a key the page ignores shows in them
TRIAL_MOVES = 3


@dataclass
class Run:
    record: dict[str, Any]
    # every move the session recorded (its own record keeps only the last few)
    moves: list[dict[str, Any]] = field(default_factory=list)
    geometry: dict[str, Any] | None = None
    made_profile: dict[str, Any] | None = None
    vision: dict[str, Any] | None = None
    looks: int = 0
    last_screen: bytes | None = None


class _Guards:
    def __init__(self, cancel: asyncio.Event) -> None:
        self.cancel = cancel

    async def cancelled(self) -> bool:
        return self.cancel.is_set()

    async def lease_held(self) -> bool:
        return True


def check(goal: GoalSpec, budget: Budget, family_id: str, policy: str | None) -> str:
    """The policy a run plays, once its goal, budget and policy are ones the family can be run with."""
    if goal.objective in ("reach_value", "actions") and goal.target_value is None:
        raise ValueError("goal_target_missing")
    if budget.max_seconds is not None and not (math.isfinite(budget.max_seconds) and budget.max_seconds > 0):
        raise ValueError("invalid_budget")
    family = catalog.family(family_id)
    policy = policy or family.descriptor["default_policy"]
    if policy not in family.policies():
        raise ValueError("policy_unknown")
    return policy


async def play(surface: Surface, *, profile: profiles.Profile | None = None, family_id: str | None = None,
               goal: GoalSpec, budget: Budget, policy: str | None = None, model: Model | None = None,
               vision: dict[str, Any] | None = None, cancel: asyncio.Event | None = None,
               watch: Watch | None = None, accept_consent: bool = False, run_id: str | None = None) -> Run:
    if (profile is None) == (family_id is None):
        raise ValueError("give a profile or a family")
    unregistered = profile is None
    family_id = family_id or profile.family_id
    if unregistered and family_id not in catalog.DISCOVERY:
        raise ValueError("family_not_discoverable")
    if not unregistered and (profile.data["fixture_only"] or "controls" not in profile.data):
        raise ValueError("profile_not_registered")
    policy = check(goal, budget, family_id, policy)
    cancel = cancel or asyncio.Event()
    run = Run(record={})
    site = await surface.address() if unregistered else None
    environment = ScreenEnvironment(surface, ReferenceAdapter(profile, family_id=family_id, site=site),
                                    watch=watch, accept_consent=accept_consent)
    if (reason := environment.unsupported()) is not None:
        run.record = ended(Outcome.UNSUPPORTED, reason, family_id=family_id)
        return run

    async def save(record: dict[str, Any]) -> None:
        for entry in record.get("history") or []:
            moves[entry["action_id"]] = entry

    async def save_geometry(record: dict[str, Any] | None) -> None:
        run.geometry = deepcopy(record)

    async def save_vision(record: dict[str, Any]) -> None:
        run.vision = record

    moves: dict[str, dict[str, Any]] = {}
    environment.save_geometry = save_geometry
    environment.reader = CorrectionReader(profile, model, run_id=run_id or uuid.uuid4().hex,
                                          budget=VisionBudget(**(vision or DEFAULT_VISION)), save=save_vision,
                                          current=environment.latest, cancelled=cancel.is_set)
    run.vision = environment.reader.record
    family = catalog.family(family_id).bind(profile.data if profile else None)
    session = GameSession.start(family, environment, goal=goal, budget=budget, policy=policy,
                                target=environment.target, clock=time.monotonic, save=save, guards=_Guards(cancel),
                                trial=TRIAL_MOVES if unregistered else 0)
    if budget.max_seconds is not None:
        environment.deadline = session.record["started_at"] + budget.max_seconds
    session.settling = SettlingPolicy(**environment.adapter.played.get("settling", {}))

    async def save_profile(made: profiles.Profile) -> None:
        """Keep the profile made for this game, and play by its rules from now on."""
        run.made_profile = {"data": deepcopy(made.data), "ref": made.geometry_ref}
        environment.reader.adopt(made)
        session.family = catalog.family(family_id).bind(made.data)
        if "settling" not in session.record:
            session.settling = SettlingPolicy(**made.data.get("settling", {}))

    environment.save_profile = save_profile
    run.record = await session.run()
    run.moves, run.looks, run.last_screen = list(moves.values()), environment.looks, environment.evidence
    return run
