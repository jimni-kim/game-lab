"""The loop a game read by generated code was played by before the agent's own (`agent`), kept beside it to
compare against (PLAN §10): the first moves explored, an adapter made, the session playing by it, and the
adapter made again from one more look whenever the session stops at a screen it cannot read. Nothing the
agent's loop does goes through here."""
from __future__ import annotations

import logging
import time
from typing import Any

from . import adaptation
from .adaptation import (MAX_RELEARNS, AdaptationFailed, Adaptation, Made, Store, Trace, adopt, explorable, identify)
from .adapters.generated import GeneratedAdapter
from .model import Model
from .recovery import Recovery
from .surface import Surface

log = logging.getLogger("gamelab.baseline")


class Adaptive:
    """One site's game read by generated code for as long as it is played: the adapter kept for the site or
    made now, and made again from one more look whenever play stops at a screen it cannot read (a value the
    game had not shown yet)."""

    def __init__(self, surface: Surface, model: Model, family_id: str, *, store: Store | None = None,
                 policy: str | None = None, compute: int = 200, fresh: bool = False,
                 accept_consent: bool = False) -> None:
        self.surface, self.family_id, self.store, self.fresh = surface, family_id, store, fresh
        # one way past the site's screens for the whole game: while exploring, playing and learning again
        self.recovery = Recovery(model, family_id, accept_consent=accept_consent)
        self.adaptation = Adaptation(model, family_id, policy=policy, compute=compute, recovery=self.recovery)
        self.trace: Trace | None = None
        self.made: Made | None = None
        self.adapter: GeneratedAdapter | None = None
        self.relearned = 0
        self.seconds = 0.0

    def _adapter(self) -> GeneratedAdapter:
        if self.adapter is not None:
            self.adapter.close()
        self.adapter = adaptation.GeneratedAdapter(self.family_id, self.made.source, rules=self.trace.rules, site=self.trace.site)
        return self.adapter

    async def start(self) -> GeneratedAdapter:
        """The adapter to play by. Making one plays the game's first moves on the surface."""
        started = time.monotonic()
        site = await self.surface.address()
        if self.store is not None:
            self.recovery.known = self.store.recoveries(site, self.family_id)
        kept = None if self.store is None or self.fresh else self.store.load(site, self.family_id)
        if kept is not None:
            source, meta, self.trace = kept
            self.made = Made(source, meta["generations"], True, meta["report"])
        else:
            self.trace = await self.adaptation.explore(self.surface)
            self.made = await self.adaptation.make(self.trace)
            if self.store is not None:
                self.store.save(self.trace, self.made)
        self.seconds += time.monotonic() - started
        return self._adapter()

    async def relearn(self, misread: bool = False) -> GeneratedAdapter | None:
        """After play stopped at a screen the adapter left unread: the model reads that screen, and when the
        adapter cannot read it as recorded a new one is written from every look so far. None when the screen
        shows no board the rules accept (nothing to learn: the game is not readable here)."""
        started = time.monotonic()
        try:
            # `misread`: the adapter read the screen whole and wrongly, so nothing of its reading is held to
            look = await self.adaptation.relabel(self.surface, self.trace, None if misread else self.adapter.partial)
            if look is None:
                return None
            self.trace.looks.append(look)
            self.relearned += 1
            report = adopt(self.made.source, self.trace)
            if not report["passed"]:
                made = await self.adaptation.make(self.trace, (self.made.source, report["problems"]))
                self.made = Made(made.source, self.made.generations + made.generations, False, made.report)
            if self.store is not None:
                self.store.save(self.trace, self.made)
            return self._adapter()
        finally:
            self.seconds += time.monotonic() - started

    def summary(self) -> dict[str, Any]:
        return {"reused": self.made.reused if self.made else None, "generations": self.made.generations if self.made else 0,
                "relearned": self.relearned, "looks": len(self.trace.looks) if self.trace else 0,
                "questions": self.adaptation.questions, "seconds": round(self.seconds, 1),
                "recovery": self.recovery.summary(),
                "digest": self.adapter.digest if self.adapter else None,
                "calls": self.adapter.calls if self.adapter else None}

    def close(self) -> None:
        if self.adapter is not None:
            self.adapter.close()
        if self.store is not None and self.trace is not None and self.recovery.known:
            self.store.save_recoveries(self.trace.site, self.family_id, self.recovery.known)


async def adapter_for(surface: Surface, model: Model, family_id: str, *, store: Store | None = None,
                      policy: str | None = None, compute: int = 200, fresh: bool = False
                      ) -> tuple[GeneratedAdapter, Made]:
    """The adapter this site's game is played by: the one kept for it, else one made now from its first moves
    (which are played on the surface) and kept. Either way its first moves in play are trial moves."""
    adaptive = Adaptive(surface, model, family_id, store=store, policy=policy, compute=compute, fresh=fresh)
    adapter = await adaptive.start()
    adaptive.made.questions = adaptive.adaptation.questions
    return adapter, adaptive.made


# a game's endings a new look at the screen may get past: the adapter left the screen unread, or its trial
# moves did not do what the rules predict
RELEARN_REASONS = ("unreadable", "cells_unread", "profile_unconfirmed", "board_not_found", "surface_unsettled")
# moves that did not do what the rules predict on a board read whole: the reading itself may be wrong (a value
# the adapter takes for another), which one more look read by the model shows
MISREAD_REASONS = ("inconsistent", "unknown", "absent")


async def _begin(surface: Surface, model: Model, family_id: str | None, make: Any, store: Store | None,
                 fresh: bool, hint: str | None) -> tuple[Adaptive | None, GeneratedAdapter | None, dict[str, Any]]:
    """The game's `Adaptive` and its adapter, for the family given or, with none given, the one the game is
    found to be: the family a kept adapter of this site names, else each family a model names for the screen,
    likeliest first, taken when its board is read from the screen and its first moves do what its rules say.
    Also what was found on the way (`identified`, `failed`); the adapter is None when none could be made."""
    found: dict[str, Any] = {}
    candidates = [family_id]
    if family_id is None:
        site = await surface.address()
        kept = [] if store is None or fresh else [name for name in explorable() if store.load(site, name) is not None]
        if kept:
            candidates, found["identified"] = kept[:1], {"family": kept[0], "source": "kept", "questions": 0}
        else:
            asked = await identify(surface, model, Recovery(model, None, known=[]), hint=hint)
            candidates = asked["families"]
            found["identified"] = {"family": None, "source": "model", "candidates": candidates, "tried": [],
                                   "questions": asked["questions"], "inputs": asked["inputs"],
                                   "situation": asked["situation"]}
            if not candidates:
                return None, None, {**found, "failed": "game_not_identified", "detail": asked["situation"] or ""}
    adaptive, failure = None, AdaptationFailed("game_not_identified")
    for candidate in candidates:
        if adaptive is not None:
            adaptive.close()
        adaptive = make(candidate)
        asked = found.get("identified", {}).get("source") == "model"
        try:
            if asked:
                found["identified"]["tried"].append(candidate)
                # a family the screen's board cannot be read as is not this game: the next one is tried
                if not await adaptive.adaptation.probe(surface):
                    failure = AdaptationFailed("game_not_identified", f"no {candidate} board was read from the screen")
                    continue
            adapter = await adaptive.start()
        except Exception as error:
            # a model that could not be reached while the adapter was made ends the game too, with its record
            if not isinstance(error, AdaptationFailed):
                log.exception("adaptation failed")
                error = AdaptationFailed("model_failed", f"{type(error).__name__}: {error}")
            failure = error
            # first moves that did not do what this family's rules say: the game may be another's
            if asked and error.reason == "label_failed":
                continue
            break
        if "identified" in found:
            found["identified"]["family"] = candidate
        return adaptive, adapter, found
    return adaptive, None, {**found, "failed": failure.reason, "detail": failure.detail[:2000]}


async def play_adaptive(surface: Surface, model: Model, family_id: str | None, *, goal: Any, budget: Any,
                        store: Store | None = None, policy: str | None = None, fresh: bool = False,
                        hint: str | None = None, **options: Any) -> tuple[Any, dict[str, Any]]:
    """One game read by generated code from its first screen to its end: `play` by the site's adapter, and
    each time it stops at a screen the adapter cannot read, one more look learned and play taken up again
    with what is left of the budget. Returns the whole game as one `Run` and how the adapter was made.
    `family_id` None: nobody says which game this is, and it is found out from the screen (`_begin`); `hint`
    is what the person said of it, if anything."""
    from .contracts import Budget, GoalSpec, Outcome
    from .play import Run, play
    from .session import ended

    def make(family: str) -> Adaptive:
        return Adaptive(surface, model, family, store=store, policy=policy, compute=budget.compute, fresh=fresh,
                        accept_consent=options.get("accept_consent", False))

    started = time.monotonic()
    whole = Run(record={})
    used = confirmed = 0
    adaptive = None
    try:
        try:
            adaptive, adapter, found = await _begin(surface, model, family_id, make, store, fresh, hint)
        except Exception as error:
            log.exception("identification failed")
            adapter, found = None, {"failed": "model_failed", "detail": f"{type(error).__name__}: {error}"[:2000]}
        if adapter is None:
            reason = found["failed"] if found["failed"] == "game_not_identified" else f"adaptation_{found['failed']}"
            whole.record = ended(Outcome.UNSUPPORTED, reason, family_id=adaptive.family_id if adaptive else family_id)
            whole.recoveries = list(adaptive.recovery.log) if adaptive else []
            return whole, {**(adaptive.summary() if adaptive else {}), **found}
        family_id = adaptive.family_id
        stalled = 0
        while True:
            left = None if budget.max_seconds is None else budget.max_seconds - (time.monotonic() - started)
            target = goal.target_value - confirmed if goal.objective == "actions" else goal.target_value
            run = await play(surface, adapter=adapter, policy=policy, model=model, recovery=adaptive.recovery,
                             goal=GoalSpec(goal.objective, target, goal.scope, goal.stop_conditions, goal.allowed_meta_actions),
                             budget=Budget(budget.max_actions - used, budget.compute, left), **options)
            record = run.record
            used += record.get("actions_used", 0)
            confirmed += record.get("confirmed_actions", 0)
            whole.moves += [{**move, "round": adaptive.relearned} for move in run.moves]
            whole.looks += run.looks
            whole.recoveries = list(adaptive.recovery.log)
            whole.geometry, whole.vision, whole.last_screen = run.geometry, run.vision, run.last_screen
            whole.record = {**record, "actions_used": used, "confirmed_actions": confirmed}
            stalled = 0 if record.get("confirmed_actions") else stalled + 1
            if (record.get("outcome") not in ("needs_recovery", "unsupported") or record.get("reason") not in (*RELEARN_REASONS, *MISREAD_REASONS)
                    or adaptive.relearned >= MAX_RELEARNS or stalled >= 2 or used >= budget.max_actions
                    or (left is not None and left <= 0)):
                break
            try:
                adapter = await adaptive.relearn(misread=record.get("reason") in MISREAD_REASONS)
            except AdaptationFailed as error:
                return whole, {**adaptive.summary(), **found, "failed": error.reason, "detail": error.detail[:2000]}
            if adapter is None:
                break
        return whole, {**adaptive.summary(), **found}
    finally:
        if adaptive is not None:
            adaptive.close()
