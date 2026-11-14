"""One game played by one loop, from the first screen to its end (PLAN §10; docs/design/2026-11-14-agent-loop.md).

    look      the screen once it has come to rest
    read      by the site's generated adapter (L3), else by a model checked by the rule model (L2);
              a screen that shows no board is put to a model for one input of its own (L0) and looked at again
    judge     the rule model says whether the goal is reached or the game over
    choose    the registered solver, leaving out the moves this board did not take
    send      where the adapter's `target` says (L3), else placed on the board box the model gave (L2)
    confirm   the screen read again and the rule model's `assess` of the move
    record    the move, who read it and what came of it; a screen read by the model is a look to write the
              adapter from
    move up or down between the levels by what the moves showed

Who reads is the only thing that changes between the levels: the loop is the same. The model reads until
enough moves are confirmed to write an adapter from; the adapter then reads until it leaves a screen unread or
its moves stop making sense, where the model reads that screen and the adapter is checked against it and
written again if it must be. When no adapter passes, the game goes on read by the model. Nothing here waits
for a person: a game ends at its goal, its own end, a budget (inputs, time, the tokens of its model
questions), or when a model sees nothing more to do (`gave_up`, with what it said of the screen).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
import logging
import time
from typing import Any, Protocol

from . import adaptation, catalog
from .adaptation import (HELD_BACK, MAX_GENERATIONS, MAX_RELEARNS, AdaptationFailed, Adaptation, Look, Made, Store, Trace,
                         _agrees, _board, _shape, adopt, explorable, identify)
from .adapters.generated import GeneratedAdapter
from .contracts import Budget, GoalSpec, NeedObservation, Observation, Outcome
from .execution import TAP_MS
from .families import state_id
from .model import Model, TurnRequest, TurnResult
from .play import Run, check
from .recovery import GIVE_UP, MAX_TRIES, NONE, Recovery

log = logging.getLogger("gamelab.agent")

RECORD_VERSION = 2
L2, L3 = "L2", "L3"
# the first moves read by an adapter (made now, or kept from another game) that must each do what the rules
# predict: one that does not sends the screen to the model
TRIAL_MOVES = 3
# moves in a row that did not do what the rules predict before the reading (L3) or the screen (L2) is doubted
MAX_STALLED = 3
# confirmed moves read by the model after an adapter could not be written, before it is tried once more, and
# how often in one game
RETRY_LOOKS, RETRIES = 12, 2
# a game that must be still but whose pixels never rest is settled once its state has read the same this long;
# a screen the adapter leaves unread the same way this long is not waited on further
RESTLESS_S = 1.0
# after its trial moves a still game waits the longest of them plus this margin, then looks once
ANIMATION_MARGIN_S, ANIMATION_MAX_S = 0.1, 2.0
# moves kept in the record and told to a model asked about a screen
HISTORY_KEPT = 8
# a direction key on a surface without keys is a swipe across the board
SWIPES = {"ArrowLeft": (-1, 0), "ArrowRight": (1, 0), "ArrowUp": (0, -1), "ArrowDown": (0, 1)}
SWIPE_SPAN, SWIPE_MS = 0.6, 120
WON = "won"


class Watch(Protocol):
    """What a run may hang on the loop to measure it; the game is not told."""

    async def seen(self, png: bytes) -> None: ...
    async def sending(self, number: int, action: dict[str, Any], state: dict[str, Any], sent: dict[str, Any],
                      observed_s: float) -> None: ...
    async def sent(self) -> None: ...


@dataclass(frozen=True)
class Settling:
    """How long a move's result is waited for (the adapter's `settling`, as the family starts it)."""
    timeout_s: float = 4.0
    interval_s: float = 0.15
    stable_s: float = 0.25
    animation_s: float = 0.0
    still: bool = False


@dataclass
class Reading:
    png: bytes
    state: dict[str, Any]
    at: float
    by: str
    # the look it was kept as, when the model read it
    look: Look | None = None

    @property
    def id(self) -> str:
        return state_id(self.state)


class Metered:
    """A model with what its questions cost counted."""

    def __init__(self, model: Model) -> None:
        self.model, self.input, self.output, self.questions = model, 0, 0, 0

    async def complete(self, request: TurnRequest) -> TurnResult:
        self.questions += 1
        result = await self.model.complete(request)
        self.input += result.usage.input_tokens
        self.output += result.usage.output_tokens
        return result


class _End(Exception):
    def __init__(self, outcome: Outcome, reason: str, detail: str = "") -> None:
        super().__init__(reason)
        self.outcome, self.reason, self.detail = outcome, reason, detail


class Agent:
    def __init__(self, surface: Any, model: Model, family_id: str | None, *, goal: GoalSpec, budget: Budget,
                 store: Store | None = None, policy: str | None = None, fresh: bool = False, hint: str | None = None,
                 watch: Watch | None = None, cancel: asyncio.Event | None = None, accept_consent: bool = False) -> None:
        self.surface, self.model, self.family_id = surface, Metered(model), family_id
        self.goal, self.budget, self.store, self.fresh, self.hint = goal, budget, store, fresh, hint
        self.asked_policy, self.watch, self.cancel, self.accept_consent = policy, watch, cancel, accept_consent
        self.started = time.monotonic()
        # what was found of the game nobody named, and the families still to try for it
        self.found: dict[str, Any] = {}
        self.candidates: list[str] = []
        self.named = False
        self.adaptation: Adaptation | None = None
        self.recovery: Recovery | None = None
        self.family: Any = None
        self.policy: str | None = None
        self.site = ""
        # the looks the model read (what an adapter is written from), the adapter and how it was made
        self.trace: Trace | None = None
        self.made: Made | None = None
        self.adapter: GeneratedAdapter | None = None
        self.settling = Settling()
        # where the model was told the board is, and the rules it is read by
        self.board: list[int] | None = None
        # the adapter's reading is doubted: the model reads until one of its moves is confirmed. `blind`: only
        # because it found no board, which an input that clears the screen puts right
        self.distrusted = self.blind = False
        # a look no move vouches for yet: dropped when the move played from it is not confirmed
        self.unproven: Look | None = None
        # the last result confirmed: the next move's board, with no second look
        self.carried: Reading | None = None
        self.state: dict[str, Any] | None = None
        self.last_png: bytes | None = None
        # why the last read gave no board, what the adapter read of it, and whether the rules alone end the game
        self.why = "board_not_found"
        self.unread: dict[str, Any] | None = None
        self.over = False
        self.moves: list[dict[str, Any]] = []
        self.used = self.confirmed = self.stalled = self.tries = self.shots = self.sequence = 0
        self.shrugged = False
        self.ineffective: dict[str, Any] | None = None
        self.trial = 0
        self.rested: list[float] = []
        self.rested_at = 0.0
        self.relearned = self.generations = self.attempts = 0
        self.need = adaptation.EXPLORE_MOVES
        self.failures: list[dict[str, Any]] = []
        self.model_seconds = 0.0
        self.levels = {L2: 0, L3: 0}

    # ── the game ──────────────────────────────────────────────────────

    async def run(self) -> tuple[Run, dict[str, Any]]:
        try:
            try:
                await self._begin()
                while True:
                    await self._turn()
            except _End as end:
                outcome, reason, detail = end.outcome, end.reason, end.detail
            except Exception as error:
                log.exception("the game's loop failed")
                outcome, reason, detail = Outcome.GAVE_UP, "error", f"{type(error).__name__}: {error}"[:2000]
            if outcome in (Outcome.GOAL_REACHED, Outcome.GAME_OVER):
                await self._keep_for_next()
            record = {"version": RECORD_VERSION, "family": self.family_id, "phase": "ended", "outcome": str(outcome),
                      "reason": reason, "detail": detail, "goal": self.goal.to_json(), "budget": self.budget.to_json(),
                      "policy": self.policy, "actions_used": self.used, "confirmed_actions": self.confirmed,
                      "levels": dict(self.levels), "history": self.moves[-HISTORY_KEPT:]}
            run = Run(record, list(self.moves), looks=self.shots, last_screen=self.last_png,
                      recoveries=list(self.recovery.log) if self.recovery else [])
            failed = {"failed": reason, "detail": detail} if outcome is Outcome.UNSUPPORTED else {}
            return run, {**self.summary(), **self.found, **failed}
        finally:
            self.close()

    def summary(self) -> dict[str, Any]:
        return {"reused": self.made.reused if self.made else None, "generations": self.generations,
                "relearned": self.relearned, "looks": len(self.trace.looks) if self.trace else 0,
                "questions": self.model.questions, "seconds": round(self.model_seconds, 1),
                "tokens": {"input": self.model.input, "output": self.model.output},
                "recovery": self.recovery.summary() if self.recovery else None,
                "digest": self.adapter.digest if self.adapter else None,
                "calls": self.adapter.calls if self.adapter else None,
                **({"generation_failures": self.failures} if self.failures else {})}

    def close(self) -> None:
        if self.adapter is not None:
            self.adapter.close()
            self.adapter = None
        if self.store is not None and self.recovery is not None and self.family_id and self.site and self.recovery.known:
            self.store.save_recoveries(self.site, self.family_id, self.recovery.known)

    async def _begin(self) -> None:
        """The family the game is played as: the one given, the one a kept adapter of this site names, else
        each a model names for the screen, likeliest first (`_take`)."""
        self.site = await self.surface.address()
        self.candidates = [self.family_id] if self.family_id else []
        if self.family_id is None:
            kept = [] if self.store is None or self.fresh else \
                [name for name in explorable() if self.store.load(self.site, name) is not None]
            if kept:
                self.candidates, self.found["identified"] = kept[:1], {"family": kept[0], "source": "kept", "questions": 0}
            else:
                asked = await identify(self.surface, self.model, Recovery(self.model, None, known=[]), hint=self.hint)
                self.candidates, self.named = list(asked["families"]), True
                self.found["identified"] = {"family": None, "source": "model", "candidates": list(self.candidates),
                                            "tried": [], "questions": asked["questions"], "inputs": asked["inputs"],
                                            "situation": asked["situation"]}
        if not await self._next_family():
            raise _End(Outcome.UNSUPPORTED, "game_not_identified", self.found.get("identified", {}).get("situation") or "")

    async def _next_family(self) -> bool:
        """The next family the game may be, taken when its board reads from the screen."""
        while self.candidates:
            if await self._take(self.candidates.pop(0)):
                return True
        return False

    async def _take(self, family_id: str) -> bool:
        if self.adapter is not None:
            self.adapter.close()
        self.adapter = self.trace = self.made = self.board = self.carried = self.unproven = None
        try:
            self.policy = check(self.goal, self.budget, family_id, self.asked_policy)
            self.recovery = Recovery(self.model, family_id, accept_consent=self.accept_consent,
                                     known=self.store.recoveries(self.site, family_id) if self.store else None)
            self.adaptation = Adaptation(self.model, family_id, policy=self.policy, compute=self.budget.compute)
        except AdaptationFailed as error:
            raise _End(Outcome.UNSUPPORTED, error.reason, error.detail) from None
        except ValueError as error:
            raise _End(Outcome.UNSUPPORTED, str(error)) from None
        self.family_id = family_id
        kept = None if self.store is None or self.fresh else self.store.load(self.site, family_id)
        if kept is not None:
            source, meta, self.trace = kept
            self.made, self.generations = Made(source, meta["generations"], True, meta["report"]), meta["generations"]
            self.board = self.trace.looks[0].board
            self._adopted()
        elif self.named:
            self.found["identified"]["tried"].append(family_id)
            # a family the screen's board cannot be read as is not this game
            if not await self._asking(self.adaptation.probe(self.surface)):
                return False
        self.family = catalog.family(family_id).bind({"rules": self.trace.rules} if self.trace else None)
        if "identified" in self.found:
            self.found["identified"]["family"] = family_id
        return True

    # ── one move ──────────────────────────────────────────────────────

    async def _turn(self) -> None:
        self._check_stops()
        before, self.carried = self.carried, None
        if before is None:
            before = await self._read(None)
            if before is None:
                await self._unstick(self.why)
                return
        self.state = before.state
        self._judge(before.state)
        excluded = self.ineffective["actions"] if self.ineffective and self.ineffective["state"] == before.id else []
        action = self.family.choose(before.state, self.policy, self.budget.compute, tuple(excluded))
        if action is None or action["kind"] in self.family.meta_actions:
            # every move this board offers was tried without effect, or the game waits at a message of its own
            await self._unstick("absent" if action is None else action["kind"])
            return
        sent = self._aim(action, before)
        if sent is None:
            self._doubt(before)
            return
        if self.adapter is None or self.distrusted:
            self._afford()               # the move's result is the model's to read: none is sent that it cannot be asked about
        if self.watch is not None:
            await self.watch.sending(self.used + 1, action, before.state, sent, before.at)
        await self._send(sent)
        self.used += 1
        if self.watch is not None:
            await self.watch.sent()
        sent_at = time.monotonic()
        try:
            after = await self._read((before, action))
        except _End:
            self._remember(before, action, "unknown", None)
            raise
        effect = "unknown" if after is None else self._effect(before.state, action, after.state)
        self._remember(before, action, effect, after)
        if self.over:
            raise _End(Outcome.GAME_OVER, "terminal")
        if effect == "confirmed":
            await self._confirmed(before, action, sent, after, sent_at)
            return
        self.stalled += 1
        if effect == "absent":
            kept = self.ineffective if self.ineffective and self.ineffective["state"] == before.id else \
                {"state": before.id, "actions": []}
            self.ineffective = {**kept, "actions": [*kept["actions"], action]}
        if before.look is not None and before.look is self.unproven:
            self._drop_unproven()
        if after is None and self.why == "label_failed":
            # the model saw no board where the move's result should be: what is in the way is dealt with now
            await self._unstick(self.why)
            return
        if before.by == L3 and (self.trial > 0 or self.stalled >= MAX_STALLED):
            self._doubt(before)

    async def _confirmed(self, before: Reading, action: dict[str, Any], sent: dict[str, Any], after: Reading,
                         sent_at: float) -> None:
        self.confirmed += 1
        self.stalled = self.tries = 0
        self.shrugged, self.ineffective, self.carried, self.state = False, None, after, after.state
        self.recovery.succeeded()
        if before.by == L3 and after.by == L3 and self.trial > 0:
            self.trial -= 1
            self._time_trial(self.rested_at - sent_at)
        if before.look is None or after.look is None:
            return
        # two screens the model read with a confirmed move between them: what an adapter is written from
        before.look.action, before.look.sent = action, sent
        if before.look is self.unproven:
            self.unproven = None
        if self.adapter is not None:
            if self.distrusted:
                # the adapter was doubted: it is checked against what the model read, and written again if it must be
                self.relearned += 1
                await self._recheck()
        elif sum(look.action is not None for look in self.trace.looks) >= self.need and self.attempts <= RETRIES:
            await self._generate(None)

    def _doubt(self, before: Reading) -> None:
        """The adapter's reading may be what is wrong: the model reads the next screens."""
        if before.by == L3 and self.adapter is not None:
            if self.relearned >= MAX_RELEARNS:
                self._demote("relearn_limit")
            else:
                self.distrusted = True
            self.stalled = 0

    def _effect(self, before: dict[str, Any], action: dict[str, Any], after: dict[str, Any]) -> str:
        return self.family.model.assess(before, action, after, self.family.rules)["effect"]

    def _judge(self, state: dict[str, Any]) -> None:
        objective = str(self.goal.objective)
        if objective == "actions" and self.confirmed >= (self.goal.target_value or 1):
            raise _End(Outcome.GOAL_REACHED, "actions")
        seen = self.family.model.status(state, self.family.rules, {"objective": objective, "target_value": self.goal.target_value})
        if seen["goal"] == "reached":
            raise _End(Outcome.GOAL_REACHED, WON if state.get("scene") == WON else objective)
        if seen["terminal"]:
            raise _End(Outcome.GAME_OVER, "terminal")

    def _remember(self, before: Reading, action: dict[str, Any], effect: str, after: Reading | None) -> None:
        self.levels[before.by] += 1
        self.moves.append({"action_id": f"a{self.used}", "kind": action.get("kind"), "action": action, "level": before.by,
                           "read_after_by": after.by if after else None, "delivery": "delivered", "effect": effect,
                           "round": self.relearned, "at_s": round(time.monotonic() - self.started, 2)})

    def _check_stops(self) -> None:
        if self.cancel is not None and self.cancel.is_set():
            raise _End(Outcome.BUDGET_EXHAUSTED, "cancelled")
        if self.used >= self.budget.max_actions:
            raise _End(Outcome.BUDGET_EXHAUSTED, "actions")
        self._check_time()

    def _check_time(self) -> None:
        if self._left() is not None and self._left() <= 0:
            raise _End(Outcome.BUDGET_EXHAUSTED, "time")

    def _left(self) -> float | None:
        return None if self.budget.max_seconds is None else self.budget.max_seconds - (time.monotonic() - self.started)

    def _affordable(self) -> bool:
        return self.budget.max_input_tokens is None or self.model.input < self.budget.max_input_tokens

    def _afford(self) -> None:
        """Before a model is asked anything: a game whose questions used up their tokens ends there."""
        if not self._affordable():
            raise _End(Outcome.BUDGET_EXHAUSTED, "tokens")

    async def _asking(self, question: Any) -> Any:
        started = time.monotonic()
        try:
            return await question
        finally:
            self.model_seconds += time.monotonic() - started

    # ── reading ───────────────────────────────────────────────────────

    async def _read(self, expect: tuple[Reading, dict[str, Any]] | None) -> Reading | None:
        """The screen as a board to play on, read by the adapter when there is one that is trusted, else by
        the model; None when it shows none (`why`). `expect`: the move just played and the board it was
        played from, whose result this is."""
        self.over = False
        if self.adapter is not None and not self.distrusted:
            reading = await self._read_code(expect)
            if reading is not None:
                return reading
            if expect is not None and self.unread is not None and self.family.model.ended(
                    expect[0].state, expect[1], self.unread, self.family.rules):
                # what the screen leaves unread (an end notice over part of it) cannot undo an end the rules see
                self.over = True
                return None
            if self.relearned >= MAX_RELEARNS:
                self._demote("relearn_limit")
            elif self.unread is None and expect is None:
                # nothing vouches for what the model will read here until a move from it is confirmed
                self.distrusted = self.blind = True
            else:
                reading = await self._read_model(expect, self.unread)
                if reading is not None:
                    self.relearned += 1
                    await self._recheck()
                return reading
        return await self._read_model(expect, None)

    def _observe(self, png: bytes) -> tuple[Any, str | None]:
        """One capture read by the adapter: its state, or why there is none."""
        adapter = self.adapter
        self.sequence += 1
        observation = Observation(f"o{self.sequence}", self.sequence, self.surface.name, 0, time.monotonic(), "agent")
        measured = adapter.layout is not None
        if not measured and isinstance(found := adapter.measure(png), NeedObservation):
            return None, found.reason
        state = adapter.read(observation, png)
        if isinstance(state, NeedObservation) and measured:
            # the board may lie elsewhere now (another window size): measured again, and read again if it does
            found = adapter.measure(png)
            if isinstance(found, NeedObservation):
                return None, found.reason
            if found:
                state = adapter.read(observation, png)
        if isinstance(state, NeedObservation):
            return None, state.reason
        return state, None

    async def _read_code(self, expect: tuple[Reading, dict[str, Any]] | None) -> Reading | None:
        """The adapter's reading once the screen has come to rest: a state seen unchanged for the settling's
        stable time, or sooner the predicted result of the move just played."""
        policy, clock = self.settling, time.monotonic
        if expect is not None and policy.animation_s:
            await asyncio.sleep(policy.animation_s)
        deadline = clock() + policy.timeout_s
        previous = pictured = read = unread = None
        stable_since = rested = read_since = unread_since = clock()
        while True:
            self._check_time()
            looked = clock()
            png = await self.surface.capture()
            self.shots += 1
            self.last_png = png
            if self.watch is not None:
                await self.watch.seen(png)
            state, why = self._observe(png)
            usable = state is not None
            if not usable:
                self.why = why
                self.unread = self.adapter.partial if why == adaptation.CELLS_UNREAD else None
                seen = (why, self.unread)
                if seen != unread:
                    unread, unread_since = seen, looked
                if clock() - unread_since >= max(policy.stable_s, RESTLESS_S) or clock() >= deadline:
                    return None
            image = self.adapter.picture(png) if usable and policy.still else None
            key = (state.id, state.geometry_ref, image) if usable else None
            if key is None or key != previous:
                stable_since = clock()
            # the same state under pixels that keep moving: an animation that never ends is not waited out
            if key is None or key[:-1] != read:
                read_since = looked
            read = key[:-1] if key is not None else None
            restless = policy.still and usable and clock() - read_since >= RESTLESS_S
            settled = not policy.still or image == pictured
            if image is None or image != pictured:
                rested = looked
            previous, pictured = key, image
            self.rested_at = read_since if restless else rested
            if usable:
                reading = Reading(png, state.payload, looked, L3)
                if expect is not None and (settled or restless) \
                        and self._effect(expect[0].state, expect[1], state.payload) == "confirmed":
                    return reading
                stable = clock() - stable_since >= policy.stable_s or restless
                if stable and (expect is None or clock() >= deadline):
                    return reading
                if clock() >= deadline:
                    self.why, self.unread = "surface_unsettled", None
                    return None
            await asyncio.sleep(max(0.0, min(policy.interval_s, deadline - clock())))

    async def _read_model(self, expect: tuple[Reading, dict[str, Any]] | None, partial: dict[str, Any] | None) -> Reading | None:
        """The screen read by the model into a state the rule model accepts, and kept as a look. With `expect`
        it must be a result the rules allow of that move (or the board unchanged: a move the game did not
        take); with `partial`, what the adapter did read of the screen must agree with it."""
        self._afford()
        if expect is None:
            self._drop_unproven()
        tool, first = self.adaptation, self.trace is None
        if expect is not None:
            await asyncio.sleep(adaptation.SETTLE_S)

        def accept(state: dict[str, Any]) -> bool:
            return (partial is None or _agrees(_board(partial), _board(state))) and (
                expect is None or self._effect(expect[0].state, expect[1], state) in ("confirmed", "absent"))
        started = time.monotonic()
        try:
            if first and tool.first is not None:
                (png, state, board, rules), tool.first = tool.first, None
            else:
                png, state, board, rules = await self._asking(tool._label(
                    self.surface, self.board, self.trace.rules if self.trace else None,
                    accept if expect is not None or partial is not None else None, patient=False))
                tool.last_state, tool.frames = state, await tool._frames(self.surface, png)
        except AdaptationFailed as error:
            # no board on the screen, or one the model could not read as the rules have it
            self.why = "label_failed" if error.detail == "no_board" else "inconsistent"
            return None
        except Exception as error:
            log.exception("the model could not be asked")
            self.why = "model_failed"
            self.failures.append({"reason": "model_failed", "detail": f"{type(error).__name__}: {error}"[:500]})
            return None
        self.last_png = png
        if first:
            self.trace, self.board = Trace(self.family_id, rules, self.site), board
            self.family = catalog.family(self.family_id).bind({"rules": rules})
        look = Look(png, state, self.board, frames=tool.frames)
        self.trace.looks.append(look)
        if expect is None and partial is None:
            self.unproven = look
        return Reading(png, state, started, L2, look)

    def _drop_unproven(self) -> None:
        """A look nothing bore out is not one to write an adapter from."""
        if self.unproven is not None and self.trace is not None:
            self.trace.looks = [look for look in self.trace.looks if look is not self.unproven]
        self.unproven = None

    # ── the adapter ───────────────────────────────────────────────────

    def _adopted(self) -> None:
        """Play by the adapter as made: its first moves are trial moves."""
        if self.adapter is not None:
            self.adapter.close()
        self.adapter = adaptation.GeneratedAdapter(self.family_id, self.made.source, rules=self.trace.rules, site=self.site)
        self.settling = Settling(**self.adapter.played.get("settling", {}))
        self.distrusted, self.blind, self.trial, self.rested, self.carried = False, False, TRIAL_MOVES, [], None

    def _demote(self, reason: str) -> None:
        """No adapter reads this game: the model reads it from here on."""
        log.info("the game goes on read by the model: %s", reason)
        if self.adapter is not None:
            self.adapter.close()
        self.adapter, self.distrusted, self.blind = None, False, False
        if self.carried is not None and self.carried.by == L3:
            self.carried = None
        self.need = sum(look.action is not None for look in self.trace.looks) + RETRY_LOOKS

    async def _recheck(self) -> None:
        """The adapter against every look so far, the one just read included: kept when it reads them all,
        else written again from them."""
        report = adopt(self.made.source, self.trace)
        if report["passed"]:
            self.distrusted, self.blind, self.trial = False, False, TRIAL_MOVES
            if self.store is not None:
                self.store.save(self.trace, self.made)
        else:
            await self._generate((self.made.source, report["problems"]))

    async def _generate(self, previous: tuple[str, list[str]] | None) -> bool:
        """An adapter written from the looks so far and adopted when it passes on all of them. When none
        passes the game goes on read by the model, and one is tried again after RETRY_LOOKS more moves."""
        tool, trace = self.adaptation, self.trace
        if not any(look.held for look in trace.looks):
            for look in trace.looks[-HELD_BACK:]:
                look.held = True
        self._settle_rules()
        self.attempts += 1
        try:
            made = await self._asking(tool.make(trace, previous, allowed=self._affordable))
        except Exception as error:
            if not isinstance(error, AdaptationFailed):
                log.exception("no adapter could be written")
                error = AdaptationFailed("model_failed", f"{type(error).__name__}: {error}")
            self.generations += MAX_GENERATIONS if error.reason == "no_adapter_passed" else 0
            self.failures.append({"reason": error.reason, "detail": error.detail[:2000]})
            self._demote(error.reason)
            return False
        self.generations += made.generations
        self.made = Made(made.source, self.generations, False, made.report, made.seconds, made.questions)
        self._adopted()
        if self.store is not None:
            self.store.save(trace, self.made)
        return True

    def _settle_rules(self) -> None:
        """A rule value the game may be played without knowing is taken from the screen only when every screen
        read said the same (a count beside the board, not a clock): a wrong value is worse than none."""
        tool, changed = self.adaptation, False
        for name in tool._unknowable():
            said = {told.get(name) for told in tool.told}
            if len(said) == 1 and None not in said and self.trace.rules.get(name) is None:
                self.trace.rules[name], changed = said.pop(), True
        if changed:
            self.family = catalog.family(self.family_id).bind({"rules": self.trace.rules})

    async def _keep_for_next(self) -> None:
        """A game that ended before an adapter was tried still showed its screens: enough of them are written
        from now, for the next game on this site."""
        if self.store is None or self.adapter is not None or self.attempts or self.trace is None \
                or len(self.trace.looks) <= HELD_BACK + 2 or not self._affordable():
            return
        try:
            await self._generate(None)
        except Exception:
            log.exception("no adapter was kept for the next game")

    def _time_trial(self, rested_s: float) -> None:
        """A trial move's time to come to rest; after the last, a game that had to be still waits the longest
        of them and looks once."""
        if not self.settling.still:
            return
        self.rested.append(round(rested_s, 3))
        if self.trial == 0:
            animation = min(ANIMATION_MAX_S, max(self.settling.animation_s, *self.rested) + ANIMATION_MARGIN_S)
            self.settling = replace(self.settling, animation_s=round(animation, 3), still=False)

    # ── input ─────────────────────────────────────────────────────────

    def _aim(self, action: dict[str, Any], before: Reading) -> dict[str, Any] | None:
        """The input a move is played with: where the adapter's `target` says for a board it read, else placed
        on the board box the model gave. None when the adapter's input is one no game is given."""
        if before.by == L2:
            return self.adaptation.input_for(action, self.board, _shape(self.family, before.state))
        asked = self.adapter._call("target", action=action, state=before.state, layout=self.adapter.layout)
        try:
            self.adapter.checked_input(asked)
        except ValueError:
            return None
        return asked

    async def _send(self, sent: dict[str, Any]) -> None:
        surface = self.surface
        if sent["kind"] == "key":
            if "keys" in surface.kinds:
                await surface.key(sent["key"])
            else:
                left, top, width, height = self.adapter.layout["board"] if self.adapter and self.adapter.layout else self.board
                across, down = SWIPES[sent["key"]]
                x, y = left + width / 2, top + height / 2
                reach_x, reach_y = across * width * SWIPE_SPAN / 2, down * height * SWIPE_SPAN / 2
                await surface.drag(x - reach_x, y - reach_y, x + reach_x, y + reach_y, SWIPE_MS)
        elif sent["kind"] == "tap":
            await surface.tap(sent["x"], sent["y"], TAP_MS)
        else:
            await surface.drag(sent["x0"], sent["y0"], sent["x1"], sent["y1"], sent["ms"])

    # ── a screen that stopped play ────────────────────────────────────

    async def _unstick(self, reason: str) -> None:
        """Play cannot go on from this screen: a model is shown it and sends one input of its own (L0), and
        the screen is looked at again. The game ends when the model sees its end on the screen, sees nothing
        to do, or MAX_TRIES inputs in a row brought no confirmed move."""
        if reason == "inconsistent" and await self._other_family():
            return
        if self.tries >= MAX_TRIES:
            if await self._other_family():
                return
            raise _End(Outcome.GAVE_UP, reason, self._situation())
        self._afford()
        self.tries += 1
        self.carried = None
        recent = [{key: move[key] for key in ("action_id", "kind", "effect")} for move in self.moves[-HISTORY_KEPT:]]
        if await self._asking(self.recovery.attempt(self.surface, reason, {"state": self.state, "recent": recent},
                                                    timeout_s=self._left())):
            if self.blind:
                self.distrusted = self.blind = False
            return
        action = self.recovery.log[-1]["action"] or {}
        if action.get("kind") == GIVE_UP and "end" in action:
            won = action["end"] == WON and str(self.goal.objective) == "play_until_end"
            raise _End(Outcome.GOAL_REACHED if won else Outcome.GAME_OVER, WON if won else "end_screen", self._situation())
        if action.get("kind") == NONE and not self.shrugged:
            # the model sees a board in play: it is read once more before the game is given up
            self.shrugged = True
            return
        if await self._other_family():
            return
        raise _End(Outcome.GAVE_UP, reason, self._situation())

    def _situation(self) -> str:
        return (self.recovery.log[-1]["situation"] or "") if self.recovery and self.recovery.log else ""

    async def _other_family(self) -> bool:
        """A game a model named whose moves never did what the family's rules say may be another's."""
        if not self.named or self.confirmed or not self.candidates:
            return False
        self.tries, self.stalled, self.shrugged = 0, 0, False
        if not await self._next_family():
            raise _End(Outcome.UNSUPPORTED, "game_not_identified", self._situation())
        return True


async def play(surface: Any, model: Model, family_id: str | None, *, goal: GoalSpec, budget: Budget,
               store: Store | None = None, policy: str | None = None, fresh: bool = False, hint: str | None = None,
               watch: Watch | None = None, cancel: asyncio.Event | None = None, accept_consent: bool = False,
               **ignored: Any) -> tuple[Run, dict[str, Any]]:
    """One game from its first screen to its end: the whole game as one `Run`, and how it was read (the
    adapter, the looks, the model's questions and what the game was found to be)."""
    return await Agent(surface, model, family_id, goal=goal, budget=budget, store=store, policy=policy, fresh=fresh,
                       hint=hint, watch=watch, cancel=cancel, accept_consent=accept_consent).run()
