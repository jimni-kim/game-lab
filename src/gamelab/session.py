"""The one loop every game session runs (plan §6): observe, estimate, plan, send one checked input,
confirm its effect, go on. One move and a whole game are the same loop with a different stop.

The session keeps its record as small JSON (`record`), saved through `save` into the task's state:
no table of its own. It holds no model, tool or surface itself: a family judges the game, an
environment observes and sends input, and `guards` tell it whether the task was cancelled or lost its
lease. What it owns are the safety conditions of plan §6.3:

- the intent (action id, plan, the state it was made on) is saved before an input is sent, and an
  input whose fate is unknown is never sent again: after a restart its action id is looked up, the
  screen is read again, and the next move is planned afresh;
- a move's confirmed result is the next move's state and its input evidence: no second look. The
  surface re-checks that observation's pixels and document right before sending; one that expired
  or was refused is read afresh, and a changed board or surface drops the plan;
- cancel and a lost lease stop new input and ask the environment to release whatever is held; a
  release nobody confirmed stays `cleanup_pending`, never reported as done;
- a cell nobody could read is never planned on;
- a move delivered with no effect is not chosen again from the same state: the next plan picks
  another (a game may ignore an input it does not take as a move, and would ignore it again);
- delivery, effect, goal and end are four separate findings, recorded as such;
- something that is not the game covering the board (a dialog, a notice) is closed with the one control
  the screen shows for it, as an input of the session's own, saved and counted like a move; a page
  that keeps covering its board ends the session.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, replace
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from .contracts import (
    ActionPlan,
    Assessment,
    Budget,
    Delivery,
    Effect,
    EnvironmentAdapter,
    EnvironmentUnavailable,
    FamilyModule,
    GoalSpec,
    GoalStatus,
    InputReceipt,
    NeedObservation,
    Neutral,
    Objective,
    Observation,
    Outcome,
    PreparedInput,
    StateEstimate,
)
from .errors import Denied, Interrupted
from .recovery import MAX_TRIES

log = logging.getLogger("gamelab.session")

RECORD_VERSION = 1
# reads of a screen that could not be used (unknown cells, a state that cannot be) before giving up
MAX_UNREADABLE = 3
# moves in a row whose effect was not the one predicted (or could not be told) before giving up
MAX_RECOVERIES = 3
# plans thrown away in a row because the screen changed before input, before giving up
MAX_DISCARDED = 3
# inputs in a row the surface refused before sending any of it (the page changed under them: an advertisement
# reloading beside the board), before giving up; they say nothing of the game, so more are borne than effects
MAX_REFUSED = 10
INPUT_REFUSED = "input_refused"
# a game whose profile was made in the session waits, after its trial moves, the longest they took to come
# to rest plus this margin, and then looks once (`still` off): the longest a settling policy may wait
ANIMATION_MARGIN_S = 0.1
# why a game ends on a scene whose button (keep playing) could not be found on its screen
BUTTON_UNSEEN = "button_unseen"
# why a game that reached its goal ends on the game's own win message
WON = "won"
# why a game ends when a trial move does not do what the rules predict
TRIAL_FAILED = "profile_unconfirmed"
ANIMATION_MAX_S = 2.0
# a game that must be still but whose pixels never rest (tiles that keep animating while it waits for a move)
# is taken as settled once its state has read the same for this long
RESTLESS_S = 1.0
# moves kept in the record: enough to explain how it ended, small enough for the task's state
HISTORY_KEPT = 8
# how long a prepared input stays valid, on the surface's clock
INPUT_VALID_S = 5.0
# the session's own input, not the game's: closing what covers the board, at the control the environment
# found for it (an observation whose public facts say `covered`); the person's approval names it
CLOSE_COVER = "close_cover"
# the session's other inputs at a cover: refusing a request to agree to the site's privacy terms, agreeing to
# one that offers no refusal (only once the person allows it, asked then), and the game's own start button
# before any board was read (later it might start another game)
DECLINE_CONSENT, ACCEPT_CONSENT, START_GAME = "decline_consent", "accept_consent", "start_game"
UNCOVERING = (CLOSE_COVER, DECLINE_CONSENT, ACCEPT_CONSENT, START_GAME)
# those the game's input approval names from the start; agreeing is asked of the person when it is needed
UNASKED = (CLOSE_COVER, DECLINE_CONSENT, START_GAME)
# why a game ends when the person would not agree to the terms a site showed in front of it
CONSENT_DECLINED = "consent_declined"
# a look at a board read whole that took a delivered move without effect: what stops play is asked of the
# whole screen once (a win notice beside the board, a notice taking the keys)
STALLED = "stalled"
# covers closed in a row, with no move confirmed between them, before giving up: a page that keeps covering
# its board is not played on, while a game that shows a notice now and then is
MAX_COVERS = 3
# why a game would end that a look by a model may get past (`_recover`): the screen, not the game, stopped it
STUCK = frozenset({"board_covered", "unreadable", "surface_unsettled", TRIAL_FAILED, BUTTON_UNSEEN,
                   str(Effect.ABSENT), str(Effect.INCONSISTENT), str(Effect.UNKNOWN)})

# a session calibrates until a look first reads the board (the environment measures it there, a model
# may say where it is, a cover may be closed), then runs: no move is ever planned while calibrating
CALIBRATING = "calibrating"
ENDED = "ended"
RUNNING = "running"
CLEANUP_NONE, CLEANUP_CONFIRMED, CLEANUP_PENDING = "none", "confirmed", "pending"


@dataclass(frozen=True)
class SettlingPolicy:
    """Bounded observation cadence; stability concerns family state, not the whole image.
    animation_s: how long the game animates a move, waited before its result is looked at.
    still: a move's result counts only once two looks in a row also show the same pixels where input is
    checked (`Observation.image_ref`), for a game whose moves leave effects fading over the board after
    its state is already the predicted one: input made on those pixels would be refused."""
    timeout_s: float = 4.0
    interval_s: float = 0.15
    stable_s: float = 0.25
    animation_s: float = 0.0
    still: bool = False


class Guards(Protocol):
    async def cancelled(self) -> bool: ...
    async def lease_held(self) -> bool: ...


class _End(Exception):
    def __init__(self, outcome: Outcome, reason: str) -> None:
        super().__init__(reason)
        self.outcome = outcome
        self.reason = reason


def ended(outcome: Outcome, reason: str, *, family_id: str | None = None) -> dict[str, Any]:
    """The record of a session that ended before it started (no environment, a family not known)."""
    return {"version": RECORD_VERSION, "family": family_id, "phase": ENDED, "outcome": str(outcome),
            "reason": reason, "intent": None, "cleanup": CLEANUP_NONE, "actions_used": 0,
            "confirmed_actions": 0, "history": []}


class GameSession:
    def __init__(self, family: FamilyModule, environment: EnvironmentAdapter, record: dict[str, Any], *,
                 clock: Callable[[], float], save: Callable[[dict[str, Any]], Awaitable[None]],
                 guards: Guards, settling: SettlingPolicy | None = None,
                 wait: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self.family = family
        self.env = environment
        self.record = record
        self.clock = clock
        self.save = save
        self.guards = guards
        self.settling, self.wait = settling, wait
        self.goal = GoalSpec.from_json(record["goal"])
        self.budget = Budget.from_json(record["budget"])
        # the last confirmed result (state and the observation it was read from): the next move's
        # starting point, never saved, since its observation dies with this process
        self.carried: tuple[StateEstimate, Observation] | None = None
        # when the pixels the last settled look showed were first seen (the move's animation over)
        self.rested_at: float | None = None
        # the last board read, told to whoever is asked about a screen play stopped on
        self.seen_state: dict[str, Any] | None = None
        # the settling the trial moves measured, kept in the record so a restart waits the same
        if isinstance(record.get("settling"), dict):
            self.settling = SettlingPolicy(**record["settling"])

    @classmethod
    def start(cls, family: FamilyModule, environment: EnvironmentAdapter, *, goal: GoalSpec, budget: Budget,
              policy: str, target: str, clock: Callable[[], float],
              save: Callable[[dict[str, Any]], Awaitable[None]], guards: Guards,
              settling: SettlingPolicy | None = None, trial: int = 0,
              wait: Callable[[float], Awaitable[None]] = asyncio.sleep) -> GameSession:
        """`trial`: the first moves that must each have their predicted effect before play goes on (a
        profile made in the session, plan §4.5); one that does not ends the game before another input."""
        record = {"version": RECORD_VERSION, "family": family.id, "phase": CALIBRATING, "outcome": None, "reason": None,
                  "goal": goal.to_json(), "budget": budget.to_json(), "policy": policy, "target": target,
                  "started_at": clock(), "next_action": 1, "actions_used": 0, "confirmed_actions": 0,
                  "intent": None, "cleanup": CLEANUP_NONE, "unreadable": 0, "recoveries": 0, "discarded": 0,
                  "discarded_plans": 0, "covers": 0, "covered_run": 0, "refused": 0, "trial": trial, "history": []}
        return cls(family, environment, record, clock=clock, save=save, guards=guards, settling=settling, wait=wait)

    # ── the loop ──────────────────────────────────────────────────────

    async def run(self) -> dict[str, Any]:
        if self.record["phase"] == ENDED:
            return self.record
        try:
            self._check_goal()
            if self.record["intent"] is not None:
                await self._reconcile_in_flight()
            while True:
                # a game the screen stopped is not ended while a model's look at it gets play going again
                try:
                    await self._step()
                except _End as end:
                    if not await self._recover(end.outcome, end.reason):
                        raise
                except EnvironmentUnavailable as error:
                    if not await self._recover(None, str(error)):
                        raise
        except _End as end:
            return await self._end(end.outcome, end.reason)
        except EnvironmentUnavailable as error:
            if self.record.get("intent") or self.record.get("cleanup") == CLEANUP_PENDING:
                return await self._end(Outcome.NEEDS_RECOVERY, str(error), release=True)
            return await self._end(Outcome.UNSUPPORTED, str(error))
        except Interrupted:
            raise
        except Exception:
            # the loop failed in a way it does not know: release input and leave it for recovery
            log.exception("game session failed: %s", self.record["family"])
            return await self._end(Outcome.NEEDS_RECOVERY, "error", release=True)

    async def _step(self) -> None:
        await self._check_stops()
        carried, self.carried = self.carried, None
        before, seen = carried or await self._read("decide")
        if before is None:
            if seen.public_facts.get("covered"):
                await self._uncover(seen)
            return
        self.seen_state = before.payload
        if self.record["phase"] == CALIBRATING:
            self.record["phase"] = RUNNING
            await self.save(self.record)
        ineffective = self.record.get("ineffective") or {}
        if ineffective.get("state") == before.id and not ineffective.get("judged"):
            # a delivered move this board did not take: something beside it may stop play (a win notice that
            # leaves the board readable); the whole screen is asked about once per such board
            judged = await self.env.observe(self.record["target"], STALLED)
            stopped = None if judged.public_facts.get("covered") else self.family.estimate(judged)
            if judged.public_facts.get("covered") or (isinstance(stopped, StateEstimate)
                                                      and stopped.payload.get("scene") != before.payload.get("scene")):
                # the screen accounts for the move without effect: it is no failed recovery
                self.record["recoveries"] = 0
                if judged.public_facts.get("covered"):
                    await self._uncover(judged)
                    return
                before, seen = stopped, judged
            else:
                self.record["ineffective"] = {**ineffective, "judged": True}
                await self.save(self.record)
                if self.record["recoveries"] >= MAX_RECOVERIES:
                    raise _End(Outcome.NEEDS_RECOVERY, str(Effect.ABSENT))
        self._check_finished(before)
        excluded = tuple(ineffective.get("actions", ())) if ineffective.get("state") == before.id else ()
        plan = self.family.propose(before, self.goal, self.record["policy"], self.budget.compute, excluded)
        if isinstance(plan, NeedObservation):
            self._unreadable(plan.reason)
            return
        await self._check_plan(plan)
        self.record["proposal"] = plan.to_json()
        # counted from a plan, not a readable screen: a readable screen with no move must still run out
        self.record["unreadable"] = 0
        for_input = seen
        if not await self.env.ready(seen):
            # expired or refused: the screen is read again and the plan kept only if nothing moved
            now, for_input = await self._read("input")
            if now is None:
                return
            if (now.id != before.id or now.geometry_ref != before.geometry_ref
                    or for_input.surface_epoch != seen.surface_epoch or for_input.clock_id != seen.clock_id):
                self._discard()                  # the screen moved on since the plan was made
                return
        await self._check_stops()
        await self._send(before, plan, for_input)

    async def _read(self, purpose: str) -> tuple[StateEstimate | None, Observation]:
        estimate, observation = await self._observe_settled(purpose)
        if estimate is None and observation.public_facts.get("blocked"):
            raise _End(Outcome.NEEDS_RECOVERY, "board_covered")
        if estimate is None and not observation.public_facts.get("covered"):
            self._unreadable("surface_unsettled")
        return estimate, observation

    async def _observe_settled(self, purpose: str, acceptable: Callable[[StateEstimate], bool] | None = None
                              ) -> tuple[StateEstimate | None, Observation]:
        """A state seen unchanged for the policy's stable time, or sooner one that is `acceptable`
        (the predicted result of a move: a transition the rules allow is not an animation frame)."""
        policy = self.settling
        deadline = self.clock() + (policy.timeout_s if policy else 0)
        previous, stable_since = None, self.clock()
        still = policy is not None and policy.still
        pictured, rested = None, self.clock()
        read, read_since = None, self.clock()
        last = None
        while True:
            # Verification still runs for the last allowed input, but never extends the time budget.
            await self._check_stops(check_actions=False)
            # the settling time is waited between looks; a look under way (a capture, and a model asked
            # what it shows, which takes seconds) is bounded by the task's time alone
            budget_left = None if self.budget.max_seconds is None else \
                self.record["started_at"] + self.budget.max_seconds - self.clock()
            if last is not None and ((policy and deadline <= self.clock()) or (budget_left is not None and budget_left <= 0)):
                return last
            looked = self.clock()
            try:
                async with asyncio.timeout(budget_left):
                    observation = await self.env.observe(self.record["target"], purpose)
            except TimeoutError:
                await self._check_stops(check_actions=False)
                if last is not None:
                    return last
                raise _End(Outcome.NEEDS_RECOVERY, "observation_timeout") from None
            await self._check_stops(check_actions=False)
            if observation.target != self.record["target"]:
                raise _End(Outcome.NEEDS_RECOVERY, "target_changed")
            if observation.public_facts.get("covered") or observation.public_facts.get("blocked"):
                return None, observation         # nothing to wait for under a cover: it is closed first
            estimate = self.family.estimate(observation)
            usable = not isinstance(estimate, NeedObservation) and not estimate.unknown_fields
            key = (estimate.id, estimate.geometry_ref, observation.surface_epoch, observation.clock_id,
                   observation.image_ref if still else None) if usable else None
            if key is None or key != previous:
                stable_since = self.clock()
            # the same state under pixels that keep moving: an animation that never ends is not waited out
            if key is None or key[:-1] != read:
                read_since = looked
            read = key[:-1] if key is not None else None
            restless = still and usable and self.clock() - read_since >= RESTLESS_S
            # a predicted result is taken from its first look, or, when the game must be still, its second
            settled = not still or observation.image_ref is None or observation.image_ref == pictured
            if observation.image_ref is None or observation.image_ref != pictured:
                rested = looked                  # when this look began: a capture takes a while itself
            previous, pictured = key, observation.image_ref
            # a move whose pixels never rest came to rest when its state did: that is what trial moves time
            self.rested_at = read_since if restless else rested
            if usable and acceptable is not None and acceptable(estimate) and (settled or restless):
                return estimate, observation
            stable = usable and (policy is None or self.clock() - stable_since >= policy.stable_s or restless)
            last = (estimate if stable else None), observation
            if stable and acceptable is None:
                return estimate, observation
            remaining = deadline - self.clock()
            if remaining <= 0:
                return (estimate if stable else None), observation
            if self.budget.max_seconds is not None:
                remaining = min(remaining, self.record["started_at"] + self.budget.max_seconds - self.clock())
            await self.wait(min(policy.interval_s, max(0, remaining)))

    async def _send(self, before: StateEstimate, plan: ActionPlan, for_input: Observation) -> None:
        receipt = await self._deliver(plan, for_input, before.payload)
        await self._settle(before, plan, receipt)

    async def _uncover(self, seen: Observation) -> None:
        """Get past what covers the board, with the input the environment found for it on this observation
        (`press`): a notice closed, a consent request refused or, offering no refusal, agreed to once the
        person allows it, the game's start screen started while no board was read yet. Its effect is the next
        look's to show: a board read again, or the next cover, until MAX_COVERS."""
        if self.record.get("covered_run", 0) >= MAX_COVERS:
            raise _End(Outcome.NEEDS_RECOVERY, "board_covered")
        kind = seen.public_facts.get("press", CLOSE_COVER)
        if kind == START_GAME and self.record["phase"] != CALIBRATING:
            raise _End(Outcome.NEEDS_RECOVERY, "board_covered")
        if kind not in UNASKED and kind not in self.goal.allowed_meta_actions \
                and kind not in self.record.setdefault("allowed_meta", []):
            # the person's own terms: asked with the next input's approval, covering only what is left
            self.record["allowed_meta"].append(kind)
            await self.save(self.record)
            await self.env.allow_meta(kind)
        try:
            ready = await self.env.ready(seen)
        except Denied:
            if kind == ACCEPT_CONSENT:
                raise _End(Outcome.USER_STOPPED, CONSENT_DECLINED) from None
            raise
        if not ready:
            self._discard()                      # the next look finds the cover again, or the board
            return
        await self._check_stops()
        self.record["covers"] += 1
        self.record["covered_run"] = self.record.get("covered_run", 0) + 1
        plan = ActionPlan(id=f"cover-{seen.id}", based_on_state=seen.id, rule_revision=0, policy_revision=kind,
                          steps=({"kind": kind},), verify_every=1)
        receipt = await self._deliver(plan, seen, None)
        await self._closed(plan, receipt)

    async def _closed(self, plan: ActionPlan, receipt: InputReceipt, *, released: bool = False) -> None:
        if receipt.input_neutral is not Neutral.CONFIRMED and not released:
            await self._release()
        self._remember(plan, receipt, Assessment(effect=Effect.UNKNOWN, goal=GoalStatus.UNKNOWN, terminal=None))
        if receipt.delivery is Delivery.REJECTED and receipt.delivered_steps == 0:
            # refused before any of it was sent (the page changed under it): the cover was not tried
            self.record["covers"] -= 1
            self.record["covered_run"] -= 1
        if self.record["cleanup"] != CLEANUP_PENDING:
            self.record["intent"] = None
        await self.save(self.record)
        if self.record["cleanup"] == CLEANUP_PENDING:
            raise _End(Outcome.NEEDS_RECOVERY, "cleanup_pending")

    async def _deliver(self, plan: ActionPlan, for_input: Observation, before: dict[str, Any] | None) -> InputReceipt:
        number = self.record["next_action"]
        action_id = f"a{number}"
        prepared = PreparedInput(action_id=action_id, attempt_id=f"{action_id}.1", target=self.record["target"],
                                 surface_epoch=for_input.surface_epoch, input_ref=for_input.input_ref or "",
                                 steps=plan.steps, expires_at=for_input.captured_at + INPUT_VALID_S,
                                 clock_id=for_input.clock_id)
        # the intent and the budget it spends are saved before anything is sent (plan §6.3-1)
        self.record["intent"] = {"action_id": action_id, "attempt_id": prepared.attempt_id,
                                 "surface_epoch": prepared.surface_epoch, "clock_id": prepared.clock_id,
                                 "plan": plan.to_json(),
                                 "before": before, "receipt": None}
        self.record["next_action"] = number + 1
        self.record["actions_used"] += 1
        await self.save(self.record)
        receipt = await self.env.execute(prepared, {"session": action_id})
        self.record["intent"]["receipt"] = receipt.to_json()
        await self.save(self.record)
        return receipt

    async def _settle(self, before: StateEstimate, plan: ActionPlan, receipt: InputReceipt, *,
                      released: bool = False) -> None:
        """Delivery, effect, goal and end, each as observed; then the next move starts from the screen."""
        if receipt.input_neutral is not Neutral.CONFIRMED and not released:
            await self._release()
        if self.record["cleanup"] == CLEANUP_PENDING:
            # A held piece can look like a consumed tray slot. Never assess it as a completed move.
            raise _End(Outcome.NEEDS_RECOVERY, "cleanup_pending")
        sent_at = self.clock()
        # nothing of it reached the game: there is no result to wait for, the next plan is made from a fresh
        # look, and it is not counted against the game
        refused = receipt.delivery is Delivery.REJECTED and receipt.delivered_steps == 0
        if refused:
            after, ended = None, False
            assessment = Assessment(effect=Effect.ABSENT, goal=GoalStatus.UNKNOWN, terminal=None)
        else:
            if self.settling and self.settling.animation_s:
                await self.wait(self.settling.animation_s)
            after, seen = await self._observe_settled("verify", acceptable=lambda state:
                self.family.assess(before, plan, receipt, state, self.goal).effect is Effect.CONFIRMED)
            # a result the screen leaves partly unread (an end banner over the tray) may still be an end the
            # rules leave no way out of: play stops, and the unread part never decides a move
            ended = after is None and self.family.ended(before, plan, self.family.estimate(seen))
            assessment = self.family.assess(before, plan, receipt, after, self.goal)
        if ended:
            assessment = replace(assessment, terminal=True)
        self._remember(plan, receipt, assessment)
        if self.record["cleanup"] != CLEANUP_PENDING:
            self.record["intent"] = None
        given_up = None
        # a profile made in the session is the screen's only while its first moves do what the rules say;
        # an input the surface refused before sending it says nothing about the profile either way
        trial = self.record.get("trial", 0) > 0 and receipt.delivery is not Delivery.REJECTED
        unconfirmed = trial and assessment.effect is not Effect.CONFIRMED
        if trial and not unconfirmed:
            self.record["trial"] -= 1
            self._time_trial(self.rested_at - sent_at)
        self.record["refused"] = self.record.get("refused", 0) + 1 if refused else 0
        if self.record["refused"] >= MAX_REFUSED:
            given_up = INPUT_REFUSED
        if assessment.effect is Effect.CONFIRMED:
            self.record["confirmed_actions"] += 1
            self.record["recoveries"] = 0
            self.record["ineffective"] = None
            self.carried = after, seen
            self.seen_state = after.payload
            self._going()
        elif not refused:
            if assessment.effect is Effect.ABSENT and receipt.delivery is Delivery.DELIVERED:
                # the game did not take it; from this same state the next plan picks another move
                kept = self.record.get("ineffective") or {}
                if kept.get("state") != before.id:
                    kept = {"state": before.id, "actions": []}
                # a board whose whole screen was already looked at is not asked about again
                self.record["ineffective"] = {**kept, "actions": [*kept["actions"], plan.steps[0]]}
            # not the predicted move: the next one is planned from what the screen shows now
            self.record["recoveries"] += 1
            # a board that took a move without effect is first looked at whole (`_step`): what stops play
            # there accounts for it, and the game ends there only when nothing does
            pending = (self.record.get("ineffective") or {}).get("state") == before.id \
                and not self.record["ineffective"].get("judged")
            if self.record["recoveries"] >= MAX_RECOVERIES and not pending:
                given_up = str(assessment.effect)
        await self.save(self.record)
        if self.record["cleanup"] == CLEANUP_PENDING:
            raise _End(Outcome.NEEDS_RECOVERY, "cleanup_pending")
        if ended:
            raise _End(Outcome.GAME_OVER, "terminal")
        if unconfirmed:
            raise _End(Outcome.UNSUPPORTED, TRIAL_FAILED)
        if given_up:
            raise _End(Outcome.NEEDS_RECOVERY, given_up)
        if after is not None:
            self._check_finished(after)

    async def _reconcile_in_flight(self) -> None:
        """A restart found an input that was recorded but never settled: it is not sent again. Its
        action id is looked up, whatever is held is released, and the screen says what happened."""
        intent = self.record["intent"]
        if intent.get("receipt"):
            receipt = _receipt(intent["receipt"])
        else:
            receipt = await self.env.lookup(intent["action_id"]) or InputReceipt.unknown(
                intent["action_id"], intent["attempt_id"], self.record["target"], intent["surface_epoch"])
        await self._release()
        if self.record["cleanup"] == CLEANUP_PENDING:
            raise _End(Outcome.NEEDS_RECOVERY, "cleanup_pending")
        plan = _plan(intent["plan"])
        if plan.steps[0].get("kind") == CLOSE_COVER:
            await self._closed(plan, receipt, released=True)
            return
        before = StateEstimate(id=plan.based_on_state, observation_ids=(), family_id=self.family.id,
                               state_schema="", rule_revision=plan.rule_revision, payload=intent["before"],
                               unknown_fields=(), evidence="intent")
        await self._settle(before, plan, receipt, released=True)

    # ── a screen that stopped play ────────────────────────────────────

    def _going(self) -> None:
        """A move was confirmed: whatever stopped play before it is behind."""
        self.record["covered_run"] = 0
        if self.record.get("recovery"):
            self.record["recovery"]["tries"] = 0
        if (recovered := getattr(self.env, "recovered", None)) is not None:
            recovered()

    async def _recover(self, outcome: Outcome | None, reason: str) -> bool:
        """Play is about to end for something on the screen (`STUCK`, or a board the environment never found):
        the environment has a model look at it and send one input of its own (PLAN §8). True when it did, and
        the loop goes on from a fresh look; MAX_TRIES of them in a row with no move confirmed end the game as
        it would have ended. Nothing is tried while an input is in flight or held."""
        recover = getattr(self.env, "recover", None)
        if recover is None or (outcome is not None and reason not in STUCK) or self.record.get("intent") is not None \
                or self.record["cleanup"] == CLEANUP_PENDING or self.record["actions_used"] >= self.budget.max_actions:
            return False
        kept = self.record.setdefault("recovery", {"tries": 0, "total": 0})
        if kept["tries"] >= MAX_TRIES:
            return False
        await self._check_stops(check_actions=False)
        if not await recover(reason, {"state": self.seen_state, "recent": self.record["history"]}):
            return False
        kept["tries"] += 1
        kept["total"] += 1
        self.record.update(unreadable=0, recoveries=0, discarded=0, covered_run=0, refused=0, ineffective=None)
        self.carried = None
        await self.save(self.record)
        return True

    # ── what ends a session ───────────────────────────────────────────

    def _check_goal(self) -> None:
        allowed = self.goal.allowed_meta_actions
        if not allowed <= self.family.meta_actions:
            raise _End(Outcome.UNSUPPORTED, "meta_action_unknown")

    async def _check_stops(self, *, check_actions: bool = True) -> None:
        if await self.guards.cancelled():
            raise _End(Outcome.USER_STOPPED, "cancelled")
        if not await self.guards.lease_held():
            raise _End(Outcome.NEEDS_RECOVERY, "lease_lost")
        if check_actions and self.record["actions_used"] >= self.budget.max_actions:
            raise _End(Outcome.BUDGET_EXHAUSTED, "actions")
        if self.budget.max_seconds is not None and self.clock() - self.record["started_at"] >= self.budget.max_seconds:
            raise _End(Outcome.BUDGET_EXHAUSTED, "time")

    def _check_finished(self, state: StateEstimate) -> None:
        if self.goal.objective == Objective.ACTIONS and self.record["confirmed_actions"] >= (self.goal.target_value or 1):
            raise _End(Outcome.GOAL_REACHED, "actions")
        reached, terminal = self.family.judge(state, self.goal)
        if reached is GoalStatus.REACHED:
            raise _End(Outcome.GOAL_REACHED, WON if state.payload.get("scene") == WON else str(self.goal.objective))
        if terminal:
            raise _End(Outcome.GAME_OVER, "terminal")

    async def _check_plan(self, plan: ActionPlan) -> None:
        """Only what this contract carries out today: one step, checked after every move, of a kind
        the family declares, and a meta action only when the person allowed it: with the task, or, for
        one pressed at a button the screen shows (keep playing after a win), when asked then (the input
        approval names it, `allow_meta`)."""
        if len(plan.steps) != 1:
            raise _End(Outcome.UNSUPPORTED, "multi_step_plan")
        if plan.verify_every != 1:
            raise _End(Outcome.UNSUPPORTED, "deferred_verification")
        kind = plan.steps[0].get("kind")
        if kind not in self.family.action_kinds:
            raise _End(Outcome.UNSUPPORTED, "action_unknown")
        if kind in self.family.meta_actions and kind not in self.goal.allowed_meta_actions:
            if kind not in self.family.buttons:
                raise _End(Outcome.UNSUPPORTED, "meta_action_not_allowed")
            if not self.env.offers(kind):
                raise _End(Outcome.UNSUPPORTED, BUTTON_UNSEEN)
            if kind not in self.record.setdefault("allowed_meta", []):
                self.record["allowed_meta"].append(kind)
                await self.save(self.record)
            await self.env.allow_meta(kind)

    def _unreadable(self, reason: str) -> None:
        self.record["unreadable"] += 1
        if self.record["unreadable"] >= MAX_UNREADABLE:
            raise _End(Outcome.NEEDS_RECOVERY, "unreadable")

    def _discard(self) -> None:
        self.record["discarded"] += 1
        self.record["discarded_plans"] += 1
        if self.record["discarded"] >= MAX_DISCARDED:
            raise _End(Outcome.NEEDS_RECOVERY, "surface_unsettled")

    async def _release(self) -> None:
        """Ask the environment to let go of any held input. This is input neutrality, never the
        game put back as it was; an answer nobody confirmed stays pending."""
        try:
            neutral = await self.env.neutralize({"session": self.record["family"], "intent": self.record.get("intent")})
        except Exception:
            log.exception("game input release failed")
            neutral = Neutral.UNKNOWN
        self.record["cleanup"] = CLEANUP_CONFIRMED if neutral == Neutral.CONFIRMED else CLEANUP_PENDING

    async def _end(self, outcome: Outcome, reason: str, *, release: bool | None = None) -> dict[str, Any]:
        interrupted = outcome is Outcome.USER_STOPPED or reason in ("lease_lost", "error")
        if (interrupted if release is None else release) and self.record["cleanup"] != CLEANUP_PENDING:
            await self._release()
        # a session that never read its board and sent nothing (not even a cover's close) has nothing to
        # recover: the game could not be played here
        if self.record["phase"] == CALIBRATING and outcome is Outcome.NEEDS_RECOVERY \
                and not self.record["actions_used"] and not self.record.get("intent") \
                and self.record["cleanup"] != CLEANUP_PENDING:
            outcome = Outcome.UNSUPPORTED
        self.record.update(phase=ENDED, outcome=str(outcome), reason=reason)
        await self.save(self.record)
        return self.record

    def _time_trial(self, rested_s: float) -> None:
        """A trial move's time to come to rest; after the last, the game waits the longest of them and looks
        once, where until then it looked until two looks in a row showed the same pixels."""
        if self.settling is None or not self.settling.still:
            return
        self.record["rested_s"] = [*self.record.get("rested_s", []), round(rested_s, 3)]
        if self.record["trial"] == 0:
            animation = min(ANIMATION_MAX_S, max(self.settling.animation_s, *self.record["rested_s"]) + ANIMATION_MARGIN_S)
            self.settling = replace(self.settling, animation_s=round(animation, 3), still=False)
            self.record["settling"] = asdict(self.settling)

    def _remember(self, plan: ActionPlan, receipt: InputReceipt, assessment: Assessment) -> None:
        self.record["discarded"] = 0
        entry = {"action_id": receipt.action_id, "kind": plan.steps[0].get("kind"),
                 "delivery": str(receipt.delivery), "delivered_steps": receipt.delivered_steps,
                 "effect": str(assessment.effect), "goal": str(assessment.goal), "terminal": assessment.terminal}
        self.record["history"] = (self.record["history"] + [entry])[-HISTORY_KEPT:]


def _plan(data: dict[str, Any]) -> ActionPlan:
    return ActionPlan(**{**data, "steps": tuple(data["steps"])})


def _receipt(data: dict[str, Any]) -> InputReceipt:
    return InputReceipt(**{**data, "delivery": Delivery(data["delivery"]), "input_neutral": Neutral(data["input_neutral"]),
                           "observation_ids": tuple(data["observation_ids"])})
